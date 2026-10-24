#!/usr/bin/env python3
"""Удаляет устаревшие образы выбранных служб, сохраняя все именованные версии и явно закреплённые образы."""
import argparse
import json
import os
import subprocess
import tempfile

REGISTRY = 'crp5f6o2ivb58ruhp27e'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('services', nargs='+',
                    choices=['control-plane', 'accounts', 'content', 'web', 'backoffice-api', 'backoffice-web'])
parser.add_argument('--apply', action='store_true')
parser.add_argument('--keep', action='append', default=[],
                    help='Дополнительный защищённый образ cr.yandex/REPOSITORY@sha256:DIGEST')
args = parser.parse_args()


def inventory():
    return json.loads(subprocess.check_output(['yc', 'container', 'image', 'list', '--registry-id', REGISTRY,
                                              '--format', 'json']))


items = inventory()
protected = set()
with tempfile.TemporaryDirectory(prefix='less-registry-cleanup-') as directory:
    os.chmod(directory, 0o700)
    token = subprocess.check_output(['yc', 'iam', 'create-token'], text=True).strip()
    subprocess.run(['docker', '--config', directory, 'login', 'cr.yandex', '-u', 'iam', '--password-stdin'],
                   input=token, text=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def protect(name, digest):
        key = (name, digest)
        if key in protected:
            return
        protected.add(key)
        raw = subprocess.check_output(['docker', '--config', directory, 'manifest', 'inspect', 'cr.yandex/'+name+'@'+digest])
        manifest = json.loads(raw)
        for child in manifest.get('manifests', []):
            protect(name, child['digest'])

    roots = {}
    for service in set(args.services):
        name = REGISTRY+'/'+service
        latest = [item for item in items if item['name'] == name and 'latest' in item.get('tags', [])]
        if len(latest) != 1:
            raise SystemExit('Expected exactly one latest: '+service)
        roots[name] = latest[0]['digest']
        for item in items:
            if item['name'] == name and any(not tag.startswith('sha256:') for tag in item.get('tags', [])):
                protect(name, item['digest'])
    for reference in args.keep:
        if not reference.startswith('cr.yandex/'+REGISTRY+'/') or '@sha256:' not in reference:
            raise SystemExit('Invalid protected image reference')
        name, digest = reference.removeprefix('cr.yandex/').split('@', 1)
        protect(name, digest)
    candidates = [item for item in items if item['name'] in roots
                  and (item['name'], item['digest']) not in protected
                  and all(tag.startswith('sha256:') for tag in item.get('tags', []))]
    for item in candidates:
        print(('DELETE ' if args.apply else 'CANDIDATE ')+item['id']+' '+item['name']+' '+item['digest'])
    if args.apply:
        fresh = inventory()
        for name, digest in roots.items():
            if not any(item['name'] == name and item['digest'] == digest and 'latest' in item.get('tags', [])
                       for item in fresh):
                raise SystemExit('Latest changed concurrently; nothing deleted')
        old_inventory = {(item['id'], item['digest'], tuple(sorted(item.get('tags', [])))) for item in items}
        new_inventory = {(item['id'], item['digest'], tuple(sorted(item.get('tags', [])))) for item in fresh}
        if old_inventory != new_inventory:
            raise SystemExit('Registry changed concurrently; nothing deleted')
        for item in candidates:
            subprocess.run(['yc', 'container', 'image', 'delete', '--id', item['id']], check=True,
                           stdout=subprocess.DEVNULL)
    print('Protected manifests:', len(protected), '; obsolete manifests:', len(candidates))
