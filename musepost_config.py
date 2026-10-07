"""Credential and presentation configuration, with no network access.

Credentials remain external to source. Legacy Taylor access files are accepted
so an existing installation can upgrade without migrating passwords first.
"""

import json
import os
from pathlib import Path
import re
import stat
import tempfile


DEFAULT_SERVER = {
    "imap_host": "mail.net30hosting.com",
    "imap_port": 993,
    "smtp_host": "mail.net30hosting.com",
    "smtp_port": 587,
    "smtp_security": "starttls",
}


class ConfigError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details or {}


def config_dir():
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "musepost"
    )


def address(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[^\s@<>,;]+@[^\s@<>,;]+", value
    ):
        raise ConfigError("INVALID_ACCOUNT", "Expected a mailbox email address.")
    return value.lower()


def read_json(path, optional=False):
    path = Path(path)
    try:
        # These files may not contain passwords, but server overrides decide
        # which host receives them. Do not trust externally writable routing.
        if path.parent.exists():
            _trusted_directory(path.parent)
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                raise ConfigError(
                    "INSECURE_CONFIG",
                    "Configuration files must be regular files writable only by their owner.",
                )
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise ConfigError(
                    "INSECURE_CONFIG", "Configuration must belong to the current user."
                )
            data = json.load(stream)
    except FileNotFoundError:
        if optional:
            return {}
        raise ConfigError(
            "CONFIG_NOT_FOUND", "Configuration file was not found."
        ) from None
    except OSError:
        raise ConfigError(
            "CONFIG_UNREADABLE", "Could not securely read configuration."
        ) from None
    except (UnicodeError, ValueError):
        raise ConfigError(
            "INVALID_CONFIG", "Configuration must contain valid UTF-8 JSON."
        ) from None
    if not isinstance(data, dict):
        raise ConfigError("INVALID_CONFIG", "Configuration must be a JSON object.")
    return data


def _trusted_directory(path):
    info = Path(path).lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        raise ConfigError(
            "INSECURE_CONFIG",
            "Configuration directories must be writable only by their owner.",
        )
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ConfigError(
            "INSECURE_CONFIG",
            "Configuration directories must belong to the current user.",
        )


