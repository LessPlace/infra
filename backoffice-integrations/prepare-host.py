#!/usr/bin/env python3
"""Root на ВМ: доставка её Lockbox, минимальная роль БД и конфигурация mTLS. Не запускает контейнеры."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import urllib.request

SETTINGS = {
 'p-common-1': ('control-plane', 'e6qt0qkmu05bul5v9r27', 'less-common-postgres-1', 'controlplane', 18491),
 'p-content-shard-1': ('content-1', 'e6qjolrsk7g87dofl5tb', 'less-content-shard-1-postgres-1', 'less_content', 18493),
}
TABLES = {
 'control-plane': 'country region site shard shard_census number_block workspace_residency_policy workspace_placement compliance_report bulletin',
 'content-1': 'organization workspace user_profile workspace_member block workspace_asset page_crdt_snapshot page_operation_log',
}


def run(args, data=None):
    result = subprocess.run(args, input=data, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError('Command failed: ' + args[0])
    return result.stdout


def save(path, value, uid=0, mode=0o400):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(value)
        os.chown(temporary, uid, uid)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def fetch(url, headers):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=headers), timeout=20) as response:
        return json.load(response)


def scram(password):
    salt = os.urandom(16)
    salted = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 4096)
    stored = hashlib.sha256(hmac.new(salted, b'Client Key', 'sha256').digest()).digest()
    server = hmac.new(salted, b'Server Key', 'sha256').digest()
    enc = lambda b: base64.b64encode(b).decode()
    return f'SCRAM-SHA-256$4096:{enc(salt)}${enc(stored)}:{enc(server)}'


def main():
    host = socket.gethostname()
    if os.geteuid() != 0 or host not in SETTINGS:
        raise RuntimeError('Wrong host/user')
    os.umask(0o077)
    service, identifier, postgres, database, port = SETTINGS[host]
    pki = Path('/etc/less/backoffice-transport')
    run(['openssl','verify','-CAfile',str(pki/'ca.cert.pem'),'-CRLfile',str(pki/'ca.crl.pem'),
         '-crl_check','-purpose','sslserver','-verify_hostname',service+'.backoffice.internal',str(pki/f'{service}.cert.pem')])
    run(['openssl','verify','-CAfile',str(pki/'ca.cert.pem'),'-CRLfile',str(pki/'ca.crl.pem'),
         '-crl_check','-purpose','sslclient',str(pki/'client.cert.pem')])
    token = fetch('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
                  {'Metadata-Flavor':'Google'})['access_token']
    payload = fetch('https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/'+identifier+'/payload',
                    {'Authorization':'Bearer '+token})
    values = {x['key']:x['textValue'] for x in payload['entries']}
    if set(values) != {'DB_PASSWORD','BACKOFFICE_PROXY_SECRET'} or any(
            re.fullmatch('[0-9a-fA-F]{64}', x) is None for x in values.values()):
        raise RuntimeError('Unexpected secret format')
    # Только нужный seed, не токены продукта, S3 или пароли других ролей.
    if service == 'control-plane':
        env = dict(line.split('=',1) for line in Path('/etc/less/secrets/control-plane.env').read_text().splitlines() if '=' in line)
    else:
        env = json.loads(Path('/etc/less/secrets/sheets-api.json').read_text())
    values['STRICT_ID_SEED'] = env['STRICT_ID_SEED']
    if any('\n' in v or '\r' in v or '\0' in v for v in values.values()):
        raise RuntimeError('Invalid environment field')
    values.update(DB_HOST='postgres', DB_PORT='5432', DB_USERNAME='backoffice_reader', DB_DATABASE=database)
    sql = """BEGIN;
DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='backoffice_reader') THEN
  CREATE ROLE backoffice_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF EXISTS (SELECT FROM pg_roles WHERE rolname='backoffice_reader' AND
 (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) OR
 EXISTS (SELECT FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='backoffice_reader')) THEN
  RAISE EXCEPTION 'Unexpected existing role';
 END IF;
