#!/usr/bin/env python3
"""На common-ВМ: ограниченная роль Accounts, секреты и отдельный вход mTLS для одобрения."""
import importlib.util
import json
import os
from pathlib import Path
import re
import socket

spec = importlib.util.spec_from_file_location('host', Path(__file__).with_name('prepare-host.py'))
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'p-common-1':
        raise RuntimeError('Wrong host/user')
    os.umask(0o077)
    pki = Path('/etc/less/backoffice-transport')
    host.run(['openssl', 'verify', '-CAfile', str(pki/'ca.cert.pem'), '-CRLfile', str(pki/'ca.crl.pem'),
              '-crl_check', '-purpose', 'sslserver', '-verify_hostname', 'accounts.backoffice.internal',
              str(pki/'accounts.cert.pem')])
    token = host.fetch('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
                       {'Metadata-Flavor': 'Google'})['access_token']
    payload = host.fetch('https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/e6qt1m8tsstubee6gass/payload',
                         {'Authorization': 'Bearer '+token})
    values = {entry['key']: entry['textValue'] for entry in payload['entries']}
    if set(values) != {'DB_PASSWORD', 'BACKOFFICE_PROXY_SECRET'} or any(
            re.fullmatch('[0-9a-fA-F]{64}', value) is None for value in values.values()):
        raise RuntimeError('Unexpected secret format')
    env = dict(line.split('=', 1) for line in Path('/etc/less/secrets/accounts.env').read_text().splitlines() if '=' in line)
    values.update(STRICT_ID_SEED=env['STRICT_ID_SEED'], DB_HOST='postgres', DB_PORT='5432',
                  DB_USERNAME='backoffice_accounts', DB_DATABASE='less_accounts', DB_TLS='disable',
                  APP_PUBLIC_ORIGIN='https://less.place')
    if any('\n' in value or '\r' in value or '\0' in value for value in values.values()):
        raise RuntimeError('Invalid environment field')
    sql = """BEGIN;
DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='backoffice_accounts') THEN
  CREATE ROLE backoffice_accounts LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF EXISTS (SELECT FROM pg_roles WHERE rolname='backoffice_accounts' AND
 (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) OR
 EXISTS (SELECT FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='backoffice_accounts')) THEN
  RAISE EXCEPTION 'Unexpected existing role';
 END IF;
END $$;
"""
    sql += "ALTER ROLE backoffice_accounts PASSWORD '"+host.scram(values['DB_PASSWORD'])+"';\n"
    sql += """ALTER ROLE backoffice_accounts SET statement_timeout='8s';
GRANT CONNECT ON DATABASE less_accounts TO backoffice_accounts;
GRANT USAGE ON SCHEMA public TO backoffice_accounts;
GRANT SELECT ON account_user TO backoffice_accounts;
GRANT SELECT(user_id,provider) ON login_identity TO backoffice_accounts;
GRANT SELECT(client_id,user_id,workspace_id,auth_provider,status,last_seen_at,expires_at,revoked_at,revoke_reason)
 ON account_session TO backoffice_accounts;
GRANT UPDATE(is_approved,updated_at) ON account_user TO backoffice_accounts;
GRANT INSERT ON registration_approval_mail TO backoffice_accounts;
GRANT SELECT(user_id) ON registration_approval_mail TO backoffice_accounts;
GRANT SELECT ON registration_invitation TO backoffice_accounts;
GRANT INSERT(id,email_address,email_canonical,created_by,created_at,expires_at)
 ON registration_invitation TO backoffice_accounts;
GRANT UPDATE(delivery_status) ON registration_invitation TO backoffice_accounts;
GRANT SELECT(id,token_hash,created_by,created_at,expires_at,used_at,user_id)
 ON registration_link TO backoffice_accounts;
GRANT INSERT(id,token_hash,created_by,created_at,expires_at)
 ON registration_link TO backoffice_accounts;
COMMIT;
"""
    host.run(['docker', 'exec', '-i', 'less-common-postgres-1', 'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1',
              '-U', 'postgres', '-d', 'less_accounts'], sql.encode())
    target = Path('/etc/less/backoffice-reader')
    host.save(target/'accounts.env', ''.join(k+'='+v+'\n' for k, v in values.items()))
    serial = host.run(['openssl', 'x509', '-in', str(pki/'client.cert.pem'), '-noout', '-serial']).decode().strip().split('=', 1)[1]
    if not re.fullmatch('[0-9A-Fa-f]+', serial):
        raise RuntimeError('Invalid serial')
    headers = f'''proxy_pass http://127.0.0.1:18482;
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
 proxy_buffering off;'''
    nginx = f'''map $ssl_client_serial $accounts_service_identity {{ default 0; "{serial}" 1; }}
server {{
 listen 18492 ssl;
 server_name accounts.backoffice.internal;
 ssl_certificate /pki/accounts.cert.pem;
 ssl_certificate_key /pki/accounts.key.pem;
 ssl_client_certificate /pki/ca.cert.pem;
 ssl_verify_client on;
 ssl_verify_depth 1;
 ssl_crl /pki/ca.crl.pem;
 ssl_protocols TLSv1.2 TLSv1.3;
 ssl_session_cache off;
 ssl_session_tickets off;
 access_log off;
 client_max_body_size 1k;
 if ($accounts_service_identity = 0) {{ return 403; }}
 location ~ ^/internal/backoffice/v1/registration-approvals/U_[A-Za-z0-9]+/approve$ {{
  limit_except POST {{ deny all; }}
  {headers}
 }}
 location = /internal/backoffice/v1/registration-invitations {{
  limit_except GET POST {{ deny all; }}
  {headers}
 }}
 location = /internal/backoffice/v1/registration-links {{
  limit_except GET POST {{ deny all; }}
  {headers}
 }}
 location /internal/backoffice/v1/ {{
  limit_except GET {{ deny all; }}
  {headers}
 }}
 location / {{ return 404; }}
}}
'''
    host.save(target/'accounts.conf', nginx)
    links = json.loads((target/'links.json').read_text())
    links = [item for item in links if item['id'] != 'accounts']
    links.append(dict(id='accounts', name='Учётные записи', service='accounts',
                      baseURL='https://accounts.backoffice.internal:18492',
                      caFile='/etc/less/transport/ca.cert.pem', certificateFile='/etc/less/transport/client.cert.pem',
                      privateKeyFile='/etc/less/transport/client.key.pem'))
    host.save(target/'links.json', json.dumps(links, ensure_ascii=False), uid=10001)
    print('Accounts: restricted role, secrets and mTLS configuration prepared; values omitted')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('Preparation failed: '+type(error).__name__)
