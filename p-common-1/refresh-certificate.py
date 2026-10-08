#!/usr/bin/env python3
"""Получает сертификат от имени ВМ; ключи не попадают в журнал или аргументы."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

CERTIFICATE = "fpquollgge5fr7htl47h"
ROOT = Path("/etc/less/secrets/certificates")
COMPOSE = ["docker", "compose", "-f", "/opt/less/p-common-1/compose.edge.yml", "exec", "-T", "edge"]


def fetch(url, headers):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
        return json.load(response)


def swap(target):
    temporary = ROOT / "next"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(ROOT / "current")


def refresh():
    os.umask(0o077)
    token = fetch(
        "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
        {"Metadata-Flavor": "Google"},
    )["access_token"]
    content = fetch(
        "https://data.certificate-manager.api.cloud.yandex.net/certificate-manager/v1/certificates/"
        + CERTIFICATE + ":getContent",
        {"Authorization": "Bearer " + token},
    )
    chain = "\n".join(content["certificateChain"]).encode()
    key = content["privateKey"].encode()
    current = ROOT / "current"
    if current.exists() and (current / "fullchain.pem").read_bytes() == chain:
        subprocess.run(["openssl", "x509", "-in", str(current / "fullchain.pem"), "-checkend", "604800", "-noout"], check=True)
        print("Certificate unchanged; valid for more than seven days")
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="candidate-", dir=ROOT) as temporary:
        candidate = Path(temporary)
        (candidate / "fullchain.pem").write_bytes(chain)
        (candidate / "privkey.pem").write_bytes(key)
        subprocess.run(["openssl", "x509", "-in", str(candidate / "fullchain.pem"), "-checkend", "604800", "-noout"], check=True)
        cert_public = subprocess.check_output(["openssl", "x509", "-in", str(candidate / "fullchain.pem"), "-pubkey", "-noout"])
        key_public = subprocess.check_output(["openssl", "pkey", "-in", str(candidate / "privkey.pem"), "-pubout"])
        if cert_public != key_public:
            raise RuntimeError("Certificate and key do not match")
        for domain in ["less.place", "less.su"]:
            subprocess.run(["openssl", "x509", "-in", str(candidate / "fullchain.pem"), "-checkhost", domain, "-noout"], check=True)
        version = "issued-" + hashlib.sha256(chain).hexdigest()[:20]
        destination = ROOT / version
        destination.mkdir(mode=0o700, exist_ok=True)
        for name in ["fullchain.pem", "privkey.pem"]:
            (destination / name).write_bytes((candidate / name).read_bytes())
        previous = os.readlink(current) if current.is_symlink() else None
        swap(version)
        try:
            subprocess.run(COMPOSE + ["nginx", "-t"], check=True)
            subprocess.run(COMPOSE + ["nginx", "-s", "reload"], check=True)
        except Exception:
            if previous:
                swap(previous)
                subprocess.run(COMPOSE + ["nginx", "-s", "reload"], check=False)
            else:
                current.unlink(missing_ok=True)
            raise
        print("Certificate installed and nginx reloaded")


if __name__ == "__main__":
    with open("/run/less-certificate-refresh.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        refresh()