END $$;
"""
    sql += f"ALTER ROLE backoffice_reader PASSWORD '{scram(values['DB_PASSWORD'])}';\n"
    sql += "ALTER ROLE backoffice_reader SET default_transaction_read_only=on;\n"
    sql += "ALTER ROLE backoffice_reader SET statement_timeout='8s';\n"
    sql += f'GRANT CONNECT ON DATABASE {database} TO backoffice_reader;\n'
    sql += 'GRANT USAGE ON SCHEMA public TO backoffice_reader;\n'
    sql += 'GRANT SELECT ON '+','.join(TABLES[service].split())+' TO backoffice_reader;\n'
    if service == 'control-plane':
        sql += '''GRANT SELECT(client_intent_id,shard_number,name,issued_by,created_at,revoked_at) ON shard_token TO backoffice_reader;
GRANT SELECT(kid,role,parent_kid,state,not_before,not_after) ON signing_key TO backoffice_reader;
GRANT SELECT(id,at,actor,action,target,reason) ON audit_log TO backoffice_reader;
'''
    sql += 'COMMIT;\n'
    run(['docker','exec','-i',postgres,'psql','-X','-q','-v','ON_ERROR_STOP=1','-U','postgres','-d',database],sql.encode())
    target = Path('/etc/less/backoffice-reader')
    target.mkdir(mode=0o700, exist_ok=True)
    save(target/'reader.env', ''.join(k+'='+v+'\n' for k,v in values.items()))
    # Sheets entrypoint по умолчанию ищет JSON; выделенный документ не содержит продуктовых секретов.
    save(target/'reader.json', json.dumps(values), uid=10001)
    logs = Path('/var/log/less/backoffice-reader')
    logs.mkdir(parents=True, exist_ok=True)
    os.chown(logs,10001,10001)
    os.chmod(logs,0o750)
    serial = run(['openssl','x509','-in',str(pki/'client.cert.pem'),'-noout','-serial']).decode().strip().split('=',1)[1]
    if not re.fullmatch('[0-9A-Fa-f]+',serial): raise RuntimeError('Invalid serial')
    nginx = f'''map $ssl_client_serial $service_identity {{ default 0; "{serial}" 1; }}
server {{
 listen {port} ssl;
 server_name {service}.backoffice.internal;
 ssl_certificate /pki/server.cert.pem;
 ssl_certificate_key /pki/server.key.pem;
 ssl_client_certificate /pki/ca.cert.pem;
 ssl_verify_client on;
 ssl_verify_depth 1;
 ssl_crl /pki/ca.crl.pem;
 ssl_protocols TLSv1.2 TLSv1.3;
 ssl_session_cache off;
 ssl_session_tickets off;
 access_log off;
 if ($service_identity = 0) {{ return 403; }}
 location /internal/backoffice/v1/ {{
  limit_except GET {{ deny all; }}
  proxy_pass http://127.0.0.1:18481;
  proxy_set_header X-Backoffice-TLS-Verify $ssl_client_verify;
  proxy_set_header X-Backoffice-Service backoffice-api;
  proxy_set_header X-Backoffice-Proxy-Token "{values['BACKOFFICE_PROXY_SECRET']}";
  proxy_set_header X-Backoffice-Actor-ID $http_x_backoffice_actor_id;
  proxy_set_header X-Backoffice-Roles "";
  proxy_set_header X-Backoffice-Admin-ID "";
  proxy_set_header X-Backoffice-Client-Cert "";
  proxy_set_header Authorization "";
  proxy_set_header Cookie "";
  proxy_connect_timeout 2s;
  proxy_read_timeout 10s;
  proxy_buffering off;
 }}
 location / {{ return 404; }}
}}
'''
    save(target/'nginx.conf',nginx)
    if service == 'control-plane':
        # UID контейнера Backoffice; серверные/CA ключи клиенту не монтируются.
        os.chown(pki/'client.key.pem',0,10001)
        os.chmod(pki/'client.key.pem',0o640)
        links = [item for item in json.loads((target/'links.json').read_text()) if item['id'] == 'accounts'] if (target/'links.json').exists() else []
        for sid, name, kind, url in [
            ('control-plane','Инфраструктура','control-plane','https://control-plane.backoffice.internal:18491'),
            ('content-1','Контент · шард № 1','sheets','https://content-1.backoffice.internal:18493'),
        ]:
            links.append(dict(id=sid,name=name,service=kind,baseURL=url,
                              caFile='/etc/less/transport/ca.cert.pem',certificateFile='/etc/less/transport/client.cert.pem',
                              privateKeyFile='/etc/less/transport/client.key.pem'))
        save(target/'links.json',json.dumps(links,ensure_ascii=False),uid=10001)
    print(service+': role, secrets and mTLS configuration prepared; values omitted')


if __name__ == '__main__':
    try: main()
    except Exception as error:
        raise SystemExit('Preparation failed: '+type(error).__name__)
