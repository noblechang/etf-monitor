# 国家队ETF动向监控

跟踪「国家队」（汇金系）重点持有的宽基 ETF 场内份额变动，自动执行两条判断规则，
供个人每日 5 分钟查看：**网页看板 + 查询 API（明细 / 趋势图 / 规则结果）**。

> 判断依据：国家队 2024–2025 单边增持托底，2026 年起转为"高抛低吸"的双向操作。
> 对个人的用法不是抄它的持仓，而是把它当**情绪的锚**：
> 恐慌时它进场（底部参照），亢奋时它撤退（不追高警示）。

## 判断规则（自动执行）

| 规则 | 触发条件 | 对应操作 |
|---|---|---|
| **底部买入参照** `BOTTOM_BUY_REF` | 基准ETF单日跌 ≥1.5%（或5日 ≥4%）**且** 沪深300系份额日增 ≥0.8%（或 ≥0.3% 且成交放量 ≥2倍均量） | 国家队进场形态，分批买入参照点 |
| **高位不追/止盈警示** `AVOID_CHASE` | 20日涨幅 ≥8%（或价格处于近120日90%分位）**且** 份额连续4日下降、累计 ≤-1% | 权重失去边际买家，不追高，拥挤仓位可部分止盈 |

两条规则均未触发 → 按自己的计划执行。阈值在 `config.json` 可调，改完执行
`.venv/bin/python update.py --force` 生效。

## 快速开始

```bash
bash setup.sh                        # 初始化 venv + 依赖（系统 Python 3.9 即可）
.venv/bin/python update.py --force   # 首次回填 550 日K线 + 当日份额快照
bash install_launchd.sh              # 安装开机自启（推荐，之后全自动）
# 手动方式: .venv/bin/python server.py
```

打开看板: <http://127.0.0.1:8686>

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v   # 31 个单元+集成用例，离线运行
```

## 文档（docs/）

| 文档 | 内容 |
|---|---|
| [01-需求文档](docs/01-需求文档.md) | 背景、目标、功能/非功能需求、验收标准 |
| [02-设计文档](docs/02-设计文档.md) | 架构、数据模型、更新管线、8 项关键设计决策 |
| [03-接口文档](docs/03-接口文档.md) | 全部 HTTP 接口的参数/响应示例/调用示例 |
| [04-测试文档](docs/04-测试文档.md) | 测试分层、31 个用例清单、缺陷回归映射、冒烟记录 |
| [05-部署与运维手册](docs/05-部署与运维手册.md) | 安装/升级/卸载、配置、备份、故障排查 |
| [06-使用说明](docs/06-使用说明.md) | 每天 5 分钟流程、信号解读与操作对照表、调参、FAQ |
| [CHANGELOG](CHANGELOG.md) | 版本变更记录（v1.1.0 为代码审查修复版） |

## 自动更新机制

- 服务进程内置调度线程：**交易日 15:45 后自动更新**（含规则评估 + 图表重绘），每 5 分钟检查一次；机器当时关机也不怕，启动时会自动补跑当天。
- `install_launchd.sh` 把服务注册为 launchd 常驻服务（开机自启、崩溃自动拉起），卸载：
  ```bash
  launchctl unload ~/Library/LaunchAgents/com.statefunds.etfmonitor.plist
  rm ~/Library/LaunchAgents/com.statefunds.etfmonitor.plist
  ```
- 也可手动更新：`.venv/bin/python update.py --force` 或 `POST /api/update`。

## API（供调用者）

基础地址 `http://127.0.0.1:8686`

| 接口 | 说明 | 示例 |
|---|---|---|
| `GET /api/summary` | 最新一轮汇总（价格/份额/规模/份额日变动 + 规则状态） | `curl http://127.0.0.1:8686/api/summary` |
| `GET /api/details` | **明细**：每日收盘/涨跌幅/成交额/份额/规模 | `curl "http://127.0.0.1:8686/api/details?code=510300&limit=60"` |
| `GET /api/signals` | **两条规则的结果**（当前状态 + 历史信号） | `curl http://127.0.0.1:8686/api/signals` |
| `GET /api/rules` | 规则定义与阈值 | `curl http://127.0.0.1:8686/api/rules` |
| `GET /api/etf/{code}/trend.png` | **趋势图**（价格/成交额/份额三联图） | `curl -o t.png "http://127.0.0.1:8686/api/etf/510300/trend.png?days=120"` |
| `GET /api/chart/combined.png` | 组合图（基准价格 + 汇金系合计份额） | 直接浏览器打开 |
| `POST /api/update?force=false` | 触发一轮更新 | `curl -X POST "http://127.0.0.1:8686/api/update?force=true"` |
| `GET /api/etfs` | 监控清单 | — |

监控标的（`config.json` 可增删）：510300 / 510310 / 159919 / 510050（沪深300+50 系）、
510500 / 512100 / 159977 / 588000（中证500、1000、创业板、科创50）。

## 数据源与口径

- **历史K线**：腾讯行情（550 日回填）；历史成交额为 `成交量×收盘价` 估算，用于放量比率这类同口径比较；历史涨跌幅由K线逐日补算。
- **每日份额快照**：主源东方财富 `f84`（场内份额，权威口径）；被限流时降级腾讯（总市值/现价 反推）。
- **同源 + 全覆盖校验**：规则只对"最新数据同来源、且组内全部 ETF 当日均有数据"的份额日做环比——东财与腾讯口径有 0.2%~3% 固定差，跨源或缺一只都会制造假信号；此类交易日规则显示 warming_up 而不是输出伪信号。
- 数据落盘 `data/etf.db`（SQLite），图表输出 `output/charts/`，日志 `logs/`。

## 重要提示

- **份额数据自部署日起积累，约 5 个交易日后规则完整生效**（此前 `/api/signals` 返回 warming_up）。
- 份额≈国家队动作的**技术形态代理**，不能区分汇金/险资/其他大机构，属概率参照而非确证。
- 节假日不更新数据但服务照常运行；周一类"首个交易日 15:45 后"数据才刷新。
- 本工具仅整理公开数据，不构成投资建议。

## 为什么装在 ~/Library/Application Support

1. 原计划的 `state-backed funds$state` 文件夹名含 `$`，pip/venv 工具链会把它当变量解析直接报错；
2. macOS 隐私保护（TCC）禁止 launchd 服务读 `~/Documents`，服务必须装在无限制目录。
`~/Documents/projects/etf-monitor` 是指向本目录的软链接，日常从这里进即可。
