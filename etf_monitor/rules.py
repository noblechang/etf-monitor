"""两条判断规则（基于我们梳理的"国家队行为规律"）：

规则一 BOTTOM_BUY_REF（底部买入参照）：
    市场大跌（基准ETF单日跌超阈值 或 5日累计跌超阈值）
    且 宽基ETF组合份额放量净申购（汇金系进场的技术形态）
    => 对应"2023.10 / 2024初 / 2025.4 汇金增持公告"前后的形态，
       是分批买入的参照点。

规则二 AVOID_CHASE（高位不追/止盈警示）：
    市场高位（20日涨幅超阈值 或 价格处于近N日高分位）
    且 宽基ETF组合连续多日净赎回且累计赎回明显
    => 对应"2026.1 / 2026.4-5 汇金大额赎回"形态，权重板块
       失去边际买家，不追高、拥挤仓位可考虑部分止盈。

份额数据自部署日起每日积累（约5个交易日后规则完整生效），
未积累够时返回 warming_up 状态，不输出伪信号。
"""
import json

from . import storage

BASE_PRIORITY = ["510300", "510310", "159919", "510050"]  # 价格基准优先级
WARM_DAYS = 5  # 同源份额数据最少积累天数，不足时不输出信号


def _pick_base(daily_by_code):
    for code in BASE_PRIORITY:
        if daily_by_code.get(code):
            return code
    for rows in daily_by_code.values():
        if rows:
            return rows[0]["code"]
    return None


def _agg_shares(daily_by_code, codes, src):
    """按日期聚合组合份额，只统计来源与最新数据一致的 ETF。

    两层口径校验，防止制造假的申赎信号：
    1) 同源：东财 f84 与腾讯"总市值/现价"反推的份额有 0.2%~3% 固定差，
       跨源比较会伪造申赎，因此只聚合同一来源的行；
    2) 全覆盖：组内某只当日缺数据（如被降级到另一来源）时，缺它的合计
       会表现为巨额假赎回（缺 588000 一只 ≈ 合计 -40%），因此当日组内
       全部 ETF 都有同源份额才收录，覆盖不全的日期不参与日环比。
    """
    sums, cnts = {}, {}
    for code in codes:
        for r in daily_by_code.get(code, []):
            if r["shares"] and r.get("source") == src:
                sums[r["date"]] = sums.get(r["date"], 0.0) + r["shares"]
                cnts[r["date"]] = cnts.get(r["date"], 0) + 1
    return {d: s for d, s in sums.items() if cnts[d] == len(codes)}


