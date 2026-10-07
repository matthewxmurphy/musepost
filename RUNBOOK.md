# MusePost runbook

## Package and installation

The source package contains `musepost`, `musepost_mail.py`, `musepost_config.py`, `musepost_state.py`, and `VERSION`. Keep these files together when running directly from a checkout. Python 3.9 or newer is required; no pip installation is needed.

Install with `sh install.sh` or `sh install.sh /path/to/prefix`. The launcher lives in `<prefix>/bin/musepost`; application files live in `<prefix>/lib/musepost`. The default prefix is `~/.local`. Set `MUSEPOST_PYTHON` to an executable when selecting a particular Python. Installation replaces the application files while configuration and mail state remain in their separate locations.

Verify the installed package without credentials or network access:

```sh
"$HOME/.local/bin/musepost" --help
"$HOME/.local/bin/musepost" --version
```

The displayed revision should match `VERSION`. Copying only `musepost` leaves its imported modules behind and is not a complete installation.

## Upgrade compatibility

This revision raises the minimum Python version from 3.8 to 3.9. Install the whole package, including its three modules and `VERSION`, using the installer or keep those files together for a direct checkout run.

For `inboxes --json` and `unread --json`, account entries moved from the root object into `accounts`. Update integrations from `feed[address]` to `feed["accounts"][address]` and check `schema_version: 1`. Errors are structured objects with `code`, `message`, and `details`; mailbox failures also produce a nonzero exit code. Other command data retains its own fields alongside the new schema marker.

The previous `read` operation marked mail seen and limited the body. It now preserves unread flags and returns the full body. Supply `--mark-read` and an optional character `--limit` explicitly when an integration requires them. Signature selection now follows the configured default.

## Configuration

Copy `example-config.json` to `~/.config/musepost/accounts.json` with mode `0600`, then edit the account addresses, passwords, and hosts. The example uses reserved `example.test` names and a password placeholder; it is not a working mailbox.

```sh
mkdir -p "$HOME/.config/musepost"
chmod 700 "$HOME/.config/musepost"
install -m 600 example-config.json "$HOME/.config/musepost/accounts.json"
```

Use `--config /absolute/path/accounts.json` or `MUSEPOST_CONFIG` for another file. Default config honors `XDG_CONFIG_HOME`; legacy `/home/taylor/TAYLOR-ACCESS.txt` is a fallback when the default JSON account file is absent. The credential file must belong to the current user and allow owner-only access; use mode `0600`. Do not loosen that mode to solve a configuration error.

The MusePost config directory and `servers.json`, `display_names.json`, and `signatures.json` must belong to the current user and allow writes only by that user. Use `0700` for the directory. Non-credential JSON files support `0644` or `0600`; credentials remain owner-only. Config files must be regular files, and neither they nor the MusePost config directory may be symlinks.

IMAP uses TLS. Configure SMTP `starttls` with port 587 or `ssl` with port 465 to match the mail server. `sent_folder` controls Sent-copy filing: omit to discover a server-designated Sent mailbox, specify a folder string to select it, or set `null` to disable it. Existing signature, display-name, and server-override configuration remains supported.

Use full addresses when several mailboxes share a domain. Confirm the selected address and server settings before sending. Keep configuration out of Git; do not paste credentials into bug reports, logs, URLs, or command arguments.

## Offline verification

