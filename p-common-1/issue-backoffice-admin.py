#!/usr/bin/env python3
"""Запускает только владелец УЦ: личный сертификат остаётся локально, регистрация — на p-common-1."""
import importlib.util
from pathlib import Path
import shlex
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parents[2] / 'backoffice-api/scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('issuer', SCRIPTS / 'issue-admin-certificate.py')
issuer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(issuer)
HOST = 'kirilltitov@p-common-1.devless.ru'
COMMAND = ['sudo', 'docker', 'compose', '-f', '/opt/less/p-common-1/compose.backoffice.yml',
           'exec', '-T', 'backoffice-api', '/app/BackofficeAPI']


def admin_command(arguments):
    arguments = list(arguments)
    if arguments[0] == 'register-certificate':
        position = arguments.index('--pem') + 1
        public_certificate = Path(arguments[position]).read_bytes()
        if b'PRIVATE KEY' in public_certificate or b'-----BEGIN CERTIFICATE-----' not in public_certificate:
            raise issuer.IssueError('Передать на ВМ можно только открытый сертификат.')
        user_id = arguments[arguments.index('--user-id') + 1]
        remote = '/etc/less/backoffice-pki/' + user_id + '.cert.pem'
        result = subprocess.run(
            ['ssh', '-o', 'BatchMode=yes', HOST, 'sudo tee ' + shlex.quote(remote) + ' >/dev/null'],
            input=public_certificate, capture_output=True,
        )
        if result.returncode:
            raise issuer.IssueError('Не удалось доставить открытый сертификат на ВМ.')
        arguments[position] = remote
    result = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', HOST, shlex.join(COMMAND + arguments)], capture_output=True, text=True,
    )
    if result.returncode:
        raise issuer.IssueError('Промышленная команда ' + arguments[0] + ' не выполнена; ключи остались локально.')


def main():
    args = issuer.parser().parse_args()
    try:
        issuer.validate(args)
        admin_command(['check-admin', '--user-id', args.user_id, '--name', args.name])
        key_password = issuer.password('Пароль нового ключа и .p12: ', args.key_password_file, confirm=True)
        p12_password = (issuer.password('Пароль .p12: ', args.p12_password_file)
                        if args.p12_password_file else key_password)
        ca_password = issuer.password('Пароль ключа административного УЦ: ', args.ca_password_file)
        bundle = issuer.issue(args, key_password, ca_password, p12_password, run_admin=admin_command)
        print('Сертификат зарегистрирован на p-common-1. Импортируйте локальный пакет: ' + str(bundle))
        print('Вход: https://backoffice.less.place; при первом входе установите отдельный PIN.')
    except (issuer.IssueError, OSError) as error:
        print('Ошибка: ' + str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nВыпуск прерван; проверьте manifest.json перед повтором.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
