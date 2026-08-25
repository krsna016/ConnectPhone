## Summary

<!-- What problem does this change solve? -->

## Changes

-

## Verification

- [ ] `python -m pip check`
- [ ] `python -m compileall -q ConnectPhone.py ConnectPhoneUI.py adb_client.py core tests`
- [ ] `python -m unittest discover -s tests -v`
- [ ] `node --check ui/index.js`
- [ ] `bash -n build_mac.sh`
- [ ] Android Companion checks run when applicable

## Risk and security review

- Permissions, device-control behavior, pairing, local API, Keychain, or
  signing behavior affected: <!-- yes/no; explain if yes -->
- Documentation and tests updated where needed: <!-- yes/no -->
