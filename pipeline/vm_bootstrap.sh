#!/usr/bin/env bash
# Развёртывание обработчика загруженных публикаций на ВМ (Ubuntu 22.04).
# Ставит зависимости, создаёт службу и запускает её в дежурном режиме.
# Запуск на ВМ:  sudo bash vm_bootstrap.sh
set -euo pipefail

APP_DIR=/opt/loess/pipeline
VENV=/opt/loess/venv
SERVICE=loess-ingest

echo "== 1/5 системные пакеты =="
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip ca-certificates

echo "== 2/5 каталог и виртуальное окружение =="
mkdir -p "$APP_DIR"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet boto3 PyMuPDF img2pdf Pillow

echo "== 3/5 проверка файлов =="
missing=0
for f in .secrets 52_extract_v2.py 57_process_uploads.py; do
  if [ ! -f "$APP_DIR/$f" ]; then echo "  ОТСУТСТВУЕТ: $APP_DIR/$f"; missing=1; fi
done
if [ "$missing" = "1" ]; then
  echo
  echo "Скопируй недостающие файлы на ВМ и запусти скрипт снова, например:"
  echo "  scp .secrets 52_extract_v2.py 57_process_uploads.py geocache_sections.json <user>@<ip>:/tmp/"
  echo "  sudo mv /tmp/{.secrets,52_extract_v2.py,57_process_uploads.py,geocache_sections.json} $APP_DIR/"
  exit 1
fi
chmod 600 "$APP_DIR/.secrets"          # ключи не должны быть доступны другим пользователям

echo "== 4/5 служба systemd =="
cat > /etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=Цифровой атлас: обработчик загруженных публикаций
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment=WATCH=1
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
ExecStart=$VENV/bin/python 57_process_uploads.py
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now $SERVICE

echo "== 5/5 проверка =="
sleep 3
systemctl is-active $SERVICE && echo "служба работает"
echo
echo "Логи:        journalctl -u $SERVICE -f"
echo "Перезапуск:  sudo systemctl restart $SERVICE"
echo "Остановить:  sudo systemctl stop $SERVICE"
