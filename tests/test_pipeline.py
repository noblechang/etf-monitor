"""更新管线集成测试：模拟两天的 update_once，验证口径关键行为（不访问网络）。"""
import contextlib
import unittest
from unittest import mock

from helpers import TempDBTestCase, dseq
from etf_monitor import charts, storage, updater


class PipelineTest(TempDBTestCase):
    def _fake_kline(self, by_code):
        def fake(symbol, days):
            code = symbol[2:]
            spec = by_code[code]
            return [{"date": d, "close": c, "volume": 1e6 + i * 1e4,
                     "amount_est": (1e6 + i * 1e4) * c}
                    for i, (d, c) in enumerate(zip(spec["dates"], spec["closes"]))]
        return fake

    @staticmethod
    def _fake_em(quote_date):
        def fake(secid):
            return {"code": secid.split(".", 1)[1], "name": secid, "price": 4.05,
                    "prev_close": 4.0, "pct_chg": 1.25, "shares": 2.4e11,
                    "scale": 9.8e10, "amount": None, "quote_date": quote_date,
                    "source": "eastmoney"}
        return fake

    def _patches(self, by_code, quote_date):
        stack = contextlib.ExitStack()
        for p in [mock.patch("etf_monitor.datasource.fetch_kline", self._fake_kline(by_code)),
                  mock.patch("etf_monitor.datasource.fetch_snapshot_em", self._fake_em(quote_date)),
                  mock.patch("etf_monitor.datasource.fetch_snapshot_qq", return_value=None),
                  mock.patch("etf_monitor.updater.time.sleep"),
                  mock.patch.object(charts, "CHART_DIR", self.tmpdir / "charts")]:
            stack.enter_context(p)
        return stack

    def test_two_day_run_preserves_share_source(self):
        """核心回归：第二天K线回填不得覆盖第一天的份额来源（否则规则永远热身）。"""
        cfg = updater.load_config()
        dates1, dates2 = dseq(30), dseq(31)
        by_code1 = {e["code"]: {"dates": dates1, "closes": [4.0] * 30} for e in cfg["etfs"]}

        with self._patches(by_code1, dates1[-1]):
            s1 = updater.update_once(cfg, force=True)
        self.assertFalse(s1["skipped"])
        self.assertEqual(s1["etfs"]["510300"]["snapshot"]["source"], "eastmoney")
        row = storage.get_daily(code="510300", limit=1)[0]
        self.assertEqual(row["date"], dates1[-1])
        self.assertEqual(row["source"], "eastmoney")
        self.assertEqual(row["shares"], 2.4e11)
        self.assertEqual(s1["rules"]["status"], "warming_up")

        by_code2 = {e["code"]: {"dates": dates2, "closes": [4.0] * 31} for e in cfg["etfs"]}
        with self._patches(by_code2, dates2[-1]):
            s2 = updater.update_once(cfg, force=True)
        yest = storage.get_daily(code="510300", end=dates1[-1], limit=1)[0]
        self.assertEqual(yest["source"], "eastmoney")      # <-- 修复点
        self.assertEqual(yest["shares"], 2.4e11)
        today = storage.get_daily(code="510300", limit=1)[0]
        self.assertEqual(today["date"], dates2[-1])
        self.assertEqual(today["source"], "eastmoney")
        self.assertEqual(today["close"], 4.05)             # 快照价覆盖K线价（同日）
        # 历史 pct_chg 已由K线补齐
        older = storage.get_daily(code="510300", limit=2)[0]
        self.assertIsNotNone(older["pct_chg"])
        self.assertEqual(s2["rules"]["status"], "warming_up")
        self.assertEqual(s2["rules"]["evidence"]["shares_days_collected"], 2)

        # 幂等：当天第二次非 force 调用直接跳过
        with self._patches(by_code2, dates2[-1]):
            s3 = updater.update_once(cfg)
        self.assertTrue(s3["skipped"])

    def test_kline_failure_does_not_abort_update(self):
        """单只K线失败只记摘要，其余 ETF 与快照流程照常。"""
        cfg = updater.load_config()
        dates = dseq(30)
        by_code = {e["code"]: {"dates": dates, "closes": [4.0] * 30} for e in cfg["etfs"]}
        real_fake = self._fake_kline(by_code)

        def flaky_kline(symbol, days):
            if symbol == "sh510310":
                raise RuntimeError("tencent down")
            return real_fake(symbol, days)

        with mock.patch("etf_monitor.datasource.fetch_kline", flaky_kline), \
             mock.patch("etf_monitor.datasource.fetch_snapshot_em", self._fake_em(dates[-1])), \
             mock.patch("etf_monitor.datasource.fetch_snapshot_qq", return_value=None), \
             mock.patch("etf_monitor.updater.time.sleep"), \
             mock.patch.object(charts, "CHART_DIR", self.tmpdir / "charts"):
            s = updater.update_once(cfg, force=True)
        self.assertIn("failed", s["etfs"]["510310"]["kline"])
        self.assertEqual(s["etfs"]["510310"]["snapshot"]["source"], "eastmoney")
        self.assertEqual(len(storage.get_daily(code="510500")), 30)

    def test_snapshot_failure_retry_cap(self):
        """快照全失败：不标记完成（可重试），连续3轮后放弃当天。"""
        cfg = updater.load_config()
        dates = dseq(30)
        by_code = {e["code"]: {"dates": dates, "closes": [4.0] * 30} for e in cfg["etfs"]}

        def patches():
            return [mock.patch("etf_monitor.datasource.fetch_kline", self._fake_kline(by_code)),
                    mock.patch("etf_monitor.datasource.fetch_snapshot_em",
                               mock.Mock(side_effect=RuntimeError("em down"))),
                    mock.patch("etf_monitor.datasource.fetch_snapshot_qq", return_value=None),
                    mock.patch("etf_monitor.updater.time.sleep"),
                    mock.patch.object(charts, "CHART_DIR", self.tmpdir / "charts")]

        with self._patches_ctx(patches):
            s1 = updater.update_once(cfg, force=True)
        self.assertEqual(s1["etfs"]["510300"]["snapshot"], "failed")
        self.assertIsNone(storage.get_meta("last_full_update"))
        with self._patches_ctx(patches):
            updater.update_once(cfg, force=True)
        with self._patches_ctx(patches):
            s3 = updater.update_once(cfg, force=True)
        self.assertEqual(storage.get_meta("last_full_update"), s3["updated_at"][:10])

    def _patches_ctx(self, patches_factory):
        stack = contextlib.ExitStack()
        for p in patches_factory():
            stack.enter_context(p)
        return stack


if __name__ == "__main__":
    unittest.main()