Run the regression checks from the source directory:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q musepost musepost_mail.py musepost_config.py musepost_state.py tests
sh -n install.sh
```

The tests substitute fake IMAP/SMTP transports, create isolated temporary config/state, and require no real accounts. They cover server/search failures, full non-marking reads, reply addressing/threading, signatures, recipient refusals, SMTP uncertainty, request deduplication, and failures after accepted SMTP submission. GitHub Actions uses Python 3.9 and 3.14.

To check the installed launcher, install into a temporary prefix containing spaces and inspect its help/version. Use a temporary directory, then remove only that directory when finished:

```sh
check_dir=$(mktemp -d)
sh install.sh "$check_dir/prefix with spaces"
"$check_dir/prefix with spaces/bin/musepost" --help
"$check_dir/prefix with spaces/bin/musepost" --version
rm -rf "$check_dir"
```

These tests establish source and fake-server behavior. They do not establish live mailbox compatibility or end-to-end delivery.

## Prepare and inspect messages

`send ... --preview` renders the composed message locally. `send ... --draft` saves an immutable local draft; `drafts show ID` displays it. Replies need to fetch the original message to determine the recipient and threading, including for preview or draft creation. Use a fake transport in tests or the intended authorized account for that read.

Before submitting, review the sender address, recipients, subject, body, signature, and attachments. Read commands do not change seen flags unless `--mark-read` is explicitly supplied. Use the same `--folder` for a UID across reads, attachment downloads, and replies.

Normal `send`, `reply`, and `drafts send ID` submit mail. Preview/draft creation, local receipt inspection, installation, and test execution do not submit mail. An explicit send command is the operational action; local preparation alone does not imply one.

## Submission receipts and recovery

Use `--request-id` with a unique stable value for each direct send or reply. Saved drafts automatically use `draft:DRAFT_ID` as their persistent submission key. Save the JSON result and exit code. Repeating the same request and content returns its existing result; a content change under the same ID is rejected. Receipt and draft IDs are identifiers for local state, not proof of delivery.

| Status | Exit code | Meaning and follow-up |
| --- | --- | --- |
| `accepted` | `0` | SMTP accepted the recipients. Delivery is unconfirmed; verify at the recipient or with authorized server evidence when required. |
| `partial` | `2` | Some recipients were accepted and some rejected. Do not resend to accepted recipients. Resolve rejected recipients separately after inspecting the receipt. |
| `rejected` | `1` | SMTP refused the submission/recipients. Inspect the receipt and correct the cause before making a new request. |
| `uncertain` | `3` | The client cannot prove whether SMTP accepted the message. Resolve through the existing receipt and authorized server/recipient evidence; do not automatically retry. |

Argument errors can also exit `2`; use the JSON `status` or `error.code` to distinguish them. Other failures exit nonzero and include structured errors in JSON mode.

Inspect stored receipts with:

```sh
musepost receipts --json
musepost receipts REQUEST_ID --json
```

An accepted receipt survives later failures while filing the Sent copy or marking a reply answered. Treat those failures as follow-up warnings. A missing Sent copy does not prove that SMTP failed. If a process was interrupted during SMTP submission, reuse the same request ID only to inspect its recorded uncertain result; do not generate a fresh request automatically.

The default private state directory is `~/.local/state/musepost` (respecting `XDG_STATE_HOME`). `MUSEPOST_STATE_DIR` selects another directory. Drafts and receipts may contain message content, recipients, and attachment data. Protect their backups. Keep the same state directory for retries; deleting state or using another directory discards the local deduplication history.

## Source review and live checks

Before a source push, inspect the exact candidate files for credentials, private keys, live mail, runtime state, and downloaded attachments. Keep useful placeholder configuration; exclude secrets and local mail data. Preserve unrelated work and use the repository's existing remotes and visibility. Do not trigger deployment as a side effect of a source backup.

When a live installation is within the requested scope, verify its Python version, installed revision, credential permissions, and configured mailbox identity. Start with an authorized read and a preview. Test submission only to the intended authorized test recipient with a unique request ID; inspect the SMTP receipt, Sent-copy outcome, and actual recipient receipt separately. Report which of these was verified. Do not call a source-only or fake-transport check a live delivery test.

For rollback, reinstall the prior complete package from its source revision. Keep private configuration and state intact so earlier receipts continue to prevent accidental duplicate submissions. Source revisions use `YYYY.MM.DD.BUILD`; update canonical `VERSION` and [CHANGELOG.md](CHANGELOG.md) together.
