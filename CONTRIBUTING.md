# Contributing to ConnectPhone

Thanks for helping improve ConnectPhone. Keep changes focused, explain the
user impact, and avoid committing device data, credentials, signing material,
or generated build output.

## Before you start

1. Check existing issues and pull requests for related work.
2. For security-sensitive changes, read [SECURITY.md](SECURITY.md) and do not
   disclose vulnerabilities in a public issue.
3. Create a branch from the current development branch.

## Local checks

Install the dependencies from `requirements.txt`, then run:

```bash
python -m pip check
python -m compileall -q ConnectPhone.py ConnectPhoneUI.py adb_client.py core tests
python -m unittest discover -s tests -v
node --check ui/index.js
bash -n build_mac.sh
```

For Companion changes, also run from `companion-android/`:

```bash
./gradlew --no-daemon test lint assembleDebug
```

Run the narrowest relevant tests while iterating, then run the full set before
requesting review. Keep documentation, tests, and security notes in sync with
behavioral changes.

## Pull requests

- Describe the problem and the change, including affected platforms.
- Include manual verification steps for UI, ADB, wireless, or device-control
  changes.
- Call out required permissions, migrations, release signing, or compatibility
  changes.
- Keep commits reviewable and do not include `.venv`, `dist`, `build`, APKs,
  logs, screenshots containing personal data, or local configuration.

## Reporting security issues

Do not open a public issue for a vulnerability. Follow the private reporting
instructions in [SECURITY.md](SECURITY.md).
