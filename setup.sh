#!/bin/bash
# 初始化 Python 环境（系统 Python 3.9 即可；3.10+ 同样适用）
set -e
cd -P "$(dirname "$0")"
[ -d .venv ] || python3 -m venv --without-pip .venv
# 新版 pip 已不支持 3.9 及以下，按解释器版本选 get-pip
if [ "$(.venv/bin/python -c 'import sys; print(1 if sys.version_info < (3, 10) else 0)')" = "1" ]; then
  GETPIP="https://bootstrap.pypa.io/pip/3.9/get-pip.py"
else
  GETPIP="https://bootstrap.pypa.io/get-pip.py"
fi
curl -sS "$GETPIP" -o /tmp/get-pip.py
.venv/bin/python /tmp/get-pip.py --quiet --disable-pip-version-check
.venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt
mkdir -p logs data output/charts
echo "依赖安装完成。下一步: .venv/bin/python update.py --force"
