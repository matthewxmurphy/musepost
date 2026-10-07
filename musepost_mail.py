"""Credential-safe IMAP/SMTP and MIME helpers for MusePost.

Copyright (c) 2026 MatthewxMurphy.com. Original credits: Taylor & Matthew Murphy.
Only explicit callers can submit mail or change mailbox flags. SMTP acceptance
is a submission receipt, not evidence that a recipient received the message.
"""

import base64
import contextlib
import html.parser
import imaplib
import mimetypes
import os
import re
import smtplib
import ssl
import time
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr, formatdate, getaddresses, make_msgid


class MailError(Exception):
    """A stable error code and safe details for a CLI or agent consumer."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _checked(result, code, message):
    typ, data = result
    if str(typ).upper() != "OK":
        raise MailError(code, message, {"response_status": str(typ)})
    return data


@contextlib.contextmanager
def open_imap(addr, pw, server, timeout=30):
    """Log in over verified TLS and always close without masking an outcome."""
    im = None
    try:
        im = imaplib.IMAP4_SSL(
            server["imap_host"],
            int(server["imap_port"]),
            ssl_context=ssl.create_default_context(),
            timeout=timeout,
        )
        _checked(im.login(addr, pw), "imap_auth_failed", "IMAP authentication failed")
        yield im
    except MailError:
        raise
    except (imaplib.IMAP4.error, OSError, UnicodeError):
        # Do not echo protocol exceptions: providers may include account data.
        raise MailError("imap_failed", "IMAP operation failed") from None
    finally:
        if im is not None:
            try:
                im.logout()
            except Exception:
                try:
                    im.shutdown()
                except Exception:
                    pass


def _folder_wire(folder):
    """Encode a mailbox using RFC 3501 modified UTF-7, then quote it."""
    if not isinstance(folder, str) or not folder or "\r" in folder or "\n" in folder:
        raise MailError("invalid_folder", "A nonempty mailbox name is required")
    out, pending = [], []

    def flush():
        if pending:
            chunk = "".join(pending).encode("utf-16-be")
            out.append(
                "&"
                + base64.b64encode(chunk).decode("ascii").rstrip("=").replace("/", ",")
                + "-"
            )
            pending.clear()

    for char in folder:
        if " " <= char <= "~":
            flush()
            out.append("&-" if char == "&" else char)
        else:
            pending.append(char)
    flush()
    return '"' + "".join(out).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _folder_text(value):
    value = (
        value.decode("ascii", errors="replace") if isinstance(value, bytes) else value
    )
    if value.startswith('"') and value.endswith('"'):
        value = re.sub(r"\\(.)", r"\1", value[1:-1])

    def decode(match):
        data = match.group(1)
        if not data:
            return "&"
        try:
            encoded = data.replace(",", "/")
            return base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode(
                "utf-16-be"
            )
        except (ValueError, UnicodeError):
            return match.group(0)

    return re.sub(r"&([^-]*)-", decode, value)


def _select(im, folder, readonly=True):
    _checked(
        im.select(_folder_wire(folder), readonly=readonly),
        "imap_select_failed",
        "Could not select the requested mailbox",
    )
    typ, values = im.response("UIDVALIDITY")
    if values and values[0] is not None:
        value = values[0]
        value = (
            value.decode("ascii", errors="replace")
            if isinstance(value, bytes)
            else str(value)
        )
        if value.isdigit() and int(value) > 0:
            return value
    raise MailError(
        "imap_invalid_response", "Selected mailbox omitted a valid UIDVALIDITY"
    )


def _uid_value(uid):
    value = uid.decode("ascii") if isinstance(uid, bytes) else str(uid)
    if not value.isdigit() or int(value) < 1:
        raise MailError("invalid_uid", "Message UID must be a positive integer")
    return value


def _search(im, query=None, unread=False):
    if query is None:
        args = (None, "UNSEEN" if unread else "ALL")
    else:
        if "\r" in query or "\n" in query or "\x00" in query:
            raise MailError(
                "invalid_query", "Search cannot contain line breaks or NUL bytes"
            )
        if not query.isascii():
            # RFC 3501 quoted strings are seven-bit. imaplib supports one
            # synchronizing literal at the end of a command, so union three
            # field searches instead of embedding invalid UTF-8 quoted strings.
            matches = set()
            for field in ("SUBJECT", "FROM", "BODY"):
                try:
                    im.literal = query.encode("utf-8")
                    prefix = ("UNSEEN",) if unread else ()
                    data = _checked(
                        im.uid("search", None, "CHARSET", "UTF-8", *prefix, field),
                        "imap_search_failed",
                        "Mailbox search failed",
                    )
                    matches.update(data[0].split() if data and data[0] else [])
                finally:
                    # A failed command must not leave a payload attached to the
                    # next operation (including LOGOUT).
                    im.literal = None
            if any(not value.isdigit() or int(value) < 1 for value in matches):
                raise MailError(
                    "imap_invalid_response", "Mailbox search returned an invalid UID"
                )
            return sorted(matches, key=int)
        quoted = query.replace("\\", "\\\\").replace('"', '\\"')
        criteria = '(OR (OR (SUBJECT "{q}") (FROM "{q}")) (BODY "{q}"))'.format(
            q=quoted
        )
        if unread:
            criteria = "UNSEEN " + criteria
        args = (None, "CHARSET", "UTF-8", criteria)
    data = _checked(
        im.uid("search", *args), "imap_search_failed", "Mailbox search failed"
    )
    return data[0].split() if data and data[0] else []


def inbox_status(addr, pw, server, timeout=30):
    with open_imap(addr, pw, server, timeout) as im:
        _select(im, "INBOX")
        return {"unread": len(_search(im, unread=True))}


def list_messages(
    addr, pw, server, folder="INBOX", limit=10, query=None, unread=False, timeout=30
):
    if not isinstance(limit, int) or limit < 0:
        raise MailError("invalid_limit", "Message limit must be a nonnegative integer")
    with open_imap(addr, pw, server, timeout) as im:
        uidvalidity = _select(im, folder)
        uids = _search(im, query, unread)
        selected = uids[-limit:][::-1] if limit else []
        found = {}
        if selected:
            # One round trip for all selected headers, with PEEK to preserve flags.
            sequence = b",".join(selected)
            data = _checked(
                im.uid(
                    "fetch",
                    sequence,
                    "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",
                ),
                "imap_fetch_failed",
                "Message header retrieval failed",
            )
            for row in data or []:
                if (
                    not isinstance(row, tuple)
                    or len(row) < 2
                    or not isinstance(row[1], bytes)
                ):
                    continue
                metadata = row[0]
                match = re.search(rb"\bUID (\d+)\b", metadata, flags=re.I)
                if not match:
                    raise MailError(
                        "imap_invalid_response",
                        "Message header response omitted its UID",
                    )
                uid = match.group(1).decode("ascii")
                msg = BytesParser(policy=policy.default).parsebytes(row[1])
                found[uid] = {
                    "uid": uid,
                    "from": str(msg.get("From", "")),
                    "subject": str(msg.get("Subject", "")) or "(no subject)",
                    "date": str(msg.get("Date", "")),
                    "seen": b"\\Seen" in imaplib.ParseFlags(metadata),
                }
        return {
            "account": addr,
            "folder": folder,
            "uidvalidity": uidvalidity,
            "messages": [
                found[u.decode("ascii")] for u in selected if u.decode("ascii") in found
            ],
            "total": len(uids),
        }


def _fetch_message(im, uid, mark_read=False):
    uid = _uid_value(uid)
    data = _checked(
        im.uid("fetch", uid, "(BODY[])" if mark_read else "(BODY.PEEK[])"),
        "imap_fetch_failed",
        "Message retrieval failed",
    )
    for row in data or []:
        if isinstance(row, tuple) and len(row) > 1 and isinstance(row[1], bytes):
            return BytesParser(policy=policy.default).parsebytes(row[1])
    raise MailError(
        "message_not_found", "Message UID was not found in the selected mailbox"
    )


class _HTMLText(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif not self.hidden and tag in (
            "br",
            "p",
            "div",
            "li",
            "tr",
            "h1",
            "h2",
            "h3",
        ):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _text_body(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, UnicodeError):
        text = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
    if not isinstance(text, str):
        return ""
    if part.get_content_type() == "text/html":
        parser = _HTMLText()
        parser.feed(text)
        parser.close()
        return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
    return text


def _attachment_parts(msg):
    # Stop at an attached message/container so its contents are not counted twice.
    if msg.get_content_disposition() == "attachment" or msg.get_filename():
        yield msg
        return
    for part in msg.iter_parts() if msg.is_multipart() else ():
        if part.get_content_disposition() == "attachment" or part.get_filename():
            yield part
        elif part.is_multipart():
            yield from _attachment_parts(part)


def _attachment_bytes(part):
    value = part.get_payload(decode=True)
    if value is not None:
        return value
    payload = part.get_payload()
    if isinstance(payload, list):
        return b"\r\n".join(p.as_bytes(policy=policy.SMTP) for p in payload)
    return str(payload or "").encode("utf-8")


def _safe_filename(value, index):
    # Strip both POSIX and Windows path components from untrusted MIME names.
    value = (value or "").replace("\\", "/").rsplit("/", 1)[-1]
    value = "".join(c for c in value if ord(c) >= 32 and ord(c) != 127)
    value = value.strip()
    return (
        value if value and value not in (".", "..") else "attachment-{}".format(index)
    )


def read_message(
    addr, pw, server, uid, folder="INBOX", mark_read=False, max_chars=None, timeout=30
):
    if max_chars is not None and (not isinstance(max_chars, int) or max_chars < 0):
        raise MailError("invalid_limit", "Body limit must be a nonnegative integer")
    with open_imap(addr, pw, server, timeout) as im:
        uidvalidity = _select(im, folder, readonly=not mark_read)
        msg = _fetch_message(im, uid, mark_read)
    body = _text_body(msg)
    size = len(body)
    truncated = max_chars is not None and size > max_chars
    attachments = [
        {
            "index": index,
            "filename": _safe_filename(part.get_filename(), index),
            "content_type": part.get_content_type(),
            "size": len(_attachment_bytes(part)),
        }
        for index, part in enumerate(_attachment_parts(msg), 1)
    ]
    return {
        "account": addr,
        "folder": folder,
        "uid": _uid_value(uid),
        "uidvalidity": uidvalidity,
        "message_id": str(msg.get("Message-ID", "")),
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "cc": str(msg.get("Cc", "")),
        "reply_to": str(msg.get("Reply-To", "")),
        "date": str(msg.get("Date", "")),
        "subject": str(msg.get("Subject", "")),
        "body": body[:max_chars] if max_chars is not None else body,
        "truncated": truncated,
        "body_chars": size,
        "attachments": attachments,
    }


def reply_source(addr, pw, server, uid, folder="INBOX", timeout=30):
    with open_imap(addr, pw, server, timeout) as im:
        uidvalidity = _select(im, folder)
        msg = _fetch_message(im, uid)
    msg._musepost_uidvalidity = uidvalidity
    return msg, _text_body(msg)


def _addresses(value):
    values = [value] if isinstance(value, str) else list(value or [])
    if any("\r" in str(v) or "\n" in str(v) for v in values):
        raise MailError(
            "invalid_recipient", "Recipient headers cannot contain line breaks"
        )
    probe = EmailMessage(policy=policy.default)
    try:
        probe["To"] = ", ".join(str(v) for v in values)
        if probe["To"].defects:
            raise ValueError("invalid address header")
        parsed = getaddresses([str(probe["To"])])
        result = []
        for name, addr in parsed:
            if not addr:
                continue
            checked = Address(addr_spec=addr)
            if not checked.username or not checked.domain:
                raise ValueError("incomplete address")
            if addr not in [a for _, a in result]:
                result.append((name, addr))
        if not result:
            raise ValueError("no recipients")
        return result
    except (ValueError, IndexError, TypeError):
        raise MailError(
            "invalid_recipient", "A valid email address is required"
        ) from None


def reply_recipients(orig):
    header = orig.get("Reply-To") or orig.get("From")
    return [addr for _, addr in _addresses(str(header or ""))]


def create_message(
    addr,
    to,
    subject,
    body,
    display_name=None,
    signature=None,
    attachments=None,
    in_reply_to=None,
    references=None,
    cc=None,
):
    sender = _addresses(addr)
    if len(sender) != 1:
        raise MailError("invalid_sender", "Exactly one sender address is required")
    recipients = _addresses(to)
    copy_recipients = _addresses(cc) if cc else []
    msg = EmailMessage(policy=policy.default)
    try:
        msg["From"] = (
            formataddr((display_name, sender[0][1])) if display_name else sender[0][1]
        )
        msg["To"] = ", ".join(
            formataddr((name, address)) for name, address in recipients
        )
        if copy_recipients:
            msg["Cc"] = ", ".join(
                formataddr((name, address)) for name, address in copy_recipients
            )
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=sender[0][1].rsplit("@", 1)[1])
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references
    except (ValueError, UnicodeError):
        raise MailError("invalid_header", "Message headers are invalid") from None
    sig = signature or {}
    text = body + ("\n" + sig["text"] if sig.get("text") else "")
    msg.set_content(text)
    if sig.get("html"):
        import html

        body_html = html.escape(body).replace("\n", "<br>\n")
        msg.add_alternative(
            "<div>" + body_html + "</div>\n" + sig["html"], subtype="html"
        )
    for path in attachments or []:
        content_type, encoding = mimetypes.guess_type(os.fspath(path))
        if encoding or not content_type:
            content_type = "application/octet-stream"
        maintype, subtype = content_type.split("/", 1)
        try:
            with open(path, "rb") as handle:
                content = handle.read()
        except OSError:
            raise MailError(
                "attachment_unreadable", "Could not read an attachment"
            ) from None
        # message/rfc822 forbids base64/quoted-printable. An 8bit embedded email
        # survives draft serialization and remains readable by MIME clients.
        transfer = {"cte": "8bit"} if content_type == "message/rfc822" else {}
        msg.add_attachment(
            content,
            maintype=maintype,
            subtype=subtype,
            filename=os.path.basename(os.fspath(path)),
            **transfer,
        )
    return msg


def _response_text(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).replace("\r", " ").replace("\n", " ")[:500]


def submit_message(addr, pw, server, msg, timeout=30):
    """Submit once; return acceptance evidence and never infer delivery."""
    message_id = str(msg.get("Message-ID", ""))
    if not message_id:
        raise MailError("invalid_message", "A Message-ID is required before submission")
    recipient_headers = [
        str(msg.get(name, "")) for name in ("To", "Cc", "Bcc") if msg.get(name)
    ]
    recipients = [a for _, a in _addresses(recipient_headers)]
    smtp = None
    send_started = False
    refused = None
    try:
        security = server.get("smtp_security", "starttls")
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(
                server["smtp_host"],
                int(server["smtp_port"]),
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        elif security == "starttls":
            smtp = smtplib.SMTP(
                server["smtp_host"], int(server["smtp_port"]), timeout=timeout
            )
            smtp.starttls(context=ssl.create_default_context())
        else:
            raise MailError("invalid_server", "SMTP security must be starttls or ssl")
        smtp.login(addr, pw)
        send_started = True
        refused = smtp.send_message(msg, from_addr=addr, to_addrs=recipients)
    except smtplib.SMTPRecipientsRefused as exc:
        refused = exc.recipients
    except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
        raise MailError(
            "smtp_rejected",
            "SMTP server rejected the message",
            {
                "message_id": message_id,
                "status": "rejected",
                "smtp_code": exc.smtp_code,
            },
        ) from None
    except (smtplib.SMTPNotSupportedError, smtplib.SMTPHeloError):
        raise MailError(
            "smtp_not_submitted",
            "SMTP server does not support the required submission",
            {"message_id": message_id, "status": "not_submitted"},
        ) from None
    except MailError:
        raise
    except (smtplib.SMTPException, OSError, UnicodeError):
        status = "uncertain" if send_started else "not_submitted"
        message = (
            "SMTP submission outcome is uncertain; do not retry automatically"
            if send_started
            else "SMTP connection or authentication failed"
        )
        raise MailError(
            "smtp_" + status, message, {"message_id": message_id, "status": status}
        ) from None
    finally:
        # QUIT failures after successful DATA cannot turn an acceptance into a
        # failed-send result. Avoid context-manager __exit__ masking that receipt.
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass
            try:
                smtp.close()
            except Exception:
                pass
    refused = refused or {}
    rejected = {
        a: {"code": value[0], "message": _response_text(value[1])}
        for a, value in refused.items()
    }
    accepted = [a for a in recipients if a not in refused]
    status = (
        "partial" if accepted and rejected else "accepted" if accepted else "rejected"
    )
    return {
        "status": status,
        "message_id": message_id,
        "accepted": accepted,
        "rejected": rejected,
        "delivery": "unconfirmed",
    }


def _folders(im):
    data = _checked(im.list(), "imap_list_failed", "Mailbox listing failed")
    result = []
    for row in data or []:
        if row is None:
            continue
        line, literal = (row[0], row[1]) if isinstance(row, tuple) else (row, None)
        match = re.match(rb'^\(([^)]*)\)\s+(NIL|"(?:\\.|[^"\\])*")\s+(.+)$', line)
        if not match:
            raise MailError(
                "imap_invalid_response", "Mailbox listing response was invalid"
            )
        result.append(
            {
                "name": _folder_text(
                    literal if literal is not None else match.group(3)
                ),
                "flags": match.group(1).decode("ascii", errors="replace").split(),
                "delimiter": None
                if match.group(2) == b"NIL"
                else _folder_text(match.group(2)),
            }
        )
    return result


def folders(addr, pw, server, timeout=30):
    with open_imap(addr, pw, server, timeout) as im:
        return _folders(im)


def archive_sent(addr, pw, server, msg, timeout=30):
    if "sent_folder" in server and not server["sent_folder"]:
        return {"status": "disabled", "folder": None}
    with open_imap(addr, pw, server, timeout) as im:
        folder = server.get("sent_folder")
        if not folder:
            candidates = [
                row["name"]
                for row in _folders(im)
                if any(flag.lower() == "\\sent" for flag in row["flags"])
            ]
            if len(candidates) != 1:
                return {"status": "unavailable", "folder": None}
            folder = candidates[0]
        _checked(
            im.append(
                _folder_wire(folder),
                "\\Seen",
                imaplib.Time2Internaldate(time.time()),
                msg.as_bytes(policy=policy.SMTP),
            ),
            "sent_archive_failed",
            "Message was submitted but its Sent copy could not be saved",
        )
        return {"status": "saved", "folder": folder}


def mark_answered(addr, pw, server, uid, folder="INBOX", timeout=30, uidvalidity=None):
    with open_imap(addr, pw, server, timeout) as im:
        current = _select(im, folder, readonly=False)
        if uidvalidity is not None and str(current) != str(uidvalidity):
            raise MailError(
                "uidvalidity_changed",
                "Mailbox identity changed; the original message flag was not updated",
            )
        _checked(
            im.uid("store", _uid_value(uid), "+FLAGS.SILENT", "(\\Answered)"),
            "answer_flag_failed",
            "Reply was submitted but its Answered flag could not be saved",
        )


def get_attachment(addr, pw, server, uid, index, folder="INBOX", timeout=30):
    if not isinstance(index, int) or index < 1:
        raise MailError("invalid_attachment", "Attachment indexes start at 1")
    with open_imap(addr, pw, server, timeout) as im:
        _select(im, folder)
        msg = _fetch_message(im, uid)
    parts = list(_attachment_parts(msg))
    if index > len(parts):
        raise MailError("attachment_not_found", "Attachment index was not found")
    part = parts[index - 1]
    return _safe_filename(part.get_filename(), index), _attachment_bytes(part)
