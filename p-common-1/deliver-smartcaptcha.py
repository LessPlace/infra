#!/usr/bin/env python3
"""На p-common-1: доставляет только SmartCaptcha, не печатает значения."""
import importlib.util
import os
from pathlib import Path
import socket


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'p-common-1':
        raise RuntimeError('Run as root on p-common-1')
    spec = importlib.util.spec_from_file_location('delivery', Path(__file__).with_name('deliver-secrets.py'))
    delivery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delivery)
    os.umask(0o077)
    token = delivery.fetch(
        'http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
        {'Metadata-Flavor': 'Google'},
    )['access_token']
    payload = delivery.fetch(
        'https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/e6qihdjbts41nkm9nvs1/payload',
        {'Authorization': 'Bearer ' + token},
    )
    fields = {entry['key']: entry['textValue'] for entry in payload['entries']}
    values = {key: fields[key] for key in ('SMARTCAPTCHA_CLIENT_KEY', 'SMARTCAPTCHA_SERVER_KEY')}
    if not values['SMARTCAPTCHA_CLIENT_KEY'].startswith('ysc1_'):
        raise ValueError('Invalid client key')
    if not values['SMARTCAPTCHA_SERVER_KEY'].startswith('ysc2_'):
        raise ValueError('Invalid server key')
    delivery.save('smartcaptcha.env', delivery.env(values))
    print('SmartCaptcha delivered; values omitted')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Delivery failed: ' + type(error).__name__)
        raise SystemExit(1)
