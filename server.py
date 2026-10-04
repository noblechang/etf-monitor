#!/usr/bin/env python3
"""API 服务 + 网页看板。

供调用者的接口（本机 http://127.0.0.1:8686）：
  GET  /                          网页看板
  GET  /api/etfs                  监控清单
  GET  /api/summary               最新一轮汇总（各ETF最新价/份额/规模 + 规则状态）
  GET  /api/details?code=510300&start=2026-09-01&end=2026-09-30&limit=250
                                  每日明细（收盘/涨跌幅/成交额/份额/规模）
  GET  /api/signals?limit=50      两条判断规则的结果（含 warming_up 状态与历史信号）
  GET  /api/rules                 规则定义与阈值（可核对/调参）
  GET  /api/etf/{code}/trend.png?days=120   单ETF趋势图
  GET  /api/chart/combined.png    组合汇总图
  POST /api/update?force=false    手动触发一轮更新

服务启动即自动补跑当天漏掉的更新，并内置交易日 15:45 后自动更新的调度线程；
配合 launchd 开机自启（install_launchd.sh）即可全自动。
"""
import argparse
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from etf_monitor import charts, rules, storage, updater

CFG = updater.load_config()
_update_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not storage.get_daily(limit=1):        # 首次启动：先同步灌入数据
        try:
            updater.update_once(CFG)
        except Exception:
            pass
    else:                                     # 之后启动：后台补跑当天更新
        threading.Thread(target=lambda: updater.update_once(CFG),
                         daemon=True).start()
    updater.start_scheduler(CFG)
    yield


app = FastAPI(title="国家队ETF动向监控", version="1.1.0", lifespan=lifespan)


# --------------------------------------------------------------------- API
@app.get("/api/etfs")
def api_etfs():
    return CFG["etfs"]


@app.get("/api/rules")
def api_rules():
    return {"rules": CFG["rules"],
            "base": rules.BASE_PRIORITY,
            "note": "阈值可在 config.json 调整后重启/手动触发更新生效"}


@app.get("/api/details")
def api_details(code: str = Query(...), start: str = None, end: str = None,
                limit: int = 250):
    if not any(e["code"] == code for e in CFG["etfs"]):
        raise HTTPException(404, f"unknown code: {code}")
    return {"code": code, "rows": storage.get_daily(code=code, start=start,
                                                    end=end, limit=limit)}


@app.get("/api/signals")
def api_signals(limit: int = 50):
    status = None
    raw = storage.get_meta("last_rule_status")
    if raw:
        try:
            status = json.loads(raw)
        except json.JSONDecodeError:
            status = None
    return {"latest_status": status, "signals": storage.get_signals(limit=limit)}


@app.get("/api/summary")
def api_summary():
    out = {"updated_at": storage.get_meta("last_full_update"),
           "last_data_date": storage.get_meta("last_data_date"), "etfs": []}
    status = storage.get_meta("last_rule_status")
    out["rule_status"] = json.loads(status) if status else None
    for e in CFG["etfs"]:
        rows = storage.get_daily(code=e["code"], limit=2)
        latest = rows[-1] if rows else None
        prev = rows[-2] if len(rows) > 1 else None
        item = {"code": e["code"], "name": e["name"], "group": e["group"]}
        if latest:
            item.update({
                "date": latest["date"], "close": latest["close"],
                "pct_chg": latest["pct_chg"],
                "amount_yi": round((latest["amount"] or latest["amount_est"] or 0) / 1e8, 2),
                "shares_yi": round(latest["shares"] / 1e8, 2) if latest["shares"] else None,
                "scale_yi": round(latest["scale"] / 1e8, 2) if latest["scale"] else None,
            })
            if (prev and prev["shares"] and latest["shares"]
                    and prev.get("source") == latest.get("source")):
                # 同源才计算日变动：东财/腾讯份额口径差 0.2%~3%，跨源比较是假变动
                item["shares_chg_1d_pct"] = round(
                    (latest["shares"] / prev["shares"] - 1) * 100, 2)
        out["etfs"].append(item)
    return out


@app.get("/api/etf/{code}/trend.png")
def api_trend(code: str, days: int = 120):
    e = next((x for x in CFG["etfs"] if x["code"] == code), None)
    if not e:
        raise HTTPException(404, f"unknown code: {code}")
    days = max(20, min(int(days), 550))
    # 120 天版由每日更新预生成；其他天数按需渲染并按天数缓存
    path = charts.CHART_DIR / (f"{code}.png" if days == 120 else f"{code}_d{days}.png")
    if not path.exists():
        rows = storage.get_daily(code=code)
        sigs = storage.get_signals(limit=60)
        p = charts.render_etf(code, e["name"], rows, sigs, days=days, out_path=path)
        if not p:
            raise HTTPException(404, "no data yet")
    return FileResponse(path, media_type="image/png")


@app.get("/api/chart/combined.png")
def api_combined():
    path = charts.CHART_DIR / "combined.png"
    if not path.exists():
        sigs = storage.get_signals(limit=60)
        charts.render_combined(CFG, sigs, days=120)
    return FileResponse(path, media_type="image/png")


@app.post("/api/update")
def api_update(force: bool = False):
    def _run():
        with _update_lock:
            updater.update_once(CFG, force=force)
    th = threading.Thread(target=_run, daemon=True)
    th.start()
    return {"started": True, "tip": "轮询 GET /api/summary 的 updated_at 判断是否完成"}


