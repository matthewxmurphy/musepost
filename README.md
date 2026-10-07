<p align="center">
  <img src="https://raw.githubusercontent.com/matthewxmurphy/musepost/main/musepost-logo.png" alt="MusePost" width="400">
</p>

<h3 align="center">Every mailbox. One command.</h3>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/version-2026.10.07.02-orange.svg" alt="Version 2026.10.07.02">
  <img src="https://img.shields.io/badge/stdlib-only-green.svg" alt="Standard library only">
</p>

# MusePost

Multi-mailbox email client for AI agents and the terminal. Read, search, prepare, reply, and send across domains using IMAP and SMTP. Python 3.9 or newer; no third-party Python dependencies.

![MusePost terminal client](https://raw.githubusercontent.com/matthewxmurphy/musepost/main/musepost-terminal.png)

## Install and configure

Install from this checkout:

```sh
sh install.sh
export PATH="$HOME/.local/bin:$PATH"
musepost --version
musepost --help
```

The installer keeps the executable and its modules together under `~/.local/lib/musepost`, with a launcher at `~/.local/bin/musepost`. An optional prefix works for other locations: `sh install.sh /path/to/prefix`. Set `MUSEPOST_PYTHON` to an interpreter executable when you need a specific Python installation.

Create a private account file from the placeholder example, then edit it with your mailbox credentials:

```sh
mkdir -p "$HOME/.config/musepost"
chmod 700 "$HOME/.config/musepost"
install -m 600 example-config.json "$HOME/.config/musepost/accounts.json"
```

```json
{
  "accounts": {
    "support@example.test": {
      "password": "REPLACE_WITH_MAILBOX_PASSWORD",
      "imap_host": "mail.example.test",
      "imap_port": 993,
      "smtp_host": "mail.example.test",
      "smtp_port": 587,
      "smtp_security": "starttls",
      "sent_folder": "Sent"
    }
  }
}
```

Use the full email address as the account key. Account identifiers accept a full address or a domain when exactly one configured mailbox matches that domain. IMAP uses TLS. SMTP supports `"starttls"` (usually port 587) and `"ssl"` (usually port 465). Omit `sent_folder` to discover a server-designated Sent folder; use a string to select one, or `null` to disable Sent-copy filing.

Configuration selection follows this order:

1. `--config /path/to/accounts.json`.
2. `MUSEPOST_CONFIG`.
3. `~/.config/musepost/accounts.json` (or the corresponding `XDG_CONFIG_HOME` location).
4. The legacy `/home/taylor/TAYLOR-ACCESS.txt`, only when the default JSON account file is absent.

The credential file must be a regular file owned by the current user and allow owner-only access; use mode `0600`. Existing legacy access-file blocks remain supported, including arbitrary mailbox usernames. The MusePost config directory and JSON signature, display-name, and server-override files must also belong to the current user and be writable only by that user. Modes `0700` for the directory and `0644` or `0600` for those files work; config-file and MusePost-directory symlinks are rejected. Do not commit credentials, downloaded mail, local drafts, or receipts. See [RUNBOOK.md](RUNBOOK.md) for setup, verification, and recovery.

## Read and search

```sh
musepost inboxes
musepost unread example.test --limit 20
musepost list example.test --limit 10
musepost read example.test 12
musepost peek example.test 12 --json
musepost read example.test 12 --mark-read
musepost read example.test 12 --limit 8000 --json
musepost search example.test 'a "quoted" café' --json
musepost folders example.test
musepost list example.test --folder Archive --limit 10
musepost attachment example.test 12 1 --output /path/to/download.pdf
```

`read` and `peek` return the complete body without marking the message seen. `--mark-read` makes that change explicit. An optional `--limit` on `read` limits body characters and reports truncation; on `list`, `unread`, and `search`, it limits message count. The legacy `musepost list example.test 10` form still works.

Messages expose attachment metadata with 1-based indices. Download an attachment using the index shown by `read` and an explicit destination. Folder selection uses `--folder`; message UIDs belong to the selected folder, so pass the same folder when reading, replying, or downloading attachments from it. Mailbox and search failures return errors instead of zero-message results.

## Prepare, draft, and send

Previewing a new message builds it locally without sending it:

```sh
musepost send example.test --to friend@example.test --subject "Hello" \
  --preview --body - <<'EOF_MESSAGE'
Your message here.
EOF_MESSAGE
```

Save a local draft with `--draft`, then inspect it before submitting:

```sh
musepost send example.test --to friend@example.test --subject "Hello" \
  --draft --body "Your message here."
musepost drafts list
musepost drafts show DRAFT_ID --json
musepost drafts send DRAFT_ID --json
musepost drafts delete DRAFT_ID
```

Normal `send`, `reply`, and `drafts send` submit mail. `--preview` and `--draft` do not submit it. A reply preview or draft reads the original message to prepare its recipient and threading, without marking it answered. Drafts preserve the built message, including its Message-ID and attachment content; use `drafts show` to review that saved content. Each saved draft uses its own persistent submission key, so repeating `drafts send` returns the existing outcome.

For a direct send or reply:

```sh
musepost send example.test --to friend@example.test --subject "Hello" \
  --request-id hello-001 --json --body - <<'EOF_MESSAGE'
Your message here.
EOF_MESSAGE

musepost reply example.test 12 --request-id reply-12-001 \
  --body "Thanks for the update." --json
```

`--body -` reads stdin. Repeat `--attach /path/to/file` to add outgoing attachments; `send` also accepts `--cc ADDRESSES`. Replies use `Reply-To` when present and `From` otherwise, require valid recipient addresses, and preserve `In-Reply-To` and `References`. An invalid reply address stops preparation so it can be corrected. The selected mailbox supplies the sender identity. Reply flag updates and Sent-copy filing occur after SMTP acceptance; their failure appears as a warning and preserves the send receipt.

Use a stable, unique `--request-id` for each logical send. Repeating the same request and content returns the existing receipt without resubmitting; using that ID for different content is an error. Inspect receipts with `musepost receipts` or `musepost receipts REQUEST_ID --json`.

Receipts distinguish `accepted`, `partial`, `rejected`, and `uncertain` SMTP outcomes, with accepted and rejected recipients, Message-ID, warnings, and Sent-copy status. **SMTP acceptance is not confirmation of delivery.** Never automatically retry a partial or uncertain submission. If the connection or process ends during submission, use the receipt and server evidence to resolve what happened before creating a new request. A missing Sent copy or failed reply-flag update does not authorize resending.

## Signatures, display names, and server overrides

Existing files under `~/.config/musepost` (or `XDG_CONFIG_HOME/musepost`) remain supported:

- `signatures.json`: text/HTML signatures and named variants per address.
- `display_names.json`: friendly sender names, for example `{"support@example.test": "Example Support"}`.
- `servers.json`: per-address IMAP/SMTP settings for legacy configurations.

```sh
musepost signature set support@example.test --name desktop \
  --text "Example Support | example.test" --html-file /path/to/signature.html --default
musepost signature show support@example.test
musepost signature clear support@example.test --name desktop
```

The configured default signature is chosen consistently. `signature set ... --default` selects the default variant. Use `--signature NAME` to choose another variant for a message, or `--no-signature` to skip it. An HTML signature produces a multipart plain-text/HTML message. Legacy flat signature entries remain supported.

## JSON and automation

`--json`, `--config`, and `--timeout` work before or after the subcommand:

```sh
musepost --config /path/to/accounts.json --json inboxes
musepost read example.test 12 --json --config /path/to/accounts.json
```

JSON output includes `schema_version: 1` and command data. Errors include `error: {"code": "...", "message": "...", "details": ...}`; credentials are redacted. Connections default to a 30-second timeout; use `--timeout SECONDS` with a positive value to change it.

Send exit codes are `0` for accepted, `2` for partial acceptance, `1` for rejected/failed, and `3` for uncertain. Argument errors also exit `2`; inspect the JSON status or error code to distinguish them. Read-only and local operations return `0` on success and a nonzero code on failure. Always inspect receipt status before deciding any follow-up action.

Local drafts and receipts are stored under `~/.local/state/musepost` (or `XDG_STATE_HOME/musepost`). `MUSEPOST_STATE_DIR` overrides this location. These private files can contain full message content and recipient addresses; keep them outside source repositories and protect their backups.

## Upgrading from 2026.10.07.01

Use Python 3.9 or newer and install the complete package with `install.sh`. The command now imports its bundled modules and reads `VERSION`; an upgrade that copies only the executable is incomplete.

JSON consumers of `inboxes` and `unread` must now read mailbox entries from `feed["accounts"][address]`, replacing the previous `feed[address]` lookup. The top level also includes `schema_version: 1`, and mailbox errors contain structured `code`, `message`, and `details` fields. Other commands add the schema marker to their command data. Check exit codes as well as JSON results.

`read` now returns the full body and preserves the unread flag. Add `--mark-read` when your workflow needs that change, or `--limit 8000` when it needs a bounded body. Signatures follow the configured default consistently.

## Verification and changelog

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q musepost musepost_mail.py musepost_config.py musepost_state.py tests
sh -n install.sh
```

The tests use fake IMAP/SMTP transports and temporary configuration/state. GitHub Actions runs them on Python 3.9 and 3.14. These checks exercise client behavior without accessing a live mailbox or sending mail. Live installation and delivery need separate verification in the intended environment.

See [CHANGELOG.md](CHANGELOG.md) for release notes and [RUNBOOK.md](RUNBOOK.md) for operational checks. `VERSION` is canonical; revisions use `YYYY.MM.DD.BUILD`, retaining this project's existing calendar format.

## Developed For

**Muse.ai agent emails** — built to give an AI agent access to multiple site mailboxes, with the correct identity for each domain, from the command line.

## Credits

Taylor & Matthew Murphy — [net30hosting.com](https://net30hosting.com)
