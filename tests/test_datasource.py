"""数据源解析测试：用固定报文验证字段位（不访问网络）。"""
import datetime as dt
import re
import unittest
from unittest import mock

from etf_monitor import datasource


class _Resp:
    def __init__(self, payload=None, text="", content=None):
        self._payload = payload
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.status_code = 200

    def json(self):
        return self._payload


class KlineTest(unittest.TestCase):
    def test_parse_qfqday(self):
        payload = {"data": {"sh510300": {"qfqday": [
            ["2026-09-01", "4.000", "4.050", "4.060", "3.990", "123456.00"],
            ["2026-09-02", "4.050", "4.100", "4.110", "4.040", "100000.00"],
        ]}}}
        with mock.patch("etf_monitor.datasource._get",
                        return_value=_Resp(payload=payload)):
            rows = datasource.fetch_kline("sh510300", 550)
        self.assertEqual([r["date"] for r in rows], ["2026-09-01", "2026-09-02"])
        self.assertEqual(rows[0]["close"], 4.05)
        self.assertEqual(rows[0]["volume"], 12345600.0)          # 手 -> 份
        self.assertAlmostEqual(rows[0]["amount_est"], 12345600.0 * 4.05)

    def test_parse_day_fallback(self):
        payload = {"data": {"sh510300": {"day": [
            ["2026-09-01", "4.000", "4.050", "4.060", "3.990", "10.0"],
        ]}}}
        with mock.patch("etf_monitor.datasource._get",
                        return_value=_Resp(payload=payload)):
            rows = datasource.fetch_kline("sh510300", 550)
        self.assertEqual(rows[0]["volume"], 1000.0)


class QQSnapshotTest(unittest.TestCase):
    def test_parse_fields(self):
        p = ["1"] * 50
        p[3], p[4] = "4.050", "4.000"              # 现价 / 昨收
        p[30] = "20260930160000"                   # 时间戳
        p[37] = "219623"                           # 成交额(万元)
        p[45] = "978.50"                           # 总市值(亿元)
        text = 'v_sh510300="' + "~".join(p) + '";'
        with mock.patch("etf_monitor.datasource._get",
                        return_value=_Resp(content=text.encode("gbk"))):
            snap = datasource.fetch_snapshot_qq("sh510300")
        self.assertEqual(snap["price"], 4.05)
        self.assertAlmostEqual(snap["pct_chg"], 1.25)
        self.assertAlmostEqual(snap["scale"], 978.50e8)
        self.assertAlmostEqual(snap["shares"], 978.50e8 / 4.05)
        self.assertAlmostEqual(snap["amount"], 219623e4)
        self.assertEqual(snap["quote_date"], "2026-09-30")
        self.assertEqual(snap["source"], "tencent")

    def test_garbled_payload_returns_none(self):
        with mock.patch("etf_monitor.datasource._get",
                        return_value=_Resp(text="pv_none=1;")):
            self.assertIsNone(datasource.fetch_snapshot_qq("sh510300"))


class EMSnapshotTest(unittest.TestCase):
    DATA = {"f57": "510300", "f58": "沪深300ETF", "f43": 4.05, "f60": 4.0,
            "f84": 2.4165e11, "f116": 9.786e10, "f170": 1.25, "f86": 1759190400}

    def test_parse(self):
        with mock.patch("etf_monitor.datasource._get",
                        return_value=_Resp(payload={"data": self.DATA})) as mg:
            snap = datasource.fetch_snapshot_em("1.510300")
        self.assertEqual(snap["shares"], 2.4165e11)
        self.assertEqual(snap["price"], 4.05)
        self.assertEqual(snap["pct_chg"], 1.25)
        self.assertEqual(snap["source"], "eastmoney")
        self.assertEqual(snap["quote_date"],
                         dt.datetime.fromtimestamp(1759190400).strftime("%Y-%m-%d"))
        self.assertIn("push2.eastmoney.com", mg.call_args[0][0])

    def test_dash_fields_become_none(self):
        data = dict(self.DATA, f43="-", f170="-", f116="-")
        with mock.patch("etf_monitor.datasource._get",
                        return_value=_Resp(payload={"data": data})):
            snap = datasource.fetch_snapshot_em("1.510300")
        self.assertIsNone(snap["price"])
        self.assertIsNone(snap["pct_chg"])
        self.assertIsNone(snap["scale"])
        self.assertEqual(snap["shares"], 2.4165e11)

    def test_host_rotation_on_failure(self):
        seen = []

        def fake_get(url, referer=None, retries=3):
            seen.append(re.match(r"https://[^/]+", url).group(0))
            if url.startswith("https://push2.") or url.startswith("https://1.push2."):
                raise RuntimeError("blocked")
            return _Resp(payload={"data": self.DATA})

        with mock.patch("etf_monitor.datasource._get", side_effect=fake_get):
            snap = datasource.fetch_snapshot_em("1.510300")
        self.assertEqual(snap["shares"], 2.4165e11)
        self.assertGreaterEqual(len(seen), 3)       # 前两个节点失败后轮换成功

    def test_all_hosts_missing_f84_raises(self):
        def fake_get(url, referer=None, retries=3):
            return _Resp(payload={"data": {"f57": "510300", "f84": "-"}})

        with mock.patch("etf_monitor.datasource._get", side_effect=fake_get):
            with self.assertRaises(RuntimeError):
                datasource.fetch_snapshot_em("1.510300")
