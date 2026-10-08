#!/usr/bin/env python3
"""Создаёт только отсутствующие прикладные роли/БД. Не сбрасывает данные и не меняет пароли существующих ролей."""
from pathlib import Path
import subprocess

PASSWORDS = dict(line.split('=', 1) for line in Path('/etc/less/secrets/common.env').read_text().splitlines())
COMMAND = ['docker', 'compose', '-f', '/opt/less/p-common-1/compose.common.yml', 'exec', '-T',
           'postgres', 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'postgres']

def sql(query):
    result = subprocess.run(COMMAND, input=query, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Database initialization failed; SQL and credentials withheld')
    return result.stdout.strip()

def literal(value):
    return "'" + value.replace("'", "''") + "'"

for database, field in [('controlplane', 'CONTROLPLANE_DB_PASSWORD'), ('less_accounts', 'ACCOUNTS_DB_PASSWORD')]:
    if not sql("SELECT 1 FROM pg_roles WHERE rolname=" + literal(database)):
        sql('CREATE ROLE ' + database + ' LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD ' + literal(PASSWORDS[field]))
    if not sql("SELECT 1 FROM pg_database WHERE datname=" + literal(database)):
        sql('CREATE DATABASE ' + database + ' OWNER ' + database)
    sql('REVOKE ALL ON DATABASE ' + database + ' FROM PUBLIC; GRANT CONNECT, TEMPORARY ON DATABASE '
        + database + ' TO ' + database)
    print(database + ': role and database ready; PUBLIC access revoked')
