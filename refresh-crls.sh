#!/bin/sh
# Запускает владелец УЦ на Mac. Пароли вводятся только в приглашениях OpenSSL.
# Обновляет все четыре CRL; выключенные локальные службы не запускает.
set -eu
umask 077
infra_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$infra_dir/.."
export LESS_CA_DIR="$PWD/pki/00-root-pki"
export LESS_MACHINE_CA_DIR="$HOME/.less-pki/backoffice-transport-prod"
local_pki="$PWD/backoffice-api/.local/integrations/pki"

common() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 \
        -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
        kirilltitov@p-common-1.devless.ru "$@"
}

shard() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 \
        -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
        -J kirilltitov@p-common-1.devless.ru \
        kirilltitov@p-content-shard-1.devless.ru "$@"
}

expect_400() {
    code=$(curl --silent --show-error --max-time 10 \
        --output /dev/null --write-out '%{http_code}' "$@")
    if [ "$code" != 400 ]; then
        printf 'Ожидался HTTP 400, получен %s: %s\n' "$code" "$*" >&2
        return 1
    fi
}

running() {
    case "
$local_containers
" in
        *"
$1
"*) return 0 ;;
        *) return 1 ;;
    esac
}

printf '\nПредварительная проверка…\n'
for tool in openssl python3 ssh scp curl; do
    command -v "$tool" >/dev/null
done
for file in \
    pki/ca.cnf pki/ca-admin.cnf \
    "$LESS_MACHINE_CA_DIR/ca.cnf" "$local_pki/ca.cnf" \
    "$LESS_CA_DIR/root/private/ca.key.pem" \
    "$LESS_CA_DIR/admin/private/ca.key.pem" \
    "$LESS_MACHINE_CA_DIR/ca.key.pem" "$local_pki/ca.key.pem"; do
    test -r "$file" || {
        printf 'Нет доступа: %s\n' "$file" >&2
        exit 1
    }
done

remote_check='
    set -eu
    sudo -n true
    sudo -n test -w /etc/less/backoffice-transport
    sudo -n docker info >/dev/null
    sudo -n docker compose \
        -f /opt/less/backoffice-integrations/compose.yml config -q
    for service in proxy reader; do
        id=$(sudo -n docker compose \
            -f /opt/less/backoffice-integrations/compose.yml ps -q "$service")
        test -n "$id"
        test "$(sudo -n docker inspect \
            --format "{{.State.Running}}" "$id")" = true
    done
    sudo -n docker compose \
        -f /opt/less/backoffice-integrations/compose.yml \
        exec -T proxy nginx -t
'
common "$remote_check"
shard "$remote_check"

common '
    set -eu
    sudo -n test -w /etc/less/backoffice-pki
    sudo -n test -r /opt/less/backoffice-integrations/verify-live.py
    id=$(sudo -n docker compose \
        -f /opt/less/backoffice-integrations/compose.yml ps -q accounts)
    test -n "$id"
    test "$(sudo -n docker inspect \
        --format "{{.State.Running}}" "$id")" = true
    sudo -n docker compose \
        -f /opt/less/p-common-1/compose.edge.yml exec -T edge nginx -t
    for endpoint in \
        control-plane.backoffice.internal:18491 \
        content-1.backoffice.internal:18493 \
        accounts.backoffice.internal:18492; do
        code=$(sudo -n docker exec less-backoffice-backoffice-api-1 \
            curl --silent --show-error --max-time 10 \
            --cacert /etc/less/transport/ca.cert.pem \
            --output /dev/null --write-out "%{http_code}" \
            "https://$endpoint/internal/backoffice/v1/collections")
        test "$code" = 400
        printf "%s: доступен\n" "$endpoint"
    done
'
curl --fail --silent --show-error --max-time 10 \
    --output /dev/null https://less.place/
expect_400 https://backoffice.less.place/

# Docker и локальные адреса нужны только для запущенных служб.
local_containers=""
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    local_containers=$(docker ps --format '{{.Names}}')
else
    printf 'Локальный Docker недоступен: проверка служб пропущена.\n'
fi

