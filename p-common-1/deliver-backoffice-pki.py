#!/usr/bin/env python3
"""Проверяет и доставляет только открытые сертификаты УЦ и CRL. Закрытые ключи не использует."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[2]
PKI = PROJECT / 'pki'
HOST = 'kirilltitov@p-common-1.devless.ru'
FILES = {
    'root.cert.pem': PKI / '00-root-pki/root/certs/ca.cert.pem',
    'admin-ca.cert.pem': PKI / '01-admin-pki/requests/admin-ca.cert.pem',
    'root.crl.pem': PKI / '00-root-pki/root/crl/root.crl.pem',
    'admin.crl.pem': PKI / '00-root-pki/admin/crl/admin.crl.pem',
}


def run(*args):
    return subprocess.check_output(list(map(str, args)), text=True).strip()


def main():
    for name in ('root.cert.pem', 'admin-ca.cert.pem'):
        run('openssl', 'x509', '-in', FILES[name], '-noout', '-checkend', '86400')
    run('openssl', 'verify', '-CAfile', FILES['root.cert.pem'], FILES['admin-ca.cert.pem'])
    for name, issuer in [('root.crl.pem', 'root.cert.pem'), ('admin.crl.pem', 'admin-ca.cert.pem')]:
        run('openssl', 'crl', '-in', FILES[name], '-noout', '-verify', '-CAfile', FILES[issuer])
        expiration = run('openssl', 'crl', '-in', FILES[name], '-noout', '-nextupdate').split('=', 1)[1]
        expires = datetime.strptime(expiration, '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
        if (expires - datetime.now(timezone.utc)).total_seconds() < 3600:
            raise RuntimeError('CRL expires in less than one hour; refresh it as CA owner first')
    with tempfile.TemporaryDirectory(prefix='less-public-pki-') as local:
        directory = Path(local)
        for name, source in FILES.items():
            content = source.read_bytes()
            if b'PRIVATE KEY' in content:
                raise RuntimeError('Unexpected private data in a public PKI file')
            (directory / name).write_bytes(content)
        (directory / 'admin-client-ca.pem').write_bytes(
            (directory / 'admin-ca.cert.pem').read_bytes() + b'\n' + (directory / 'root.cert.pem').read_bytes())
        (directory / 'client-crls.pem').write_bytes(
            (directory / 'admin.crl.pem').read_bytes() + b'\n' + (directory / 'root.crl.pem').read_bytes())
        remote = run('ssh', '-o', 'BatchMode=yes', HOST, 'mktemp -d /tmp/less-public-pki.XXXXXXXX')
        if not remote.startswith('/tmp/less-public-pki.') or not remote.replace('/', '').replace('-', '').replace('.', '').isalnum():
            raise RuntimeError('Invalid remote staging directory')
        try:
            subprocess.run(['scp', *map(str, directory.iterdir()), HOST + ':' + remote + '/'], check=True)
            run('ssh', HOST, 'sudo install -d -m 755 /etc/less/backoffice-pki && '
                'sudo install -m 644 ' + remote + '/*.pem /etc/less/backoffice-pki/')
        finally:
            run('ssh', HOST, 'rm -rf -- ' + remote)
    print('Public CA certificates and current CRLs delivered; nginx reload is a separate deployment step')


if __name__ == '__main__':
    main()
