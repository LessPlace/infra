#!/usr/bin/env python3
"""Публикует уже собранный образ в существующий реестр; ресурсы облака не создаёт."""
import os
import subprocess
import tempfile

IMAGE = "cr.yandex/crp5f6o2ivb58ruhp27e/web:latest"

with tempfile.TemporaryDirectory(prefix="less-registry-") as directory:
    os.chmod(directory, 0o700)
    token = subprocess.check_output(["yc", "iam", "create-token"], text=True).strip()
    subprocess.run(
        ["docker", "--config", directory, "login", "cr.yandex", "-u", "iam", "--password-stdin"],
        input=token,
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(["docker", "--config", directory, "push", IMAGE], check=True)
