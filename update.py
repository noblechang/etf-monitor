#!/usr/bin/env python3
"""命令行更新入口。

用法:
  python update.py                 # 常规更新（幂等，当天已更新则跳过）
  python update.py --force         # 强制重新拉取
  python update.py --no-charts     # 不重绘图表
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from etf_monitor import updater  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="国家队ETF动向监控 - 数据更新")
    ap.add_argument("--force", action="store_true", help="强制更新（忽略当天已更新）")
    ap.add_argument("--no-charts", action="store_true", help="跳过图表重绘")
    args = ap.parse_args()

    cfg = updater.load_config()
    summary = updater.update_once(cfg, force=args.force, with_charts=not args.no_charts)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
