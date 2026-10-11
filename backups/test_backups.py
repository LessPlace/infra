"""Проверки полноты описи, безопасных имён и обнаружения повреждений."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import common
import postgres
import s3


class BackupTests(unittest.TestCase):
    def test_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').write_bytes(b'original')
            common.seal(root)
            common.check(root)
            (root / 'data').write_bytes(b'changed')
            with self.assertRaises(RuntimeError):
                common.check(root)

    def test_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common.save(root / 'checksums.json', {'../outside': 'invalid'})
            with self.assertRaises(RuntimeError):
                common.check(root)

    def test_existing_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):
                common.private_root(tmp)

    def test_pagination_versions_and_delete_markers(self):
        storage = s3.Storage()
        pages = [b'''<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
          <Version><Key>..%2Fa%20b</Key><VersionId>1</VersionId><Size>5</Size></Version>
          <IsTruncated>true</IsTruncated><NextKeyMarker>..%2Fa%20b</NextKeyMarker>
          <NextVersionIdMarker>1</NextVersionIdMarker></ListVersionsResult>''',
          b'''<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
          <DeleteMarker><Key>..%2Fa%20b</Key><VersionId>2</VersionId></DeleteMarker>
          <IsTruncated>false</IsTruncated></ListVersionsResult>''']
        queries = []
        def xml(bucket, query):
            queries.append(dict(query))
            return pages.pop(0)
        with patch.object(storage, 'xml', side_effect=xml):
            entries = storage.versions('less')
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['Key'], '../a b')
        self.assertEqual(queries[1]['key-marker'], '../a b')
        self.assertEqual(entries[1]['kind'], 'DeleteMarker')

    def test_yandex_mixed_namespaces_are_supported(self):
        storage = s3.Storage()
        page = b'''<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
          <Version xmlns=""><Key>file</Key><VersionId>null</VersionId><Size>5</Size></Version>
          <IsTruncated>false</IsTruncated></ListVersionsResult>'''
        with patch.object(storage, 'xml', return_value=page):
            entries = storage.versions('less')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['VersionId'], 'null')

    def test_unknown_s3_response_is_rejected(self):
        storage = s3.Storage()
        with patch.object(storage, 'xml', return_value=b'<Unexpected/>'):
            with self.assertRaises(RuntimeError):
                storage.versions('less')

    def test_denied_metadata_cannot_be_skipped(self):
        storage = s3.Storage()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            storage, 'xml', side_effect=RuntimeError('S3 less: HTTP 403, AccessDenied')):
            with self.assertRaises(RuntimeError):
                s3.optional_xml(storage, Path(tmp), 'less', 'acl')

    def test_inventory_shares_dump_snapshot_and_quotes_identifiers(self):
        results = [json.dumps([['public', 'a"b']]).encode(), b'[["public", "a\\"b", 7]]']
        with patch.object(postgres, 'run', side_effect=results) as mock:
            rows = postgres.inventory(['psql'], '00000001-00000001-1')
        self.assertEqual(rows[0][2], 7)
        query = mock.call_args.kwargs['data'].decode()
        self.assertIn('SET TRANSACTION SNAPSHOT', query)
        self.assertIn('"public"."a""b"', query)


if __name__ == '__main__':
    unittest.main()