def evaluate(cfg, trade_date=None):
    """评估最新交易日（或指定日期）的两条规则，写入 signals 表并返回结果。"""
    etfs = cfg["etfs"]
    rcfg = cfg["rules"]
    daily_by_code = {e["code"]: storage.get_daily(code=e["code"]) for e in etfs}
    base_code = _pick_base(daily_by_code)
    if not base_code:
        return {"status": "no_data", "signals": []}

    base = daily_by_code[base_code]
    dates = [r["date"] for r in base]
    if trade_date:
        if trade_date not in dates:
            return {"status": "no_data_for_date", "signals": []}
        i = dates.index(trade_date)
    else:
        i = len(dates) - 1
    t = dates[i]

    def close(idx):
        return base[idx]["close"]

    def amt(idx):
        r = base[idx]
        return r["amount"] or r["amount_est"]

    group_codes = [e["code"] for e in etfs]
    hs300_codes = [e["code"] for e in etfs if e["group"] == "hs300"]
    t_src = next((r.get("source") for r in reversed(base) if r["shares"]), None)
    agg_all = _agg_shares(daily_by_code, group_codes, t_src)
    agg_hs = _agg_shares(daily_by_code, hs300_codes, t_src)

    def day_inflow(agg, d_idx):
        """d_idx 为 base 序列下标；返回该日组合份额日增幅(%)。"""
        if d_idx < 1:
            return None
        s0, s1 = agg.get(dates[d_idx - 1]), agg.get(dates[d_idx])
        if not s0 or not s1:
            return None
        return (s1 / s0 - 1.0) * 100.0

    signals = []
    bb = rcfg["bottom_buy"]
    ac = rcfg["avoid_chase"]

    # ---- 市场状态 ----
    drop1 = base[i]["pct_chg"]
    drop5 = (close(i) / close(i - 5) - 1.0) * 100.0 if i >= 5 else None
    ret20 = (close(i) / close(i - 20) - 1.0) * 100.0 if i >= 20 else None
    win = base[max(0, i - ac["high_pctile_days"] + 1): i + 1]
    pctile = None
    if len(win) >= max(30, ac["high_pctile_days"] // 2):
        closes = sorted(w["close"] for w in win)
        k = min(len(closes) - 1, int(round(ac["high_pctile"] / 100.0 * (len(closes) - 1))))
        pctile = closes[k]

    # ---- 份额数据成熟度（按最新数据的同源口径计） ----
    shares_days = sum(1 for d in dates[: i + 1] if agg_hs.get(d))
    inflow1 = day_inflow(agg_hs, i)
    inflow_prev = [day_inflow(agg_hs, j) for j in range(max(0, i - ac["redeem_days"]), i)]
    warm = shares_days < WARM_DAYS

    ev = {"base_code": base_code, "date": t, "drop_1d_pct": drop1,
          "drop_5d_pct": drop5, "hs300_inflow_1d_pct": inflow1,
          "all_inflow_1d_pct": day_inflow(agg_all, i), "ret_20d_pct": ret20,
          "close_pctile": pctile, "shares_days_collected": shares_days,
          "share_source": t_src}

    if warm:
        result = {"status": "warming_up",
                  "message": (f"份额数据积累中（同源 {shares_days}/{WARM_DAYS} 个交易日，"
                              f"来源 {t_src or '无'}），规则暂不生效"),
                  "signals": [], "evidence": ev}
        storage.set_meta("last_rule_status", json.dumps(result, ensure_ascii=False))
        return result

    # ---- 规则一：底部买入参照 ----
    m_drop = (drop1 is not None and drop1 <= bb["drop_1d_pct"]) or \
             (drop5 is not None and drop5 <= bb["drop_5d_pct"])
    amt_ratio = None
    if i >= 6 and amt(i) and all(amt(j) for j in range(i - 5, i)):
        amt_ratio = amt(i) / (sum(amt(j) for j in range(i - 5, i)) / 5.0)
    m_inflow = False
    if inflow1 is not None:
        m_inflow = (inflow1 >= bb["inflow_1d_pct"]) or (
            inflow1 >= bb["inflow_1d_pct_vol"]
            and amt_ratio is not None and amt_ratio >= bb["vol_multiple"])
    if m_drop and m_inflow:
        strong = inflow1 >= bb["inflow_1d_pct"] and drop1 is not None and drop1 <= bb["drop_1d_pct"]
        drop5_txt = f"、5日 {drop5:+.2f}%" if drop5 is not None else ""
        signals.append({
            "date": t, "rule": "BOTTOM_BUY_REF",
            "level": "strong" if strong else "medium",
            "message": (f"大跌+放量净申购：{base_code} 单日 {drop1:+.2f}%{drop5_txt}，"
                        f"沪深300系ETF份额日增 {inflow1:+.2f}%"
                        + (f"，成交量为前5日均值 {amt_ratio:.1f} 倍" if amt_ratio else "")
                        + " —— 符合国家队进场形态，可作分批买入参照点"),
            "evidence": ev,
        })

    # ---- 规则二：高位不追/止盈警示 ----
    m_high = (ret20 is not None and ret20 >= ac["high_20d_pct"]) or \
             (pctile is not None and close(i) >= pctile)
    k = ac["redeem_days"]
    consec = [x for x in inflow_prev[:k] if x is not None]
    m_redeem = (len(consec) == k and all(x < 0 for x in consec)
                and inflow1 is not None and inflow1 < 0
                and (inflow1 + sum(consec)) <= ac["redeem_total_pct"])
    if m_high and m_redeem:
        total_redeem = inflow1 + sum(consec)
        high_txt = []
        if ret20 is not None:
            high_txt.append(f"20日 {ret20:+.1f}%")
        if pctile is not None and close(i) >= pctile:
            high_txt.append(f"处于近{len(win)}日{ac['high_pctile']}分位上方")
        signals.append({
            "date": t, "rule": "AVOID_CHASE",
            "level": "strong" if total_redeem <= ac["redeem_total_pct"] * 2 else "medium",
            "message": (f"高位+连续净赎回：{base_code} {'、'.join(high_txt)}，"
                        f"沪深300系ETF份额连续{k+1}日下降、累计 {total_redeem:+.2f}%"
                        + " —— 权重股失去边际买家，不追高，拥挤仓位可考虑部分止盈"),
            "evidence": ev,
        })

    for s in signals:
        storage.upsert_signal(s)

    result = {
        "status": "ok",
        "date": t,
        "signals": signals,
        "no_signal": not signals,
        "no_signal_message": "两条规则均未触发 —— 按自己的计划执行，国家队在场不等于可放松风控",
        "evidence": ev,
    }
    storage.set_meta("last_rule_status", json.dumps(result, ensure_ascii=False))
    return result
