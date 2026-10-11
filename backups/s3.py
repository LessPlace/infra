#!/usr/bin/env python3
"""Полная выгрузка бакетов Less через IAM: объекты, версии, метаданные и настройки."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from common import check, digest, private_root, run, save, seal

ENDPOINT = 'https://storage.yandexcloud.net'
ABSENT = {'NoSuchBucketPolicy', 'NoSuchCORSConfiguration', 'NoSuchLifecycleConfiguration',
          'NoSuchTagSet', 'NoSuchWebsiteConfiguration', 'ServerSideEncryptionConfigurationNotFoundError',
          'ObjectLockConfigurationNotFoundError', 'NotImplemented'}


class Storage:
    def __init__(self):
        self.token = None
        self.issued = 0

    def get(self, bucket, key=None, query=None, headers=None):
        if time.monotonic() - self.issued > 1800 or self.token is None:
            self.token = run(['yc', 'iam', 'create-token']).decode().strip()
            self.issued = time.monotonic()
        url = ENDPOINT + '/' + urllib.parse.quote(bucket, safe='')
        if key is not None:
            # Ключ не используется как локальный путь; ../, слеши и Unicode безопасны.
            url += '/' + urllib.parse.quote(key, safe='/')
        if query:
            url += '?' + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + self.token,
                                                      **(headers or {})})
        for attempt in range(4):
            try:
                return urllib.request.urlopen(request, timeout=120)
            except urllib.error.HTTPError as error:
                if error.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                try:
                    code = ET.fromstring(error.read()).findtext('Code')
                except ET.ParseError:
                    code = 'UnknownError'
                raise RuntimeError(f'S3 {bucket}: HTTP {error.code}, {code}') from None

    def xml(self, bucket, key=None, query=None):
        with self.get(bucket, key, query) as response:
            return response.read()

    def versions(self, bucket):
        query = {'versions': '', 'encoding-type': 'url'}
        entries = []
        while True:
            root = ET.fromstring(self.xml(bucket, query=query))
            # Yandex отдаёт смешанный XML: корень с namespace, Version без него.
            for element in root.iter():
                element.tag = element.tag.split('}')[-1]
            if root.tag != 'ListVersionsResult' or root.findtext('IsTruncated') not in ('true', 'false'):
                raise RuntimeError('Нераспознанный ответ ListObjectVersions')
            for kind in ['Version', 'DeleteMarker']:
                for node in root.findall(kind):
                    item = {child.tag.split('}')[-1]: child.text for child in node}
                    item['Key'] = urllib.parse.unquote(item['Key'])
                    entries.append({'kind': kind, **item})
            if root.findtext('IsTruncated') != 'true':
                return sorted(entries, key=lambda e: (e['Key'], e['VersionId'], e['kind']))
            marker = root.findtext('NextKeyMarker')
            if marker is None:
                raise RuntimeError('S3 не вернул маркер следующей страницы')
            query['key-marker'] = urllib.parse.unquote(marker)
            query['version-id-marker'] = root.findtext('NextVersionIdMarker', default='')


def buckets():
    return json.loads(run(['yc', 'storage', 'bucket', 'list', '--format', 'json']))


def optional_xml(storage, directory, bucket, setting, key=None, version=None):
    query = {setting: ''}
    if version:
        query['versionId'] = version
    try:
        (directory / (setting + '.xml')).write_bytes(storage.xml(bucket, key, query))
    except RuntimeError as error:
        if str(error).rsplit(', ', 1)[-1] not in ABSENT:
            raise
        save(directory / (setting + '-absent.json'), {'status': str(error).rsplit(', ', 1)[-1]})


def backup(root):
    storage = Storage()
    initial = buckets()
    if not initial:
        raise RuntimeError('Список бакетов пуст: проверьте папку yc')
    if any(not item['name'].startswith('less') for item in initial):
        raise RuntimeError('В выбранной папке есть посторонние бакеты; выберите папку Less через yc')
    save(root / 'buckets.json', initial)
    for bucket in initial:
        name = bucket['name']
        directory = root / name
        directory.mkdir()
        config = json.loads(run(['yc', 'storage', 'bucket', 'get', '--name', name, '--full', '--format', 'json']))
        save(directory / 'bucket.json', config)
        for setting in ['acl', 'policy', 'cors', 'lifecycle', 'tagging', 'versioning', 'website',
                        'notification', 'logging', 'encryption', 'object-lock']:
            optional_xml(storage, directory, name, setting)
        entries = storage.versions(name)
        # Независимая текущая опись CLI защищает от ошибок разбора версий.
        current = []
        continuation = None
        while True:
            command = ['yc', 'storage', 's3api', 'list-objects', '--bucket', name, '--format', 'json']
            if continuation:
                command += ['--continuation-token', continuation]
            page = json.loads(run(command))
            current.extend((e['key'], int(e['size']), e['etag']) for e in page.get('contents', []))
            if not page.get('is_truncated', False):
                break
            continuation = page['next_continuation_token']
        latest = [(e['Key'], int(e['Size']), e['ETag']) for e in entries
                  if e['kind'] == 'Version' and e.get('IsLatest') == 'true']
        if sorted(current) != sorted(latest):
            raise RuntimeError('Текущая опись CLI не совпала с описью версий: ' + name)
        save(directory / 'inventory.json', entries)
        for entry in entries:
            if entry['kind'] == 'DeleteMarker':
                continue
            identity = hashlib.sha256(json.dumps([entry['Key'], entry['VersionId']],
                                       ensure_ascii=False).encode()).hexdigest()
            objdir = directory / 'objects' / identity
            objdir.mkdir(parents=True)
            query = {'versionId': entry['VersionId']}
            with storage.get(name, entry['Key'], query, {'If-Match': entry['ETag']}) as response:
                headers = dict(response.headers.items())
                size = 0
                hasher = hashlib.sha256()
                with (objdir / 'data.partial').open('xb') as target:
                    for chunk in iter(lambda: response.read(1024 * 1024), b''):
                        target.write(chunk)
                        size += len(chunk)
                        hasher.update(chunk)
            if size != int(entry['Size']):
                raise RuntimeError('Размер объекта изменился: ' + name)
            (objdir / 'data.partial').rename(objdir / 'data')
            save(objdir / 'metadata.json', {**entry, 'headers': headers, 'sha256': hasher.hexdigest()})
            optional_xml(storage, objdir, name, 'acl', entry['Key'], entry['VersionId'])
            optional_xml(storage, objdir, name, 'tagging', entry['Key'], entry['VersionId'])
        if storage.versions(name) != entries:
            raise RuntimeError('Бакет изменился во время выгрузки: ' + name + '; повторите выгрузку')
        final_config = json.loads(run(['yc', 'storage', 'bucket', 'get', '--name', name,
                                      '--full', '--format', 'json']))
        if final_config != config:
            raise RuntimeError('Настройки бакета изменились во время выгрузки: ' + name)
        print(f"{name}: объектов/версий {sum(e['kind'] == 'Version' for e in entries)}, "
              f"байт {sum(int(e.get('Size', 0)) for e in entries)}", flush=True)
    if {b['name'] for b in buckets()} != {b['name'] for b in initial}:
        raise RuntimeError('Список бакетов изменился во время выгрузки')
    seal(root)


def verify(root):
    check(root)
    for bucket in json.loads((root / 'buckets.json').read_text()):
        directory = root / bucket['name']
        entries = json.loads((directory / 'inventory.json').read_text())
        for entry in entries:
            if entry['kind'] != 'Version':
                continue
            identity = hashlib.sha256(json.dumps([entry['Key'], entry['VersionId']],
                                       ensure_ascii=False).encode()).hexdigest()
            objdir = directory / 'objects' / identity
            metadata = json.loads((objdir / 'metadata.json').read_text())
            if digest(objdir / 'data') != metadata['sha256'] or (objdir / 'data').stat().st_size != int(entry['Size']):
                raise RuntimeError('Повреждён объект в ' + bucket['name'])
    print('S3: все файлы, размеры и SHA-256 проверены', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['backup', 'verify'])
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    if args.action == 'backup':
        backup(private_root(args.directory))
    else:
        verify(args.directory.resolve())
