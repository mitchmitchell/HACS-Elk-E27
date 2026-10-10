# Changelog

All notable changes to the Elk E27 Alarm Engine Integration. Full release notes are on the
[Releases page](https://github.com/mitchmitchell/HACS-Elk-E27/releases).

## [Unreleased] - 0.1.8 (draft)

### Fixed
- `elke27.alarm_arm_automatic` no longer sends a redundant second arm when two calls for the
  same area run back to back and the panel status lags (#49, fixes #43).

### Changed (pending #48, not merged yet)
- Setup: bad credentials or an invalid link show a re-link prompt. Temporary errors retry
  setup.
- The reconnect loop starts a re-link prompt when the link keys are rejected.
- The re-link flow uses the standard `reauth_confirm` step, keeps the panel identity and
  refuses a different panel.
- Non-numeric codes are rejected with a validation error.
- Refreshes are serialized and deduped; error handling is narrowed.

### Breaking (pending #48)
- `elke27.alarm_arm_automatic` and `elke27.zone_bypass` reject a non-numeric `code`. Use
  digits only.
- Re-link step id changed from `relink` to `reauth_confirm`. Reopen any re-link prompt that
  was open during the upgrade.
- Auth or link failures at setup now give a setup error plus a re-link prompt.

## [0.1.7]
See the [v0.1.7 release](https://github.com/mitchmitchell/HACS-Elk-E27/releases/tag/v0.1.7).

## [0.1.6]
See the [v0.1.6 release](https://github.com/mitchmitchell/HACS-Elk-E27/releases/tag/v0.1.6).
