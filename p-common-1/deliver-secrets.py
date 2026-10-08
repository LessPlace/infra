#!/usr/bin/env python3
"""На ВМ получает Lockbox в память и сохраняет закрытые файлы; значения не печатает."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request

ROOT = Path('/etc/less/secrets')
SECRETS = {
    'common': 'e6q18nsadoqdvooo367u',
    'control-plane': 'e6q8a1n97jhj0a8fns4o',
    'accounts': 'e6q5lo2hreokmh7bkbh4',
    'accounts-s3': 'e6q7mq9ucvhbuqmi49tp',
    'mail-sender': 'e6qlu4jkubles359bgl2',
}

def fetch(url, headers):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=headers), timeout=20) as response:
        return json.load(response)

def save(name, content):
    fd, temporary = tempfile.mkstemp(prefix='.delivery-', dir=ROOT)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(content)
        os.replace(temporary, ROOT / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def env(values):
    for key, value in values.items():
        if not isinstance(value, str) or not value or any(c in value for c in '\r\n\0'):
            raise ValueError('Invalid secret field: ' + key)
    return ''.join(key + '=' + value + '\n' for key, value in values.items())

def main():
    if os.geteuid() != 0:
        raise RuntimeError('Run as root on p-common-1')
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = fetch('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
                  {'Metadata-Flavor': 'Google'})['access_token']
    payloads = {}
    for name, identifier in SECRETS.items():
        payload = fetch('https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/' + identifier + '/payload',
                        {'Authorization': 'Bearer ' + token})
        payloads[name] = {entry['key']: entry['textValue'] for entry in payload['entries']}
    common = payloads['common']
    cp = dict(payloads['control-plane'], DB_PASSWORD=common['CONTROLPLANE_DB_PASSWORD'],
              STRICT_ID_SEED=common['STRICT_ID_SEED'])
    accounts = dict(payloads['accounts'], **payloads['accounts-s3'],
                    DB_PASSWORD=common['ACCOUNTS_DB_PASSWORD'], STRICT_ID_SEED=common['STRICT_ID_SEED'],
                    VALKEY_PASSWORD=common['VALKEY_ACCOUNTS_PASSWORD'])
    mail = {'MAIL_SMTP_USERNAME': payloads['mail-sender']['ID'],
            'MAIL_SMTP_PASSWORD': payloads['mail-sender']['KEY']}
    files = {'control-plane.env': env(cp), 'accounts.env': env(accounts), 'mail.env': env(mail),
             'common.env': env(common), 'postgres-admin-password': common['POSTGRES_ADMIN_PASSWORD']}
    digest = hashlib.sha256(common['VALKEY_ACCOUNTS_PASSWORD'].encode()).hexdigest()
    files['valkey.acl'] = 'user default off\nuser accounts on #' + digest + ' ~less:accounts:v1:* &less:accounts:v1:* +@all -@admin -@dangerous\n'
    for name, content in files.items():
        save(name, content)
    # Valkey понижает привилегии до uid 999. Родительский каталог на хосте остаётся root:0700.
    os.chown(ROOT / 'valkey.acl', 999, 999)
    print('Delivered secret files; values omitted')

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Secret delivery failed: ' + type(error).__name__)
        raise SystemExit(1)
