#!/usr/bin/env bash
# Запускается через sudo на p-content-shard-1; не создаёт облачные ресурсы.
set -euo pipefail
test "$(id -u)" = 0
test "$(hostname)" = p-content-shard-1
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null; then
  # Та же поддерживаемая Ubuntu-сборка, что установлена на p-common-1.
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl docker.io docker-compose-v2
fi
install -d -m 0755 /opt/less/p-content-shard-1 /srv/less
install -d -m 0700 /etc/less/secrets
# PostgreSQL 18 понижает uid перед созданием БД в дочернем 18/docker: нужен проход через bind-root.
install -d -m 0750 -o 999 -g 999 /srv/less/postgres /srv/less/valkey
install -d -m 0750 -o 10001 -g 10001 /srv/less/logs/content
systemctl enable --now docker
docker version --format '{{.Server.Version}}'
docker compose version
