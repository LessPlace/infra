#!/usr/bin/env python3
"""Проверка зависимостей на шарде; значения секретов и ответы с ними не печатаются."""
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import urllib.error
import urllib.request
import uuid


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'p-content-shard-1':
        raise RuntimeError('Run as root on p-content-shard-1')
    values = json.loads(Path('/etc/less/secrets/sheets-api.json').read_text())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request('http://10.130.0.13:7010/registry/slice', headers={
        'Authorization': 'Bearer ' + values['CONTROL_PLANE_BEARER_TOKEN'],
    })
    with opener.open(request, timeout=10) as response:
        registry = json.load(response)
    assert registry['success']
    assert any(item['shard_number'] == 1 for item in registry['result']['shards'])
    print('Control Plane: data-plane token accepted, shard 1 registered')

    for authorized in (False, True):
        headers = {'Content-Type': 'application/json'}
        if authorized:
            headers['Authorization'] = 'Bearer ' + values['AUTH_REMOTE_BEARER_TOKEN']
        request = urllib.request.Request('http://10.130.0.13:1713/internal/v1/sessions/introspect',
                                         data=b'{}', headers=headers, method='POST')
        try:
            with opener.open(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        # Пустой запрос не создаёт данных: после служебной авторизации он отвергается декодером.
        assert status == (400 if authorized else 401), 'Accounts auth boundary status ' + str(status)
    print('Accounts: internal bearer accepted; unauthenticated request rejected')

    def s3(method, bucket, key, body=None):
        host = 'storage.yandexcloud.net'
        path = '/' + bucket + '/' + key
        now = datetime.datetime.now(datetime.timezone.utc)
        stamp, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
        digest = hashlib.sha256(body or b'').hexdigest()
        signed_headers = 'host;x-amz-content-sha256;x-amz-date'
        canonical_headers = f'host:{host}\nx-amz-content-sha256:{digest}\nx-amz-date:{stamp}\n'
        canonical = '\n'.join([method, path, '', canonical_headers, signed_headers, digest])
        scope = day + '/ru-central1/s3/aws4_request'
        to_sign = '\n'.join(['AWS4-HMAC-SHA256', stamp, scope,
                             hashlib.sha256(canonical.encode()).hexdigest()])
        signing_key = ('AWS4' + values['S3_SECRET_KEY']).encode()
        for part in (day, 'ru-central1', 's3', 'aws4_request'):
            signing_key = hmac.new(signing_key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
        headers = {'Host': host, 'x-amz-date': stamp, 'x-amz-content-sha256': digest,
                   'Authorization': 'AWS4-HMAC-SHA256 Credential=' + values['S3_ACCESS_KEY'] + '/' + scope
                   + ', SignedHeaders=' + signed_headers + ', Signature=' + signature}
        request = urllib.request.Request('https://' + host + path, data=body, headers=headers, method=method)
        try:
            with opener.open(request, timeout=20) as response:
                return response.status, response.read(1024)
        except urllib.error.HTTPError as error:
            return error.code, b''

    key = '.deployment-probes/' + uuid.uuid4().hex
    payload = b'less content shard dependency check\n'
    written = False
    try:
        status, _ = s3('PUT', 'less', key, payload)
        assert status == 200, 'S3 PUT status ' + str(status)
        written = True
        status, result = s3('GET', 'less', key)
        assert status == 200 and result == payload, 'S3 GET mismatch'
        print('S3 less: temporary object PUT/GET OK')
    finally:
        if written:
            status, _ = s3('DELETE', 'less', key)
            assert status == 204, 'S3 cleanup failed: ' + key
            print('S3 less: temporary object deleted')
    for bucket in ('less-accounts-prod', 'less-backups'):
        # Несуществующий объект может дать 404 до проверки прав; проверяем сам бакет.
        status, _ = s3('HEAD', bucket, '')
        print('S3 ' + bucket + ': HTTP ' + str(status))
        assert status == 403, 'Unexpected cross-bucket status ' + str(status)
        print('S3 ' + bucket + ': access denied as expected')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Dependency verification failed: ' + type(error).__name__)
        raise SystemExit(1)
