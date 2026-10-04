"""图表测试：小数据量下可渲染、单点份额可见、信号竖线、组合图全覆盖断线。"""
import unittest
from pathlib import Path
from unittest import mock

from helpers import TempDBTestCase, dseq, make_cfg, seed_rows
from etf_monitor import charts, storage


class RenderEtfTest(TempDBTestCase):
    def _rows(self, share_values=None):
        dates = dseq(30)
        rows = seed_rows(dates, [4.0] * 29 + [4.02], pct_last=0.5,
                         share_values=share_values)
        return [r for r in rows if r["code"] == "510300"]

    def test_render_basic(self):
        with mock.patch.object(charts, "CHART_DIR", self.tmpdir / "charts"):
            p = charts.render_etf("510300", "测试ETF", self._rows(), [], days=30)
        self.assertTrue(p and Path(p).exists())
        self.assertGreater(Path(p).stat().st_size, 10000)

    def test_render_single_share_point_with_marker(self):
        with mock.patch.object(charts, "CHART_DIR", self.tmpdir / "charts"):
            p = charts.render_etf("510300", "测试ETF",
                                  self._rows(share_values=[1e9]), [], days=30)
        self.assertTrue(p and Path(p).exists())

    def test_render_with_signal_line(self):
        dates = dseq(30)
        signals = [{"date": dates[-1], "rule": "BOTTOM_BUY_REF"},
                   {"date": dates[-1], "rule": "AVOID_CHASE"}]
        with mock.patch.object(charts, "CHART_DIR", self.tmpdir / "charts"):
            p = charts.render_etf("510300", "测试ETF", self._rows(), signals, days=30)
        self.assertTrue(p and Path(p).exists())

    def test_empty_rows_returns_none(self):
        self.assertIsNone(charts.render_etf("510300", "测试ETF", [], [], days=30))


class RenderCombinedTest(TempDBTestCase):
    def test_render_combined(self):
        storage.upsert_daily(seed_rows(dseq(30), [4.0] * 30, pct_last=0.1,
                                       share_values=[1e9] * 3))
        with mock.patch.object(charts, "CHART_DIR", self.tmpdir / "charts"):
            p = charts.render_combined(make_cfg(), [], days=30)
        self.assertTrue(p and Path(p).exists())

    def test_render_combined_without_base_data(self):
        with mock.patch.object(charts, "CHART_DIR", self.tmpdir / "charts"):
            self.assertIsNone(charts.render_combined(make_cfg(), [], days=30))


if __name__ == "__main__":
    unittest.main()
