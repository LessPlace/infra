#!/usr/bin/env python3
"""Владелец запускает на p-common-1: выпускает новый токен и показывает его ТОЛЬКО в своём терминале."""
import importlib.util
from pathlib import Path
import uuid


def main():
    spec = importlib.util.spec_from_file_location('cp_access', Path(__file__).with_name('control-plane-access.py'))
    access = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(access)
    result = access.request('/shard/1/token', {'client_intent_id': str(uuid.uuid4())})
    print('Добавьте SHARD_TELEMETRY_TOKEN в новую версию less-prod-content-shard-1, сохранив остальные поля.')
    print('Не отправляйте значение в чат. Новый запуск выпустит ещё один токен.')
    print(result['token'])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Token issuance failed: ' + type(error).__name__)
        raise SystemExit(1)
