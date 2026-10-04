"""测试公共设施：临时数据库、合成行情数据、测试配置。仅依赖标准库 + 被测包。"""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from etf_monitor import storage  # noqa: E402

HS_CODES = ["510300", "510310", "159919", "510050"]
BROAD_CODES = ["510500", "512100", "159977", "588000"]
ALL_CODES = HS_CODES + BROAD_CODES


def make_cfg():
    """与 config.json 阈值一致的最小配置（规则引擎只用到 code/group/rules）。"""
    return {
        "etfs": [{"code": c, "name": f"ETF{c}",
                  "group": "hs300" if c in HS_CODES else "broad"} for c in ALL_CODES],
        "rules": {
            "bottom_buy": {"name": "底部买入参照", "drop_1d_pct": -1.5, "drop_5d_pct": -4.0,
                           "inflow_1d_pct": 0.8, "inflow_1d_pct_vol": 0.3,
                           "vol_multiple": 2.0},
            "avoid_chase": {"name": "高位不追/止盈警示", "high_20d_pct": 8.0,
                            "high_pctile": 90, "high_pctile_days": 120,
                            "redeem_days": 3, "redeem_total_pct": -1.0},
        },
    }


def dseq(n, start="2026-06-01"):
    """n 个连续自然日（测试用，无需是真实交易日）。"""
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


class TempDBTestCase(unittest.TestCase):
    """每个用例独立的临时 SQLite：把 storage.DB_PATH 指到临时目录。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(tmp.name)
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(storage, "DB_PATH", self.tmpdir / "test.db")
        patcher.start()
        self.addCleanup(patcher.stop)


def seed_rows(dates, closes, pct_last=None, share_values=None,
              share_source="eastmoney", share_codes=None, amounts=None):
    """为全部 8 只 ETF 生成 daily 行（不落库，配合 upsert_daily 使用）。

    - closes/amounts 按日应用于所有代码；
    - pct_last 只给最后一个交易日（此前日期 0.0/None）；
    - share_values 与 dates 尾部对齐，share_codes 内的每只代码取相同值，
      因此组合合计的日环比与单只一致。
    """
    share_codes = share_codes or HS_CODES
    n = len(dates)
    n_sh = len(share_values) if share_values else 0
    rows = []
    for i, (d, c) in enumerate(zip(dates, closes)):
        for code in ALL_CODES:
            row = {"code": code, "date": d, "close": c,
                   "pct_chg": (pct_last if i == n - 1 else (0.0 if i > 0 else None)),
                   "volume": 1e8, "amount": amounts[i] if amounts else 2e8,
                   "amount_est": None, "shares": None, "scale": None, "source": None}
            if share_values and code in share_codes and i >= n - n_sh:
                row["shares"] = share_values[i - (n - n_sh)]
                row["source"] = share_source
            rows.append(row)
    return rows