# ------------------------------------------------------------------- 看板
DASH_CSS = """
body{font-family:'PingFang SC','Hiragino Sans GB',sans-serif;margin:0;background:#f5f6f8;color:#222}
.wrap{max-width:1080px;margin:0 auto;padding:20px}
h1{font-size:20px;margin:0 0 4px} .sub{color:#777;font-size:12px;margin-bottom:16px}
.card{background:#fff;border-radius:10px;padding:14px 16px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:6px 8px;text-align:right;border-bottom:1px solid #eee}
th:first-child,td:first-child{text-align:left}
th{color:#666;font-weight:500}
.up{color:#c0392b}.down{color:#1e8449}
.sig{border-left:4px solid #ccc;padding:8px 12px;margin:6px 0;background:#fafafa;font-size:13px}
.sig.buy{border-color:#c0392b}.sig.warn{border-color:#e67e22}
.chip{display:inline-block;font-size:11px;padding:1px 8px;border-radius:10px;margin-left:6px;color:#fff}
.chip.buy{background:#c0392b}.chip.warn{background:#e67e22}.chip.dim{background:#999}
img.chart{width:100%;border-radius:6px;margin-top:8px}
.note{font-size:12px;color:#777;line-height:1.7}
h2{font-size:15px;margin:0 0 8px}
"""


def _fmt(v, nd=2, suffix=""):
    return "-" if v is None else f"{v:,.{nd}f}{suffix}"


def _cls(v):
    if v is None:
        return ""
    return "up" if v > 0 else ("down" if v < 0 else "")


def dashboard() -> str:
    summary = api_summary()
    sigs = storage.get_signals(limit=8)
    rule_cfg = CFG["rules"]

    rows_html = ""
    for e in summary["etfs"]:
        chg = e.get("shares_chg_1d_pct")
        rows_html += (
            f"<tr><td>{e['name']}（{e['code']}）</td>"
            f"<td>{_fmt(e.get('close'), 3)}</td>"
            f"<td class='{_cls(e.get('pct_chg'))}'>{_fmt(e.get('pct_chg'))}%</td>"
            f"<td>{_fmt(e.get('amount_yi'), 1)}</td>"
            f"<td>{_fmt(e.get('shares_yi'))}</td>"
            f"<td class='{_cls(chg)}'>{_fmt(chg)}%</td>"
            f"<td>{_fmt(e.get('scale_yi'), 0)}</td></tr>")

    sig_html = ""
    st = summary.get("rule_status") or {}
    if st.get("status") == "warming_up":
        sig_html = f"<div class='sig'>{st.get('message', '')}</div>"
    else:
        if st.get("no_signal"):
            sig_html += ("<div class='sig'>当前无信号 —— 两条规则均未触发，"
                         "按自己的计划执行；国家队在场不等于可放松风控。</div>")
        if sigs:
            sig_html += "<div class='note' style='margin:8px 0 4px'>历史信号：</div>"
            for s in sigs:
                cls = "buy" if s["rule"] == "BOTTOM_BUY_REF" else "warn"
                name = (rule_cfg["bottom_buy"]["name"] if cls == "buy"
                        else rule_cfg["avoid_chase"]["name"])
                sig_html += (f"<div class='sig {cls}'><span class='chip {cls}'>{s['date']} {name}"
                             f"·{s['level']}</span><br>{s['message']}</div>")
        if not sig_html:
            sig_html = "<div class='sig'>暂无状态数据，等待下一轮更新。</div>"

    charts_html = "".join(
        f"<img class='chart' src='/api/etf/{e['code']}/trend.png?days=120'>" for e in CFG["etfs"])

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>国家队ETF动向监控</title><style>{DASH_CSS}</style>
<meta http-equiv="refresh" content="300"></head><body><div class="wrap">
<h1>国家队ETF动向监控</h1>
<div class="sub">数据截至 {summary.get('last_data_date') or '-'} · 最近更新 {summary.get('updated_at') or '-'} · 每5分钟自动刷新 · 份额为汇金系重点宽基ETF场内份额</div>

<div class="card"><h2>判断规则结果</h2>{sig_html}
<div class="note">规则一「{rule_cfg['bottom_buy']['name']}」：大跌 + 宽基ETF放量净申购 → 底部区域形态，分批买入参照点。
规则二「{rule_cfg['avoid_chase']['name']}」：高位 + 连续净赎回 → 权重失去边际买家，不追高、拥挤仓位可部分止盈。</div></div>

<div class="card"><h2>持仓明细（最新交易日）</h2>
<table><tr><th>ETF</th><th>收盘</th><th>涨跌幅</th><th>成交额(亿)</th><th>份额(亿份)</th><th>份额日变动</th><th>规模(亿)</th></tr>
{rows_html}</table>
<div class="note">份额数据自部署日起每日自动积累（约5个交易日后规则完整生效）；成交额为腾讯口径估算值。</div></div>

<div class="card"><h2>趋势图</h2>
<img class="chart" src="/api/chart/combined.png">{charts_html}</div>

<div class="card note">数据源：腾讯行情（历史K线）+ 东方财富/腾讯（每日份额快照）。本工具仅整理公开数据、输出形态参照，不构成投资建议。
API：<code>/api/summary</code> <code>/api/details?code=510300</code> <code>/api/signals</code> <code>/api/rules</code></div>
</div></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return dashboard()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=CFG["listen_host"])
    ap.add_argument("--port", type=int, default=CFG["listen_port"])
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