if running less-backoffice-https-local; then
    test -r backoffice-api/.env.local
    test -d backoffice-web/dist
    docker image inspect nginx:1.27-alpine >/dev/null
    docker exec less-backoffice-https-local nginx -t
    expect_400 --cacert backoffice-api/.local/https/localhost.cert.pem \
        https://localhost:17472/
fi

for name in less-backoffice-services-local less-backoffice-services-test; do
    if running "$name"; then
        docker exec "$name" nginx -t
        if [ "$name" = less-backoffice-services-local ]; then
            ports="18491 18492 18493"
        else
            ports="28491 28492 28493"
        fi
        for port in $ports; do
            expect_400 --cacert "$local_pki/ca.cert.pem" \
                "https://localhost:$port/internal/backoffice/v1/collections"
        done
    fi
done

# Выпускаем и проверяем все списки до установки первого из них.
stage=$(mktemp -d /tmp/less-crls.XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT

issue_crl() {
    openssl ca -config "$1" -gencrl -out "$stage/$3"
    openssl crl -in "$stage/$3" -noout -verify -CAfile "$2"
    openssl crl -in "$stage/$3" -noout -lastupdate -nextupdate
}

printf '\nПроверки прошли. Выпуск всех CRL…\n'
issue_crl pki/ca-admin.cnf "$LESS_CA_DIR/admin/certs/ca.cert.pem" admin.pem
issue_crl pki/ca.cnf "$LESS_CA_DIR/root/certs/ca.cert.pem" root.pem
issue_crl "$LESS_MACHINE_CA_DIR/ca.cnf" "$LESS_MACHINE_CA_DIR/ca.cert.pem" machine.pem
issue_crl "$local_pki/ca.cnf" "$local_pki/ca.cert.pem" local.pem

mkdir -p "$LESS_CA_DIR/admin/crl" "$LESS_CA_DIR/root/crl"
cat "$stage/admin.pem" > "$LESS_CA_DIR/admin/crl/admin.crl.pem"
cat "$stage/root.pem" > "$LESS_CA_DIR/root/crl/root.crl.pem"
cat "$stage/machine.pem" > "$LESS_MACHINE_CA_DIR/ca.crl.pem"
# Запись в существующий файл сохраняет локальные файловые монтирования.
cat "$stage/local.pem" > "$local_pki/ca.crl.pem"

printf '\nУстановка в прод…\n'
python3 infra/p-common-1/deliver-backoffice-pki.py
common '
    set -eu
    cd /opt/less/p-common-1
    sudo -n docker compose -f compose.edge.yml exec -T edge nginx -t
    sudo -n docker compose -f compose.edge.yml exec -T edge nginx -s reload
'

install_crl='
    set -eu
    sudo -n install -m 0644 /dev/stdin /etc/less/backoffice-transport/ca.crl.pem
    sudo -n openssl crl \
        -in /etc/less/backoffice-transport/ca.crl.pem \
        -noout -verify -CAfile /etc/less/backoffice-transport/ca.cert.pem
'
common "$install_crl" < "$stage/machine.pem"
shard "$install_crl" < "$stage/machine.pem"

common 'sudo -n docker compose \
    -f /opt/less/backoffice-integrations/compose.yml \
    up -d --force-recreate --wait --wait-timeout 120 proxy reader accounts'
shard 'sudo -n docker compose \
    -f /opt/less/backoffice-integrations/compose.yml \
    up -d --force-recreate --wait --wait-timeout 120 proxy reader'

# Обновляем только те локальные службы, которые уже работали.
if running less-backoffice-https-local; then
    python3 backoffice-api/scripts/local-https.py up
    expect_400 --cacert backoffice-api/.local/https/localhost.cert.pem \
        https://localhost:17472/
fi
for name in less-backoffice-services-local less-backoffice-services-test; do
    if running "$name"; then
        docker exec "$name" nginx -t
        docker exec "$name" nginx -s reload
    fi
done

printf '\nИтоговая проверка прода…\n'
common 'sudo -n python3 /opt/less/backoffice-integrations/verify-live.py'
expect_400 https://backoffice.less.place/
printf '\nВсе четыре CRL обновлены. Проверка прода прошла.\n'
