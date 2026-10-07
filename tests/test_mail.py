"""Offline protocol regression tests; no real mail account or network is used."""

import os
import sys
import tempfile
import unittest
from email.message import EmailMessage
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import musepost_mail as mail


ACCOUNT = "taylor@example.test"
PASSWORD = "fake-offline-password"
SERVER = {
    "imap_host": "imap.example.test",
    "imap_port": 993,
    "smtp_host": "smtp.example.test",
    "smtp_port": 587,
    "smtp_security": "starttls",
}


def message(body="Original body", reply_to=None):
    msg = EmailMessage()
    msg["From"] = "Sender <sender@example.test>"
    msg["To"] = ACCOUNT
    msg["Subject"] = "Test subject"
    msg["Message-ID"] = "<source@example.test>"
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    return msg


class FakeIMAP:
    def __init__(self):
        self.calls = []
        self.literal = None
        self.search_literals = []
        self.raw = message().as_bytes()
        self.search_result = ("OK", [b"1 2"])
        self.header_rows = [
            (
                b"1 (UID 1 FLAGS (\\Seen) BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {64}",
                b"From: One <one@example.test>\r\nSubject: First\r\n\r\n",
            ),
            (
                b"2 (UID 2 FLAGS () BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {64}",
                b"From: Two <two@example.test>\r\nSubject: Second\r\n\r\n",
            ),
            b")",
        ]
        self.folder_rows = [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Sent) "/" "Sent Messages"',
        ]
        self.store_result = ("OK", [b"stored"])
        self.append_result = ("OK", [b"appended"])
        self.logout_error = False

    def login(self, addr, pw):
        self.calls.append(("login", addr, pw))
        return "OK", [b"logged in"]

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def response(self, name):
        return name, [b"7654"]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "search":
            self.search_literals.append(self.literal)
            return self.search_result
        if command == "fetch":
            if "HEADER.FIELDS" in str(args[-1]):
                return "OK", self.header_rows
            return "OK", [(b"1 (BODY[] {100}", self.raw), b")"]
        if command == "store":
            return self.store_result
        raise AssertionError("Unexpected offline command: " + command)

    def list(self):
        self.calls.append(("list",))
        return "OK", self.folder_rows

    def append(self, *args):
        self.calls.append(("append", *args))
        return self.append_result

    def logout(self):
        self.calls.append(("logout",))
        if self.logout_error:
            raise mail.imaplib.IMAP4.abort("simulated cleanup failure")
        return "BYE", [b"bye"]

    def shutdown(self):
        self.calls.append(("shutdown",))


