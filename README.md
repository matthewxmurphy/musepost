<p align="center">
  <img src="https://raw.githubusercontent.com/matthewxmurphy/musepost/main/musepost-logo.png" alt="MusePost" width="400">
</p>

<h3 align="center">Every mailbox. One command.</h3>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.8%2B-blue.svg" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/version-2026.10.02.03-orange.svg" alt="Version 2026.10.02.03">
  <img src="https://img.shields.io/badge/stdlib-only-green.svg" alt="Standard library only">
</p>

# MusePost

Multi-mailbox email client for AI agents and the terminal. Read, search, reply, and send across domains using IMAP and SMTP.

![MusePost in action](https://raw.githubusercontent.com/matthewxmurphy/musepost/main/musepost-swagger.png)

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

1. Create a credentials file (mode `0600`) with one block per mailbox:

```
taylor@example.com
Username: taylor@example.com
New password: your-password-here
Mailbox quota: 128 MB
```

2. Point `musepost` at it by editing `ACCESS_FILE` near the top of the script
   (default: `/home/taylor/TAYLOR-ACCESS.txt`).

3. Make it executable and put it on your `PATH`:

```sh
chmod 755 musepost
```

**Never commit your credentials file.**

## Usage

```
musepost inboxes                  list accounts with unread counts
musepost unread [account]         show unread messages (all accounts, or one)
musepost list <account> [n=10]    recent messages in an inbox
musepost read <account> <uid>     read a full message
musepost search <account> <query> search subject/from/body on the server
musepost reply <account> <uid> --body -
                                  reply with proper threading headers
musepost send <account> --to ADDR --subject SUBJ --body -
                                  send a new message
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
# Set a signature for a mailbox (reads from stdin)
musepost signature net30hosting.com --set <<'EOF'
Taylor Quinn | Net30Hosting
EOF

# Show it, or delete it
musepost signature net30hosting.com
musepost signature net30hosting.com --delete
```

Signatures are appended automatically to sent messages and replies.
Use `--no-signature` on any send or reply to skip it.

## Security Notes

- Passwords are read at runtime from your `0600` credentials file and never
  printed, logged, or stored elsewhere by this tool.
- All connections use TLS (IMAP SSL on 993, SMTP STARTTLS on 587).

## Versioning

`YYYY.MM.DD.BUILD` — date plus an incrementing build number for the day.

## Developed For

**Muse.ai agent emails** — built to give an AI agent full command of 19 site mailboxes: listing, reading, searching, replying, and sending as the correct identity for each domain, all from the command line.

## Credits

Taylor & Matthew Murphy — [net30hosting.com](https://net30hosting.com)
