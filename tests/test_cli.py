"""Offline regression tests for the user-facing MusePost command contract.

Every test uses a temporary home and dummy credentials. Socket connections are
blocked in addition to mocked mail operations, so failures cannot contact a real
mailbox or submit mail.
"""

import contextlib
from email.message import EmailMessage
import importlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class OfflineCliTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.home / "config"),
                "XDG_STATE_HOME": str(self.home / "state"),
                "MUSEPOST_STATE_DIR": str(self.home / "state" / "musepost"),
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.socket_guard = mock.patch(
            "socket.create_connection",
            side_effect=AssertionError(
                "Offline tests must never open network connections"
            ),
        )
        self.socket_guard.start()
        self.addCleanup(self.socket_guard.stop)
        self.socket_connect_guard = mock.patch(
            "socket.socket.connect",
            side_effect=AssertionError(
                "Offline tests must never open network connections"
            ),
        )
        self.socket_connect_guard.start()
        self.addCleanup(self.socket_connect_guard.stop)
        self.config_path = self.home / "accounts.json"
        self.write_config(
            {
                "accounts": {
                    "support@example.test": {
                        "password": "dummy-fixture-password",
                        "imap_host": "imap.example.test",
                        "smtp_host": "smtp.example.test",
                    }
                }
            }
        )
        self.config = importlib.import_module("musepost_config")
        self.namespace = runpy.run_path(
            str(ROOT / "musepost"), run_name="musepost_cli_under_test"
        )
        self.main = self.namespace["main"]

    def write_config(self, value, mode=0o600):
        self.config_path.write_text(json.dumps(value), encoding="utf-8")
        self.config_path.chmod(mode)

    def invoke(self, arguments, stdin=""):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            mock.patch("sys.stdin", io.StringIO(stdin)),
        ):
            try:
                status = self.main(arguments)
            except SystemExit as error:
                status = error.code
        return status, stdout.getvalue(), stderr.getvalue()

    def configured(self, *arguments):
        return ["--config", str(self.config_path), *arguments]

    def test_arbitrary_mailbox_in_json_configuration(self):
        self.write_config(
            {
                "accounts": {
                    "SUPPORT@Example.Test": {
                        "password": "dummy-fixture-password",
                        "imap_host": "imap.example.test",
                    }
                }
            }
        )
        accounts = self.config.load_accounts(str(self.config_path))
        self.assertEqual(set(accounts), {"support@example.test"})

    def test_arbitrary_mailbox_in_legacy_configuration(self):
        self.config_path.write_text(
            "Address and username: Billing@Example.Test\nNew password: dummy-fixture-password\n",
            encoding="utf-8",
        )
        accounts = self.config.load_accounts(str(self.config_path))
        self.assertEqual(set(accounts), {"billing@example.test"})

    def test_credentials_require_private_permissions(self):
        self.config_path.chmod(0o644)
        with self.assertRaises(Exception) as rejected:
            self.config.load_accounts(str(self.config_path))
        self.assertIn("600", str(rejected.exception))
        self.assertNotIn("dummy-fixture-password", str(rejected.exception))

    def test_malformed_json_is_an_error(self):
        self.config_path.write_text('{"accounts":', encoding="utf-8")
        with self.assertRaises(Exception):
            self.config.load_accounts(str(self.config_path))

    def test_invalid_server_configuration_is_an_error(self):
        self.write_config(
            {
                "accounts": {
                    "support@example.test": {
                        "password": "dummy-fixture-password",
                        "imap_port": "not-a-port",
                    }
                }
            }
        )
        with self.assertRaises(Exception) as rejected:
            self.config.load_accounts(str(self.config_path))
        self.assertNotIn("dummy-fixture-password", str(rejected.exception))

    def write_routing_override(self, mode=0o644):
        directory = self.config.config_dir()
        directory.mkdir(parents=True, mode=0o700)
        routing = directory / "servers.json"
        routing.write_text(
            json.dumps(
                {
                    "support@example.test": {
                        "imap_host": "private-imap.example.test",
                        "smtp_host": "private-smtp.example.test",
                    }
                }
            ),
            encoding="utf-8",
        )
        routing.chmod(mode)
        return routing

    def test_world_writable_routing_override_is_rejected_before_transport(self):
        mail = self.mock_mail()
        self.write_routing_override(mode=0o666)
        status, stdout, stderr = self.invoke(self.configured("inboxes", "--json"))
        self.assertNotEqual(status, 0)
        self.assertTrue(json.loads(stdout).get("error"))
        mail.inbox_status.assert_not_called()
        self.assertNotIn("dummy-fixture-password", stdout + stderr)

    def test_routing_override_symlink_is_rejected(self):
        routing = self.write_routing_override()
        target = self.home / "outside-routing.json"
        routing.replace(target)
        routing.symlink_to(target)
        with self.assertRaises(self.config.ConfigError):
            self.config.load_accounts(str(self.config_path))

    def test_owner_readable_routing_override_is_accepted(self):
        self.write_config(
            {
                "accounts": {
                    "support@example.test": {
                        "password": "dummy-fixture-password",
                    }
                }
            }
        )
        self.write_routing_override(mode=0o644)
        account = self.config.load_accounts(str(self.config_path))[
            "support@example.test"
        ]
        self.assertEqual(account["imap_host"], "private-imap.example.test")
        self.assertEqual(account["smtp_host"], "private-smtp.example.test")

    def test_missing_optional_routing_configuration_is_accepted(self):
        missing = self.config.config_dir() / "servers.json"
        self.assertFalse(missing.exists())
        self.assertEqual(self.config.read_json(missing, optional=True), {})
        self.assertIn(
            "support@example.test", self.config.load_accounts(str(self.config_path))
        )

    def test_body_option_requires_a_value(self):
        status, stdout, stderr = self.invoke(
            self.configured(
                "send",
                "support@example.test",
                "--to",
                "recipient@example.test",
                "--subject",
                "Subject",
                "--body",
            )
        )
        self.assertNotEqual(status, 0)
        self.assertIn("--body", stdout + stderr)
        self.assertNotIn("Traceback", stdout + stderr)

    def test_help_does_not_load_credentials_or_contact_mail(self):
        with mock.patch.object(
            self.config,
            "load_accounts",
            side_effect=AssertionError(
                "Help must work before any accounts are configured"
            ),
        ):
            status, stdout, stderr = self.invoke(["--help"])
        self.assertEqual(status, 0, stderr)
        self.assertIn("send", stdout)
        self.assertIn("--config", stdout)

    def test_json_configuration_error_has_nonzero_status_and_no_secret(self):
        self.config_path.chmod(0o644)
        status, stdout, stderr = self.invoke(self.configured("inboxes", "--json"))
        self.assertNotEqual(status, 0)
        result = json.loads(stdout)
        self.assertTrue(result.get("error"))
        self.assertNotIn("dummy-fixture-password", stdout + stderr)
        self.assertNotIn("Traceback", stdout + stderr)

    def mock_mail(self):
        """Keep MIME composition real while stubbing every transport operation."""
        real_mail = self.main.__globals__["mail"]
        fake_mail = mock.MagicMock(spec=real_mail)
        fake_mail.MailError = real_mail.MailError
        fake_mail.create_message.side_effect = real_mail.create_message
        fake_mail.reply_recipients.side_effect = real_mail.reply_recipients
        patcher = mock.patch.dict(self.main.__globals__, {"mail": fake_mail})
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake_mail

    def message(self, body="A full message body"):
        return {
            "account": "support@example.test",
            "folder": "INBOX",
            "uid": "42",
            "uidvalidity": "999",
            "message_id": "<fixture@example.test>",
            "from": "sender@example.test",
            "to": "support@example.test",
            "cc": "",
            "reply_to": "",
            "date": "",
            "subject": "Fixture message",
            "body": body,
            "truncated": False,
            "body_chars": len(body),
            "attachments": [],
        }

    def test_read_returns_full_body_without_marking_it_read(self):
        mail = self.mock_mail()
        body = "A long body that exceeds the old limit.\n" * 400
        mail.read_message.return_value = self.message(body)
        status, stdout, stderr = self.invoke(
            self.configured("read", "support@example.test", "42", "--json")
        )
        self.assertEqual(status, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["body"], body)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["body_chars"], len(body))
        self.assertFalse(mail.read_message.call_args.kwargs["mark_read"])
        self.assertIsNone(mail.read_message.call_args.kwargs["max_chars"])

    def test_peek_is_readonly_and_honors_explicit_limit_folder_and_timeout(self):
        mail = self.mock_mail()
        mail.read_message.return_value = dict(
            self.message("First ten"), truncated=True, body_chars=300
        )
        status, stdout, stderr = self.invoke(
            self.configured(
                "peek",
                "support@example.test",
                "42",
                "--folder",
                "Archive",
                "--limit",
                "10",
                "--timeout",
                "12",
                "--json",
            )
        )
        self.assertEqual(status, 0, stderr)
        self.assertTrue(json.loads(stdout)["truncated"])
        called = mail.read_message.call_args.kwargs
        self.assertEqual(called["folder"], "Archive")
        self.assertEqual(called["max_chars"], 10)
        self.assertEqual(called["timeout"], 12)
        self.assertFalse(called["mark_read"])

    def test_mark_read_is_explicit(self):
        mail = self.mock_mail()
        mail.read_message.return_value = self.message()
        status, _, stderr = self.invoke(
            self.configured(
                "read", "support@example.test", "42", "--mark-read", "--json"
            )
        )
        self.assertEqual(status, 0, stderr)
        self.assertTrue(mail.read_message.call_args.kwargs["mark_read"])

    def test_global_and_subcommand_options_both_apply(self):
        mail = self.mock_mail()
        mail.list_messages.return_value = {
            "account": "support@example.test",
            "folder": "Archive",
            "uidvalidity": "999",
            "messages": [],
            "total": 0,
        }
        status, stdout, stderr = self.invoke(
            [
                "--json",
                "--timeout",
                "9",
                "list",
                "support@example.test",
                "7",
                "--config",
                str(self.config_path),
                "--folder",
                "Archive",
            ]
        )
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(stdout)["messages"], [])
        called = mail.list_messages.call_args.kwargs
        self.assertEqual(called["folder"], "Archive")
        self.assertEqual(called["limit"], 7)
        self.assertEqual(called["timeout"], 9)

    def test_search_preserves_quotes_and_unicode(self):
        mail = self.mock_mail()
        mail.list_messages.return_value = {
            "account": "support@example.test",
            "folder": "INBOX",
            "uidvalidity": "999",
            "messages": [],
            "total": 0,
        }
        query = 'customer "quoted" café'
        status, _, stderr = self.invoke(
            self.configured("search", "support@example.test", query, "--json")
        )
        self.assertEqual(status, 0, stderr)
        self.assertEqual(mail.list_messages.call_args.kwargs["query"], query)

    def test_selected_default_signature_is_deterministic(self):
        self.config.save_signatures(
            {
                "support@example.test": {
                    "variants": {
                        "desktop": {"text": "Desktop signature", "html": ""},
                        "phone": {"text": "Phone signature", "html": ""},
                    },
                    "default": "phone",
                }
            }
        )
        for _ in range(10):
            self.assertEqual(
                self.config.get_signature("support@example.test")["text"],
                "Phone signature",
            )
        self.assertEqual(
            self.config.get_signature("support@example.test", "desktop")["text"],
            "Desktop signature",
        )

    def test_transport_failure_is_json_and_redacts_password(self):
        mail = self.mock_mail()
        mail.list_messages.side_effect = mail.MailError(
            "imap_error", "Authentication failed for dummy-fixture-password"
        )
        status, stdout, stderr = self.invoke(
            self.configured("list", "support@example.test", "--json")
        )
        self.assertNotEqual(status, 0)
        self.assertTrue(json.loads(stdout).get("error"))
        self.assertNotIn("dummy-fixture-password", stdout + stderr)
        self.assertNotIn("Traceback", stdout + stderr)

    def test_preview_builds_message_from_stdin_without_transport(self):
        mail = self.mock_mail()
        status, stdout, stderr = self.invoke(
            self.configured(
                "send",
                "support@example.test",
                "--to",
                "recipient@example.test",
                "--subject",
                "Preview only",
                "--body",
                "-",
                "--no-signature",
                "--preview",
                "--json",
            ),
            stdin="The complete proposed message.\n",
        )
        self.assertEqual(status, 0, stderr)
        self.assertIn("The complete proposed message.", stdout)
        self.assertIn("recipient@example.test", stdout)
        mail.submit_message.assert_not_called()
        mail.archive_sent.assert_not_called()
        self.assertEqual(
            mail.create_message.call_args.args[3], "The complete proposed message.\n"
        )

    def test_attachment_download_writes_bytes_to_explicit_output(self):
        mail = self.mock_mail()
        payload = b"\x00\xffattachment fixture\n"
        mail.get_attachment.return_value = ("../../remote-name.bin", payload)
        destination = self.home / "chosen-name.bin"
        status, stdout, stderr = self.invoke(
            self.configured(
                "attachment",
                "support@example.test",
                "42",
                "1",
                "--folder",
                "Archive",
                "--output",
                str(destination),
                "--json",
            )
        )
        self.assertEqual(status, 0, stderr)
        self.assertEqual(destination.read_bytes(), payload)
        self.assertEqual(mail.get_attachment.call_args.kwargs["folder"], "Archive")
        self.assertNotIn("dummy-fixture-password", stdout)

    def test_draft_send_records_one_receipt_even_if_archiving_fails(self):
        mail = self.mock_mail()
        status, stdout, stderr = self.invoke(
            self.configured(
                "send",
                "support@example.test",
                "--to",
                "recipient@example.test",
                "--subject",
                "Saved draft",
                "--body",
                "Draft body",
                "--no-signature",
                "--draft",
                "--json",
            )
        )
        self.assertEqual(status, 0, stderr)
        saved = json.loads(stdout)
        draft_id = saved["draft_id"]
        mail.submit_message.assert_not_called()
        mail.submit_message.return_value = {
            "status": "accepted",
            "message_id": saved["message_id"],
            "accepted": ["recipient@example.test"],
            "rejected": {},
            "delivery": "unconfirmed",
        }
        mail.archive_sent.side_effect = mail.MailError(
            "imap_error", "Sent folder unavailable"
        )
        status, stdout, stderr = self.invoke(
            self.configured("drafts", "send", draft_id, "--json")
        )
        self.assertEqual(status, 0, stderr)
        receipt = json.loads(stdout)
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(receipt["message_id"], saved["message_id"])
        self.assertEqual(mail.submit_message.call_count, 1)
        status, repeated, stderr = self.invoke(
            self.configured("drafts", "send", draft_id, "--json")
        )
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(repeated)["message_id"], saved["message_id"])
        self.assertEqual(mail.submit_message.call_count, 1)
        status, stdout, stderr = self.invoke(self.configured("receipts", "--json"))
        self.assertEqual(status, 0, stderr)
        self.assertIn(saved["message_id"], stdout)
        self.assertNotIn("dummy-fixture-password", stdout)
        state_root = self.home / "state" / "musepost"
        self.assertEqual(state_root.stat().st_mode & 0o777, 0o700)
        files = [path for path in state_root.rglob("*") if path.is_file()]
        self.assertTrue(files)
        for path in files:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(b"dummy-fixture-password", path.read_bytes())

    def test_request_id_reuses_receipt_and_rejects_different_content(self):
        mail = self.mock_mail()
        mail.submit_message.return_value = {
            "status": "accepted",
            "message_id": "<receipt@example.test>",
            "accepted": ["recipient@example.test"],
            "rejected": {},
            "delivery": "unconfirmed",
        }
        mail.archive_sent.return_value = {"status": "disabled", "folder": None}
        command = self.configured(
            "send",
            "support@example.test",
            "--to",
            "recipient@example.test",
            "--subject",
            "Idempotent send",
            "--body",
            "Same content",
            "--no-signature",
            "--request-id",
            "fixture-operation-1",
            "--json",
        )
        first_status, first_output, first_error = self.invoke(command)
        self.assertEqual(first_status, 0, first_error)
        second_status, second_output, second_error = self.invoke(command)
        self.assertEqual(second_status, 0, second_error)
        self.assertEqual(
            json.loads(first_output)["request_id"],
            json.loads(second_output)["request_id"],
        )
        self.assertEqual(mail.submit_message.call_count, 1)
        changed = list(command)
        changed[changed.index("--body") + 1] = "Different content"
        status, stdout, stderr = self.invoke(changed)
        self.assertNotEqual(status, 0)
        self.assertTrue(json.loads(stdout).get("error"))
        self.assertEqual(mail.submit_message.call_count, 1)
        self.assertNotIn("dummy-fixture-password", stdout + stderr)

    def test_partial_and_rejected_submissions_keep_recipient_results(self):
        mail = self.mock_mail()
        mail.archive_sent.return_value = {"status": "disabled", "folder": None}
        cases = [
            ("partial", ["accepted@example.test"], 2),
            ("rejected", [], 1),
        ]
        rejected = {
            "rejected@example.test": {"code": 550, "message": "No such mailbox"}
        }
        for submission_status, accepted, expected_exit in cases:
            with self.subTest(status=submission_status):
                mail.submit_message.return_value = {
                    "status": submission_status,
                    "message_id": "<fixture@example.test>",
                    "accepted": accepted,
                    "rejected": rejected,
                    "delivery": "unconfirmed",
                }
                status, stdout, stderr = self.invoke(
                    self.configured(
                        "send",
                        "support@example.test",
                        "--to",
                        "accepted@example.test,rejected@example.test",
                        "--subject",
                        submission_status,
                        "--body",
                        "Recipient result fixture",
                        "--no-signature",
                        "--json",
                    )
                )
                self.assertEqual(status, expected_exit, stderr)
                receipt = json.loads(stdout)
                self.assertEqual(receipt["status"], submission_status)
                self.assertEqual(receipt["accepted"], accepted)
                self.assertEqual(receipt["rejected"], rejected)
                self.assertEqual(receipt["delivery"], "unconfirmed")

    def test_reply_flag_failure_preserves_accepted_send_result(self):
        mail = self.mock_mail()
        original = EmailMessage()
        original["From"] = "Website <website@example.test>"
        original["Reply-To"] = "Contact <contact@example.test>"
        original["Subject"] = "Customer question"
        original["Message-ID"] = "<original@example.test>"
        original.set_content("The original customer message.")
        mail.reply_source.return_value = (original, "The original customer message.")
        mail.submit_message.return_value = {
            "status": "accepted",
            "message_id": "<reply@example.test>",
            "accepted": ["contact@example.test"],
            "rejected": {},
            "delivery": "unconfirmed",
        }
        mail.archive_sent.return_value = {"status": "disabled", "folder": None}
        mail.mark_answered.side_effect = mail.MailError(
            "imap_error", "Flag update failed"
        )
        command = self.configured(
            "reply",
            "support@example.test",
            "42",
            "--body",
            "Our complete response.",
            "--no-signature",
            "--request-id",
            "reply-fixture-1",
            "--json",
        )
        status, stdout, stderr = self.invoke(command)
        self.assertEqual(status, 0, stderr)
        receipt = json.loads(stdout)
        self.assertEqual(receipt["status"], "accepted")
        self.assertTrue(receipt["warnings"])
        sent = mail.submit_message.call_args.args[3]
        self.assertIn("contact@example.test", str(sent["To"]))
        self.assertEqual(str(sent["In-Reply-To"]), "<original@example.test>")
        repeated_status, _, stderr = self.invoke(command)
        self.assertEqual(repeated_status, 0, stderr)
        self.assertEqual(mail.submit_message.call_count, 1)

    def test_interrupted_submission_is_reported_uncertain_without_resending(self):
        mail = self.mock_mail()
        message = mail.create_message(
            "support@example.test",
            "recipient@example.test",
            "Interrupted submission",
            "Exact body",
        )
        state = importlib.import_module("musepost_state")
        state.claim_request("interrupted-fixture", "support@example.test", message)
        status, stdout, stderr = self.invoke(
            self.configured(
                "send",
                "support@example.test",
                "--to",
                "recipient@example.test",
                "--subject",
                "Interrupted submission",
                "--body",
                "Exact body",
                "--no-signature",
                "--request-id",
                "interrupted-fixture",
                "--json",
            )
        )
        self.assertEqual(status, 3, stderr)
        receipt = json.loads(stdout)
        self.assertEqual(receipt["status"], "uncertain")
        self.assertEqual(receipt["delivery"], "unconfirmed")
        mail.submit_message.assert_not_called()
        mail.archive_sent.assert_not_called()

    def test_accepted_outcome_survives_receipt_write_failure_without_duplicate_retry(
        self,
    ):
        mail = self.mock_mail()
        mail.submit_message.return_value = {
            "status": "accepted",
            "message_id": "<accepted@example.test>",
            "accepted": ["recipient@example.test"],
            "rejected": {},
            "delivery": "unconfirmed",
        }
        mail.archive_sent.return_value = {"status": "disabled", "folder": None}
        state = importlib.import_module("musepost_state")
        command = self.configured(
            "send",
            "support@example.test",
            "--to",
            "recipient@example.test",
            "--subject",
            "Persistence failure",
            "--body",
            "Exact body",
            "--no-signature",
            "--request-id",
            "persistence-fixture",
            "--json",
        )
        with mock.patch.object(
            state,
            "save_receipt",
            side_effect=state.StateError(
                "STATE_UNAVAILABLE", "Fixture disk write failure"
            ),
        ):
            status, stdout, stderr = self.invoke(command)
        self.assertEqual(status, 0, stderr)
        receipt = json.loads(stdout)
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(receipt["accepted"], ["recipient@example.test"])
        self.assertTrue(receipt["warnings"])
        retry_status, retry_output, stderr = self.invoke(command)
        self.assertEqual(retry_status, 3, stderr)
        self.assertEqual(json.loads(retry_output)["status"], "uncertain")
        self.assertEqual(mail.submit_message.call_count, 1)

    def test_multipart_request_replays_despite_new_ids_dates_and_boundaries(self):
        mail = self.mock_mail()
        state = importlib.import_module("musepost_state")
        attachment = self.home / "fixture.bin"
        attachment.write_bytes(b"identical attachment content")
        arguments = (
            "support@example.test",
            "recipient@example.test",
            "Multipart retry",
            "Identical body",
        )
        options = {
            "signature": {"text": "Plain signature", "html": "<p>HTML signature</p>"},
            "attachments": [str(attachment)],
        }
        first = mail.create_message(*arguments, **options)
        second = mail.create_message(*arguments, **options)
        first.replace_header("Date", "Mon, 01 Jan 2024 12:00:00 +0000")
        second.replace_header("Date", "Tue, 02 Jan 2024 12:00:00 +0000")
        for index, part in enumerate(first.walk()):
            if part.is_multipart():
                part.set_boundary("first-boundary-{}".format(index))
        for index, part in enumerate(second.walk()):
            if part.is_multipart():
                part.set_boundary("second-boundary-{}".format(index))
        self.assertNotEqual(first.as_bytes(), second.as_bytes())
        receipt, claimed = state.claim_request(
            "multipart-fixture", "support@example.test", first
        )
        self.assertTrue(claimed)
        receipt["status"] = "accepted"
        state.save_receipt(receipt)
        replay, claimed = state.claim_request(
            "multipart-fixture", "support@example.test", second
        )
        self.assertFalse(claimed)
        self.assertEqual(replay["status"], "accepted")
        self.assertEqual(replay["message_id"], str(first["Message-ID"]))

    def test_same_request_id_rejects_changed_attachment_content(self):
        mail = self.mock_mail()
        state = importlib.import_module("musepost_state")
        attachment = self.home / "fixture.bin"
        attachment.write_bytes(b"original attachment content")
        arguments = (
            "support@example.test",
            "recipient@example.test",
            "Attachment retry",
            "Identical body",
        )
        original = mail.create_message(*arguments, attachments=[str(attachment)])
        state.claim_request("attachment-fixture", "support@example.test", original)
        attachment.write_bytes(b"different attachment content")
        changed = mail.create_message(*arguments, attachments=[str(attachment)])
        with self.assertRaises(state.StateError) as rejected:
            state.claim_request("attachment-fixture", "support@example.test", changed)
        self.assertEqual(rejected.exception.code, "REQUEST_CONFLICT")
        mail.submit_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
