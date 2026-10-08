"""Вспомогательный доступ с p-common-1; токен оператора остаётся в памяти процесса."""
import json
from pathlib import Path
import socket
import subprocess
import urllib.request


def request(path, payload=None):
    if socket.gethostname() != 'p-common-1':
        raise RuntimeError('Run on p-common-1')
    values = dict(line.split('=', 1) for line in
                  Path('/etc/less/secrets/control-plane.env').read_text().splitlines() if '=' in line)
    field = 'AUTH_DATA_PLANE_TOKENS' if path == '/registry/slice' else 'AUTH_OPERATOR_TOKENS'
    token = next(value.strip() for value in values[field].split(',') if value.strip())
    address = subprocess.check_output([
        'docker', 'inspect', 'less-services-control-plane-1', '--format',
        '{{(index .NetworkSettings.Networks "less-database").IPAddress}}',
    ], text=True).strip()
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    data = None if payload is None else json.dumps(payload).encode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request('http://' + address + ':7010' + path,
                                           headers=headers, data=data), timeout=15) as response:
        body = json.load(response)
    if not body.get('success'):
        raise RuntimeError('Control Plane rejected the operation')
    return body['result']
