"""Offline mailbox configuration and unchanged TLS/send-boundary regressions."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import stat
import tempfile
import unittest
from unittest.mock import patch

REAL_FDOPEN = os.fdopen
ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('musepost_test_subject', str(ROOT / 'musepost'))
spec = importlib.util.spec_from_loader(loader.name, loader)
app = importlib.util.module_from_spec(spec)
loader.exec_module(app)


class Accounts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'accounts.json'
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(patch.object(app.imaplib, 'IMAP4_SSL', side_effect=AssertionError('offline test')))
        self.stack.enter_context(patch.object(app.smtplib, 'SMTP', side_effect=AssertionError('offline test')))

    def write(self, text):
        self.path.write_text(text, encoding='utf-8')
        self.path.chmod(0o600)
        return self.path

    def structured(self, accounts=None):
        return self.write(json.dumps({'accounts': accounts or {
            'owner+alerts@example.test': {'password': 'synthetic-only'},
            'taylor@example.test': {'password': 'also-synthetic'},
            'another@other.test': {'password': 'not-a-secret'},
        }}))

    def test_structured_different_localparts_and_domain_collision(self):
        accounts = app.load_accounts(self.structured())
        self.assertEqual(len(accounts), 3)
        self.assertEqual(app.resolve(accounts, 'OWNER+ALERTS@EXAMPLE.TEST'), 'owner+alerts@example.test')
        self.assertEqual(app.resolve(accounts, 'other.test'), 'another@other.test')
        with self.assertRaisesRegex(SystemExit, 'ambiguous account'):
            app.resolve(accounts, 'example.test')

    def test_legacy_blocks_and_password_with_at_no_space(self):
        self.write('Owner+alerts@Example.test\nUsername: owner+alerts@example.test\nPassword:synthetic@fixture\nMailbox quota: 128 MB\n\nAddress and username: other@other.test\nNew password: next-fixture\n')
        self.assertEqual(app.load_accounts(self.path), {
            'owner+alerts@example.test': 'synthetic@fixture', 'other@other.test': 'next-fixture'})

    def test_explicit_then_environment_then_legacy_default(self):
        self.structured()
        missing = str(self.path.parent / 'missing')
        with patch.object(app, 'ACCESS_FILE', str(self.path)):
            self.assertEqual(len(app.load_accounts()), 3)
            with patch.dict(os.environ, {'MUSEPOST_CONFIG': missing}):
                with self.assertRaises(app.AccountConfigError):
                    app.load_accounts()
                self.assertEqual(len(app.load_accounts(self.path)), 3)
        with patch.dict(os.environ, {'MUSEPOST_CONFIG': str(self.path)}):
            self.assertEqual(len(app.load_accounts()), 3)

    @contextlib.contextmanager
    def forbid_credential_read(self, fd, **kwargs):
        with REAL_FDOPEN(fd, **kwargs) as stream:
            class GuardedStream:
                def fileno(self): return stream.fileno()
                def read(self): raise AssertionError('read before file validation')
            yield GuardedStream()

    def test_permissions_rejected_before_read(self):
        self.structured()
        for mode in (0o644, 0o640, 0o660, 0o400, 0o700):
            with self.subTest(mode=oct(mode)):
                self.path.chmod(mode)
                with patch.object(app.os, 'fdopen', side_effect=self.forbid_credential_read) as opened:
                    with self.assertRaisesRegex(app.AccountConfigError, 'mode 0600'):
                        app.load_accounts(self.path)
                    self.assertEqual(opened.call_count, 1)

    def test_owner_rejected_before_read(self):
        self.structured()
        info = self.path.stat()
        values = list(info)
        values[stat.ST_UID] = info.st_uid + 1
        with patch.object(app.os, 'fstat', return_value=os.stat_result(values)), \
                patch.object(app.os, 'fdopen', side_effect=self.forbid_credential_read):
            with self.assertRaisesRegex(app.AccountConfigError, 'owned by this user'):
                app.load_accounts(self.path)

    def test_symlink_hardlink_directory_fifo_rejected(self):
        self.structured()
        link = self.path.parent / 'link'
        link.symlink_to(self.path)
        with self.assertRaises(app.AccountConfigError):
            app.load_accounts(link)
        link.unlink()
        os.link(self.path, link)
        with self.assertRaises(app.AccountConfigError):
            app.load_accounts(self.path)
        link.unlink()
        with self.assertRaises(app.AccountConfigError):
            app.load_accounts(self.path.parent)
        fifo = self.path.parent / 'fifo'
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(app.AccountConfigError):
            app.load_accounts(fifo)

    def test_missing_empty_and_invalid_utf8_rejected(self):
        with self.assertRaises(app.AccountConfigError):
            app.load_accounts(self.path)
        for value in ('', '[]', '{broken', '{"accounts":{}}'):
            self.write(value)
            with self.assertRaises(app.AccountConfigError):
                app.load_accounts(self.path)
        self.path.write_bytes(b'\xff')
        with self.assertRaises(app.AccountConfigError):
            app.load_accounts(self.path)

    def test_json_duplicate_keys_addresses_and_schema_rejected(self):
        for text in (
            '{"accounts":{},"accounts":{}}',
            '{"accounts":{"owner@example.test":{"password":"a","password":"b"}}}',
            '{"accounts":{"owner@example.test":{"password":"a"},"OWNER@example.test":{"password":"b"}}}',
            '{"accounts":{"owner@example.test":{"password":"a","imap_host":"override"}}}',
            '{"accounts":{"owner@example.test":"a"}}',
            '{"accounts":[],"other":"value"}',
        ):
            with self.subTest(text=text):
                with self.assertRaises(app.AccountConfigError):
                    app.load_accounts(self.write(text))

    def test_invalid_address_and_password_are_not_reported(self):
        for address in ('not-mail', 'Name <owner@example.test>', 'owner@', '@example.test',
                        'owner@example.test\r\nBcc:other@example.test', 'owner..x@example.test'):
            with self.subTest(address=address):
                self.structured({address: {'password': 'SYNTHETIC-SENSITIVE-MARKER'}})
                with self.assertRaises(app.AccountConfigError) as error:
                    app.load_accounts(self.path)
                self.assertNotIn('SYNTHETIC-SENSITIVE-MARKER', str(error.exception))
                self.assertNotIn(address, str(error.exception))
        for password in ('', '<redacted>', 'line\nbreak', None, 2):
            self.structured({'owner@example.test': {'password': password}})
            with self.assertRaises(app.AccountConfigError):
                app.load_accounts(self.path)

    def test_legacy_missing_mismatch_duplicate_and_invalid_block_rejected(self):
        for text in (
            'owner@example.test\nPassword: fixture\nother@example.test\n',
            'owner@example.test\nUsername: other@example.test\nPassword: fixture\n',
            'owner@example.test\nPassword: a\nowner@example.test\nPassword: b\n',
            'owner@example.test\nPassword: a\nNew password: b\n',
            'owner@example.test\nPassword: a\nbad@@example.test\nPassword: b\n',
            'Address and username: invalid\nPassword: fixture\n',
            'owner@example.test\nbad @example.test\nPassword: fixture\n',
        ):
            with self.subTest(text=text):
                with self.assertRaises(app.AccountConfigError):
                    app.load_accounts(self.write(text))

    def test_cli_config_error_and_help_never_open_mail(self):
        self.write('{"accounts":{"owner@example.test":{"password":"SYNTHETIC-SENSITIVE-MARKER"}}}')
        self.path.chmod(0o644)
        with patch.dict(os.environ, {'MUSEPOST_CONFIG': str(self.path)}):
            with patch.object(app.sys, 'argv', ['musepost', 'inboxes']):
                with self.assertRaises(SystemExit) as error:
                    app.main()
                self.assertNotIn('SYNTHETIC-SENSITIVE-MARKER', str(error.exception))
            with patch.object(app.sys, 'argv', ['musepost', '--help']):
                with contextlib.redirect_stdout(io.StringIO()):
                    app.main()

    def test_only_explicit_send_or_reply_dispatch_to_smtp_commands(self):
        accounts = app.load_accounts(self.structured())
        with patch.object(app, 'load_accounts', return_value=accounts), \
                patch.object(app, 'cmd_inboxes') as inboxes, \
                patch.object(app, 'cmd_send') as send, patch.object(app, 'cmd_reply') as reply:
            with patch.object(app.sys, 'argv', ['musepost', 'inboxes']):
                app.main()
            inboxes.assert_called_once()
            send.assert_not_called()
            reply.assert_not_called()
            with patch.object(app.sys, 'argv', ['musepost', 'send', 'owner+alerts@example.test',
                                              '--to', 'recipient@example.test', '--subject', 'Fixture',
                                              '--body', 'Synthetic body', '--no-signature']):
                app.main()
            self.assertEqual(send.call_args.args[0]['owner+alerts@example.test'], 'synthetic-only')
            self.assertEqual(send.call_args.args[1], 'owner+alerts@example.test')
            reply.assert_not_called()
            with patch.object(app.sys, 'argv', ['musepost', 'reply', 'owner+alerts@example.test',
                                              '1', '--body', 'Synthetic reply', '--no-signature']):
                app.main()
            reply.assert_called_once()

    def test_control_or_unicode_address_is_rejected(self):
        for address in ('owner@example\u0080.test', 'owner@example\u202e.test', 'owner@exämple.test'):
            with self.subTest(address=address):
                self.structured({address: {'password': 'synthetic-only'}})
                with self.assertRaises(app.AccountConfigError):
                    app.load_accounts(self.path)

    def test_quoted_local_part_keeps_actual_message_id_domain(self):
        address = '"owner@part"@example.test'
        accounts = app.load_accounts(self.structured({address: {'password': 'synthetic-only'}}))
        self.assertEqual(app.resolve(accounts, 'example.test'), address)
        with patch.object(app, 'format_from', return_value=address):
            message = app.build_send(address, ['recipient@example.test'], 'Fixture', 'Synthetic')
        self.assertEqual(message['From'].addresses[0].username, 'owner@part')
        self.assertEqual(message['From'].addresses[0].domain, 'example.test')
        self.assertTrue(str(message['Message-ID']).endswith('@example.test>'))
        self.assertFalse(message['Message-ID'].defects)

    def test_transport_certificate_validation_and_starttls_before_login(self):
        events = []
        class FakeSMTP:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def starttls(self, *, context):
                self_check(context)
                events.append('tls')
            def login(self, address, password): events.append('login')
            def send_message(self, message): events.append('send')
        def self_check(context):
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        with patch.object(app, 'get_servers_for', return_value=('imap.example.test', 993, 'smtp.example.test', 587)), \
                patch.object(app.imaplib, 'IMAP4_SSL') as imap, \
                patch.object(app.smtplib, 'SMTP', return_value=FakeSMTP()) as smtp:
            app.imap_connect('owner@example.test', 'synthetic')
            self_check(imap.call_args.kwargs['ssl_context'])
            app.smtp_send('owner@example.test', 'synthetic', object())
            smtp.assert_called_once_with('smtp.example.test', 587)
        self.assertEqual(events, ['tls', 'login', 'send'])


if __name__ == '__main__':
    unittest.main()
