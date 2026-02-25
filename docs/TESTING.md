# Testing Guide

## Automated Tests

Run all tests:

```powershell
python -m unittest discover -s tests -v
```

Covered areas include:

- ADIF parsing
- QRZ HTML email extraction (including obfuscated `qmail`)
- sent-state idempotency
- renderer backends (`Pillow`, `System.Drawing`)
- upload credential fallback from `qrz.com.txt`
- anti-block/rate-limit behavior (unit-level)

## ADIF Test Data Notes

Tests cover both:

- synthetic/minimal ADIF records embedded in unit tests
- ADIF files exported by BBLogger (generic ADIF format compatibility)

The repository does not commit personal BBLogger exports. A local real-file parsing test can be enabled by setting `BBLOGGER_ADIF_FIXTURE` to a local `.adi` path.

## Live Integration Test (Opt-in)

Real QRZ login + real Gmail send to yourself:

```powershell
$env:RUN_LIVE_EQSL_TEST='1'
python -m unittest tests.test_eqsl_live_integration -v
```

Required local files:

- `qrz.com.txt`
- `gmail_app_password.txt`

Check Inbox/Spam/Promotions for the delivered eQSL.
