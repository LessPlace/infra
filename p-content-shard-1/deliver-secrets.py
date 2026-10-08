#!/usr/bin/env python3
"""Доставка Lockbox на ВМ. Значения не попадают ни в stdout, ни в аргументы процессов."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import urllib.request

ROOT = Path('/etc/less/secrets')


def fetch(url, headers):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=headers), timeout=20) as response:
        return json.load(response)


def save(name, contents, uid=0):
    fd, temporary = tempfile.mkstemp(prefix='.delivery-', dir=ROOT)
    try:
        with os.fdopen(fd, 'w') as target:
            target.write(contents)
        os.chown(temporary, uid, uid)
        os.chmod(temporary, 0o400)
        os.replace(temporary, ROOT / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shared-secret-id', required=True)
    parser.add_argument('--shard-secret-id', required=True)
    parser.add_argument('--storage-total-bytes', type=int)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'p-content-shard-1':
        raise RuntimeError('Run as root on p-content-shard-1')
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = fetch('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
                  {'Metadata-Flavor': 'Google'})['access_token']

    def secret(identifier):
        payload = fetch('https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/' + identifier + '/payload',
                        {'Authorization': 'Bearer ' + token})
        return {entry['key']: entry['textValue'] for entry in payload['entries']}

    shared = secret(args.shared_secret_id)
    shard = secret(args.shard_secret_id)
    s3 = secret('e6qkbdm11rko16nh13ak')
    values = {key: shared[key] for key in (
        'STRICT_ID_SEED', 'AUTH_REMOTE_BEARER_TOKEN', 'CONTENT_BEARER_TOKEN', 'CONTROL_PLANE_BEARER_TOKEN',
        'PAGE_CREATION_TICKET_PRIVATE_KEY', 'PAGE_CREATION_TICKET_PUBLIC_KEY',
    )}
    values.update({key: s3[key] for key in ('S3_ACCESS_KEY', 'S3_SECRET_KEY')})
    values.update({key: shard[key] for key in ('DB_PASSWORD', 'VALKEY_PASSWORD')})
    if 'SHARD_TELEMETRY_TOKEN' in shard:
        if args.storage_total_bytes is None or args.storage_total_bytes <= 0:
            raise ValueError('Telemetry requires an explicit verified storage budget')
        values['SHARD_TELEMETRY_TOKEN'] = shard['SHARD_TELEMETRY_TOKEN']
        values['SHARD_TELEMETRY_STORAGE_TOTAL_BYTES'] = str(args.storage_total_bytes)
        values['SHARD_TELEMETRY_REPORTER'] = 'p-content-shard-1'
    for key, value in dict(values, POSTGRES_ADMIN_PASSWORD=shard['POSTGRES_ADMIN_PASSWORD']).items():
        if not isinstance(value, str) or not value or '\0' in value:
            raise ValueError('Invalid field: ' + key)
        if key not in ('PAGE_CREATION_TICKET_PRIVATE_KEY', 'PAGE_CREATION_TICKET_PUBLIC_KEY'):
            if '\n' in value or '\r' in value:
                raise ValueError('Invalid multiline field: ' + key)
    # PEM остаются многострочными. Администраторский пароль БД приложению не передаётся.
    save('sheets-api.json', json.dumps(values), uid=10001)
    save('postgres-admin-password', shard['POSTGRES_ADMIN_PASSWORD'])
    digest = hashlib.sha256(shard['VALKEY_PASSWORD'].encode()).hexdigest()
    # Этот экземпляр Valkey выделен одному шарду; служебные ключи используют несколько префиксов.
    save('valkey.acl', 'user default off\nuser content on #' + digest
         + ' ~* &* +@all -@admin -@dangerous\n', uid=999)
    print('Shard secrets delivered; values omitted; telemetry=' + str('SHARD_TELEMETRY_TOKEN' in values))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Secret delivery failed: ' + type(error).__name__)
        raise SystemExit(1)
