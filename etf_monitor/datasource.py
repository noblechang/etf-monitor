"""数据抓取：腾讯历史K线 + 东财/腾讯每日份额快照。

- 历史K线（价格/成交量）：腾讯 ifzq 接口，一次请求可回填550个交易日。
- 每日份额快照：主源东财 push2（f84=份额, f116=规模）；被限流时自动降级
  腾讯 qt.gtimg.cn（份额 = 总市值/现价）。
- 所有请求带退避重试；每日任务一天只发一轮请求，避免触发东财限流。
"""
import datetime as dt
import re
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
TIMEOUT = 15

# 关键：trust_env=False 绕过系统/环境代理（macOS 会把 Clash 等系统代理注入
# requests，境外出口 IP 会被东财拒绝；A股数据源直连即可）。
_session = requests.Session()
_session.trust_env = False


def _get(url, referer=None, retries=3):
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    last_err = None
    for i in range(retries):
        try:
            r = _session.get(url, headers=headers, timeout=TIMEOUT)
            if r.status_code == 200 and r.text.strip():
                return r
            last_err = f"HTTP {r.status_code}"
        except Exception as e:  # 网络抖动/限流都退避重试
            last_err = repr(e)
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET failed: {url} ({last_err})")


# ---------------------------------------------------------------- 腾讯历史K线
def fetch_kline(symbol, days):
    """返回按日期升序的 [{date, close, volume(份), amount_est(元)}]。

    腾讯日线无成交额，amount_est = 成交量(份) * 收盘价，仅用于放量比率
    这类同口径比值判断，不代表精确成交额。
    """
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={symbol},day,,,{int(days)},qfq")
    data = _get(url, referer="https://gu.qq.com/").json()["data"][symbol]
    rows = data.get("qfqday") or data.get("day") or []
    out = []
    for row in rows:
        close = float(row[2])
        vol_hand = float(row[5]) if len(row) > 5 else 0.0
        out.append({
            "date": row[0],
            "close": close,
            "volume": vol_hand * 100.0,            # 手 -> 份
            "amount_est": vol_hand * 100.0 * close,
        })
    out.sort(key=lambda x: x["date"])
    return out


# ------------------------------------------------------- 东财实时快照（主源）
_EM_HOSTS = ("", "1.", "23.", "90.")  # push2 边缘节点轮换，单节点限流时换节点


def _f(v):
    """东财数值字段兜底：停牌/缺失时接口返回 '-'，统一转 None。"""
    return float(v) if isinstance(v, (int, float)) else None


def fetch_snapshot_em(secid):
    """东财 push2 单只基金快照：份额 f84、规模 f116。"""
    last = None
    d = None
    for host in _EM_HOSTS:
        url = (f"https://{host}push2.eastmoney.com/api/qt/stock/get?"
               f"secid={secid}&fltt=2&invt=2&fields=f43,f57,f58,f60,f84,f116,f170,f86")
        try:
            d = _get(url, referer="https://quote.eastmoney.com/", retries=2).json().get("data")
        except Exception as exc:
            last = exc
            continue
        if d and isinstance(d.get("f84"), (int, float)):
            break
    else:
        raise RuntimeError(f"eastmoney snapshot failed for {secid}: "
                           f"{last or 'f84 missing on all hosts'}")
    ts = d.get("f86")
    return {
        "code": d.get("f57"),
        "name": d.get("f58"),
        "price": _f(d.get("f43")),
        "prev_close": _f(d.get("f60")),
        "pct_chg": _f(d.get("f170")),
        "shares": float(d["f84"]),
        "scale": _f(d.get("f116")),
        "amount": None,
        "quote_date": dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else None,
        "source": "eastmoney",
    }


# ------------------------------------------------------ 腾讯快照（备用/降级）
def fetch_snapshot_qq(symbol):
    """腾讯 qt.gtimg.cn 快照：份额 = 总市值(亿元)/现价。"""
    text = _get(f"https://qt.gtimg.cn/q={symbol}",
                referer="https://gu.qq.com/").content.decode("gbk", "ignore")
    m = re.search(r"v_" + re.escape(symbol) + r'="([^"]+)"', text)
    if not m:
        return None
    p = m.group(1).split("~")

    def _num(idx, mult=1.0):
        try:
            v = p[idx]
            return float(v) * mult if v not in ("", "-") else None
        except (IndexError, ValueError):
            return None

    price = _num(3)
    prev = _num(4)
    scale = _num(45, 1e8)          # 总市值：亿元 -> 元
    amount = _num(37, 1e4)         # 成交额：万元 -> 元（p[33]/p[34] 是最高/最低价）
    qdate = None
    if p[30]:
        try:
            qdate = dt.datetime.strptime(p[30][:8], "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    pct = round((price / prev - 1.0) * 100, 2) if price and prev else None
    return {
        "price": price,
        "prev_close": prev,
        "pct_chg": pct,
        "shares": (scale / price) if scale and price else None,
        "scale": scale,
        "amount": amount,
        "quote_date": qdate,
        "source": "tencent",
    }
