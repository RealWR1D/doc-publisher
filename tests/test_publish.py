import contextlib
import hashlib
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import publish

DOC = '# Title\n## Section\nBody\n'


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'doc.md'
        self.source.write_text(DOC, encoding='utf-8')
        self.config = self.root / 'targets.json'
        self.write_config({'url': 'https://example.invalid/upload'})
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def write_config(self, profile):
        self.config.write_text(json.dumps({'default': 'test', 'targets': {'test': profile}}), encoding='utf-8')

    def invoke(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = publish.main([str(self.source), '--config', str(self.config), '--json', *args])
        return code, json.loads(output.getvalue())

    def profile(self, extra=None):
        args = publish.parser().parse_args([str(self.source)])
        return publish.validate_profile({'url': 'https://example.invalid/upload', **(extra or {})}, args, str(self.config), {})

    def test_bad_explicit_config_does_not_fall_back(self):
        for content in ('not json', '{}', '[]', '{"targets": []}', '{"default": "missing", "targets": {"test": {}}}'):
            self.config.write_text(content)
            code, result = self.invoke('--dry-run')
            self.assertEqual(code, 1)
            self.assertFalse(result['ok'])
        self.config.unlink()
        self.assertEqual(self.invoke('--dry-run')[0], 1)

    def test_invalid_first_discovered_config_is_fatal(self):
        (self.root / '.doc-publisher.json').write_text('{broken')
        with patch.object(publish.Path, 'cwd', return_value=self.root):
            with self.assertRaises(publish.PublishError):
                publish.find_config()

    def test_unknown_target_never_uses_env(self):
        os.environ['DOC_PUBLISHER_URL'] = 'https://other.invalid/upload'
        code, result = self.invoke('--target', 'typo', '--dry-run')
        self.assertEqual(code, 1)
        self.assertNotIn('endpoint', result)

    def test_selected_target_missing_url_does_not_use_env_url(self):
        self.write_config({'token': 'SECRET'})
        os.environ['DOC_PUBLISHER_URL'] = 'https://other.invalid/upload'
        self.assertEqual(self.invoke('--dry-run')[0], 1)

    def test_json_boolean_success_does_not_accept_number(self):
        self.write_config({'url': 'https://example.invalid/upload', 'response': {'success_field': 'done', 'success_value': True}})
        with patch.object(publish, 'request', return_value=(200, b'{"done":1}')):
            self.assertEqual(self.invoke()[0], 2)

    def test_unknown_type_and_missing_local_dest(self):
        for profile in ({'type': 'unknown', 'url': 'https://example.invalid'}, {'type': 'local-copy'}):
            self.write_config(profile)
            self.assertEqual(self.invoke('--dry-run')[0], 1)

    def test_invalid_urls_rejected_before_mutation(self):
        for url in ('invalid-url', 'file:///tmp/test', 'http://example.invalid', 'https://user:secret@example.invalid', 'https://example.invalid/#fragment', 'https://example.invalid:bad', 'https://example.invalid/\n'):
            with self.subTest(url=url), patch.object(publish, 'request') as network:
                self.assertEqual(self.invoke('--url', url, '--dry-run')[0], 1)
                network.assert_not_called()
        self.assertEqual(self.invoke('--url', 'http://127.0.0.1/upload', '--dry-run')[0], 0)
        self.assertEqual(self.invoke('--url', 'http://example.invalid/upload', '--allow-http', '--dry-run')[0], 0)

    def test_empty_invalid_utf8_and_lint_block_upload(self):
        for content in (b'', b'\xff', b'No heading', b'# Title\n## Section\n~~~python\nx=1'):
            self.source.write_bytes(content)
            with patch.object(publish, 'request') as network:
                self.assertEqual(self.invoke()[0], 1)
                network.assert_not_called()

    def test_skip_lint_is_explicit(self):
        self.source.write_text('No heading')
        code, result = self.invoke('--skip-lint', '--dry-run')
        self.assertEqual(code, 0)
        self.assertEqual(result['lint'], 'skipped')
        self.source.write_text('')
        self.assertEqual(self.invoke('--skip-lint', '--dry-run')[0], 1)

    def test_strict_blocks_warnings(self):
        self.source.write_text('# Title\n## Section\n```\nx\n```')
        self.assertEqual(self.invoke('--strict', '--dry-run')[0], 1)

    def test_url_override_drops_credentials_and_preview(self):
        self.write_config({'url': 'https://example.invalid/upload', 'token': 'SECRET', 'token_env': 'PRIVATE_TOKEN', 'headers': {'Authorization': 'Bearer SECRET'}, 'verify_url': 'https://example.invalid/docs'})
        with patch.object(publish, 'request', return_value=(200, b'{}')) as network:
            code, result = self.invoke('--url', 'https://other.invalid/upload')
            self.assertEqual(code, 0)
            headers = network.call_args.args[3]
            self.assertNotIn('Authorization', headers)
            self.assertNotIn('X-Upload-Token', headers)
            self.assertNotIn('SECRET', json.dumps(result))
        with patch.object(publish, 'request') as network:
            self.assertEqual(self.invoke('--url', 'https://other.invalid/upload', '--verify')[0], 1)
            network.assert_not_called()

    def test_token_env_and_explicit_override(self):
        self.write_config({'url': 'https://example.invalid/upload', 'token_env': 'PRIVATE_TOKEN'})
        self.assertEqual(self.invoke('--dry-run')[0], 1)
        os.environ['PRIVATE_TOKEN'] = 'SECRET'
        with patch.object(publish, 'request', return_value=(200, b'{}')) as network:
            self.assertEqual(self.invoke()[0], 0)
            self.assertEqual(network.call_args.args[3]['X-Upload-Token'], 'SECRET')
            self.assertEqual(self.invoke('--token', 'OVERRIDE')[0], 0)
            self.assertEqual(network.call_args.args[3]['X-Upload-Token'], 'OVERRIDE')

    def test_default_auth_is_header_only(self):
        self.write_config({'url': 'https://example.invalid/upload', 'token': 'SECRET'})
        with patch.object(publish, 'request', return_value=(200, b'{}')) as network:
            self.assertEqual(self.invoke()[0], 0)
            self.assertEqual(network.call_args.args[0], 'https://example.invalid/upload')
            self.assertEqual(network.call_args.args[3]['X-Upload-Token'], 'SECRET')

    def test_bearer_query_and_none_auth(self):
        for auth in ('bearer', 'query', 'none'):
            profile = self.profile({'auth': auth, 'token': 'a/b?&secret', 'url': 'https://example.invalid/upload?token=old&x=y'})
            url, headers = publish.auth_request(profile, profile['url'])
            if auth == 'bearer':
                self.assertEqual(headers['Authorization'], 'Bearer a/b?&secret')
            elif auth == 'query':
                self.assertEqual(dict(publish.urllib.parse.parse_qsl(publish.urllib.parse.urlsplit(url).query))['token'], 'a/b?&secret')
                self.assertEqual(url.count('token='), 1)
            else:
                self.assertNotIn('Authorization', headers)
                self.assertNotIn('X-Upload-Token', headers)

    def test_dry_run_has_no_network_or_destination_writes(self):
        with patch.object(publish, 'request') as network:
            code, result = self.invoke('--dry-run')
            self.assertEqual(code, 0)
            self.assertEqual(result['sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
            network.assert_not_called()
        self.write_config({'type': 'local-copy', 'dest': 'nested/output.md'})
        self.assertEqual(self.invoke('--dry-run')[0], 0)
        self.assertFalse((self.root / 'nested').exists())

    def test_local_copy_paths_are_config_relative_and_verified(self):
        self.write_config({'type': 'local-copy', 'dest': 'nested/output.md'})
        code, result = self.invoke('--verify')
        self.assertEqual(code, 0)
        self.assertEqual((self.root / 'nested/output.md').read_bytes(), self.source.read_bytes())
        self.assertEqual(result['verification'], 'sha256-match')
        self.assertFalse(list((self.root / 'nested').glob('.doc-publisher-*')))
        self.assertTrue(self.invoke('--dry-run')[1]['overwrite'])

    def test_local_atomic_failure_preserves_old_document(self):
        dest = self.root / 'output.md'
        dest.write_text('old')
        self.write_config({'type': 'local-copy', 'dest': 'output.md'})
        with patch.object(publish.os, 'replace', side_effect=OSError('failure')):
            self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(dest.read_text(), 'old')
        self.assertFalse(list(self.root.glob('.doc-publisher-*')))

    def test_local_same_file_directory_symlink_and_http_flags_rejected(self):
        for dest in ('doc.md', '.'):
            self.write_config({'type': 'local-copy', 'dest': dest})
            self.assertEqual(self.invoke('--dry-run')[0], 1)
        link = self.root / 'link.md'
        try:
            link.symlink_to(self.source)
        except (OSError, NotImplementedError):
            pass  # Some Windows runners cannot create symlinks.
        else:
            self.write_config({'type': 'local-copy', 'dest': 'link.md'})
            self.assertEqual(self.invoke('--dry-run')[0], 1)
        self.write_config({'type': 'local-copy', 'dest': 'output.md'})
        self.assertEqual(self.invoke('--url', 'https://example.invalid')[0], 1)
        self.assertEqual(self.invoke('--verify-content', 'text', '--verify')[0], 1)

    def test_response_business_failure_and_contract(self):
        for body in (b'{"success":false}', b'{"ok":false}', b'{"error":"SECRET"}', b'{"status":"failed"}'):
            with self.subTest(body=body), patch.object(publish, 'request', return_value=(200, body)):
                code, result = self.invoke()
                self.assertEqual(code, 2)
                self.assertNotIn('SECRET', json.dumps(result))
        self.write_config({'url': 'https://example.invalid/upload', 'response': {'success_field': 'result.done', 'success_value': True}})
        with patch.object(publish, 'request', return_value=(200, b'{"result":{"done":true}}')):
            self.assertEqual(self.invoke()[0], 0)
        with patch.object(publish, 'request', return_value=(200, b'{}')):
            self.assertEqual(self.invoke()[0], 2)

    def test_missing_field_does_not_match_null(self):
        self.write_config({'url': 'https://example.invalid/upload', 'response': {'success_field': 'done', 'success_value': None}})
        with patch.object(publish, 'request', return_value=(200, b'{}')):
            self.assertEqual(self.invoke()[0], 2)

    def test_accepted_is_not_published(self):
        with patch.object(publish, 'request', return_value=(202, b'{}')):
            code, result = self.invoke()
            self.assertEqual(code, 0)
            self.assertEqual(result['status'], 'accepted')

    def test_verify_missing_url_fails_before_upload(self):
        with patch.object(publish, 'request') as network:
            self.assertEqual(self.invoke('--verify')[0], 1)
            network.assert_not_called()

    def test_verification_failure_is_nonzero(self):
        self.write_config({'url': 'https://example.invalid/upload', 'verify': {'url': 'https://example.invalid/docs', 'attempts': 1}})
        with patch.object(publish, 'request', side_effect=[(200, b'{}'), publish.PublishError('HTTP 404', 'verification', 2)]):
            code, result = self.invoke('--verify')
            self.assertEqual(code, 4)
            self.assertEqual(result['status'], 'uploaded')
            self.assertFalse(result['ok'])

    def test_accessibility_does_not_claim_content_updated_or_send_token(self):
        self.write_config({'url': 'https://example.invalid/upload', 'token': 'SECRET', 'headers': {'X-Private': 'SECRET'}, 'verify_url': 'https://preview.invalid/docs'})
        with patch.object(publish, 'request', return_value=(200, b'old page')) as network:
            code, result = self.invoke('--verify')
            self.assertEqual(code, 0)
            self.assertEqual(result['status'], 'uploaded')
            self.assertEqual(result['verification'], 'reachable')
            self.assertNotIn('SECRET', str(network.call_args))

    def test_content_verification_polls_stale_page(self):
        self.write_config({'url': 'https://example.invalid/upload', 'verify': {'url': 'https://example.invalid/docs', 'mode': 'contains', 'expected': 'version-2', 'attempts': 2, 'interval': 0}})
        with patch.object(publish, 'request', side_effect=[(200, b'{}'), (200, b'version-1'), (200, b'version-2')]):
            code, result = self.invoke('--verify')
            self.assertEqual(code, 0)
            self.assertEqual(result['verification'], 'content-match')

    def test_sha256_raw_and_json(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        for options, body in (({'mode': 'sha256'}, self.source.read_bytes()), ({'mode': 'sha256', 'field': 'document.sha256'}, json.dumps({'document': {'sha256': digest}}).encode())):
            self.write_config({'url': 'https://example.invalid/upload', 'verify': {'url': 'https://example.invalid/raw', 'attempts': 1, **options}})
            with patch.object(publish, 'request', side_effect=[(200, b'{}'), (200, body)]):
                self.assertEqual(self.invoke('--verify')[1]['verification'], 'sha256-match')
            with patch.object(publish, 'request', side_effect=[(200, b'{}'), (200, b'old')]):
                self.assertEqual(self.invoke('--verify')[0], 4)

    def test_json_version_verification(self):
        self.write_config({'url': 'https://example.invalid/upload', 'verify': {'url': 'https://example.invalid/version', 'mode': 'json', 'field': 'version', 'expected': 'v2', 'attempts': 1}})
        with patch.object(publish, 'request', side_effect=[(200, b'{}'), (200, b'{"version":"v2"}')]):
            self.assertEqual(self.invoke('--verify')[1]['verification'], 'json-match')

    def test_build_job_polling_and_no_upload_retry(self):
        self.write_config({'url': 'https://example.invalid/upload', 'token': 'SECRET', 'build': {'url': 'https://example.invalid/build', 'job_field': 'job.id', 'attempts': 2, 'interval': 0}})
        with patch.object(publish, 'request', side_effect=[(202, b'{"job":{"id":"id&1"}}'), (200, b'{"status":"building"}'), (200, b'{"status":"completed"}')]) as network:
            code, result = self.invoke()
            self.assertEqual(code, 0)
            self.assertEqual(result['status'], 'build-completed')
            self.assertEqual(network.call_args_list[1].args[0], 'https://example.invalid/build?job_id=id%261')
            self.assertEqual(sum(call.args[1] == 'POST' for call in network.call_args_list if len(call.args) > 1), 1)

    def test_build_failure_timeout_missing_job_and_origin(self):
        profile = {'url': 'https://example.invalid/upload', 'build': {'url': 'https://example.invalid/build', 'attempts': 1}}
        self.write_config(profile)
        for body in (b'{"status":"failed"}', b'{"status":"building"}', b'not json'):
            with patch.object(publish, 'request', side_effect=[(202, b'{}'), (200, body)]):
                self.assertEqual(self.invoke()[0], 3)
        profile['build']['job_field'] = 'id'
        self.write_config(profile)
        with patch.object(publish, 'request', return_value=(202, b'{}')):
            self.assertEqual(self.invoke()[0], 3)
        profile['build']['url'] = 'https://other.invalid/status'
        self.write_config(profile)
        self.assertEqual(self.invoke('--dry-run')[0], 1)

    def test_payload_json_and_multipart(self):
        body, content_type = publish.payload(DOC.encode(), self.source, self.profile({'format': 'json', 'content_field': 'markdown'}))
        self.assertEqual(json.loads(body)['markdown'], DOC)
        self.assertEqual(json.loads(body)['filename'], 'doc.md')
        self.assertIn('application/json', content_type)
        body, content_type = publish.payload(DOC.encode(), self.source, self.profile({'format': 'multipart'}))
        self.assertIn(b'name="file"; filename="doc.md"', body)
        self.assertIn(DOC.encode(), body)
        self.assertIn('multipart/form-data; boundary=', content_type)

    def test_invalid_options_and_headers(self):
        for options in ({'timeout': 10 ** 400}, {'verify_url': 'invalid'}, {'build': {'url': 'https://example.invalid/build', 'success_values': ['failed'], 'failure_values': ['failed']}}, {'content_field': 'filename'}, {'timeout': 0}, {'timeout': True}, {'auth': 'unknown'}, {'method': 'GET'}, {'format': 'xml'}, {'headers': {'X-Test': 'x\r\ny'}}, {'headers': {'Host': 'evil'}}, {'token_header': 'Host'}, {'response': {'success_field': 'ok'}}, {'verify': {'attempts': 0}}, {'verify': {'mode': 'unknown'}}, {'build': {'url': 'https://example.invalid/build', 'attempts': 31}}):
            self.write_config({'url': 'https://example.invalid/upload', **options})
            with self.subTest(options=options):
                self.assertEqual(self.invoke('--dry-run')[0], 1)

    def test_output_redacts_query_and_does_not_echo_body(self):
        self.write_config({'url': 'https://example.invalid/upload?token=SECRET', 'token': 'SECRET'})
        with patch.object(publish, 'request', return_value=(200, b'{"echo":"SECRET"}')):
            code, result = self.invoke()
            self.assertEqual(code, 0)
            self.assertNotIn('SECRET', json.dumps(result))


class HTTPIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.received = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                cls.received.append((self.path, dict(self.headers), self.rfile.read(int(self.headers['Content-Length']))))
                if self.path == '/redirect':
                    self.send_response(307)
                    self.send_header('Location', '/must-not-follow')
                    self.end_headers()
                else:
                    self.send_response(201)
                    self.end_headers()
                    self.wfile.write(b'{"success":true}')
            def do_GET(self):
                cls.received.append((self.path, dict(self.headers), b''))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(DOC.encode())
            def log_message(self, *args):
                pass
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_real_http_upload_and_hash_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'doc.md'
            source.write_bytes(DOC.encode("utf-8"))
            config = Path(directory) / 'targets.json'
            config.write_text(json.dumps({'default': 'test', 'targets': {'test': {'url': self.url + '/upload', 'token': 'LOCAL-TEST-TOKEN', 'verify': {'url': self.url + '/raw', 'mode': 'sha256', 'attempts': 1}}}}))
            with contextlib.redirect_stdout(io.StringIO()) as output, patch.dict(os.environ, {}, clear=True):
                code = publish.main([str(source), '--config', str(config), '--verify', '--json'])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['http_status'], 201)
            post = self.received[-2]
            self.assertEqual(post[0], '/upload')
            self.assertEqual(post[1]['X-Upload-Token'], 'LOCAL-TEST-TOKEN')
            self.assertEqual(post[2], DOC.encode())
            self.assertNotIn('X-Upload-Token', self.received[-1][1])

    def test_redirect_does_not_resend_credentials(self):
        before = len(self.received)
        with self.assertRaises(publish.PublishError):
            publish.request(self.url + '/redirect', 'POST', b'doc', {'X-Upload-Token': 'LOCAL-TEST-TOKEN'})
        self.assertEqual(len(self.received) - before, 1)
        self.assertEqual(self.received[-1][0], '/redirect')


if __name__ == '__main__':
    unittest.main()
