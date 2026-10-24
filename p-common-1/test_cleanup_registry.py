import contextlib
import copy
import io
import json
import pathlib
import runpy
import sys
import unittest
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).with_name('cleanup-registry.py')
NAME = 'crp5f6o2ivb58ruhp27e/content'


class CleanupRegistryTests(unittest.TestCase):
    def execute(self, changed=False):
        items = [
            {'id': value, 'name': NAME, 'digest': 'sha256:'+value, 'tags': tags}
            for value, tags in [('latest', ['latest']), ('release', ['release-1']),
                                ('live', []), ('child', []), ('release-child', []), ('obsolete', [])]
        ]
        calls = []
        reads = 0

        def output(command, **kwargs):
            nonlocal reads
            if command[:3] == ['yc', 'container', 'image']:
                reads += 1
                fresh = copy.deepcopy(items)
                if changed and reads > 1:
                    fresh[-1]['tags'] = ['newly-protected']
                return json.dumps(fresh)
            if command[:3] == ['yc', 'iam', 'create-token']:
                return 'test-token'
            if 'manifest' in command:
                value = command[-1].split('@sha256:')[1]
                children = {'latest': ['child'], 'release': ['release-child']}.get(value, [])
                return json.dumps({'manifests': [{'digest': 'sha256:'+x} for x in children]})
            raise AssertionError(command)

        def run(command, **kwargs):
            calls.append(command)

        argv = [str(SCRIPT), 'content', '--apply', '--keep', 'cr.yandex/'+NAME+'@sha256:live']
        with patch.object(sys, 'argv', argv), patch('subprocess.check_output', output), \
                patch('subprocess.run', run), contextlib.redirect_stdout(io.StringIO()):
            try:
                runpy.run_path(str(SCRIPT), run_name='__main__')
            except SystemExit:
                if not changed:
                    raise
        return [c for c in calls if c[:4] == ['yc', 'container', 'image', 'delete']]

    def test_preserves_named_release_children_and_live_digest(self):
        self.assertEqual(self.execute(), [['yc', 'container', 'image', 'delete', '--id', 'obsolete']])

    def test_concurrent_registry_change_cancels_all_deletions(self):
        self.assertEqual(self.execute(changed=True), [])


if __name__ == '__main__':
    unittest.main()
