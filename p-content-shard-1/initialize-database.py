#!/usr/bin/env python3
"""Создать только отсутствующие роль и БД less_content на первом шарде; существующие данные не трогать."""
import json
import os
from pathlib import Path
import socket
import subprocess

if os.geteuid() != 0 or socket.gethostname() != 'p-content-shard-1':
    raise SystemExit('Run as root on p-content-shard-1')
with open('/etc/less/secrets/sheets-api.json') as source:
    password = json.load(source)['DB_PASSWORD']
command = ['docker', 'compose', '-f', '/opt/less/p-content-shard-1/compose.yml', 'exec', '-T',
           'postgres', 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'postgres']


def sql(query):
    result = subprocess.run(command, input=query, text=True, capture_output=True)
    if result.returncode:
        raise SystemExit('Database initialization failed; SQL and credentials withheld')
    return result.stdout.strip()


if not sql("SELECT 1 FROM pg_roles WHERE rolname='less_content'"):
    sql("CREATE ROLE less_content LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '"
        + password.replace("'", "''") + "'")
if not sql("SELECT 1 FROM pg_database WHERE datname='less_content'"):
    sql('CREATE DATABASE less_content OWNER less_content')
sql('REVOKE ALL ON DATABASE less_content FROM PUBLIC; GRANT CONNECT, TEMPORARY ON DATABASE less_content TO less_content')
print('less_content: role and database ready; PUBLIC access revoked')