class FakeSMTP:
    def __init__(self):
        self.calls = []
        self.refused = {}
        self.send_error = None
        self.login_error = None
        self.quit_error = False

    def starttls(self, **kwargs):
        self.calls.append(("starttls", kwargs))
        return 220, b"TLS ready"

    def login(self, addr, pw):
        self.calls.append(("login", addr, pw))
        if self.login_error:
            raise self.login_error
        return 235, b"authenticated"

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.calls.append(("send", msg, from_addr, to_addrs))
        if self.send_error:
            raise self.send_error
        return self.refused

    def quit(self):
        self.calls.append(("quit",))
        if self.quit_error:
            raise mail.smtplib.SMTPServerDisconnected("simulated QUIT loss")

    def close(self):
        self.calls.append(("close",))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.imap = FakeIMAP()
        self.smtp = FakeSMTP()
        self.imap_constructor = self.patch(mail.imaplib, "IMAP4_SSL", self.imap)
        self.smtp_constructor = self.patch(mail.smtplib, "SMTP", self.smtp)
        self.smtp_ssl_constructor = self.patch(mail.smtplib, "SMTP_SSL", self.smtp)

    def patch(self, target, name, result):
        patcher = mock.patch.object(target, name, return_value=result)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def test_full_read_is_peek_readonly_and_has_identity(self):
        self.imap.raw = message("x" * 8500).as_bytes()
        result = mail.read_message(ACCOUNT, PASSWORD, SERVER, 2)
        self.assertIn(("select", '"INBOX"', True), self.imap.calls)
        self.assertIn(("fetch", "2", "(BODY.PEEK[])"), self.imap.calls)
        self.assertEqual(result["body"], "x" * 8500 + "\n")
        self.assertFalse(result["truncated"])
        self.assertEqual(result["uidvalidity"], "7654")
        self.assertEqual(result["message_id"], "<source@example.test>")
        self.assertEqual(result["body_chars"], 8501)
        self.assertEqual(self.imap_constructor.call_args.kwargs["timeout"], 30)

    def test_mark_read_is_explicit_and_truncation_is_visible(self):
        self.imap.raw = message("abcdef").as_bytes()
        result = mail.read_message(
            ACCOUNT, PASSWORD, SERVER, "2", mark_read=True, max_chars=3
        )
        self.assertIn(("select", '"INBOX"', False), self.imap.calls)
        self.assertIn(("fetch", "2", "(BODY[])"), self.imap.calls)
        self.assertEqual(result["body"], "abc")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["body_chars"], 7)

    def test_failed_search_cannot_look_like_empty_success(self):
        self.imap.search_result = ("BAD", [b"bad charset"])
        with self.assertRaises(mail.MailError) as caught:
            mail.list_messages(ACCOUNT, PASSWORD, SERVER, query="hello")
        self.assertEqual(caught.exception.code, "imap_search_failed")
        self.assertIn(("logout",), self.imap.calls)

    def test_ascii_quotes_and_backslashes_are_escaped(self):
        mail.list_messages(
            ACCOUNT, PASSWORD, SERVER, query='invoice "October" path\\invoice'
        )
        search = next(c for c in self.imap.calls if c[0] == "search")
        self.assertEqual(search[1:4], (None, "CHARSET", "UTF-8"))
        self.assertIsInstance(search[-1], str)
        self.assertIn('invoice \\"October\\" path\\\\invoice', search[-1])
        self.assertNotIn('"October"', search[-1])

    def test_unicode_search_uses_literals_and_unions_uids(self):
        query = 'café "October" path\\invoice'
        result = mail.list_messages(ACCOUNT, PASSWORD, SERVER, query=query, unread=True)
        searches = [c for c in self.imap.calls if c[0] == "search"]
        self.assertEqual([c[-1] for c in searches], ["SUBJECT", "FROM", "BODY"])
        self.assertTrue(
            all(c[1:-1] == (None, "CHARSET", "UTF-8", "UNSEEN") for c in searches)
        )
        self.assertEqual(self.imap.search_literals, [query.encode("utf-8")] * 3)
        self.assertEqual(result["total"], 2)
        self.assertIsNone(self.imap.literal)

    def test_failed_unicode_search_clears_literal(self):
        self.imap.search_result = ("BAD", [b"unsupported charset"])
        with self.assertRaises(mail.MailError):
            mail.list_messages(ACCOUNT, PASSWORD, SERVER, query="café")
        self.assertIsNone(self.imap.literal)
        self.assertEqual(len(self.imap.search_literals), 1)

    def test_search_rejects_protocol_line_injection(self):
        with self.assertRaises(mail.MailError) as caught:
            mail.list_messages(
                ACCOUNT,
                PASSWORD,
                SERVER,
                query="hello\r\nUID STORE 1 +FLAGS (\\Deleted)",
            )
        self.assertEqual(caught.exception.code, "invalid_query")
        self.assertFalse(any(c[0] == "search" for c in self.imap.calls))

    def test_headers_are_batched_and_sorted_by_selected_uid(self):
        result = mail.list_messages(ACCOUNT, PASSWORD, SERVER, limit=2)
        fetches = [c for c in self.imap.calls if c[0] == "fetch"]
        self.assertEqual(len(fetches), 1)
        self.assertEqual(fetches[0][1], b"2,1")
        self.assertEqual([m["uid"] for m in result["messages"]], ["2", "1"])
        self.assertFalse(result["messages"][0]["seen"])
        self.assertTrue(result["messages"][1]["seen"])
        self.assertEqual(result["total"], 2)

    def test_logout_failure_does_not_hide_a_successful_read(self):
        self.imap.logout_error = True
        result = mail.read_message(ACCOUNT, PASSWORD, SERVER, 2)
        self.assertEqual(result["subject"], "Test subject")
        self.assertIn(("shutdown",), self.imap.calls)

    def test_logout_failure_does_not_hide_business_error(self):
        self.imap.logout_error = True
        self.imap.search_result = ("NO", [b"denied"])
        with self.assertRaises(mail.MailError) as caught:
            mail.inbox_status(ACCOUNT, PASSWORD, SERVER)
        self.assertEqual(caught.exception.code, "imap_search_failed")

    def test_failed_select_cannot_continue_to_fetch(self):
        self.imap.select = mock.Mock(return_value=("NO", [b"no mailbox"]))
        with self.assertRaises(mail.MailError) as caught:
            mail.read_message(ACCOUNT, PASSWORD, SERVER, 2, folder="Missing")
        self.assertEqual(caught.exception.code, "imap_select_failed")
        self.assertFalse(any(c[0] == "fetch" for c in self.imap.calls))

    def test_missing_uidvalidity_fails_before_fetch(self):
        self.imap.response = mock.Mock(return_value=(None, [None]))
        with self.assertRaises(mail.MailError) as caught:
            mail.read_message(ACCOUNT, PASSWORD, SERVER, 2)
        self.assertEqual(caught.exception.code, "imap_invalid_response")
        self.assertFalse(any(c[0] == "fetch" for c in self.imap.calls))

    def test_sent_folder_uses_special_use_with_space_and_seen_flag(self):
        msg = mail.create_message(ACCOUNT, "receiver@example.test", "Subject", "Body")
        result = mail.archive_sent(ACCOUNT, PASSWORD, SERVER, msg)
        self.assertEqual(result, {"status": "saved", "folder": "Sent Messages"})
        append = next(c for c in self.imap.calls if c[0] == "append")
        self.assertEqual(append[1], '"Sent Messages"')
        self.assertEqual(append[2], "\\Seen")
        self.assertIn(str(msg["Message-ID"]).encode(), append[-1])

    def test_sent_disabled_does_not_connect(self):
        result = mail.archive_sent(
            ACCOUNT, PASSWORD, dict(SERVER, sent_folder=None), message()
        )
        self.assertEqual(result["status"], "disabled")
        self.imap_constructor.assert_not_called()

    def test_sent_unavailable_does_not_guess_folder(self):
        self.imap.folder_rows = [b'(\\HasNoChildren) "/" "INBOX"']
        result = mail.archive_sent(ACCOUNT, PASSWORD, SERVER, message())
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(any(c[0] == "append" for c in self.imap.calls))

    def test_failed_append_is_distinct_from_submission(self):
        self.imap.append_result = ("NO", [b"quota"])
        with self.assertRaises(mail.MailError) as caught:
            mail.archive_sent(ACCOUNT, PASSWORD, SERVER, message())
        self.assertEqual(caught.exception.code, "sent_archive_failed")

    def test_answered_flag_failure_is_checked(self):
        self.imap.store_result = ("NO", [b"denied"])
        with self.assertRaises(mail.MailError) as caught:
            mail.mark_answered(ACCOUNT, PASSWORD, SERVER, "2")
        self.assertEqual(caught.exception.code, "answer_flag_failed")
        self.assertIn(("store", "2", "+FLAGS.SILENT", "(\\Answered)"), self.imap.calls)

    def test_delayed_reply_does_not_mark_reused_uid(self):
        orig, body = mail.reply_source(ACCOUNT, PASSWORD, SERVER, "2")
        self.assertEqual(orig._musepost_uidvalidity, "7654")
        with self.assertRaises(mail.MailError) as caught:
            mail.mark_answered(ACCOUNT, PASSWORD, SERVER, "2", uidvalidity="1234")
        self.assertEqual(caught.exception.code, "uidvalidity_changed")
        self.assertFalse(any(c[0] == "store" for c in self.imap.calls))

    def test_partial_submission_lists_accepted_and_rejected(self):
        self.smtp.refused = {"bad@example.test": (550, b"No such recipient")}
        msg = mail.create_message(
            ACCOUNT,
            '"Doe, Jane" <good@example.test>, bad@example.test',
            "Subject",
            "Body",
        )
        result = mail.submit_message(ACCOUNT, PASSWORD, SERVER, msg)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["accepted"], ["good@example.test"])
        self.assertEqual(result["rejected"]["bad@example.test"]["code"], 550)
        self.assertEqual(result["delivery"], "unconfirmed")
        sent = next(c for c in self.smtp.calls if c[0] == "send")
        self.assertEqual(sent[2:], (ACCOUNT, ["good@example.test", "bad@example.test"]))
        self.assertEqual(self.smtp_constructor.call_args.kwargs["timeout"], 30)

    def test_all_recipients_refused_is_a_definite_rejection(self):
        self.smtp.send_error = mail.smtplib.SMTPRecipientsRefused(
            {"bad@example.test": (550, b"no")}
        )
        msg = mail.create_message(ACCOUNT, "bad@example.test", "Subject", "Body")
        result = mail.submit_message(ACCOUNT, PASSWORD, SERVER, msg)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["accepted"], [])

    def test_socket_loss_during_send_is_uncertain_and_not_retried(self):
        self.smtp.send_error = mail.smtplib.SMTPServerDisconnected("lost after DATA")
        msg = mail.create_message(ACCOUNT, "receiver@example.test", "Subject", "Body")
        with self.assertRaises(mail.MailError) as caught:
            mail.submit_message(ACCOUNT, PASSWORD, SERVER, msg)
        self.assertEqual(caught.exception.code, "smtp_uncertain")
        self.assertEqual(caught.exception.details["status"], "uncertain")
        self.assertEqual(caught.exception.details["message_id"], str(msg["Message-ID"]))
        self.assertEqual(len([c for c in self.smtp.calls if c[0] == "send"]), 1)

    def test_login_failure_is_not_submitted_and_does_not_expose_password(self):
        self.smtp.login_error = mail.smtplib.SMTPAuthenticationError(
            535, PASSWORD.encode()
        )
        msg = mail.create_message(ACCOUNT, "receiver@example.test", "Subject", "Body")
        with self.assertRaises(mail.MailError) as caught:
            mail.submit_message(ACCOUNT, PASSWORD, SERVER, msg)
        self.assertEqual(caught.exception.details["status"], "not_submitted")
        self.assertNotIn(PASSWORD, str(caught.exception))
        self.assertFalse(any(c[0] == "send" for c in self.smtp.calls))

    def test_explicit_data_rejection_is_not_uncertain(self):
        self.smtp.send_error = mail.smtplib.SMTPDataError(552, b"too large")
        msg = mail.create_message(ACCOUNT, "receiver@example.test", "Subject", "Body")
        with self.assertRaises(mail.MailError) as caught:
            mail.submit_message(ACCOUNT, PASSWORD, SERVER, msg)
        self.assertEqual(caught.exception.details["status"], "rejected")
        self.assertEqual(caught.exception.details["smtp_code"], 552)

    def test_quit_failure_does_not_mask_accepted_send(self):
        self.smtp.quit_error = True
        msg = mail.create_message(ACCOUNT, "receiver@example.test", "Subject", "Body")
        result = mail.submit_message(ACCOUNT, PASSWORD, SERVER, msg)
        self.assertEqual(result["status"], "accepted")
        self.assertIn(("close",), self.smtp.calls)

    def test_smtp_ssl_does_not_starttls(self):
        server = dict(SERVER, smtp_security="ssl", smtp_port=465)
        msg = mail.create_message(ACCOUNT, "receiver@example.test", "Subject", "Body")
        mail.submit_message(ACCOUNT, PASSWORD, server, msg)
        self.smtp_ssl_constructor.assert_called_once()
        self.smtp_constructor.assert_not_called()
        self.assertFalse(any(c[0] == "starttls" for c in self.smtp.calls))

    def test_attachment_extraction_uses_peek_and_safe_filename(self):
        msg = message()
        msg.add_attachment(
            b"exact file bytes",
            maintype="application",
            subtype="octet-stream",
            filename="..\\..\\private/report.txt",
        )
        self.imap.raw = msg.as_bytes()
        result = mail.read_message(ACCOUNT, PASSWORD, SERVER, 2)
        self.assertEqual(
            result["attachments"],
            [
                {
                    "index": 1,
                    "filename": "report.txt",
                    "content_type": "application/octet-stream",
                    "size": 16,
                }
            ],
        )
        name, content = mail.get_attachment(ACCOUNT, PASSWORD, SERVER, 2, 1)
        self.assertEqual((name, content), ("report.txt", b"exact file bytes"))
        self.assertFalse(any(c[0] == "select" and not c[2] for c in self.imap.calls))


