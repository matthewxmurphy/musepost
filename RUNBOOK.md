# MusePost configuration operations

## Architecture and compatibility

`musepost` is the stdlib-only entry point. `load_accounts(path=None)` selects its
explicit argument, then `MUSEPOST_CONFIG`, then historical `ACCESS_FILE`. It opens
one private descriptor, validates its ownership/type/link count/exact `0600`
mode before reading, parses JSON or legacy blocks and returns `{address: password}`
for existing command handlers. The final path component cannot be a symlink.
Operators must also control parent directories. No automatic chmod or migration
writes occur. Strict checks may reject older permissive files: fix those files
locally before invoking a mailbox command.

Account lookup keeps lowercase addresses and rejects a shared-domain shortcut.
Routing/display-name/signature configuration retains its existing separate paths.
IMAP uses certificate-validating SSL; SMTP requires certificate-validating
STARTTLS before authentication. Loading configuration makes no network calls.
Only explicit `send`/`reply` commands invoke outgoing-message handlers. This
change does not add provider delivery receipts or alter other mail operations.

## Safe verification and support

Run `python3 -m unittest discover -s tests -v` using synthetic fixtures. Do not run
mailbox commands as a test against live credentials. Offline transport doubles
verify TLS context settings and SMTP ordering, not a real server handshake.
The suite exercises file rejection, address parsing, domain collisions and
non-sensitive CLI diagnostics. No real mailbox/provider acceptance is claimed.

Report bugs through repository issues with Python/OS version, sanitized fixture,
expected/actual output and test command. Security reports must omit secrets and
private messages; request a private reporting channel from the repository owner.
Contributions should preserve existing mail side effects and TLS boundaries,
include focused regression evidence and undergo a separate source review.

## Rollback and release boundary

Keep the prior executable before an operator-authorized installation. Reverting
the source commit restores the prior parser and loses non-Taylor identities and
file safety checks. Do not weaken file permissions to work around a rejection.
No provider/account settings, mailboxes, runtime files or deployed installations
are modified by this source checkpoint. License/distribution terms and broader
SOP integration remain owner-review gaps documented in README.