def _credential_text(path):
    # Validate the descriptor we read, rather than checking one file and opening
    # a different file after a symlink or path replacement.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
        with os.fdopen(fd, encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ConfigError(
                    "INSECURE_CONFIG",
                    "Credentials must be an owner-only regular file; use chmod 600.",
                )
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise ConfigError(
                    "INSECURE_CONFIG", "Credentials must belong to the current user."
                )
            return stream.read()
    except ConfigError:
        raise
    except FileNotFoundError:
        raise ConfigError(
            "CONFIG_NOT_FOUND",
            "No credentials file found. Use --config or MUSEPOST_CONFIG.",
        ) from None
    except (OSError, UnicodeError):
        raise ConfigError(
            "CONFIG_UNREADABLE", "Could not securely read the credentials file."
        ) from None


def _legacy_accounts(text):
    accounts, current, password = {}, None, None

    def finish():
        if current:
            if password is None:
                raise ConfigError("INVALID_CONFIG", "A mailbox block has no password.")
            if current in accounts:
                raise ConfigError(
                    "INVALID_CONFIG", "Duplicate mailbox blocks are not allowed."
                )
            accounts[current] = {"password": password}

    for raw in text.splitlines():
        line = raw.strip()
        match = re.fullmatch(
            r"(?:address and username:\s*)?([^\s@]+@[^\s@]+)", line, re.I
        )
        if match:
            finish()
            current, password = address(match.group(1)), None
            continue
        match = re.fullmatch(r"Username:\s*(\S+)", line, re.I)
        if match:
            username = address(match.group(1))
            if current is None:
                current = username
            elif username != current:
                raise ConfigError(
                    "INVALID_CONFIG", "Mailbox address and username must match."
                )
            continue
        match = re.fullmatch(r"(?:new\s+)?password:\s*(.*)", line, re.I)
        if match:
            if current is None or password is not None:
                raise ConfigError(
                    "INVALID_CONFIG", "Password appears outside a valid mailbox block."
                )
            password = match.group(1)
            if not password or password.lower() == "<redacted>":
                raise ConfigError("INVALID_CONFIG", "Mailbox password is missing.")
    finish()
    return accounts


def _server(raw):
    if not isinstance(raw, dict):
        raise ConfigError("INVALID_CONFIG", "Each server override must be an object.")
    result = dict(DEFAULT_SERVER)
    result.update(raw)
    for key in ("imap_host", "smtp_host"):
        value = result[key]
        if not isinstance(value, str) or not value or any(c.isspace() for c in value):
            raise ConfigError(
                "INVALID_CONFIG", "Mail server hosts must be nonempty hostnames."
            )
    for key in ("imap_port", "smtp_port"):
        value = result[key]
        if type(value) is not int or not 1 <= value <= 65535:
            raise ConfigError(
                "INVALID_CONFIG", "Mail ports must be integers from 1 to 65535."
            )
    if result["smtp_security"] not in ("starttls", "ssl"):
        raise ConfigError("INVALID_CONFIG", "smtp_security must be starttls or ssl.")
    if "sent_folder" in result and result["sent_folder"] is not None:
        if not isinstance(result["sent_folder"], str) or not result["sent_folder"]:
            raise ConfigError(
                "INVALID_CONFIG", "sent_folder must be a folder name or null."
            )
    return result


def load_accounts(path=None):
    chosen = path or os.environ.get("MUSEPOST_CONFIG")
    if chosen is None:
        candidate = config_dir() / "accounts.json"
        chosen = (
            candidate
            if candidate.exists() or candidate.is_symlink()
            else "/home/taylor/TAYLOR-ACCESS.txt"
        )
    text = _credential_text(Path(chosen).expanduser())
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            raise ConfigError(
                "INVALID_CONFIG", "Credentials must contain valid JSON."
            ) from None
        if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
            raise ConfigError(
                "INVALID_CONFIG", "JSON credentials require an accounts object."
            )
        records = data["accounts"]
    else:
        records = _legacy_accounts(text)
    if not records:
        raise ConfigError("INVALID_CONFIG", "No mailbox accounts found.")
    servers = read_json(config_dir() / "servers.json", optional=True)
    names = read_json(config_dir() / "display_names.json", optional=True)
    accounts = {}
    for raw_addr, record in records.items():
        addr = address(raw_addr)
        if addr in accounts or not isinstance(record, dict):
            raise ConfigError(
                "INVALID_CONFIG", "Mailbox records must be unique objects."
            )
        password = record.get("password")
        if not isinstance(password, str) or not password:
            raise ConfigError(
                "INVALID_CONFIG", "Every mailbox requires a nonempty password."
            )
        override = servers.get(addr, {})
        if not isinstance(override, dict):
            raise ConfigError("INVALID_CONFIG", "Server override must be an object.")
        combined = dict(override)
        combined.update(record)
        account = _server(combined)
        name = record.get("display_name", names.get(addr))
        if name is not None and (
            not isinstance(name, str) or "\n" in name or "\r" in name
        ):
            raise ConfigError(
                "INVALID_CONFIG", "Display names must be single-line strings."
            )
        account["display_name"] = name
        accounts[addr] = account
    return accounts


def resolve(accounts, ident):
    ident = ident.lower()
    if ident in accounts:
        return ident
    matches = [a for a in accounts if a.endswith("@" + ident)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ConfigError("UNKNOWN_ACCOUNT", "No mailbox matches this account.")
    raise ConfigError(
        "AMBIGUOUS_ACCOUNT",
        "Use the full email address when a domain has several mailboxes.",
    )


def _normalize_signature(entry):
    if entry is None:
        return {"variants": {}, "default": "desktop"}
    if not isinstance(entry, dict):
        raise ConfigError("INVALID_CONFIG", "Signature entries must be objects.")
    if "variants" in entry:
        variants = entry["variants"]
        default = entry.get("default", "desktop")
    else:
        variants = {
            "desktop": {"text": entry.get("text", ""), "html": entry.get("html", "")}
        }
        default = "desktop"
    if not isinstance(variants, dict) or not isinstance(default, str):
        raise ConfigError("INVALID_CONFIG", "Invalid signature variants or default.")
    for name, variant in variants.items():
        if not isinstance(variant, dict) or not all(
            isinstance(variant.get(k, ""), str) for k in ("text", "html")
        ):
            raise ConfigError(
                "INVALID_CONFIG", "Signature text and HTML must be strings."
            )
    if variants and default not in variants:
        default = sorted(variants)[0]
    return {"variants": variants, "default": default}


def load_signatures():
    return read_json(config_dir() / "signatures.json", optional=True)


def signatures_for(addr):
    return _normalize_signature(load_signatures().get(addr.lower()))


def get_signature(addr, name=None):
    entry = signatures_for(addr)
    variants = entry["variants"]
    if name is not None and name not in variants:
        raise ConfigError(
            "UNKNOWN_SIGNATURE", "The requested signature variant does not exist."
        )
    if not variants:
        return None
    sig = variants[name or entry["default"]]
    result = {"text": sig.get("text", "").strip(), "html": sig.get("html", "").strip()}
    return result if any(result.values()) else None


def save_signatures(data):
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    _trusted_directory(directory)
    fd, temporary = tempfile.mkstemp(prefix=".signatures-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "signatures.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def set_signature(addr, name=None, text=None, html=None, default=False):
    sigs = load_signatures()
    norm = _normalize_signature(sigs.get(addr))
    name = name or norm["default"]
    current = dict(norm["variants"].get(name, {"text": "", "html": ""}))
    if text is not None:
        current["text"] = text
    if html is not None:
        current["html"] = html
    norm["variants"][name] = current
    if default:
        norm["default"] = name
    sigs[addr] = norm
    save_signatures(sigs)
    return norm


def clear_signature(addr, name=None):
    sigs = load_signatures()
    if name is None:
        sigs.pop(addr, None)
    else:
        norm = _normalize_signature(sigs.get(addr))
        norm["variants"].pop(name, None)
        if norm["variants"]:
            sigs[addr] = _normalize_signature(norm)
        else:
            sigs.pop(addr, None)
    save_signatures(sigs)
