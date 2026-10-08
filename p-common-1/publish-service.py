#!/usr/bin/env python3
"""Публикует локально собранный сервис в существующий реестр."""
import argparse
import os
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('service', choices=['control-plane', 'accounts', 'backoffice-api', 'backoffice-web', 'content'])
service = parser.parse_args().service
image = 'cr.yandex/crp5f6o2ivb58ruhp27e/' + service + ':latest'
platform = subprocess.check_output(
    ['docker', 'image', 'inspect', image, '--format', '{{.Os}}/{{.Architecture}}'], text=True,
).strip()
if platform != 'linux/amd64':
    raise SystemExit('Публикация отменена: ВМ требуют linux/amd64, получено ' + platform)
with tempfile.TemporaryDirectory(prefix='less-registry-') as directory:
    os.chmod(directory, 0o700)
    token = subprocess.check_output(['yc', 'iam', 'create-token'], text=True).strip()
    subprocess.run(['docker', '--config', directory, 'login', 'cr.yandex', '-u', 'iam', '--password-stdin'],
                   input=token, text=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['docker', '--config', directory, 'push', image], check=True)
