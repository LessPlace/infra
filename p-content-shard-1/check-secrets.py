#!/usr/bin/env python3
"""Владелец запускает локально. Только чтение Lockbox; выводит результаты без значений и отпечатков."""
import hmac
import json
import subprocess
import sys

SECRETS = {
    'common': 'less-prod-common',
    'accounts': 'less-prod-accounts',
    'control-plane': 'less-prod-control-plane',
    'content': 'less-prod-content',
    'shard': 'less-prod-content-shard-1',
}


def load(name):
    result = subprocess.run(
        ['yc', 'lockbox', 'payload', 'get', '--name', name, '--format', 'json'],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError('Не удалось прочитать ' + name + '; проверьте вход yc и права')
    try:
        entries = json.loads(result.stdout)['entries']
        values = {entry['key']: entry.get('text_value', entry.get('textValue')) for entry in entries}
    except (ValueError, KeyError, TypeError):
        raise RuntimeError('Неожиданный формат ответа для ' + name) from None
    return values


def same(first, second):
    return (isinstance(first, str) and isinstance(second, str) and bool(first) and bool(second)
            and hmac.compare_digest(first.encode(), second.encode()))


def public_key(pem, private=False):
    if not isinstance(pem, str) or not pem:
        return None
    command = ['openssl', 'pkey']
    if not private:
        command.append('-pubin')
    # Значение передаётся через stdin, без файлов, аргументов с ключами и вывода ошибок OpenSSL.
    result = subprocess.run(command + ['-pubout', '-outform', 'DER'], input=pem.encode(),
                            capture_output=True, check=False)
    if result.returncode:
        return None
    # SubjectPublicKeyInfo с OID Ed25519 и ровно 32 байтами открытого ключа.
    prefix = bytes.fromhex('302a300506032b6570032100')
    if not result.stdout.startswith(prefix) or len(result.stdout) != len(prefix) + 32:
        return None
    return result.stdout


def evaluate(values):
    common, accounts, cp, content, shard = (values[key] for key in SECRETS)
    data_tokens = [value.strip() for value in cp.get('AUTH_DATA_PLANE_TOKENS', '').split(',') if value.strip()]
    operator_tokens = [value.strip() for value in cp.get('AUTH_OPERATOR_TOKENS', '').split(',') if value.strip()]
    selected = content.get('CONTROL_PLANE_BEARER_TOKEN')
    results = [
        ('STRICT_ID_SEED совпадает с less-prod-common',
         same(content.get('STRICT_ID_SEED'), common.get('STRICT_ID_SEED'))),
        ('AUTH_REMOTE_BEARER_TOKEN совпадает с AUTH_INTERNAL_BEARER_TOKEN Accounts',
         same(content.get('AUTH_REMOTE_BEARER_TOKEN'), accounts.get('AUTH_INTERNAL_BEARER_TOKEN'))),
        ('CONTENT_BEARER_TOKEN совпадает с Accounts',
         same(content.get('CONTENT_BEARER_TOKEN'), accounts.get('CONTENT_BEARER_TOKEN'))),
        ('CONTROL_PLANE_BEARER_TOKEN — один из токенов data plane',
         any(same(selected, token) for token in data_tokens)),
        ('Токен шарда не является операторским токеном CP',
         bool(selected) and not any(same(selected, token) for token in operator_tokens)),
    ]
    private_public = public_key(content.get('PAGE_CREATION_TICKET_PRIVATE_KEY'), private=True)
    supplied_public = public_key(content.get('PAGE_CREATION_TICKET_PUBLIC_KEY'))
    results.append(('PEM-ключи образуют корректную пару Ed25519',
                    private_public is not None and supplied_public is not None
                    and hmac.compare_digest(private_public, supplied_public)))
    passwords = [shard.get(key) for key in ('POSTGRES_ADMIN_PASSWORD', 'DB_PASSWORD', 'VALKEY_PASSWORD')]
    results.append(('Три пароля шарда непустые и различаются',
                    all(isinstance(value, str) and bool(value) for value in passwords)
                    and len(set(passwords)) == 3))
    old_passwords = [value for key, value in common.items() if 'PASSWORD' in key]
    results.append(('Пароли шарда не скопированы с общей ВМ',
                    all(not same(new, old) for new in passwords for old in old_passwords)))
    return results


def main():
    print('Чтение пяти секретов в память. Значения, хеши и ключи на экран не выводятся.')
    values = {key: load(name) for key, name in SECRETS.items()}
    results = evaluate(values)
    for label, passed in results:
        print(('OK   ' if passed else 'FAIL ') + label)
    if not all(passed for _, passed in results):
        print('Есть несовпадения. Пришлите только строки FAIL, без значений секретов.')
        return 1
    print('Все проверки пройдены. Lockbox и другие ресурсы не изменялись.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, FileNotFoundError) as error:
        # Эти сообщения создаются нашим кодом; stderr yc/OpenSSL намеренно не выводится.
        print('FAIL ' + (str(error) if isinstance(error, RuntimeError) else 'Не найдены yc или openssl'))
        sys.exit(2)
    except Exception:
        print('FAIL Не удалось завершить проверку; значения и диагностические ответы скрыты.')
        sys.exit(2)
