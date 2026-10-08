#!/usr/bin/env python3
"""Установка закрытого веба на существующую ВМ. Не создаёт облачных ресурсов."""
import argparse
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent
HOST = "kirilltitov@p-common-1.devless.ru"
IMAGE = "cr.yandex/crp5f6o2ivb58ruhp27e/web:latest"


def remote(command, data=None):
    return subprocess.run(["ssh", HOST, command], input=data, check=True)


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as bundle:
        for name, content, mode in files:
            info = tarfile.TarInfo(name)
            info.mode = mode
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
    return stream.getvalue()


def prepare_secrets(directory):
    certs = directory / "certificates" / "initial"
    certs.mkdir(parents=True, mode=0o700)
    subprocess.run([
        "yc", "certificate-manager", "certificate", "content", "less-prod",
        "--chain", str(certs / "fullchain.pem"), "--key", str(certs / "privkey.pem"),
    ], check=True, stdout=subprocess.DEVNULL)
    for file in certs.iterdir():
        file.chmod(0o600)
    # Пароль приходит через stdin, не появляется в аргументах процесса или репозитории.
    password = os.environ["LESS_PREVIEW_PASSWORD"]
    digest = subprocess.check_output(["openssl", "passwd", "-apr1", "-stdin"], input=password.encode()).strip()
    (directory / "web.htpasswd").write_bytes(b"test:" + digest + b"\n")
    (directory / "web.htpasswd").chmod(0o644)
    (directory / "certificates" / "current").symlink_to("initial")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.prepare_only:
        args.prepare_only.mkdir(parents=True, exist_ok=True, mode=0o700)
        prepare_secrets(args.prepare_only)
        return
    with tempfile.TemporaryDirectory(prefix="less-web-secrets-") as temporary:
        directory = Path(temporary)
        prepare_secrets(directory)
        remote("sudo install -d -m 755 /opt/less/p-common-1; sudo install -d -m 700 /etc/less/secrets")
        files = [(name, (ROOT / name).read_bytes(), 0o644) for name in [
            "compose.edge.yml", "compose.web.yml", "nginx.conf",
        ]]
        remote("sudo tar xzf - -C /opt/less/p-common-1", archive(files))
        secret_files = [
            ("certificates/initial/" + name, (directory / "certificates/initial" / name).read_bytes(), 0o600)
            for name in ["fullchain.pem", "privkey.pem"]
        ]
        secret_files.append(("web.htpasswd", (directory / "web.htpasswd").read_bytes(), 0o644))
        remote("sudo tar xzf - -C /etc/less/secrets", archive(secret_files))
        remote("sudo ln -sfn initial /etc/less/secrets/certificates/current")
        # Краткоживущий токен оператора нужен только до выдачи ВМ права images.puller.
        # Конфигурация Docker с токеном живёт в /run и удаляется при любом исходе.
        token = subprocess.check_output(["yc", "iam", "create-token"])
        remote(
            "sudo sh -c 'set -eu; d=$(mktemp -d /run/less-registry.XXXXXX); "
            "trap \"rm -rf \\\"$d\\\"\" EXIT; "
            "docker --config \"$d\" login cr.yandex -u iam --password-stdin >/dev/null; "
            f"docker --config \"$d\" pull {IMAGE}'",
            token,
        )
        remote(
            "sudo docker network inspect less-web >/dev/null 2>&1 || sudo docker network create less-web"
        )
        remote(
            "cd /opt/less/p-common-1 && "
            "sudo docker compose -f compose.web.yml up -d --wait && "
            "sudo docker compose -f compose.edge.yml run --rm --no-deps edge nginx -t && "
            "sudo docker compose -f compose.edge.yml up -d --wait"
        )


if __name__ == "__main__":
    main()