class MIMEValidationTests(unittest.TestCase):
    def test_unicode_literal_uses_stdlib_continuation_framing_offline(self):
        class OfflineProtocol(mail.imaplib.IMAP4):
            def __init__(self):
                self.state = "SELECTED"
                self.untagged_responses = {}
                self.is_readonly = True
                self.tagnum = 0
                self.tagpre = b"OFFLINE"
                self.tagged_commands = {}
                self._encoding = "ascii"
                self.literal = None
                self.debug = 0
                self.utf8_enabled = False
                self.wire = []

            def _log(self, *args):
                pass

            def send(self, data):
                self.wire.append(data)

            def _get_response(self):
                return None  # Simulated synchronizing continuation request.

            def uid(self, command, *args):
                self._command("UID", command.upper(), *args)
                responses = {"SUBJECT": b"10 2", "FROM": b"2", "BODY": b"3"}
                return "OK", [responses[args[-1]]]

        im = OfflineProtocol()
        result = mail._search(im, "café", unread=True)
        self.assertEqual(result, [b"2", b"3", b"10"])
        self.assertEqual(
            im.wire[0], b"OFFLINE0 UID SEARCH CHARSET UTF-8 UNSEEN SUBJECT {5}\r\n"
        )
        self.assertEqual(im.wire[1:3], ["café".encode("utf-8"), b"\r\n"])
        self.assertEqual(len(im.wire), 9)
        self.assertIsNone(im.literal)

    def test_reply_to_is_preferred_and_from_is_fallback(self):
        self.assertEqual(
            mail.reply_recipients(message(reply_to="Help <support@example.test>")),
            ["support@example.test"],
        )
        self.assertEqual(mail.reply_recipients(message()), ["sender@example.test"])

    def test_invalid_reply_to_does_not_silently_redirect_to_sender(self):
        with self.assertRaises(mail.MailError):
            mail.reply_recipients(message(reply_to="broken"))

    def test_comma_in_display_name_is_preserved(self):
        msg = mail.create_message(
            ACCOUNT,
            '"Doe, Jane" <jane@example.test>, bob@example.test',
            "Subject",
            "Body",
        )
        self.assertEqual(
            [a.addr_spec for a in msg["To"].addresses],
            ["jane@example.test", "bob@example.test"],
        )
        self.assertEqual(msg["To"].addresses[0].display_name, "Doe, Jane")

    def test_header_injection_and_incomplete_addresses_are_rejected(self):
        for recipient in (
            "not-an-email",
            "user@",
            "user@example.test\nBcc: secret@example.test",
        ):
            with self.subTest(recipient=recipient), self.assertRaises(mail.MailError):
                mail.create_message(ACCOUNT, recipient, "Subject", "Body")
        with self.assertRaises(mail.MailError):
            mail.create_message(
                ACCOUNT,
                "receiver@example.test",
                "Subject\nBcc: other@example.test",
                "Body",
            )

    def test_html_fallback_decodes_entities_and_ignores_scripts(self):
        msg = EmailMessage()
        msg.set_content(
            "<style>hidden</style><p>Hello &amp; goodbye</p><script>secret()</script><p>Next</p>",
            subtype="html",
        )
        body = mail._text_body(msg)
        self.assertIn("Hello & goodbye", body)
        self.assertIn("Next", body)
        self.assertNotIn("secret", body)
        self.assertNotIn("hidden", body)

    def test_plain_body_wins_over_html_and_attached_text(self):
        msg = message("Real plain body")
        msg.add_alternative("<p>HTML body</p>", subtype="html")
        msg.add_attachment("Attached text", filename="notes.txt")
        self.assertEqual(mail._text_body(msg), "Real plain body\n")

    def test_top_level_attachment_is_included(self):
        msg = EmailMessage()
        msg.set_content(b"binary", maintype="application", subtype="octet-stream")
        msg["Content-Disposition"] = 'attachment; filename="root.bin"'
        self.assertEqual(list(mail._attachment_parts(msg)), [msg])
        self.assertEqual(mail._attachment_bytes(msg), b"binary")

    def test_signature_html_body_is_escaped_and_attachment_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "example.txt")
            with open(path, "wb") as handle:
                handle.write(b"offline attachment")
            msg = mail.create_message(
                ACCOUNT,
                "receiver@example.test",
                "Subject",
                "<literal> & text",
                signature={"text": "Signature", "html": "<b>Signature</b>"},
                attachments=[path],
                cc="copy@example.test",
            )
        plain = msg.get_body(preferencelist=("plain",)).get_content()
        html = msg.get_body(preferencelist=("html",)).get_content()
        self.assertIn("Signature", plain)
        self.assertIn("&lt;literal&gt; &amp; text", html)
        self.assertEqual(str(msg["Cc"]), "copy@example.test")
        attachment = list(mail._attachment_parts(msg))[0]
        self.assertEqual(mail._attachment_bytes(attachment), b"offline attachment")

    def test_modified_utf7_mailbox_round_trip(self):
        name = "Correo y español & Amigos"
        self.assertEqual(mail._folder_text(mail._folder_wire(name)), name)

    def test_eml_attachment_survives_saved_draft_serialization(self):
        content = b"From: sender@example.test\r\nTo: receiver@example.test\r\nSubject: Embedded\r\n\r\nBody\r\n"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "forwarded.eml")
            with open(path, "wb") as handle:
                handle.write(content)
            msg = mail.create_message(
                ACCOUNT, "receiver@example.test", "Subject", "Body", attachments=[path]
            )
        parsed = BytesParser(policy=policy.default).parsebytes(
            msg.as_bytes(policy=policy.SMTP)
        )
        attachment = list(mail._attachment_parts(parsed))[0]
        self.assertEqual(attachment.get("Content-Transfer-Encoding"), "8bit")
        self.assertEqual(mail._attachment_bytes(attachment), content)


if __name__ == "__main__":
    unittest.main()
