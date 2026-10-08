#!/usr/bin/env python3
"""Проверка на ВМ от root: состояние, безопасные настройки и наличие ключа в журналах без их вывода."""
import json
from pathlib import Path
import re
import socket
import subprocess


def main():
    hostname = socket.gethostname()
    services = {
        'p-common-1': [
            'less-services-control-plane-1', 'less-services-accounts-1',
            'less-backoffice-backoffice-api-1', 'less-backoffice-reader-reader-1',
            'less-backoffice-reader-accounts-1',
        ],
        'p-content-shard-1': [
            'less-content-shard-1-content-1', 'less-backoffice-reader-reader-1',
        ],
    }
    if hostname not in services:
        raise ValueError('Unexpected machine')
    config = Path('/etc/less/secrets/monium.env').read_text()
    key = re.search(r'Authorization=Api-Key ([^,\n]+)', config).group(1)
    success = True
    for name in services[hostname]:
        detail = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
        environment = dict(item.split('=', 1) for item in detail['Config']['Env'])
        health = detail['State'].get('Health', {}).get('Status')
        logs = subprocess.run(['docker', 'logs', '--since', '15m', '--tail', '2000', name],
                              capture_output=True, text=True, check=True)
        output = logs.stdout + logs.stderr
        if environment.get('LOG_FILE_PATH'):
            file_log = subprocess.run(['docker', 'exec', name, 'tail', '-n', '2000',
                                       environment['LOG_FILE_PATH']], capture_output=True, text=True, check=True)
            output += file_log.stdout
        exposed = key in output
        failures = [line for line in output.splitlines() if 'Failed to export metrics' in line]
        latest = re.search(r'^\d{4}-\d{2}-\d{2}T\S+', failures[-1]) if failures else None
        enabled = environment.get('OTEL_SDK_DISABLED') == 'false'
        safe_log = environment.get('OTEL_LOG_LEVEL') == 'warning'
        success &= health == 'healthy' and enabled and safe_log and not exposed
        print(json.dumps({
            'container': name, 'health': health,
            'service': environment.get('OTEL_SERVICE_NAME'),
            'enabled': enabled, 'safe_log_level': safe_log,
            'key_found_in_logs': exposed, 'export_failures_in_checked_logs': len(failures),
            'latest_export_failure': latest.group() if latest else None,
        }))
    return 0 if success else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('Monium verification failed: ' + type(error).__name__)
        raise SystemExit(1)
