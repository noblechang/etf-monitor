#!/bin/bash
# 安装/升级 launchd 开机自启（服务常驻 + 内置交易日15:45后自动更新）
# 卸载: launchctl unload ~/Library/LaunchAgents/com.statefunds.etfmonitor.plist && rm 该文件
set -e

DIR="$(cd -P "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.statefunds.etfmonitor.plist"
PY="$DIR/.venv/bin/python"
mkdir -p "$DIR/logs"

if [ ! -x "$PY" ]; then echo "错误: 未找到 $PY，请先运行 setup.sh"; exit 1; fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.statefunds.etfmonitor</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$DIR/server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/server.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/server.err.log</string>
</dict></plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
sleep 2
if curl -s -m 5 http://127.0.0.1:8686/api/etfs >/dev/null; then
  echo "已安装并启动: http://127.0.0.1:8686  （开机自启、崩溃自动拉起、交易日15:45后自动更新）"
else
  echo "已加载 launchd，服务启动中… 稍后打开 http://127.0.0.1:8686（日志: $DIR/logs/）"
fi
