"""更新编排：拉取 -> 入库 -> 评估规则 -> 重绘图表。

自动更新策略：
- update_once() 幂等，一天最多完整跑一轮（meta.last_full_update 控制，可 force）。
- 服务进程内置调度线程：交易日 >= update_time 后自动执行；启动时补跑当天漏掉的。
- 快照全失败时当天不标记完成，调度器自动重试；连续 3 轮全失败则放弃当天。
- 请求量很小（每ETF 1次K线 + 1次快照），且带退避重试，不易触发东财限流。
"""
import datetime as dt
import json
import time
import threading
from pathlib import Path

from . import charts, datasource, rules, storage

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"

_update_lock = threading.Lock()


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _today():
    return dt.datetime.now().strftime("%Y-%m-%d")


def _hhmm():
    return dt.datetime.now().strftime("%H:%M")


def should_update_now(cfg):
    """交易日且已到更新时间、且今天还没更新过。"""
    if dt.datetime.now().weekday() >= 5:
        return False
    if _hhmm() < cfg.get("update_time", "15:45"):
        return False
    return storage.get_meta("last_full_update") != _today()


def update_once(cfg, force=False, with_charts=True):
    """执行一轮完整更新，返回摘要 dict。线程安全。"""
    with _update_lock:
        if not force and storage.get_meta("last_full_update") == _today():
            return {"skipped": True, "reason": "今天已更新过", "date": _today()}

        summary = {"skipped": False, "etfs": {}, "signals": [], "charts": []}
        dates_by_symbol = {}

        # 1) 历史K线（价格/成交量，腾讯源，550日一次拉全量）。
        #    source 必须传 None：COALESCE 才不会覆盖快照日已写入的份额来源
        #    （否则次日回填会把 eastmoney 覆盖成 kline 来源，规则永远热身中）。
        #    单只失败只记摘要，不中止整轮更新。
        for e in cfg["etfs"]:
            try:
                k = datasource.fetch_kline(e["symbol"], cfg.get("history_days", 550))
            except Exception as exc:
                summary["etfs"][e["code"]] = {"kline": f"failed: {exc}"}
                continue
            rows, prev_close = [], None
            for r in k:
                pct = round((r["close"] / prev_close - 1.0) * 100, 2) if prev_close else None
                prev_close = r["close"]
                rows.append(dict(r, code=e["code"], pct_chg=pct, amount=None,
                                 shares=None, scale=None, source=None))
            storage.upsert_daily(rows)
            dates_by_symbol[e["symbol"]] = {r["date"] for r in k}
            summary["etfs"][e["code"]] = {"kline_rows": len(k),
                                          "latest": k[-1]["date"] if k else None}

        # 2) 每日份额快照（东财主源，失败降级腾讯；东财限流敏感，逐只间隔请求）
        for e in cfg["etfs"]:
            snap = None
            try:
                snap = datasource.fetch_snapshot_em(e["secid"])
            except Exception:
                snap = None
            if not snap:
                try:
                    snap = datasource.fetch_snapshot_qq(e["symbol"])
                except Exception:
                    snap = None
            time.sleep(1.0)
            if not snap:
                summary["etfs"][e["code"]]["snapshot"] = "failed"
                continue
            trade_date = snap.get("quote_date")
            kdates = dates_by_symbol.get(e["symbol"]) or set()
            if trade_date and kdates and trade_date not in kdates:
                # 快照时间戳不在K线日期内（极少见）：退回K线最新交易日，
                # 且不覆盖该日价格字段（价格与该日期不匹配）。
                trade_date = summary["etfs"][e["code"]].get("latest") or trade_date
            if not trade_date:
                summary["etfs"][e["code"]]["snapshot"] = "skipped"
                continue
            same_day = trade_date == snap.get("quote_date")
            row = {"code": e["code"], "date": trade_date,
                   "close": snap.get("price") if same_day else None,
                   "pct_chg": snap.get("pct_chg") if same_day else None,
                   "volume": None, "amount": snap.get("amount"),
                   "amount_est": None, "shares": snap.get("shares"),
                   "scale": snap.get("scale"), "source": snap.get("source")}
            storage.upsert_daily([row])
            summary["etfs"][e["code"]]["snapshot"] = {
                "date": trade_date, "shares_yi": round(snap["shares"] / 1e8, 2) if snap.get("shares") else None,
                "scale_yi": round(snap["scale"] / 1e8, 2) if snap.get("scale") else None,
                "source": snap.get("source")}

        # 3) 规则评估（两条判断规则）
        try:
            rule_result = rules.evaluate(cfg)
            summary["rules"] = rule_result
            summary["signals"] = rule_result.get("signals", [])
        except Exception as exc:  # 规则失败不阻塞更新
            summary["rules"] = {"status": "error", "message": repr(exc)}

        # 4) 重绘图表
        if with_charts:
            try:
                sigs = storage.get_signals(limit=60)
                summary["charts"] = [str(p) for p in charts.render_all(cfg, sigs, days=120)]
            except Exception as exc:
                summary["charts_error"] = repr(exc)

        # 5) 收尾。份额快照全部失败时不标记"今天已更新"，让调度器稍后自动重试；
        #    连续 3 轮全失败则放弃当天（避免对数据源持续加压），次日再试。
        snap_ok = sum(1 for v in summary["etfs"].values()
                      if isinstance(v.get("snapshot"), dict))
        fails = int(storage.get_meta("snapshot_fail_streak") or 0)
        if snap_ok > 0 or fails >= 2:
            storage.set_meta("snapshot_fail_streak", 0)
            storage.set_meta("last_full_update", _today())
        else:
            storage.set_meta("snapshot_fail_streak", fails + 1)
        latest = [v.get("latest") for v in summary["etfs"].values() if v.get("latest")]
        if latest:
            storage.set_meta("last_data_date", max(latest))
        summary["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        return summary


def start_scheduler(cfg, interval_sec=300):
    """守护线程：交易日下午自动更新（供服务进程调用）。"""

    def _loop():
        while True:
            try:
                if should_update_now(cfg):
                    print(f"[scheduler] {dt.datetime.now():%Y-%m-%d %H:%M:%S} "
                          f"触发当日自动更新", flush=True)
                    update_once(cfg)
            except Exception as exc:
                print(f"[scheduler] {dt.datetime.now():%Y-%m-%d %H:%M:%S} "
                      f"更新失败: {exc!r}", flush=True)
            time.sleep(interval_sec)

    th = threading.Thread(target=_loop, daemon=True, name="etf-auto-update")
    th.start()
    return th
