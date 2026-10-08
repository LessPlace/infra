#!/usr/bin/env python3
"""На ВМ: доставить отдельный Lockbox-секрет и создать только БД/роль backoffice."""
import argparse
import importlib.util
import os
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--secret-id', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Run as root on p-common-1')
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location('delivery', Path(__file__).with_name('deliver-secrets.py'))
    delivery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delivery)
    token = delivery.fetch(
        'http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
        {'Metadata-Flavor': 'Google'},
    )['access_token']
    payload = delivery.fetch(
        'https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/' + args.secret_id + '/payload',
        {'Authorization': 'Bearer ' + token},
    )
    fields = {entry['key']: entry['textValue'] for entry in payload['entries']}
    values = {key: fields[key] for key in ('DB_PASSWORD', 'AUTH_PROXY_SECRET', 'SECURITY_PIN_PEPPER')}
    # Прокси включает значение в nginx-конфигурацию: никаких управляющих символов и подстановок.
    if any(not re.fullmatch(r'[0-9a-fA-F]{64,}', value) for value in values.values()):
        raise ValueError('Expected independently generated hexadecimal secrets, at least 32 bytes each')
    if len(set(values.values())) != 3:
        raise ValueError('The three secrets must differ')
    common = dict(line.split('=', 1) for line in (delivery.ROOT / 'common.env').read_text().splitlines())
    values['STRICT_ID_SEED'] = common['STRICT_ID_SEED']
    command = ['docker', 'compose', '-f', '/opt/less/p-common-1/compose.common.yml', 'exec', '-T',
               'postgres', 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'postgres']

    def sql(query):
        result = subprocess.run(command, input=query, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError('Database preparation failed; query and credentials withheld')
        return result.stdout.strip()

    if not sql("SELECT 1 FROM pg_roles WHERE rolname='backoffice'"):
        sql("CREATE ROLE backoffice LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '"
            + values['DB_PASSWORD'] + "'")
    if not sql("SELECT 1 FROM pg_database WHERE datname='backoffice'"):
        sql('CREATE DATABASE backoffice OWNER backoffice')
    sql('REVOKE ALL ON DATABASE backoffice FROM PUBLIC; GRANT CONNECT, TEMPORARY ON DATABASE backoffice TO backoffice')
    delivery.save('backoffice.env', delivery.env(values))
    delivery.save('backoffice-proxy.conf', 'proxy_set_header X-Backoffice-Proxy-Token "'
                  + values['AUTH_PROXY_SECRET'] + '";\n')
    print('backoffice: role/database ready, secret files delivered; values omitted')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Backoffice preparation failed: ' + type(error).__name__ + '; details withheld')
        raise SystemExit(1)
