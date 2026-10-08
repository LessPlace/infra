#!/usr/bin/env python3
"""Root на common: чтение справочников через контейнер Backoffice, без вывода записей или ключей."""
import json
import os
import socket
import subprocess
import urllib.parse

CONTAINER = 'less-backoffice-backoffice-api-1'
BASE = '/etc/less/transport/'
ACTOR = 'AU_gQ5J6I3'


def request(url, certificate=True, actor=ACTOR, method='GET'):
    cmd=['docker','exec',CONTAINER,'curl','--silent','--show-error','--max-time','15',
         '--cacert',BASE+'ca.cert.pem','--write-out','\n%{http_code}',
         '--header','X-Backoffice-Actor-ID: '+actor,'--request',method]
    if certificate: cmd+=['--cert',BASE+'client.cert.pem','--key',BASE+'client.key.pem']
    result=subprocess.run([*cmd,url],capture_output=True)
    if result.returncode: raise RuntimeError('Transport failure')
    body,status=result.stdout.rsplit(b'\n',1)
    try: data=json.loads(body)
    except ValueError:data=None
    return int(status),data


def main():
    if os.geteuid()!=0 or socket.gethostname()!='p-common-1': raise RuntimeError('Wrong host/user')
    for service,port in [('control-plane',18491),('content-1',18493),('accounts',18492)]:
        base=f'https://{service}.backoffice.internal:{port}/internal/backoffice/v1/collections'
        assert request(base,certificate=False)[0]==400, 'Anonymous access'
        assert request(base,method='POST')[0]==403, 'Write method'
        assert request(base,actor='AU_a,AU_b')[0]==401, 'Invalid actor'
        status,body=request(base)
        assert status==200 and body['success'], 'Catalog discovery'
        for collection in body['result']:
            name=collection['id']
            status,page=request(base+'/'+name+'?limit=1')
            assert status==200 and page['success'], service+'/'+name+': query failed'
            data=page['result']
            assert data['collection']['id']==name and len(data['items'])<=1
            assert request(base+'/'+name+'?limit=101')[0]==400
            status,empty=request(base+'/'+name+'?q='+urllib.parse.quote("%_\\' OR true --",safe=''))
            assert status==200 and empty['result']['total']==0
            for row in data['items']:
                assert not set(row['values']) & {'token_sha256','token_hash','private_key_ref','payload','sid_hash'}
                status,detail=request(base+'/'+name+'/'+urllib.parse.quote(row['id'],safe=''))
                assert status==200 and detail['result']['items'][0]['id']==row['id']
            if name=='shards':
                status,shard=request(base+'/shards/1')
                assert status==200 and shard['result']['items'][0]['id']=='1'
                fields=shard['result']['items'][0]['values']
                assert fields.get('state')=='active' and fields.get('freshness')=='Свежий'
                print('Shard 1: active, fresh telemetry')
            print(service+'/'+name+': OK')
        if service == 'accounts':
            queue=f'https://accounts.backoffice.internal:18492/internal/backoffice/v1/registration-approvals'
            status,body=request(queue)
            assert status==200 and body['success'] and isinstance(body['result']['items'],list), 'Approval queue'
            # Курсор проходит тот же UserID.parse, что и одобрение. Проверяет настройку
            # StrictID в отдельном процессе serve-backoffice без изменения регистрации.
            if body['result']['items']:
                cursor=urllib.parse.quote(body['result']['items'][0]['id'],safe='')
                assert request(queue+'?after='+cursor)[0]==200, 'Approval ID bootstrap'
            assert request(queue+'?after=invalid')[0]==400, 'Invalid approval cursor'
            assert request(queue,method='POST')[0]==403, 'Unexpected write route'
            print('accounts/registration-approvals: OK')
        print(service+': anonymous, POST and malformed actor rejected')
    print('All remote checks passed; no record bodies or secret values printed')


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Только наше диагностическое сообщение, не ответы HTTP, SQL или curl.
        if isinstance(error,AssertionError): print('FAIL '+str(error))
        else: print('FAIL '+type(error).__name__)
        raise SystemExit(1)
