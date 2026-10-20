"""Publication faults at HTTP/CLI process boundaries; no native execution matrix."""
from __future__ import annotations

import argparse
import contextlib
import http.server
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/rookframe_development"))
import rookframe_publication as publication
from rookframe_package_build import source_identity


class CatalogueConsumerTests(unittest.TestCase):
    def test_explicit_submission_and_lost_response_retry_keep_one_remote_identity(self):
        requests, records = [], {}
        class Service(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body, self.headers.get('Authorization')))
                records.setdefault(body['proposalId'], {'id': 'submission-1', 'state': 'pending'})
                if len(requests) == 1:
                    self.connection.close()  # Write committed; its response was lost.
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(records[body['proposalId']]).encode())
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(records['proposal-1']).encode())
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Service)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'ROOKFRAME_CATALOGUE_TOKEN': 'author-only-token'}):
                origin = f'http://127.0.0.1:{server.server_port}'
                path = Path(folder) / 'proposal.json'
                path.write_text(json.dumps({'catalogue': origin, 'id': 'proposal-1'}))
                args = argparse.Namespace(catalogue_url=origin, local=True, action='submit', proposal=path, confirm=False, note='Ready')
                self.assertEqual(publication.catalogue_command(args)['status'], 'confirmation_required')
                self.assertEqual(requests, [])
                args.confirm = True
                with self.assertRaises(publication.PublicationError):
                    publication.catalogue_command(args)
                result = publication.catalogue_command(args)
                self.assertEqual(result['readback']['id'], 'submission-1')
                self.assertEqual(len(records), 1)
                self.assertEqual([r[1]['proposalId'] for r in requests], ['proposal-1', 'proposal-1'])
                self.assertTrue(all(r[2] == 'Bearer author-only-token' for r in requests))
                path.write_text(json.dumps({'catalogue': 'https://another.example', 'id': 'proposal-1'}))
                with self.assertRaisesRegex(publication.PublicationError, 'different Catalogue'):
                    publication.catalogue_command(args)
                self.assertEqual(len(requests), 2)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_credentials_are_private_and_keyed_by_origin(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ROOKFRAME_AUTHOR_CREDENTIALS': directory}):
            publication.save_credentials('https://catalogue.example', 'private', 'id')
            file = publication.credential_file('https://catalogue.example')
            self.assertEqual(json.loads(file.read_text()), {
                'catalogue': 'https://catalogue.example', 'token': 'private', 'tokenId': 'id'})
            self.assertFalse(publication.credential_file('https://other.example').exists())
            publication.save_credentials('https://catalogue.example', 'replacement', 'next-id')
            self.assertEqual(json.loads(file.read_text())['token'], 'replacement')
            self.assertEqual(list(Path(directory).iterdir()), [file])
            if os.name == 'nt':
                # Windows mode bits do not describe access control; inspect the ACL.
                script = '''
$ErrorActionPreference = 'Stop'
$acl = [System.IO.File]::GetAccessControl($env:ROOKFRAME_TEST_CREDENTIAL_FILE)
$rules = $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
@{
    user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    allowed = @($rules | Where-Object { $_.AccessControlType -eq 'Allow' } |
        ForEach-Object { $_.IdentityReference.Value })
} | ConvertTo-Json -Compress
'''
                result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                         '-Command', script], check=True, capture_output=True,
                                        text=True, env={**os.environ,
                                            'ROOKFRAME_TEST_CREDENTIAL_FILE': str(file)})
                access = json.loads(result.stdout)
                self.assertEqual(access['owner'], access['user'])
                self.assertTrue(set(access['allowed']) & {access['user'], 'S-1-3-4'})
                self.assertLessEqual(set(access['allowed']),
                                     {access['user'], 'S-1-3-4', 'S-1-5-32-544', 'S-1-5-18'})
            else:
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_failed_credential_save_keeps_previous_file_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ROOKFRAME_AUTHOR_CREDENTIALS': directory}):
            file = publication.credential_file('https://catalogue.example')
            previous = json.dumps({'catalogue': 'https://catalogue.example',
                                   'token': 'previous', 'tokenId': 'previous-id'})
            file.write_text(previous)
            for boundary in ('json.dump', 'os.fsync', 'os.replace'):
                with self.subTest(boundary=boundary):
                    with patch('rookframe_publication.' + boundary,
                               side_effect=OSError('credential save refused')):
                        with self.assertRaisesRegex(OSError, 'credential save refused'):
                            publication.save_credentials('https://catalogue.example', 'new', 'new-id')
                    self.assertEqual(file.read_text(), previous)
                    self.assertEqual(list(Path(directory).iterdir()), [file])


class GitHubPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.manifest = {'manifestVersion': 1, 'id': 'aae579da-5392-4507-8eaa-0d918af58076', 'version': '1.0.0', 'name': 'Calendar', 'sdk': {'edition': '2029', 'minimumRevision': 1},
                         'manifest': 'https://github.com/publisher/calendar/releases/download/v1.0.0/rookframe.json',
                         'download': 'https://github.com/publisher/calendar/releases/download/v1.0.0/Calendar.rookpackage'}
        (self.project / 'rookframe.json').write_text(json.dumps(self.manifest))
        (self.project / 'export_presets.cfg').write_text('[preset.0]\nname="package"\nplatform="macOS"\ncustom_features="s3tc,bptc,etc2,astc"\nscript_export_mode=0\n')
        (self.project / '.gitignore').write_text('build/\n')
        for command in [['git', 'init', '-q'], ['git', 'add', '.'], ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'Author input']]:
            subprocess.run(command, cwd=self.project, check=True, capture_output=True)
        self.commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.project, text=True).strip()
        (self.project / 'build').mkdir()
        self.archive = self.project / 'build/Calendar.rookpackage'
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('rookframe.json', (self.project / 'rookframe.json').read_bytes())
            archive.writestr('artifacts/build.json', json.dumps({'buildId': 'fixture-build', 'sourceSha256': source_identity(self.project), 'profiles': {'package': {}}}))
        self.release, self.assets, self.calls = None, {}, []
        self.tag, self.fail_after_upload, self.checker_rejects = None, False, False
        self.real_run = subprocess.run

    def process(self, command, **kwargs):
        if command[0] == 'git':
            return self.real_run(command, **kwargs)
        if command[0] == 'rookframe-test':
            self.assertIn('--package-check=artifact', command)
            report = Path(next(item.removeprefix('--report=') for item in command if item.startswith('--report=')))
            report.write_text(json.dumps({'status': 'rejected' if self.checker_rejects else 'artifact_checked', 'minimumSdkRevision': 1}))
            return subprocess.CompletedProcess(command, int(self.checker_rejects), json.dumps({'minimumSdkRevision': 1}), 'rejected' if self.checker_rejects else '')
        self.assertEqual(command[:2], ['gh', 'api'])
        endpoint = command[2]
        method = command[command.index('--method') + 1] if '--method' in command else 'GET'
        self.calls.append((method, endpoint))
        body = json.loads(Path(command[command.index('--input') + 1]).read_text()) if '--input' in command and 'uploads.github.com' not in endpoint else None
        response = {}
        if '/git/ref/' in endpoint:
            if self.tag is None:
                return subprocess.CompletedProcess(command, 1, '', 'HTTP 404')
            response = {'object': {'type': 'commit', 'sha': self.tag}}
        elif '/releases/assets/' in endpoint:
            data = self.assets[int(endpoint.rsplit('/', 1)[1])][1]
            kwargs['stdout'].write(data)
            return subprocess.CompletedProcess(command, 0, None, b'')
        elif endpoint.startswith('https://uploads.github.com/'):
            name = endpoint.rsplit('=', 1)[1]
            data = Path(command[command.index('--input') + 1]).read_bytes()
            asset = {'id': len(self.assets) + 1, 'name': name, 'size': len(data), 'state': 'uploaded'}
            self.assets[asset['id']] = (asset, data)
            if self.fail_after_upload:
                self.fail_after_upload = False
                return subprocess.CompletedProcess(command, 1, '', 'lost upload response')
            response = asset
        elif endpoint.endswith('/releases') and method == 'POST':
            self.release = {'id': 1, 'html_url': 'https://github.com/publisher/calendar/releases/tag/untagged-fixture', 'draft': True, 'target_commitish': body['target_commitish'], 'prerelease': body['prerelease']}
            response = {**self.release, 'assets': []}
        elif '/releases/' in endpoint:
            if self.release is None:
                return subprocess.CompletedProcess(command, 1, '', 'HTTP 404')
            if method == 'PATCH':
                self.release['draft'] = False
                self.release['html_url'] = 'https://github.com/publisher/calendar/releases/tag/v' + self.manifest['version']
                self.tag = self.commit
            response = {**self.release, 'assets': [a for a, _ in self.assets.values()]}
        elif endpoint == 'repos/publisher/calendar':
            response = {'private': False}
        return subprocess.CompletedProcess(command, 0, json.dumps(response), '')

    def publish(self, confirm=True):
        args = argparse.Namespace(project=self.project, archive=self.archive, repo='publisher/calendar', tag='v' + self.manifest['version'], commit=self.commit, notes=None, confirm=confirm)
        # Only the installed Rookframe/gh processes and public HTTP reply are replaced.
        # Real artifact admission is proved by the Calendar clean-clone release evidence.
        with patch('rookframe_authoring_checks.configure_application'), patch('rookframe_authoring_checks._application', Path('rookframe-test')), patch.object(subprocess, 'run', side_effect=self.process), patch('urllib.request.OpenerDirector.open', return_value=io.BytesIO((self.project / 'rookframe.json').read_bytes())):
            return publication.publish_github(args)

    def test_media_uploads_exact_checked_bytes_and_retry_reuses_them(self):
        from rookframe_media import edit
        image = self.project / "cover.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nfixture-cover")
        video = self.project / "trailer.webm"
        video.write_bytes(b"\x1a\x45\xdf\xa3fixture-video")
        edit(self.project, image, "cover")
        edit(self.project, video, "gallery")
        self.manifest = json.loads((self.project / "rookframe.json").read_text())
        subprocess.run(["git", "add", "."], cwd=self.project, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "Release media"], cwd=self.project, check=True, capture_output=True)
        self.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.project, text=True).strip()
        from rookframe_media import project_media
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("rookframe.json", (self.project / "rookframe.json").read_bytes())
            archive.writestr("artifacts/build.json", json.dumps({"buildId": "media", "sourceSha256": source_identity(self.project), "profiles": {"package": {}}}))
            for path, data in project_media(self.project, self.manifest).items(): archive.writestr(path, data)
        result = self.publish()
        self.assertEqual(result["status"], "published")
        self.assertEqual(len(result["assets"]), 4)
        uploaded = {a["name"]: data for a, data in self.assets.values()}
        for path, data in project_media(self.project, self.manifest).items(): self.assertEqual(uploaded[Path(path).name], data)
        self.publish()
        self.assertEqual(len(self.assets), 4)

    def test_plan_performs_no_remote_write_and_checker_failure_never_publishes(self):
        self.assertEqual(self.publish(confirm=False)['status'], 'confirmation_required')
        self.assertTrue(all(method == 'GET' for method, _ in self.calls))
        self.calls.clear()
        self.checker_rejects = True
        with self.assertRaisesRegex(RuntimeError, 'CHECK.PRODUCTION'):
            self.publish()
        self.assertEqual(self.calls, [])

    def test_uncertain_upload_retry_reuses_existing_bytes_and_publishes_once(self):
        self.fail_after_upload = True
        with self.assertRaises(publication.PublicationError) as failure:
            self.publish()
        self.assertEqual(failure.exception.progress['releaseId'], 1)
        self.assertTrue(self.release['draft'])
        published = self.publish()
        self.assertEqual(published['status'], 'published')
        self.assertEqual(published['releaseUrl'], 'https://github.com/publisher/calendar/releases/tag/v1.0.0')
        self.assertEqual(self.publish()['status'], 'published')
        self.assertEqual(len(self.assets), 2)
        self.assertEqual(sum(method == 'POST' and endpoint.endswith('/releases') for method, endpoint in self.calls), 1)
        self.assertEqual(sum(method == 'PATCH' for method, _ in self.calls), 1)
        self.assertFalse(any(method == 'DELETE' for method, _ in self.calls))

    def test_conflicting_existing_bytes_and_moved_tags_are_preserved(self):
        self.publish()
        asset, data = self.assets[1]
        self.assets[1] = (asset, b'x' * len(data))
        before = len(self.calls)
        with self.assertRaisesRegex(publication.PublicationError, 'differs'):
            self.publish()
        self.assertTrue(all(method == 'GET' for method, _ in self.calls[before:]))
        self.tag = 'f' * 40
        with self.assertRaisesRegex(publication.PublicationError, 'another commit'):
            self.publish()
        self.assertEqual(self.tag, 'f' * 40)

    def test_changed_source_does_not_upload_a_stale_build(self):
        (self.project / 'export_presets.cfg').write_text((self.project / 'export_presets.cfg').read_text() + '\n# changed\n')
        with self.assertRaisesRegex(publication.PublicationError, 'current authoring inputs'):
            self.publish()
        self.assertEqual(self.calls, [])

    def test_hyphenated_build_metadata_does_not_publish_a_stable_version_as_prerelease(self):
        self.manifest['version'] = '1.0.0+build-test'
        for field in ('manifest', 'download'):
            self.manifest[field] = self.manifest[field].replace('/v1.0.0/', '/v1.0.0+build-test/')
        (self.project / 'rookframe.json').write_text(json.dumps(self.manifest))
        subprocess.run(['git', 'add', 'rookframe.json'], cwd=self.project, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'Stable build metadata'], cwd=self.project, check=True)
        self.commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.project, text=True).strip()
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('rookframe.json', (self.project / 'rookframe.json').read_bytes())
            archive.writestr('artifacts/build.json', json.dumps({'buildId': 'fixture-build', 'sourceSha256': source_identity(self.project), 'profiles': {'package': {}}}))
        self.assertEqual(self.publish()['status'], 'published')
        self.assertFalse(self.release['prerelease'])


if __name__ == '__main__':
    unittest.main()
