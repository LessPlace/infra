#!/usr/bin/env python3
"""Запускает владелец: отдельный машинный УЦ; закрытый ключ УЦ никуда не передаётся."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backoffice-api/scripts'))
from pki_support import openssl, password

IDENTITIES = {
    'control-plane': ('control-plane.backoffice.internal', 'serverAuth', 'DNS:control-plane.backoffice.internal'),
    'content-1': ('content-1.backoffice.internal', 'serverAuth', 'DNS:content-1.backoffice.internal'),
    'client': ('backoffice-api', 'clientAuth', 'URI:urn:less:service:backoffice-api'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True, help='Новый защищённый каталог вне Git')
    parser.add_argument('--csr-dir', type=Path, default=Path(__file__).parent / 'requests')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit('Каталог уже существует; повторный выпуск не перезаписывает УЦ')
    # Проверка только открытых запросов, до создания УЦ.
    for name, (cn, _, _) in IDENTITIES.items():
        csr = args.csr_dir / f'{name}.csr.pem'
        openssl(['req', '-in', csr, '-verify', '-noout'])
        subject = openssl(['req', '-in', csr, '-noout', '-subject', '-nameopt', 'RFC2253']).strip()
        if subject != f'subject=CN={cn}':
            raise SystemExit(f'Неожиданная личность в {name}')
    passphrase = password('Новый пароль машинного УЦ: ', confirm=True)
    os.umask(0o077)
    output.mkdir(parents=True, mode=0o700)
    os.chmod(output, 0o700)
    (output / 'newcerts').mkdir(mode=0o700)
    (output / 'index.txt').touch()
    (output / 'crlnumber').write_text('1000\n')
    config = output / 'ca.cnf'
    config.write_text(
        '[ca]\ndefault_ca=issuer\n[issuer]\ndir=$ENV::LESS_MACHINE_CA_DIR\n'
        'database=$dir/index.txt\nnew_certs_dir=$dir/newcerts\ncertificate=$dir/ca.cert.pem\n'
        'private_key=$dir/ca.key.pem\ncrlnumber=$dir/crlnumber\nrand_serial=yes\n'
        'default_md=sha384\ndefault_days=90\ndefault_crl_days=7\n'
        'policy=policy\nunique_subject=no\ncopy_extensions=none\n'
        'crl_extensions=crl_ext\n[policy]\ncommonName=supplied\n'
        '[crl_ext]\nauthorityKeyIdentifier=keyid:always\n')
    openssl(['genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-384',
             '-aes-256-cbc', '-out', output/'ca.key.pem'], {'-pass': passphrase})
    openssl(['req', '-x509', '-new', '-sha384', '-days', '3650', '-key', output/'ca.key.pem',
             '-out', output/'ca.cert.pem', '-subj', '/CN=Less Production Backoffice Transport CA',
             '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
             '-addext', 'keyUsage=critical,keyCertSign,cRLSign'], {'-passin': passphrase})
    env = {**os.environ, 'LESS_MACHINE_CA_DIR': str(output)}
    for name, (_, purpose, san) in IDENTITIES.items():
        ext = output/f'{name}.ext'
        ext.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n'
                       f'extendedKeyUsage={purpose}\nsubjectAltName={san}\n'
                       'subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n')
        openssl(['ca', '-batch', '-config', config, '-in', args.csr_dir/f'{name}.csr.pem',
                 '-out', output/f'{name}.cert.pem', '-notext', '-extfile', ext],
                {'-passin': passphrase}, env=env)
        openssl(['verify', '-purpose', 'sslclient' if name == 'client' else 'sslserver',
                 '-CAfile', output/'ca.cert.pem', output/f'{name}.cert.pem'])
    openssl(['ca', '-config', config, '-gencrl', '-out', output/'ca.crl.pem'],
            {'-passin': passphrase}, env=env)
    # Передаются только открытые сертификаты/CRL; ни один закрытый ключ не покидает свой узел.
    for host, names in [
        ('p-common-1.devless.ru', ['ca', 'control-plane', 'client']),
        ('p-content-shard-1.devless.ru', ['ca', 'content-1', 'client']),
    ]:
        command = ['ssh']
        if host.startswith('p-content'):
            command += ['-J', 'kirilltitov@p-common-1.devless.ru']
        for filename in [*(f'{name}.cert.pem' for name in names), 'ca.crl.pem']:
            subprocess.run([*command, f'kirilltitov@{host}',
                            f'sudo install -m 0644 /dev/stdin /etc/less/backoffice-transport/{filename}'],
                           input=(output/filename).read_bytes(), check=True)
    print('Сертификаты и CRL доставлены. Срок сертификатов — 90 дней, CRL — 7 дней.')
    print('Сохраните каталог УЦ и пароль: они нужны для отзыва, обновления CRL и следующего выпуска.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('Выпуск остановлен: ' + type(error).__name__)
