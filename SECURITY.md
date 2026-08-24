# Security Policy

## Supported version

Security fixes are applied to the latest commit on the default development
branch. Older source snapshots and development APKs are not supported releases.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do
not include pairing QR payloads, API tokens, Android PINs, Keychain contents,
or other credentials in a public issue. Include the affected commit, a minimal
reproduction, impact, and whether physical device access is required.

## Security boundaries

- The desktop control API binds only to loopback and uses a new random token on
  every launch.
- ADB functionality requires Android's own USB or Wireless Debugging trust.
- Companion Mode requires explicit QR enrollment and supports only authenticated
  device status and alert commands in the current release.
- ConnectPhone does not bypass Android lock screens, app locks, permission
  dialogs, MediaProjection consent, or Accessibility consent.
- Production artifacts must be signed with operator-controlled Android and
  Apple signing identities; no private signing material belongs in this repo.
