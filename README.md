<p align="center">
  <img src="https://raw.githubusercontent.com/matthewxmurphy/musepost/main/musepost-logo.png" alt="MusePost" width="400">
</p>

<h3 align="center">Every mailbox. One command.</h3>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.8%2B-blue.svg" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/version-2026.10.07.01-orange.svg" alt="Version 2026.10.07.01">
  <img src="https://img.shields.io/badge/stdlib-only-green.svg" alt="Standard library only">
</p>

# MusePost

Multi-mailbox email client for AI agents and the terminal. Read, search, reply, and send across domains using IMAP and SMTP.

![MusePost terminal client](https://raw.githubusercontent.com/matthewxmurphy/musepost/main/musepost-terminal.png)

## Quick Start

```sh
# List all mailboxes with unread counts
musepost inboxes

# Read recent messages
musepost list net30hosting.com

# Send an email
musepost send net30hosting.com --to you@example.com --subject "Hello" --body - <<'EOF'
Your message here.
EOF
```

## Setup

1. Store a private JSON file outside this checkout, owned by the user running
   MusePost, with exact mode `0600`. Create its directory with mode `0700` and
   use `umask 077` before writing credentials. The values below are placeholders:

```json
{"accounts": {
  "owner+alerts@example.test": {"password": "replace-locally"},
  "taylor@example.test": {"password": "replace-locally"}
}}
```

2. Select that file without modifying source:

```sh
export MUSEPOST_CONFIG="$HOME/.config/musepost/accounts.json"
chmod 600 "$MUSEPOST_CONFIG"
```

3. Make `musepost` executable and put it on your `PATH`:

```sh
chmod 755 musepost
```

If `MUSEPOST_CONFIG` is unset, the historical `/home/taylor/TAYLOR-ACCESS.txt`
path remains the fallback. Legacy blocks still work with any validated bare
mailbox address:

```text
owner@example.test
Username: owner@example.test
New password: replace-locally
Mailbox quota: 128 MB
```

`Address and username: owner@example.test` is also supported. Addresses are
normalized to lowercase for compatibility. Supply bare ASCII addresses without display
names, whitespace or control characters; use ASCII/Punycode domain names.
Quoted local parts without whitespace are supported, including embedded `@`. Duplicate normalized addresses, missing
or redacted passwords, mismatched usernames and multiple passwords per block
are rejected; JSON accepts only the `accounts` map and one `password` per record.
Use full addresses when several mailboxes share a domain: a domain shortcut
fails on ambiguity instead of choosing a mailbox.

The file must be regular, singly linked, owned by the current effective user,
and exactly `0600`; final-component symlinks, directories and FIFOs are rejected
before contents are read. Keep its parent directories controlled by your user.
An invalid explicit path fails without falling back to another credential file.
No permissions or ownership are changed automatically.

**Never commit credentials.** Configuration loading itself opens no mailbox and
sends no email. Existing `send` and `reply` commands are explicit operations.

## Usage

```
musepost inboxes [--json]         list accounts with unread counts
musepost unread [account] [--json]
                                  show unread messages (all accounts, or one)
musepost list <account> [n=10] [--json]
                                  recent messages in an inbox
musepost read <account> <uid> [--json]
                                  read a full message
musepost search <account> <query> [--json]
                                  search subject/from/body on the server
musepost reply <account> <uid> --body - [--no-signature]
                                  reply with proper threading headers
musepost send <account> --to ADDR --subject SUBJ --body - [--no-signature]
              [--signature NAME] [--attach FILE]
                                  send a new message (with optional file attachment)
musepost signature set <account> [--text TXT] [--html-file PATH]
musepost signature show <account>
musepost signature clear <account>
```

`<account>` accepts the full address or just the domain
(e.g. `musepost list boldteehub.com`).

`--body -` reads the message body from stdin, so it composes well with
heredocs and pipes:

```sh
musepost send boldteehub.com --to customer@example.com --subject "Re: your order" --body - <<'EOF'
Hi there — your order is on its way.
— Taylor
EOF
```

Replies set `In-Reply-To`/`References`, quote the original, and mark the
original as answered. Every send uses the mailbox's own address as `From`.

## Signatures

```sh
# Set a signature for a mailbox (plain text and/or HTML file)
musepost signature set taylor@taylorxquinn.com --text "Taylor Quinn | taylorxquinn.com" --html-file ~/taylor-email-signature.html

# Show it, or clear it
musepost signature show taylor@taylorxquinn.com
musepost signature clear taylor@taylorxquinn.com
```

Signatures are stored per email address in `~/.config/musepost/signatures.json`
(signature rules only; passwords remain in the separate credentials file). They are applied automatically to sent messages and replies --
multipart (plain + HTML) when an HTML signature exists.
Use `--no-signature` on any send or reply to skip it.

## JSON feed

Every read command accepts `--json` and emits a machine-readable JSON feed
instead of human text -- for scripts, dashboards, and agents:

```sh
musepost unread taylorxquinn.com --json
musepost read taylorxquinn.com 12 --json
```

## Security Notes

- Credentials are read at runtime after the ownership/type/mode checks above.
  Configuration errors do not include credential contents.
- All connections use TLS (IMAP SSL on 993, SMTP STARTTLS on 587).

## Display Names

Set a friendly sender name so recipients see "Taylor Quinn" instead of a raw email address:

```sh
# ~/.config/musepost/display_names.json
{"taylor@taylorxquinn.com": "Taylor Quinn"}
```

## Per-Address Server Overrides

Override IMAP/SMTP hosts and ports per mailbox (for custom mail servers):

```sh
# ~/.config/musepost/servers.json
{
  "taylor@taylorxquinn.com": {
    "imap_host": "mail.custom.com",
    "imap_port": 993,
    "smtp_host": "mail.custom.com",
    "smtp_port": 587
  }
}
```

## Attachments

Attach files to outgoing mail:

```sh
musepost send taylorxquinn.com --to friend@example.com --subject "Listen" \
  --attach /path/to/audio.mp3 --body - <<'EOF'
Here's that recording.
EOF
```

## Changelog

### v2026.10.07.01 (2026-10-07)
- Display names: `From` header shows friendly name (e.g. "Taylor Quinn") via `~/.config/musepost/display_names.json`
- Attachments: `--attach FILE` flag on `send` for file attachments (audio, images, documents)
- Per-address server overrides: custom IMAP/SMTP hosts and ports via `~/.config/musepost/servers.json`

### v2026.10.06.02
- Per-address signature variants (desktop/iPhone)

## Versioning

Historical versions above are preserved. Future releases use `YYYY.M.DD.XXX`
(America/Chicago; unpadded month, two-digit day, daily counter `001`–`999`).
This source fix does not reserve or distribute a new release.

## Developed For

**Muse.ai agent emails** — built to give an AI agent full command of 19 site mailboxes: listing, reading, searching, replying, and sending as the correct identity for each domain, all from the command line.

## Credits

Taylor & Matthew Murphy — [net30hosting.com](https://net30hosting.com)


## Development and verification

Run the offline suite with `python3 -m unittest discover -s tests -v`.
It uses synthetic files, blocks mail connections, checks private-file rejection,
multiple mailbox identities/domain ambiguity, safe CLI errors, explicit command
dispatch and certificate validation/TLS ordering through transport doubles.
Python 3.9 and 3.14 were checked for this source change. Actual mailbox login,
provider delivery and operating-system versions beyond the test host remain
separate acceptance work. Existing message-reading and sending behavior is not
expanded by this configuration change.

See [RUNBOOK.md](RUNBOOK.md) for operations, architecture and rollback and
[CHANGELOG.md](CHANGELOG.md) for source changes. Report reproducible bugs with
sanitized fixtures through this repository's issues; propose scoped PRs with the
suite result. Do not paste credentials or private messages into issues/PRs.

Engineering SOP adoption: this scoped change follows the
[shared SOP](https://github.com/matthewxmurphy/developer-standards), with review
and exact candidate scanning. Portable checker/CI enforcement, full metadata and
complete license/notices still require a separate owner-reviewed adoption step.
No license grant is inferred from this repository's missing license file;
preserve existing author credits and obtain applicable terms before distribution.
Maintained by Matthew X Murphy, [MatthewxMurphy.com](https://MatthewxMurphy.com),
[Built Not Begged](https://builtnotbegged.com), alongside the existing credits.
