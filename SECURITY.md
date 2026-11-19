# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub private vulnerability reporting](https://github.com/adityam789/swipesort/security/advisories/new).
Don't open a public issue. You'll get an acknowledgement within 7 days.

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | ✅ |

## Threat model

SwipeSort is a local daemon with access to the folders you choose.

- **Network exposure.** By default the daemon binds `127.0.0.1` only. `serve --lan` binds all interfaces.
- **Auth.**
  - Loopback clients are trusted. Loopback `/api` requests must carry a local `Host` header (DNS-rebinding guard).
  - Every other client needs a device token, obtained through a pairing code:
    - The code is 8 characters, single-use, expires after 5 minutes, and is rate-limited.
    - Only a loopback client can mint one.
    - Tokens are 256-bit random values; only their SHA-256 is stored; they can be revoked.
    - The token travels as an `HttpOnly`, `SameSite=Strict` cookie.
- **Desktop-only operations.** Creating sessions, browsing the filesystem, pairing and device management are refused for LAN clients, even with a valid token.
- **Filesystem.**
  - Every mutation goes through a journaled executor.
  - Symlinks are never followed, and paths outside the session roots are rejected.
  - Deletion is a quarantine move. Only `purge` deletes.
- **Known limitation.** LAN traffic is plain HTTP. Use `--lan` only on networks you trust.
