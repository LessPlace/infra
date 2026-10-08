#!/usr/bin/env python3
"""Владелец подписывает новый CSR существующим машинным УЦ; агент этот скрипт не запускает."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backoffice-api/scripts'))
from pki_support import openssl,password

def main():
    ca=Path.home()/'.less-pki/backoffice-transport-prod'
    csr=Path(__file__).parent/'requests/accounts.csr.pem'
    cert=ca/'accounts.cert.pem'
    if cert.exists(): raise SystemExit('Сертификат уже существует; повторный выпуск не выполняется')
    openssl(['req','-in',csr,'-verify','-noout'])
    subject=openssl(['req','-in',csr,'-noout','-subject','-nameopt','RFC2253']).strip()
    if subject!='subject=CN=accounts.backoffice.internal': raise SystemExit('Неожиданный CSR')
    openssl(['x509','-in',ca/'ca.cert.pem','-noout','-checkend',str(91*86400)])
    secret=password('Пароль существующего машинного УЦ: ')
    os.umask(0o077)
    ext=ca/'accounts.ext'
    ext.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n'
                   'extendedKeyUsage=serverAuth\nsubjectAltName=DNS:accounts.backoffice.internal\n'
                   'subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n')
    with (ca/'.issue.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        openssl(['ca','-batch','-config',ca/'ca.cnf','-in',csr,'-out',cert,'-notext','-extfile',ext],
                {'-passin':secret},env={**os.environ,'LESS_MACHINE_CA_DIR':str(ca)})
    openssl(['verify','-purpose','sslserver','-verify_hostname','accounts.backoffice.internal',
             '-CAfile',ca/'ca.cert.pem',cert])
    subprocess.run(['ssh','kirilltitov@p-common-1.devless.ru',
                    'sudo install -m 0644 /dev/stdin /etc/less/backoffice-transport/accounts.cert.pem'],
                   input=cert.read_bytes(),check=True)
    print('Открытый сертификат Accounts доставлен на ВМ; ключи остались на своих местах.')
if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit('Выпуск остановлен: '+type(error).__name__)
