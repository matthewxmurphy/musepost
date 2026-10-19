# Source changes

## Unreleased

- Accept configured mailbox identities independently of operator local-part.
- Add a private structured accounts file selected with `MUSEPOST_CONFIG`, while
  preserving the legacy file fallback and block format.
- Reject unsafe ownership, file type, links, permissions, malformed accounts,
  duplicate identities and ambiguous domain selection without printing secrets.
- Keep the correct Message-ID domain when a configured quoted local part includes `@`.
- Add offline identity/file-safety/TLS/command-dispatch regression coverage.

Historical product versions remain documented in README; no release was created.
