"""规则引擎测试：两条规则的触发/不触发、热身期、同源+全覆盖口径守卫。"""
from helpers import TempDBTestCase, dseq, make_cfg, seed_rows  # noqa: F401
from etf_monitor import rules, storage


class BottomBuyTest(TempDBTestCase):
    def test_fires_strong_when_drop_plus_inflow(self):
        dates = dseq(30)
        shares = [1e9] * 5 + [1.01e9]          # 末日组合日增 +1% (>= 0.8)
        amounts = [2e8] * 29 + [6e8]           # 末日成交量为前5日均值 3 倍
        storage.upsert_daily(seed_rows(dates, [4.0] * 29 + [3.90], pct_last=-2.5,
                                       share_values=shares, amounts=amounts))
        res = rules.evaluate(make_cfg())
        self.assertEqual(res["status"], "ok")
        self.assertEqual([s["rule"] for s in res["signals"]], ["BOTTOM_BUY_REF"])
        self.assertEqual(res["signals"][0]["level"], "strong")
        self.assertIn("3.0 倍", res["signals"][0]["message"])
        self.assertEqual(storage.get_signals()[0]["rule"], "BOTTOM_BUY_REF")

    def test_not_fired_without_market_drop(self):
        dates = dseq(30)
        storage.upsert_daily(seed_rows(dates, [4.0] * 30, pct_last=0.1,
                                       share_values=[1e9] * 6))
        res = rules.evaluate(make_cfg())
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["no_signal"])
        self.assertEqual(res["signals"], [])

    def test_message_ok_when_drop5_missing(self):
        """只有 5 个交易日（i=4 < 5）时 drop5 为 None，消息不得崩溃。"""
        dates = dseq(5)
        storage.upsert_daily(seed_rows(dates, [4.0] * 4 + [3.90], pct_last=-2.5,
                                       share_values=[1e9] * 4 + [1.01e9]))
        res = rules.evaluate(make_cfg())
        self.assertEqual([s["rule"] for s in res["signals"]], ["BOTTOM_BUY_REF"])
        self.assertNotIn("5日", res["signals"][0]["message"])


class AvoidChaseTest(TempDBTestCase):
    def test_fires_when_high_and_consecutive_redeem(self):
        n = 30
        dates = dseq(n)
        closes = [4.0] * 10 + [round(4.0 * 1.005 ** (i - 9), 4) for i in range(10, n)]
        s0 = 1e9
        shares = [s0, s0 * 0.996, s0 * 0.996 ** 2, s0 * 0.996 ** 3, s0 * 0.996 ** 4]
        storage.upsert_daily(seed_rows(dates, closes, pct_last=0.5,
                                       share_values=shares))
        res = rules.evaluate(make_cfg())
        self.assertEqual(res["status"], "ok")
        fired = [s["rule"] for s in res["signals"]]
        self.assertIn("AVOID_CHASE", fired)
        sig = next(s for s in res["signals"] if s["rule"] == "AVOID_CHASE")
        self.assertEqual(sig["level"], "medium")   # 累计约 -1.6%，未到 strong 的 -2%
        self.assertIn("连续4日", sig["message"])

    def test_not_fired_when_redeem_not_consecutive(self):
        n = 30
        dates = dseq(n)
        closes = [4.0] * 10 + [round(4.0 * 1.005 ** (i - 9), 4) for i in range(10, n)]
        s0 = 1e9
        # 中间一天反弹 → 连续性被打破，不应触发
        shares = [s0, s0 * 0.996, s0 * 1.001, s0 * 0.996 ** 2, s0 * 0.996 ** 3]
        storage.upsert_daily(seed_rows(dates, closes, pct_last=0.5,
                                       share_values=shares))
        res = rules.evaluate(make_cfg())
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["no_signal"])


class WarmingTest(TempDBTestCase):
    def test_warming_up_when_insufficient_share_days(self):
        dates = dseq(30)
        storage.upsert_daily(seed_rows(dates, [4.0] * 30, pct_last=0.1,
                                       share_values=[1e9] * 3))
        res = rules.evaluate(make_cfg())
        self.assertEqual(res["status"], "warming_up")
        self.assertEqual(res["signals"], [])
        self.assertIn("3/5", res["message"])
        self.assertEqual(res["evidence"]["shares_days_collected"], 3)

    def test_no_data_at_all(self):
        res = rules.evaluate(make_cfg())
        self.assertEqual(res["status"], "no_data")


class SourceGuardTest(TempDBTestCase):
    def test_partial_coverage_day_excluded_from_inflow(self):
        """回归：组内某只末日被降级为另一来源时，当日不得参与日环比。"""
        dates = dseq(30)
        rows = seed_rows(dates, [4.0] * 29 + [3.90], pct_last=-2.5,
                         share_values=[1e9] * 5 + [1.01e9])
        for r in rows:
            if r["code"] == "510310" and r["date"] == dates[-1] and r["shares"]:
                r["source"] = "tencent"    # 模拟当日该只降级
        storage.upsert_daily(rows)
        res = rules.evaluate(make_cfg())
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["no_signal"])               # 不输出假信号
        self.assertIsNone(res["evidence"]["hs300_inflow_1d_pct"])

    def test_share_days_ignore_other_source_history(self):
        """来源切换（如东财限流全天降级腾讯）时按新来源重新计热身天数。"""
        dates = dseq(30)
        rows = seed_rows(dates, [4.0] * 30, pct_last=0.1, share_values=[1e9] * 6)
        for r in rows:
            if r["shares"] and r["date"] < dates[-1]:
                r["source"] = "eastmoney"
            elif r["shares"]:
                r["source"] = "tencent"    # 仅最后一天是腾讯口径
        storage.upsert_daily(rows)
        res = rules.evaluate(make_cfg())
        self.assertEqual(res["status"], "warming_up")
        self.assertEqual(res["evidence"]["shares_days_collected"], 1)
