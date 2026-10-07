"""Local receipt identity regression tests; never connect to mail servers."""

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import musepost_state as state


class ReceiptIdentityTests(unittest.TestCase):
    def embedded_message(self, subject):
        inside = EmailMessage()
        inside["From"] = "vendor@example.test"
        inside["Subject"] = subject
        inside.set_content("Same attachment body")
        outer = EmailMessage()
        outer["From"] = "support@example.test"
        outer["To"] = "recipient@example.test"
        outer["Subject"] = "Invoice attachment"
        outer["Message-ID"] = "<outer@example.test>"
        outer.set_content("See attached")
        outer.add_attachment(inside, filename="invoice.eml")
        return BytesParser(policy=policy.default).parsebytes(
            outer.as_bytes(policy=policy.SMTP)
        )

    def test_changed_embedded_headers_reject_reused_request(self):
        first = self.embedded_message("Original invoice")
        second = self.embedded_message("Changed invoice")
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ, {"MUSEPOST_STATE_DIR": str(Path(directory) / "state")}
            ):
                state.claim_request("same-request", "support@example.test", first)
                with self.assertRaises(state.StateError) as caught:
                    state.claim_request("same-request", "support@example.test", second)
                self.assertEqual(caught.exception.code, "REQUEST_CONFLICT")

    def test_changed_embedded_filename_changes_fingerprint(self):
        first = self.embedded_message("Invoice")
        second = self.embedded_message("Invoice")
        attachment = next(second.iter_attachments())
        attachment.replace_header(
            "Content-Disposition", 'attachment; filename="other.eml"'
        )
        self.assertNotEqual(
            state.fingerprint("support@example.test", first),
            state.fingerprint("support@example.test", second),
        )


if __name__ == "__main__":
    unittest.main()
