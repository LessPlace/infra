#!/usr/bin/env python3
"""Добавляет Monium в основные Compose-файлы, сохраняя остальные настройки и резервную копию."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess


def configure(text, names):
    for service, metric_name in names.items():
        pattern = re.compile(r'(?ms)^  ' + re.escape(service) + r':\n.*?(?=^  [\w-]+:|^\S|\Z)')
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise ValueError('Expected one service: ' + service)
        block = matches[0].group()
        if '    environment:\n' not in block:
            raise ValueError('Expected environment section: ' + service)
        if '/etc/less/secrets/monium.env' not in block:
            # Последний env_file имеет приоритет над предыдущими файлами.
            prefix = '' if '    env_file:\n' in block else '    env_file:\n'
            block = block.replace('    environment:\n',
                                  prefix + '      - path: /etc/less/secrets/monium.env\n'
                                  '        format: raw\n    environment:\n', 1)
        block = re.sub(r'^      OTEL_(SDK_DISABLED|SERVICE_NAME):.*\n', '', block, flags=re.M)
        block = block.replace('    environment:\n',
                              "    environment:\n      OTEL_SDK_DISABLED: 'false'\n"
                              '      OTEL_SERVICE_NAME: ' + metric_name + '\n', 1)
        text = text[:matches[0].start()] + block + text[matches[0].end():]
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('machine', choices=['common', 'shard', 'source'])
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    common = [
        ('p-common-1/compose.services.yml',
         {'control-plane': 'control-plane-api', 'accounts': 'less-accounts-api'}),
        ('p-common-1/compose.backoffice.yml', {'backoffice-api': 'backoffice-api'}),
        ('backoffice-integrations/compose.yml',
         {'reader': 'control-plane-backoffice-reader', 'accounts': 'accounts-backoffice-reader'}),
    ]
    shard = [
        ('p-content-shard-1/compose.yml', {'content': 'sheets-api'}),
        ('backoffice-integrations/compose.yml', {'reader': 'content-backoffice-reader'}),
    ]
    if args.machine == 'source':
        if args.source_root is None:
            parser.error('--source-root is required')
        root = args.source_root
        common[-1] = ('backoffice-integrations/compose.common.yml', common[-1][1])
        shard[-1] = ('backoffice-integrations/compose.shard.yml', shard[-1][1])
        files = common + shard
    else:
        root = Path('/opt/less')
        files = common if args.machine == 'common' else shard
    originals = {}
    try:
        for relative, names in files:
            path = root / relative
            original = path.read_text()
            updated = configure(original, names)
            print(relative, ','.join(names), 'unchanged' if updated == original else 'update')
            if args.apply and updated != original:
                originals[path] = original
                backup = path.with_name(path.name + '.before-monium')
                if not backup.exists():
                    shutil.copy2(path, backup)
                path.write_text(updated)
        if args.apply and args.machine != 'source':
            for relative, _ in files:
                command = ['docker', 'compose', '-f', str(root / relative)]
                if relative == 'p-common-1/compose.services.yml':
                    command += ['-f', str(root / 'p-common-1/compose.content-shard.yml')]
                subprocess.run(command + ['config', '--quiet'], check=True)
    except BaseException:
        for path, original in originals.items():
            path.write_text(original)
        raise


if __name__ == '__main__':
    main()
