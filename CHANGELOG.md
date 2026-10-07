# Changelog

## 2026.10.07.02 — 2026-10-07

- Added portable, owner-only account configuration with legacy access-file support, configurable server settings, TLS modes, and connection timeouts.
- Replaced manual argument parsing with an argparse CLI and consistent JSON results and errors.
- Raised the minimum Python version to 3.9; the executable now ships with three required modules and `VERSION`. JSON `inboxes` and `unread` account entries moved into an `accounts` object beside `schema_version: 1`.
- Made reads complete and non-marking by default; added `peek`, explicit `--mark-read`, optional body limits, folder selection, and attachment downloads.
- Corrected quoted and Unicode IMAP search handling and surfaced mailbox errors instead of reporting empty results.
- Honored validated `Reply-To` recipients, retained reply threading, and chose the configured signature default consistently.
- Added SMTP receipts for accepted, rejected, partial, and uncertain submissions, stable Message-IDs, request deduplication, and preservation of SMTP results when later IMAP work fails.
- Added preview and local draft workflows, receipt inspection, and Sent-folder filing without automatic resubmission.
- Added a self-contained installer, example configuration, operational runbook, and offline IMAP/SMTP regression checks in GitHub Actions.

## 2026.10.07.01 — 2026-10-07

- Display names: friendly `From` header names via `~/.config/musepost/display_names.json`.
- Attachments: `--attach FILE` on `send` for audio, images, and documents.
- Per-address server overrides: custom IMAP/SMTP hosts and ports via `~/.config/musepost/servers.json`.

## 2026.10.06.02 — 2026-10-06

- Per-address signature variants for desktop and iPhone.
