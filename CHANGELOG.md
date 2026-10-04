# 变更日志

本项目的所有显著变更将记录在本文件。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [1.1.0] - 2026-10-04

### Fixed（代码审查发现并修复）
- **致命** `updater`：K线回填携带 `source="tencent_kline"`，次日回填经 COALESCE 覆盖快照写入的份额来源
  （eastmoney→kline），导致同源聚合只剩最新一天、规则**永远停留在 warming_up 无法生效**。
  修复：K线行统一 `source=None`，不触碰快照字段。（回归：tests S2 / P1）
- **致命** `rules._agg_shares`：聚合缺少"组内全覆盖"校验——组内某只当日降级到另一数据源时，
  其份额从当日合计消失（缺 588000 一只 ≈ 合计 -40%），会制造巨额假赎回信号。
  修复：当日组内全部 ETF 均有同源份额才参与日环比，覆盖不全的日期不计入。（回归：tests R8 / R9）
- `rules`：`drop5` / `ret20` 为 None 时信号消息格式化崩溃（TypeError）。修复：分段拼装。（回归：R3）
- `updater`：单只 ETF 的 K线拉取失败会中止整轮更新（数据/规则/图表全停）。修复：逐只容错，
  快照仍按 quote_date 独立入库。（回归：P2）
- `updater`：份额快照全部失败时仍标记"今天已更新"，当天不再自动重试。修复：失败连击计数，
  当天最多重试 3 轮。（回归：P3）
- `updater`：快照时间戳不在K线日期内时，把跨日快照价格写到了回退日期。修复：仅同日才覆盖价格字段。
- `server`：看板与 /api/summary 的"份额日变动"可能跨数据源比较（口径差 0.2%~3% 即假变动）。
  修复：仅前后两日同源才计算。
- `charts`：价格图例（收盘价/MA20）被信号图例覆盖丢失；组合图在无份额序列时 matplotlib 空图例警告；
  组合图标题硬编码 510300（回退到 510310 时不符）。均已修复。

### Added
- `tests/`：31 个单元 + 集成用例（纯标准库 unittest），覆盖存储 COALESCE 语义、
  数据源字段位解析、规则真值表、两日更新管线、失败降级矩阵；运行：
  `.venv/bin/python -m unittest discover -s tests -v`。
- K线回填逐日补算历史 `pct_chg`（此前历史明细该列为空）。
- `/api/etf/{code}/trend.png` 支持 `days` 20~550 按需渲染并按天数缓存（此前固定 120 天）。
- 调度器触发/失败日志（此前静默吞异常）。
- `docs/` 文档体系：需求、设计、接口、测试、部署运维、使用说明六篇。

### Changed
- 版本号 1.0.0 → 1.1.0（`etf_monitor.__version__` 与 FastAPI version）。
- `setup.sh`：按解释器版本自动选择 get-pip（3.9 及以下用 bootstrap.pypa.io/pip/3.9）。

## [1.0.0] - 2026-10-04

### Added
- 初始版本：
  - 数据源：腾讯 550 日K线回填 + 东财 f84 场内份额快照（4 边缘节点轮换）+ 腾讯快照降级；
  - 存储：SQLite（WAL）`daily` / `signals` / `meta` 三表，全字段 COALESCE upsert；
  - 规则引擎：BOTTOM_BUY_REF（大跌+放量净申购）/ AVOID_CHASE（高位+连续净赎回），
    同源守卫 + 5 日热身期，strong/medium 分级，evidence 证据留痕；
  - 服务：FastAPI（127.0.0.1:8686）网页看板 + 9 个 HTTP 接口（明细/趋势图/信号/规则/手动更新）；
  - 自动化：服务内调度线程（交易日 15:45 后自动更新、启动补跑）+ launchd 常驻
    （com.statefunds.etfmonitor，开机自启、崩溃拉起）；
  - 图表：8 张单ETF三联图 + 1 张组合图（信号竖线标注、单点份额可见、中文字体）；
  - 数据修复：绕过 macOS 系统代理（trust_env=False）、腾讯快照成交额字段位修正（p37）。
