#!/usr/bin/env bash
# 一键部署：跟单信号监控 -> Bark
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/talksignal}"
SERVICE_NAME="talksignal"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 运行：sudo bash install.sh"
  exit 1
fi

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "未找到 python3，请先安装：apt install -y python3 或 yum install -y python3"
  exit 1
fi

mkdir -p "$APP_DIR/logs"
cp -f monitor.py "$APP_DIR/monitor.py"
if [ -f config.json ]; then
  if [ ! -f "$APP_DIR/config.json" ]; then
    cp -f config.json "$APP_DIR/config.json"
    echo "已写入初始 config.json（记得填 bark_key 与 unique_code）"
  else
    echo "保留已有 $APP_DIR/config.json，未覆盖"
  fi
fi
chmod 700 "$APP_DIR"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Copy-trade signals monitor -> Bark
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
ExecStart=${PY} ${APP_DIR}/monitor.py
Restart=always
RestartSec=15
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
systemctl restart "$SERVICE_NAME"

sleep 2
systemctl --no-pager --lines=8 status "$SERVICE_NAME" || true
echo
echo "部署完成：$(dirname "$APP_DIR")"
echo "查看日志：journalctl -u ${SERVICE_NAME} -f"
echo "改完 config 后：sudo systemctl restart ${SERVICE_NAME}"