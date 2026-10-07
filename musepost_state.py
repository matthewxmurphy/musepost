"""Private local drafts and submission receipts.

A request is claimed with O_EXCL *before* contacting SMTP. If the process stops
while submitting, a later invocation reports uncertainty rather than resending.
This protects retries using the same request ID; it is not a delivery guarantee
or a cross-device deduplication service.
"""

from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid


class StateError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details or {}


def now():
    return datetime.now(timezone.utc).isoformat()


def state_dir():
    override = os.environ.get("MUSEPOST_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return (
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
        / "musepost"
    )


def _directory(name):
    root = state_dir()
    for path in (root, root / name):
        try:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
                raise StateError(
                    "INSECURE_STATE",
                    "Local state directories must be owner-only; use chmod 700.",
                )
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise StateError(
                    "INSECURE_STATE", "Local state must belong to the current user."
                )
        except OSError:
            raise StateError(
                "STATE_UNAVAILABLE", "Could not access private local state."
            ) from None
    return root / name


def _read(path):
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise StateError(
                    "INSECURE_STATE", "State files must be owner-only regular files."
                )
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise StateError(
                    "INSECURE_STATE", "State files must belong to the current user."
                )
            return stream.read()
    except FileNotFoundError:
        raise StateError(
            "STATE_NOT_FOUND", "Local draft or receipt was not found."
        ) from None
    except OSError:
        raise StateError(
            "STATE_UNAVAILABLE", "Could not securely read local state."
        ) from None


def _read_json(path):
    try:
        result = json.loads(_read(path))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError):
        raise StateError("INVALID_STATE", "Local state JSON is invalid.") from None


def _sync_dir(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _exclusive(path, data):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_dir(path.parent)


def _atomic_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fingerprint(account, msg):
    # Generated Date, Message-ID and MIME boundary strings must not invalidate
    # a retry of the same content. Hash decoded MIME leaves and semantic headers.
    data = {"account": account, "headers": {}}
    for key in ("From", "To", "Cc", "Bcc", "Subject", "In-Reply-To", "References"):
        data["headers"][key] = str(msg.get(key, ""))

    def semantic_part(part, outer=False):
        result = {
            "type": part.get_content_type(),
            "charset": part.get_content_charset(),
            "filename": part.get_filename(),
            "disposition": part.get_content_disposition(),
        }
        if not outer:
            # Embedded .eml headers are attachment content. Retain their Date,
            # Message-ID and Subject while ignoring generated MIME boundaries.
            result["headers"] = [
                (key.lower(), str(value))
                for key, value in part.items()
                if key.lower()
                not in (
                    "content-type",
                    "content-transfer-encoding",
                    "mime-version",
                    "content-disposition",
                )
            ]
        if part.is_multipart():
            # Python 3.9 iter_parts() omits message/rfc822 children even though
            # is_multipart() is true; the parsed payload list covers both kinds.
            result["parts"] = [semantic_part(child) for child in part.get_payload()]
        else:
            result["content"] = hashlib.sha256(
                part.get_payload(decode=True) or b""
            ).hexdigest()
        return result

    data["mime"] = semantic_part(msg, outer=True)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def _receipt_path(request_id):
    if not isinstance(request_id, str) or not request_id or len(request_id) > 200:
        raise StateError(
            "INVALID_REQUEST_ID", "Request IDs must contain 1 to 200 characters."
        )
    key = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return _directory("receipts") / (key + ".json")


def claim_request(request_id, account, msg):
    path = _receipt_path(request_id)
    digest = fingerprint(account, msg)
    receipt = {
        "request_id": request_id,
        "account": account,
        "message_id": str(msg["Message-ID"]),
        "fingerprint": digest,
        "created_at": now(),
        "status": "submitting",
        "accepted": [],
        "rejected": {},
        "delivery": "unconfirmed",
        "warnings": [],
    }
    try:
        _exclusive(path, json.dumps(receipt).encode("utf-8"))
        return receipt, True
    except FileExistsError:
        existing = _read_json(path)
        if existing.get("fingerprint") != digest:
            raise StateError(
                "REQUEST_CONFLICT",
                "This request ID already belongs to different message content.",
            )
        if existing.get("status") == "submitting":
            existing["status"] = "uncertain"
            existing.setdefault("warnings", []).append(
                "The earlier submission was interrupted; check the server before making a new request."
            )
        existing["replayed"] = True
        return existing, False
    except OSError:
        raise StateError(
            "STATE_UNAVAILABLE",
            "Could not record the request before submission; no mail was submitted.",
        ) from None


def save_receipt(receipt):
    try:
        _atomic_json(_receipt_path(receipt["request_id"]), receipt)
    except OSError:
        raise StateError(
            "STATE_UNAVAILABLE", "Could not persist the submission outcome."
        ) from None


def get_receipt(request_id):
    receipt = _read_json(_receipt_path(request_id))
    if receipt.get("status") == "submitting":
        receipt["status"] = "uncertain"
        receipt.setdefault("warnings", []).append(
            "Submission was interrupted; the server outcome is unknown."
        )
    return receipt


def list_receipts():
    return sorted(
        (
            get_receipt(_read_json(path)["request_id"])
            for path in _directory("receipts").glob("*.json")
        ),
        key=lambda r: r.get("created_at", ""),
        reverse=True,
    )


def create_draft(account, msg, reply_context=None):
    directory = _directory("drafts")
    draft_id = uuid.uuid4().hex
    metadata = {
        "draft_id": draft_id,
        "account": account,
        "message_id": str(msg["Message-ID"]),
        "created_at": now(),
        "status": "draft",
        "reply_context": reply_context,
    }
    try:
        _exclusive(directory / (draft_id + ".eml"), msg.as_bytes(policy=policy.SMTP))
        _exclusive(
            directory / (draft_id + ".json"), json.dumps(metadata).encode("utf-8")
        )
    except OSError:
        raise StateError(
            "STATE_UNAVAILABLE", "Could not save a private local draft."
        ) from None
    return metadata


def _draft_paths(draft_id):
    if not isinstance(draft_id, str) or not re.fullmatch(r"[a-f0-9]{32}", draft_id):
        raise StateError(
            "INVALID_DRAFT_ID",
            "Draft IDs are 32-character lowercase hexadecimal strings.",
        )
    directory = _directory("drafts")
    return directory / (draft_id + ".json"), directory / (draft_id + ".eml")


def load_draft(draft_id):
    metadata_path, message_path = _draft_paths(draft_id)
    metadata = _read_json(metadata_path)
    msg = BytesParser(policy=policy.default).parsebytes(_read(message_path))
    if str(msg.get("Message-ID", "")) != metadata.get("message_id"):
        raise StateError("INVALID_STATE", "Draft metadata and message do not match.")
    return metadata, msg


def list_drafts():
    return sorted(
        (_read_json(path) for path in _directory("drafts").glob("*.json")),
        key=lambda r: r.get("created_at", ""),
        reverse=True,
    )


def delete_draft(draft_id):
    metadata_path, message_path = _draft_paths(draft_id)
    load_draft(draft_id)
    try:
        metadata_path.unlink()
        message_path.unlink()
        _sync_dir(metadata_path.parent)
    except OSError:
        raise StateError(
            "STATE_UNAVAILABLE", "Could not delete the local draft."
        ) from None
