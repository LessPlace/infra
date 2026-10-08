#!/usr/bin/env python3
"""Загружает только образы сервисов с краткоживущим токеном ВМ."""
import argparse
import json
import os
import subprocess
import tempfile
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('service', choices=['control-plane', 'accounts', 'backoffice-api', 'backoffice-web', 'content', 'web'])
service = parser.parse_args().service
compose = ('/opt/less/p-content-shard-1/compose.yml' if service == 'content' else
           '/opt/less/p-common-1/' + ('compose.backoffice.yml' if service.startswith('backoffice-')
                                    else 'compose.web.yml' if service == 'web' else 'compose.services.yml'))

request = urllib.request.Request(
    'http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
    headers={'Metadata-Flavor': 'Google'},
)
with urllib.request.urlopen(request, timeout=10) as response:
    token = json.load(response)['access_token']
with tempfile.TemporaryDirectory(prefix='less-registry-', dir='/run') as directory:
    os.chmod(directory, 0o700)
    subprocess.run(['docker', '--config', directory, 'login', 'cr.yandex', '-u', 'iam', '--password-stdin'],
                   input=token, text=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['docker', '--config', directory, 'compose', '-f',
                    compose, 'pull', service], check=True)
