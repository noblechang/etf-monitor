"""趋势图：每只ETF一张三联图（价格/成交额/份额）+ 组合汇总图。"""
import datetime as dt
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

CHART_DIR = Path(__file__).resolve().parent.parent / "output" / "charts"


def _setup_font():
    available = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ["PingFang SC", "Hiragino Sans GB", "Heiti SC", "STHeiti",
                 "Arial Unicode MS", "Songti SC"]:
        if cand in available:
            plt.rcParams["font.sans-serif"] = [cand]
            break
    plt.rcParams["axes.unicode_minus"] = False


def _signal_dates(signals):
    out = {}
    for s in signals:
        out.setdefault(s["date"], []).append(s["rule"])
    return out


def _mark_signals(ax, sig_by_date, dates):
    for d, rules in sig_by_date.items():
        if d not in dates:
            continue
        x = dates.index(d)
        for rule in rules:
            color = "red" if rule == "BOTTOM_BUY_REF" else "darkorange"
            ax.axvline(x, color=color, ls="--", lw=1, alpha=0.8)


def render_etf(code, name, rows, signals, days=120, out_path=None):
    """单只ETF三联图，返回文件路径。rows 按日期升序。"""
    rows = rows[-days:]
    if not rows:
        return None
    dates = [r["date"] for r in rows]
    closes = [r["close"] for r in rows]
    amts = [(r["amount"] or r["amount_est"] or 0) / 1e8 for r in rows]
    shares = [r["shares"] / 1e8 if r["shares"] else None for r in rows]
    ma20 = [None] * len(closes)
    for i in range(19, len(closes)):
        ma20[i] = sum(closes[i - 19: i + 1]) / 20.0
    sig_by_date = _signal_dates(signals)

    _setup_font()
    fig, axes = plt.subplots(3, 1, figsize=(10, 7.6), sharex=True,
                             gridspec_kw={"height_ratios": [2.2, 1, 1.3]})
    ax1, ax2, ax3 = axes
    x = list(range(len(dates)))

    ax1.plot(x, closes, lw=1.4, color="#1f6fb2", label="收盘价")
    ax1.plot(x, ma20, lw=1.0, color="#e08020", label="MA20")
    ax1.set_title(f"{name}（{code}） 近{len(dates)}个交易日", fontsize=12)
    ax1.grid(alpha=0.3)

    ax2.bar(x, amts, width=0.8, color="#7ba7d0")
    ax2.set_ylabel("成交额(亿,估)", fontsize=8)
    ax2.grid(alpha=0.3)

    if any(v is not None for v in shares):
        xs = [i for i, v in enumerate(shares) if v is not None]
        ys = [shares[i] for i in xs]
        ax3.plot(xs, ys, lw=1.4, color="#c04050",
                 marker="o" if len(xs) < 3 else None, ms=5)
        ax3.set_ylabel("份额(亿份)", fontsize=8)
        first = next(i for i, v in enumerate(shares) if v is not None)
        ax3.annotate(f"份额数据自 {dates[first]} 起每日积累", (0.99, 0.06),
                     xycoords="axes fraction", ha="right", fontsize=7, color="#666666")
    else:
        ax3.text(0.5, 0.5, "份额数据积累中（部署后每日自动记录）",
                 ha="center", va="center", transform=ax3.transAxes, fontsize=9, color="#888888")
    ax3.grid(alpha=0.3)

    for ax in axes:
        _mark_signals(ax, sig_by_date, dates)
    step = max(1, len(dates) // 8)
    ax3.set_xticks(x[::step])
    ax3.set_xticklabels(dates[::step], rotation=30, fontsize=7)

    from matplotlib.lines import Line2D
    handles, labels = ax1.get_legend_handles_labels()
    handles += [Line2D([0], [0], color="red", ls="--", lw=1, label="底部买入参照"),
                Line2D([0], [0], color="darkorange", ls="--", lw=1, label="高位不追警示")]
    labels += ["底部买入参照", "高位不追警示"]
    ax1.legend(handles, labels, loc="upper left", fontsize=7, ncol=2)

    fig.tight_layout()
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    path = Path(out_path) if out_path else CHART_DIR / f"{code}.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def render_combined(cfg, signals, days=120):
    """组合图：基准ETF价格 + 沪深300系合计份额 + 全组合合计份额。"""
    from . import storage
    base_rows = storage.get_daily(code="510300", limit=days) or \
                storage.get_daily(code="510310", limit=days)
    if not base_rows:
        return None
    dates = [r["date"] for r in base_rows]

    def agg(codes):
        per = {c: {r["date"]: r["shares"] for r in storage.get_daily(code=c) if r["shares"]}
               for c in codes}
        out = []
        for d in dates:
            vals = [per[c][d] for c in codes if d in per[c]]
            # 组内当日全覆盖才计入，覆盖不全的日期断线，避免画出假的申赎跳变
            out.append(sum(vals) / 1e8 if len(vals) == len(codes) else None)
        return out

    hs300_codes = [e["code"] for e in cfg["etfs"] if e["group"] == "hs300"]
    all_codes = [e["code"] for e in cfg["etfs"]]
    hs = agg(hs300_codes)
    al = agg(all_codes)

    _setup_font()
    fig, axes = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True,
                             gridspec_kw={"height_ratios": [1.6, 1.4]})
    x = list(range(len(dates)))
    ax1, ax2 = axes
    base_code = base_rows[0]["code"]
    base_name = next((e["name"] for e in cfg["etfs"] if e["code"] == base_code), base_code)
    ax1.plot(x, [r["close"] for r in base_rows], lw=1.4, color="#1f6fb2")
    ax1.set_title(f"{base_name}（{base_code}） 与 汇金系宽基ETF合计份额", fontsize=12)
    ax1.set_ylabel("收盘价", fontsize=8)
    ax1.grid(alpha=0.3)

    plotted = False
    for series, label, color in ((hs, "沪深300系4只合计", "#c04050"), (al, "全组合8只合计", "#7a4fb0")):
        xs = [i for i, v in enumerate(series) if v is not None]
        ys = [series[i] for i in xs]
        if xs:
            ax2.plot(xs, ys, lw=1.5, color=color, label=label,
                     marker="o" if len(xs) < 3 else None, ms=5)
            plotted = True
    if plotted:
        ax2.legend(loc="upper left", fontsize=8)
    n_hs = sum(1 for v in hs if v is not None)
    if n_hs == 0:
        ax2.text(0.5, 0.5, "份额数据积累中（部署后每日自动记录）",
                 ha="center", va="center", transform=ax2.transAxes, fontsize=9, color="#888888")
    elif n_hs < 5:
        ax2.annotate("份额数据自部署日起每日积累，点数少为正常", (0.01, 0.06),
                     xycoords="axes fraction", ha="left", fontsize=7, color="#666666")
    ax2.set_ylabel("合计份额(亿份)", fontsize=8)
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(alpha=0.3)

    sig_by_date = _signal_dates(signals)
    for ax in axes:
        _mark_signals(ax, sig_by_date, dates)
    step = max(1, len(dates) // 8)
    ax2.set_xticks(x[::step])
    ax2.set_xticklabels(dates[::step], rotation=30, fontsize=7)

    fig.tight_layout()
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    path = CHART_DIR / "combined.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def render_all(cfg, signals, days=120):
    from . import storage
    paths = []
    for e in cfg["etfs"]:
        rows = storage.get_daily(code=e["code"])
        p = render_etf(e["code"], e["name"], rows, signals, days=days)
        if p:
            paths.append(p)
    p = render_combined(cfg, signals, days=days)
    if p:
        paths.append(p)
    return paths
