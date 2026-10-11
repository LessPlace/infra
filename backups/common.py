"""Общие операции резервного копирования; внешние команды без вывода секретов."""
import hashlib
import json
import os
from pathlib import Path
import subprocess


def run(args, *, data=None, output=None):
    result = subprocess.run(args, input=data, stdout=output or subprocess.PIPE,
                            stderr=subprocess.PIPE)
    if result.returncode:
        # stderr PostgreSQL может содержать строки данных и пароли ролей.
        raise RuntimeError(f'Ошибка команды {args[0]} (код {result.returncode}); вывод скрыт')
    return result.stdout


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.partial')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def seal(root):
    save(root / 'checksums.json', {str(p.relative_to(root)): digest(p)
         for p in sorted(root.rglob('*')) if p.is_file() and p.name != 'checksums.json'})


def check(root):
    for name, expected in json.loads((root / 'checksums.json').read_text()).items():
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()) or digest(path) != expected:
            raise RuntimeError('Повреждение резервной копии: ' + name)


def private_root(path):
    os.umask(0o077)
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    return root
