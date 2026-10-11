#!/usr/bin/env python3
"""Проверяет изоляцию лимитов на настоящем nginx в одноразовых контейнерах."""
import contextlib
import http.client
from pathlib import Path
import re
import resource
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
ADMIN = ROOT / 'nginx-backoffice/backoffice.conf'
LOCAL = ROOT.parents[1] / 'backoffice-api/deploy/nginx/localhost.conf.template'
IMAGE = 'nginx:1.27-alpine'


def directives(source, declaration=False):
    suffix = '_zone' if declaration else ''
    return re.findall(r'^\s*(limit_(?:req|conn)' + suffix + r'\s+[^;]+;)', source, re.MULTILINE)


class NginxProbe:
    def __init__(self, admin_source, request_limits=True):
        self.directory = tempfile.TemporaryDirectory(prefix='less-backoffice-limits-')
        self.name = 'less-backoffice-limits-' + uuid.uuid4().hex[:12]
        root = Path(self.directory.name)
        root.chmod(0o755)
        (root / 'index.html').write_text('ok\n')
        # Передача длится достаточно долго, чтобы соединения оставались учтёнными.
        (root / 'hold').write_bytes(b'x' * (1024 * 1024))
        product = (ROOT / 'nginx.conf').read_text()
        zones = '\n'.join(directives(product, True) + directives(admin_source, True))
        servers = []
        for host, source in [('product.test', product), ('admin.test', admin_source)]:
            limits = '\n'.join(
                directive for directive in directives(source)
                if request_limits or not directive.startswith('limit_req ')
            )
            servers.append(f'''server {{
                listen 8080; server_name {host}; root /srv;
                {limits}
                location = /health {{ return 200 'ok'; }}
                location / {{ proxy_pass http://127.0.0.1:8081; proxy_buffering off; }}
            }}''')
        capacity = re.search(r'events\s*\{[^}]+\}', product).group()
        nofile = re.search(r'worker_rlimit_nofile\s+\d+;', product).group()
        config = nofile + '\n' + capacity + '\nhttp {\n' + zones
        config += '\naccess_log off; limit_req_status 429; limit_conn_status 429;\n'
        config += 'server { listen 8081; root /srv; location = /hold { limit_rate 1k; } }\n'
        config += '\n'.join(servers) + '\n}\n'
        (root / 'nginx.conf').write_text(config)
        try:
            subprocess.run([
                'docker', 'run', '-d', '--rm', '--name', self.name,
                '--publish', '127.0.0.1::8080', '--memory', '128m', '--pids-limit', '64',
                '--mount', f'type=bind,src={root},dst=/srv,readonly',
                '--mount', f'type=bind,src={root / "nginx.conf"},dst=/etc/nginx/nginx.conf,readonly',
                '--entrypoint', 'nginx', IMAGE, '-g', 'daemon off;',
            ], check=True, stdout=subprocess.DEVNULL)
            binding = subprocess.check_output(['docker', 'port', self.name, '8080'], text=True).strip()
            self.port = int(binding.rsplit(':', 1)[1])
            for _ in range(50):
                try:
                    if self.request('admin.test', '/health') == 200:
                        break
                except (OSError, http.client.HTTPException):
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError('Проверочный nginx не запустился')
        except BaseException:
            self.close()
            raise

    def request(self, host, path='/index.html'):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.request('GET', path, headers={'Host': host})
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    @contextlib.contextmanager
    def hold(self, host, count):
        connections = []
        try:
            for _ in range(count):
                connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
                connections.append(connection)
                connection.request('GET', '/hold', headers={'Host': host})
                response = connection.getresponse()
                if response.status != 200:
                    raise AssertionError(f'Не удалось удержать соединение: {response.status}')
                # HTTPConnection не удерживает ссылку на незавершённый ответ при следующем вызове.
                connection.probe_response = response
            yield
        finally:
            for connection in connections:
                if hasattr(connection, 'probe_response'):
                    connection.probe_response.close()
                connection.close()
            time.sleep(0.05)

    def close(self):
        subprocess.run(['docker', 'rm', '-f', self.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.directory.cleanup()


class BackofficeLimitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        cls.previous_nofile = (soft, hard)
        resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, 2048), hard))

    @classmethod
    def tearDownClass(cls):
        resource.setrlimit(resource.RLIMIT_NOFILE, cls.previous_nofile)

    def test_previous_shared_zone_reproduces_429(self):
        legacy = 'limit_req zone=per_ip burst=50 nodelay;\nlimit_conn connections 10;'
        probe = NginxProbe(legacy)
        try:
            with probe.hold('product.test', 10):
                self.assertEqual(probe.request('admin.test'), 429)
        finally:
            probe.close()

    def test_connection_capacity_and_isolation(self):
        # Частоту проверяем отдельно: здесь нужны сотни одновременно открытых запросов.
        probe = NginxProbe(ADMIN.read_text(), request_limits=False)
        try:
            with probe.hold('product.test', 500):
                self.assertEqual(probe.request('product.test'), 429)
                self.assertEqual(probe.request('admin.test'), 200)
                with probe.hold('admin.test', 100):
                    self.assertEqual(probe.request('admin.test'), 429)
                self.assertEqual(probe.request('admin.test'), 200)
            self.assertEqual(probe.request('product.test'), 200)
            with probe.hold('admin.test', 100):
                self.assertEqual(probe.request('admin.test'), 429)
                self.assertEqual(probe.request('product.test'), 200)
        finally:
            probe.close()

    def test_request_rate_isolation_and_limits(self):
        probe = NginxProbe(ADMIN.read_text())
        try:
            statuses = [probe.request('product.test') for _ in range(180)]
            self.assertIn(429, statuses)
            self.assertEqual(probe.request('admin.test'), 200)
            self.assertIn(429, [probe.request('admin.test') for _ in range(100)])
        finally:
            probe.close()

    def test_local_and_production_limits_match(self):
        local = LOCAL.read_text()
        production = ADMIN.read_text()
        self.assertEqual(directives(local, True), directives(production, True))
        self.assertEqual(directives(local), directives(production))
        self.assertIn('limit_req_status 429;', local)
        self.assertIn('limit_conn_status 429;', local)
        self.assertIn('ssl_verify_client on;', local)
        self.assertIn('ssl_verify_client on;', production)


if __name__ == '__main__':
    unittest.main(verbosity=2)
