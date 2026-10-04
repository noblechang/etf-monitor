"""storage 层测试：upsert COALESCE 语义是整个系统的口径基石。"""
from helpers import TempDBTestCase  # noqa: F401
from etf_monitor import storage


SNAP_510300 = {"code": "510300", "date": "2026-09-02", "close": 4.1, "pct_chg": 2.5,
               "volume": None, "amount": None, "amount_est": None,
               "shares": 2.4e11, "scale": 9.8e10, "source": "eastmoney"}
KLINE_510300 = {"code": "510300", "date": "2026-09-02", "close": 4.1, "pct_chg": 2.5,
                "volume": 1.2e8, "amount": None, "amount_est": 4.9e8,
                "shares": None, "scale": None, "source": None}


KLINE_510300_D1 = {"code": "510300", "date": "2026-09-01", "close": 4.0, "pct_chg": None,
                   "volume": 1.0e8, "amount": None, "amount_est": 4.0e8,
                   "shares": None, "scale": None, "source": None}


class DailyTest(TempDBTestCase):
    def test_roundtrip_and_filters(self):
        storage.upsert_daily([KLINE_510300_D1, SNAP_510300,
                              dict(KLINE_510300_D1, code="510500", close=6.0)])
        self.assertEqual(len(storage.get_daily(code="510300")), 2)
        self.assertEqual(len(storage.get_daily(code="510300", has_shares=True)), 1)
        self.assertEqual(storage.get_daily(code="510300", start="2026-09-02")[0]["date"],
                         "2026-09-02")
        self.assertEqual(storage.get_daily(code="510300", end="2026-09-01")[0]["date"],
                         "2026-09-01")
        self.assertEqual(len(storage.get_daily(limit=1)), 1)

    def test_kline_upsert_must_not_overwrite_share_source(self):
        """回归：次日K线回填（source=None）不得覆盖快照写入的来源与份额。"""
        storage.upsert_daily([SNAP_510300])
        storage.upsert_daily([KLINE_510300])
        row = storage.get_daily(code="510300", limit=1)[0]
        self.assertEqual(row["source"], "eastmoney")
        self.assertEqual(row["shares"], 2.4e11)
        self.assertEqual(row["scale"], 9.8e10)
        self.assertEqual(row["volume"], 1.2e8)          # K线字段正常写入
        self.assertEqual(row["amount_est"], 4.9e8)

    def test_snapshot_fills_kline_row(self):
        """反向：K线先到，快照后到补齐份额/来源，保留K线已有字段。"""
        storage.upsert_daily([dict(KLINE_510300, pct_chg=None)])
        storage.upsert_daily([SNAP_510300])
        row = storage.get_daily(code="510300", limit=1)[0]
        self.assertEqual(row["source"], "eastmoney")
        self.assertEqual(row["shares"], 2.4e11)
        self.assertEqual(row["volume"], 1.2e8)          # K线的量不被快照的 None 清掉
        self.assertEqual(row["pct_chg"], 2.5)


class SignalTest(TempDBTestCase):
    def test_roundtrip(self):
        storage.upsert_signal({"date": "2026-09-02", "rule": "BOTTOM_BUY_REF",
                               "level": "strong", "message": "测试消息",
                               "evidence": {"drop_1d_pct": -2.5}})
        storage.upsert_signal({"date": "2026-09-02", "rule": "AVOID_CHASE",
                               "level": "medium", "message": "m2", "evidence": {}})
        out = storage.get_signals()
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["rule"], "AVOID_CHASE")   # 同日按 rule 排序
        self.assertEqual(out[1]["evidence"]["drop_1d_pct"], -2.5)
        # 覆盖更新
        storage.upsert_signal({"date": "2026-09-02", "rule": "BOTTOM_BUY_REF",
                               "level": "medium", "message": "改", "evidence": {}})
        rows = [r for r in storage.get_signals() if r["rule"] == "BOTTOM_BUY_REF"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["level"], "medium")


class MetaTest(TempDBTestCase):
    def test_meta(self):
        self.assertIsNone(storage.get_meta("nope", None))
        self.assertEqual(storage.get_meta("nope", "dft"), "dft")
        storage.set_meta("last_full_update", "2026-10-04")
        storage.set_meta("last_full_update", "2026-10-05")
        self.assertEqual(storage.get_meta("last_full_update"), "2026-10-05")
