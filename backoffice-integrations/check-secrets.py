#!/usr/bin/env python3
"""Проверка владельцем: только чтение Lockbox, без вывода значений, хешей или ответов yc."""
import argparse
import hmac
import json
import re
import subprocess
import sys

NAMES = ('less-prod-backoffice-control-plane', 'less-prod-backoffice-content-1')
FIELDS = {'DB_PASSWORD', 'BACKOFFICE_PROXY_SECRET'}
EXISTING = ('less-prod-common', 'less-prod-control-plane', 'less-prod-content-shard-1',
            'less-prod-backoffice', 'less-prod-accounts')


def load(name):
    result = subprocess.run(['yc', 'lockbox', 'payload', 'get', '--name', name, '--format', 'json'],
                            capture_output=True, check=False, timeout=45)
    if result.returncode:
        raise RuntimeError('Не удалось прочитать ' + name + '; проверьте вход yc и права')
    try:
        entries = json.loads(result.stdout)['entries']
        values = {}
        for entry in entries:
            key = entry['key']
            if key in values:
                raise ValueError()
            values[key] = entry.get('text_value', entry.get('textValue'))
        return values
    except (ValueError, KeyError, TypeError):
        raise RuntimeError('Неожиданный формат ' + name) from None


def same(a, b):
    return isinstance(a, str) and isinstance(b, str) and hmac.compare_digest(a.encode(), b.encode())


def evaluate(documents, existing=None):
    results, values = [], []
    for name in NAMES:
        document = documents[name]
        results.append((name + ': ровно два ожидаемых поля', set(document) == FIELDS))
        for key in sorted(FIELDS):
            value = document.get(key)
            results.append((name + '/' + key + ': 64 шестнадцатеричных символа',
                            isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{64}', value) is not None))
            values.append(value)
    results.append(('Все четыре значения различаются',
                    all(isinstance(v, str) and bool(v) for v in values)
                    and all(not same(a, b) for i, a in enumerate(values) for b in values[i+1:])))
    if existing is not None:
        # Сравниваются также отдельные элементы списков токенов, не только вся строка.
        old = [part.strip() for doc in existing.values() for value in doc.values()
               if isinstance(value, str) for part in value.split(',') if part.strip()]
        results.append(('Новые значения не совпадают со значениями пяти прежних секретов',
                        all(not same(value, previous) for value in values for previous in old)))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare-existing', action='store_true',
                        help='Также прочитать пять прежних секретов и проверить отсутствие повторного использования')
    args = parser.parse_args()
    print('Только чтение в память; значения, хеши и ответы yc не выводятся и не сохраняются.')
    documents = {name: load(name) for name in NAMES}
    existing = {name: load(name) for name in EXISTING} if args.compare_existing else None
    results = evaluate(documents, existing)
    for label, passed in results:
        print(('OK   ' if passed else 'FAIL ') + label)
    print('Проверка формата не доказывает случайность: значения должны быть получены через openssl rand -hex 32.')
    print('Это проверка содержимого от имени вашего yc, не проверка доступа сервисных аккаунтов ВМ.')
    return 0 if all(passed for _, passed in results) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except RuntimeError as error:
        print('FAIL ' + str(error))
        sys.exit(2)
    except Exception:
        print('FAIL Проверка не завершена; диагностические ответы скрыты.')
        sys.exit(2)
