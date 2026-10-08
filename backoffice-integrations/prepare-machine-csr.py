#!/usr/bin/env python3
"""Запуск root на каждой ВМ: машинные ключи остаются на своей машине; наружу только CSR."""
import os
from pathlib import Path
import socket
import subprocess

ROOT = Path('/etc/less/backoffice-transport')
HOSTS = {
    'p-common-1': [('control-plane', 'control-plane.backoffice.internal'), ('client', 'backoffice-api'),
                   ('accounts', 'accounts.backoffice.internal')],
    'p-content-shard-1': [('content-1', 'content-1.backoffice.internal')],
}


def main():
    if os.geteuid() != 0 or socket.gethostname() not in HOSTS:
        raise SystemExit('Требуется root на одной из двух промышленных ВМ')
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    for name, cn in HOSTS[socket.gethostname()]:
        key, csr = ROOT / f'{name}.key.pem', ROOT / f'{name}.csr.pem'
        if key.exists() != csr.exists():
            raise SystemExit('Неполный выпуск: остановка без перезаписи ключа')
        if not key.exists():
            subprocess.run(['openssl', 'req', '-new', '-newkey', 'ec', '-pkeyopt',
                            'ec_paramgen_curve:P-384', '-noenc', '-subj', f'/CN={cn}',
                            '-keyout', str(key), '-out', str(csr)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['openssl', 'req', '-in', str(csr), '-verify', '-noout'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(csr)


if __name__ == '__main__':
    main()
