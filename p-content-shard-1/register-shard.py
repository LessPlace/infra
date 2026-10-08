#!/usr/bin/env python3
"""Ручная регистрация первого пробного шарда. Запускать на p-common-1; без сброса существующих записей."""
import importlib.util
import os
from pathlib import Path
import socket
import subprocess


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'p-common-1':
        raise RuntimeError('Run as root on p-common-1')
    spec = importlib.util.spec_from_file_location('cp_access', Path(__file__).with_name('control-plane-access.py'))
    access = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(access)
    site = {'site_id': 'yc-ru-central1-d', 'region_code': 'ru-central1',
            'role': 'data', 'entrypoint': 'less.place'}
    geography = access.request('/geography')
    existing = next((item for item in geography['sites'] if item['site_id'] == site['site_id']), None)
    if existing is None:
        access.request('/site', site)
    elif any(existing.get(key) != value for key, value in site.items()):
        raise RuntimeError('Existing site differs; manual review required')
    expected = {'shard_number': 1, 'residency_class': 'free', 'region_code': 'ru-central1',
                'data_sites': [site['site_id']], 'tier': 'shared'}
    registry = access.request('/registry/slice')
    existing = next((item for item in registry['shards'] if item['shard_number'] == 1), None)
    if existing is None:
        access.request('/shard', expected)
    elif any(existing.get(key) != value for key, value in expected.items()):
        raise RuntimeError('Existing shard differs; manual review required')
    # Пока нет ручки аллокатора: ровно один облачный номер, без вымышленных будущих диапазонов.
    sql = """DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM number_block WHERE block_start=1 AND block_end=1 AND state='active') THEN
        INSERT INTO number_block(block_start, block_end, kind) VALUES (1,1,'cloud');
      END IF;
    END $$;"""
    subprocess.run([
        'docker', 'compose', '-f', '/opt/less/p-common-1/compose.common.yml', 'exec', '-T', 'postgres',
        'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-U', 'controlplane', '-d', 'controlplane',
    ], input=sql, text=True, check=True)
    print('Trial shard 1 and its site registered; number 1 allocated; no residency promise added')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Registration failed: ' + type(error).__name__)
        raise SystemExit(1)
