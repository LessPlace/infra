#!/usr/bin/env python3
"""Доставляет только OAuth из Lockbox на common-ВМ, не выводя значения."""
import importlib.util
import os
from pathlib import Path


def main():
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
        'https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/e6qaamd9pdau01b8cf98/payload',
        {'Authorization': 'Bearer ' + token},
    )
    values = {entry['key']: entry['textValue'] for entry in payload['entries']}
    keys = ('OAUTH_VK_CLIENT_ID', 'OAUTH_VK_SERVICE_TOKEN',
            'OAUTH_YANDEX_CLIENT_ID', 'OAUTH_YANDEX_CLIENT_SECRET',
            'OAUTH_GOOGLE_CLIENT_ID', 'OAUTH_GOOGLE_SERVICE_TOKEN')
    selected = {key: values[key] for key in keys}
    if any(not value.strip() for value in selected.values()):
        raise ValueError('Empty OAuth field')
    delivery.save('oauth.env', delivery.env(selected))
    print('OAuth: six nonempty fields delivered; values omitted')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('OAuth delivery failed: ' + type(error).__name__)
        raise SystemExit(1)
