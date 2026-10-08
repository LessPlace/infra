#!/usr/bin/env python3
"""Запускать от root на каждой ВМ. Значения Lockbox не выводятся."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import urllib.request


def fetch(url, headers):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as response:
        return json.load(response)


def make_environment(key, folder, hostname):
    if not re.fullmatch(r'[A-Za-z0-9_./+=-]{20,4096}', key):
        raise ValueError('Invalid key format')
    if not re.fullmatch(r'[a-z0-9]{10,64}', folder):
        raise ValueError('Invalid folder')
    if hostname not in ('p-common-1', 'p-content-shard-1'):
        raise ValueError('Unexpected machine')
    return '\n'.join([
        'OTEL_SDK_DISABLED=false',
        # swift-otel при уровне info журналирует значения переменных, включая заголовки авторизации.
        'OTEL_LOG_LEVEL=warning',
        'OTEL_METRICS_EXPORTER=otlp',
        'OTEL_TRACES_EXPORTER=none',
        'OTEL_LOGS_EXPORTER=none',
        'OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=http/protobuf',
        'OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=https://ingest.monium.yandex.cloud/otlp/v1/metrics',
        'OTEL_EXPORTER_OTLP_METRICS_TIMEOUT=10000',
        'OTEL_METRIC_EXPORT_INTERVAL=60000',
        'OTEL_METRIC_EXPORT_TIMEOUT=10000',
        # swift-otel parses header values literally, without URL decoding.
        f'OTEL_EXPORTER_OTLP_METRICS_HEADERS=Authorization=Api-Key {key},x-monium-project=folder__{folder},x-monium-cluster=production',
        f'OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=production,host.name={hostname}',
        '',
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--secret-id', required=True)
    parser.add_argument('--folder-id', default='b1gk1vp06cum61i9d4fe')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Run as root')
    if not re.fullmatch(r'[a-z0-9]{10,64}', args.secret_id):
        raise ValueError('Invalid secret ID')
    token = fetch('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
                  {'Metadata-Flavor': 'Google'})['access_token']
    payload = fetch(f'https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/{args.secret_id}/payload',
                    {'Authorization': 'Bearer ' + token})
    fields = {entry['key']: entry.get('textValue', '') for entry in payload['entries']}
    content = make_environment(fields['MONIUM_API_KEY'], args.folder_id, socket.gethostname())
    directory = Path('/etc/less/secrets')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = directory / '.monium.env.new'
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, directory / 'monium.env')
    print('Monium environment delivered; values omitted')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Monium delivery failed: ' + type(error).__name__)
        raise SystemExit(1)
