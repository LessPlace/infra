#!/bin/sh
# Выгрузка продакшена и проверка восстановления; существующий стенд не меняется.
set -eu
umask 077
scripts=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=${1:-"$scripts/../../output/production-backups/$(date -u +%Y%m%dT%H%M%SZ)"}
mkdir -p "$(dirname -- "$root")"
mkdir -m 700 "$root"
python3 "$scripts/postgres.py" backup "$root/postgres"
python3 "$scripts/postgres.py" verify "$root/postgres"
python3 "$scripts/s3.py" backup "$root/s3"
python3 "$scripts/s3.py" verify "$root/s3"
printf '%s\n' 'Все выгрузки и проверки завершены.' > "$root/COMPLETE"
printf 'Резервная копия: %s\n' "$root"
