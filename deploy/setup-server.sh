#!/usr/bin/env bash
# Первичная настройка чистого сервера Ubuntu 24.04. Запускать один раз от root:
#   bash deploy/setup-server.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "Запустите от root"; exit 1; fi

echo "==> Обновление системы и нужные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y ca-certificates curl git ufw fail2ban unattended-upgrades docker.io docker-compose-v2

echo "==> Swap 2 ГБ (запас памяти для сервера с 2 ГБ RAM)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
sysctl -p /etc/sysctl.d/99-swappiness.conf >/dev/null

echo "==> Docker: запасное зеркало образов (если Docker Hub недоступен)"
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{
  "registry-mirrors": ["https://mirror.gcr.io"],
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
JSON
systemctl enable --now docker
systemctl restart docker

echo "==> Файрвол: открыты только SSH, 80 и 443"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "==> Защита от подбора пароля SSH и автообновления безопасности"
systemctl enable --now fail2ban
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Папка для бэкапов"
mkdir -p /opt/backups

echo
echo "Готово. Проверка: docker --version && docker compose version"
