#!/usr/bin/env python3
"""Выгрузка PostgreSQL по SSH и строгое восстановление в новых локальных контейнерах."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time
import uuid
from common import check, private_root, run, save, seal

HOSTS = [
    {'name': 'common', 'host': 'kirilltitov@p-common-1.devless.ru',
     'container': 'less-common-postgres-1'},
    {'name': 'content-1', 'host': 'kirilltitov@10.130.0.4',
     'jump': 'kirilltitov@p-common-1.devless.ru', 'container': 'less-content-shard-1-postgres-1'},
]


def remote(host, args, interactive=False):
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15']
    if host.get('jump'):
        command += ['-J', host['jump']]
    return command + [host['host'], shlex.join(['sudo', '-n', 'docker', 'exec'] +
           (['-i'] if interactive else []) + [host['container']] + args)]


def psql(database, user='postgres'):
    return ['psql', '-XqAt', '-v', 'ON_ERROR_STOP=1', '-U', user, '-d', database]


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def inventory(command, snapshot=None):
    prefix = ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET TRANSACTION SNAPSHOT '" +
              snapshot + "'; ") if snapshot else ''
    tables = json.loads(run(command, data=(prefix + "SELECT coalesce(json_agg(json_build_array("
        "n.nspname,c.relname) ORDER BY n.nspname,c.relname),'[]') FROM pg_class c JOIN pg_namespace n "
        "ON n.oid=c.relnamespace WHERE c.relkind IN ('r','m') AND n.nspname NOT LIKE 'pg_%' "
        "AND n.nspname <> 'information_schema';").encode()))
    if not tables:
        return []
    queries = []
    for schema, table in tables:
        schema_literal = "'" + schema.replace("'", "''") + "'"
        table_literal = "'" + table.replace("'", "''") + "'"
        queries.append('SELECT ' + schema_literal + ' AS schema, ' + table_literal +
                       ' AS name, count(*) AS rows FROM ' + quote(schema) + '.' + quote(table))
    query = prefix + 'SELECT json_agg(json_build_array(schema,name,rows) ORDER BY schema,name) FROM (' + \
        ' UNION ALL '.join(queries) + ') AS counts;'
    return json.loads(run(command, data=query.encode()))


def backup(root, hosts):
    manifest = []
    for host in hosts:
        directory = root / host['name']
        directory.mkdir()
        version = run(remote(host, ['psql', '-XAt', '-U', 'postgres', '-d', 'postgres',
                                    '-c', 'SHOW server_version_num'])).decode().strip()
        if not version.startswith('18'):
            raise RuntimeError('Ожидался PostgreSQL 18; нужен соответствующий образ восстановления')
        databases = json.loads(run(remote(host, psql('postgres') + ['-c',
            "SELECT json_agg(datname ORDER BY datname) FROM pg_database WHERE NOT datistemplate"])))
        tablespaces = run(remote(host, psql('postgres') + ['-c',
            "SELECT count(*) FROM pg_tablespace WHERE spcname NOT IN ('pg_default','pg_global')"]))
        if int(tablespaces):
            raise RuntimeError('Нестандартные табличные пространства требуют отдельной конфигурации восстановления')
        with (directory / 'globals.sql').open('xb') as target:
            run(remote(host, ['pg_dumpall', '-U', 'postgres', '--globals-only']), output=target)
        entries = []
        for index, database in enumerate(databases):
            filename = f'{index:03d}.dump'
            session = subprocess.Popen(remote(host, psql(database), True), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                session.stdin.write(b'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();\n')
                session.stdin.flush()
                snapshot = session.stdout.readline().decode().strip()
                if not snapshot or any(c not in '0123456789ABCDEFabcdef-' for c in snapshot):
                    raise RuntimeError('Не удалось получить снимок PostgreSQL')
                with (directory / (filename + '.partial')).open('xb') as target:
                    run(remote(host, ['pg_dump', '-U', 'postgres', '--format=custom', '--create',
                        '--snapshot=' + snapshot, '--dbname=' + database]), output=target)
                rows = inventory(remote(host, psql(database), True), snapshot)
                (directory / (filename + '.partial')).rename(directory / filename)
                entries.append({'database': database, 'file': filename, 'tables': rows})
                print(f"{host['name']}: {database}, таблиц {len(rows)}", flush=True)
            finally:
                session.stdin.close()
                session.wait(timeout=30)
        final_databases = json.loads(run(remote(host, psql('postgres') + ['-c',
            "SELECT json_agg(datname ORDER BY datname) FROM pg_database WHERE NOT datistemplate"])))
        if final_databases != databases:
            raise RuntimeError('Список БД изменился во время выгрузки: ' + host['name'])
        manifest.append({**host, 'version': version, 'databases': entries})
    save(root / 'manifest.json', manifest)
    seal(root)


def verify(root):
    check(root)
    report = []
    for host in json.loads((root / 'manifest.json').read_text()):
        name = 'less-restore-check-' + uuid.uuid4().hex[:12]
        user = 'restore_' + uuid.uuid4().hex
        password = uuid.uuid4().hex
        run(['docker', 'run', '-d', '--name', name, '--label', 'less.backup-verification=true',
             '--network', 'none', '--memory', '1g', '--cpus', '1',
             '--volume', '/var/lib/postgresql', '-e', 'POSTGRES_USER=' + user,
             '-e', 'POSTGRES_DB=restore_bootstrap', '-e', 'POSTGRES_PASSWORD=' + password, 'postgres:18'])
        try:
            for _ in range(60):
                # initdb's temporary server accepts Unix sockets before the final server starts.
                ready = subprocess.run(['docker', 'exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', user,
                                        '-d', 'restore_bootstrap'], capture_output=True)
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError('PostgreSQL для проверки не запустился')
            directory = root / host['name']
            run(['docker', 'exec', '-i', name] + psql('restore_bootstrap', user),
                data=(directory / 'globals.sql').read_bytes())
            for entry in host['databases']:
                # --create восстанавливает имя, владельца, ACL и настройки БД; postgres уже создан initdb.
                # --clean затрагивает только новый контейнер, никогда рабочий локальный PostgreSQL.
                with (directory / entry['file']).open('rb') as source:
                    process = subprocess.run(['docker', 'exec', '-i', name, 'pg_restore', '-U', user,
                        '--exit-on-error', '--clean', '--if-exists', '--create',
                        '--dbname=restore_bootstrap'], stdin=source, capture_output=True)
                if process.returncode:
                    raise RuntimeError('Ошибка восстановления ' + entry['database'] + '; вывод скрыт')
                rows = inventory(['docker', 'exec', '-i', name] + psql(entry['database'], user))
                if sorted(rows) != sorted(entry['tables']):
                    raise RuntimeError('Число строк не совпало: ' + entry['database'])
                report.append({'host': host['name'], 'database': entry['database'],
                               'tables': len(rows), 'rows': sum(r[2] for r in rows), 'verified': True})
                print(f"Восстановлена и сверена: {host['name']}/{entry['database']}", flush=True)
        finally:
            run(['docker', 'rm', '-fv', name])
    save(root.parent / (root.name + '-restore-report.json'), report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['backup', 'verify'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--hosts', type=Path, help='JSON со списком ВМ; по умолчанию обе действующие')
    args = parser.parse_args()
    if args.action == 'backup':
        backup(private_root(args.directory), json.loads(args.hosts.read_text()) if args.hosts else HOSTS)
    else:
        verify(args.directory.resolve())
