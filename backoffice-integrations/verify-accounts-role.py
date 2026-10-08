#!/usr/bin/env python3
"""Проверка границ роли Accounts без чтения секретов или изменения данных."""
import os
import socket
import subprocess

if os.geteuid() != 0 or socket.gethostname() != 'p-common-1':
    raise SystemExit('Wrong host/user')
sql = """SELECT has_column_privilege('backoffice_accounts','account_user','is_approved','UPDATE')
 AND NOT has_column_privilege('backoffice_accounts','account_user','given_name','UPDATE')
 AND NOT has_column_privilege('backoffice_accounts','account_session','sid_hash','SELECT')
 AND NOT has_table_privilege('backoffice_accounts','email_link_challenge','SELECT')
 AND NOT has_schema_privilege('backoffice_accounts','public','CREATE');"""
result = subprocess.run(['docker', 'exec', '-i', 'less-common-postgres-1', 'psql', '-XAt',
                         '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'less_accounts'],
                        input=sql, text=True, capture_output=True)
if result.returncode or result.stdout.strip() != 't':
    raise SystemExit('Role privilege bounds: FAILED')
print('Role: approval allowed; profile writes, credentials and schema creation denied')

probe = """BEGIN;
SET LOCAL ROLE backoffice_accounts;
SELECT is_approved,email_address FROM account_user WHERE FALSE FOR UPDATE;
UPDATE account_user SET is_approved=TRUE,updated_at=now() WHERE FALSE;
INSERT INTO registration_approval_mail(user_id,approved_by,approved_at,recipient,available_at)
SELECT id,'verification',now(),email_address,now() FROM account_user WHERE FALSE
ON CONFLICT (user_id) DO NOTHING;
ROLLBACK;
"""
result = subprocess.run(['docker', 'exec', '-i', 'less-common-postgres-1', 'psql', '-Xq',
                         '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'less_accounts'],
                        input=probe, text=True, capture_output=True)
if result.returncode:
    raise SystemExit('Exact approval SQL privileges: FAILED: '+next((line for line in result.stderr.splitlines() if 'permission denied' in line), 'unexpected database response'))
print('Exact approval SQL allowed; no rows read or changed')
