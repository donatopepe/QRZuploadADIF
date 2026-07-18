# QRZ ADIF Upload + Email eQSL

[![Tests](https://github.com/donatopepe/QRZuploadADIF/actions/workflows/tests.yml/badge.svg)](https://github.com/donatopepe/QRZuploadADIF/actions/workflows/tests.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Privacy-aware Python automation for:

- uploading ADIF files to QRZ Logbook
- sending personalized email eQSL postcards (JPG attachment)
- looking up recipient email from QRZ callsign pages when ADIF has no `EMAIL`

## Features

- QRZ login (with optional 2FA field in config)
- ADIF upload to selected QRZ logbook (`book_id`)
- eQSL postcard generation (Pillow or System.Drawing fallback)
- random image selection from a local gallery folder
- Gmail SMTP send (App Password)
- sent-state JSON to prevent duplicate eQSL for the same QSO
- basic anti-block/rate-limit controls (delay, jitter, batch pause, hourly cap)
- proxy-environment fallback (`trust_env=False`) for broken local proxy settings

## Release

Download the credential-free package from [v1.0.0](https://github.com/donatopepe/QRZuploadADIF/releases/tag/v1.0.0) and verify it against the published `SHA256SUMS.txt`.

```bash
sha256sum -c SHA256SUMS.txt
python -m zipfile -e QRZuploadADIF-v1.0.0.zip .
```

Release archives contain example configuration only. Never commit QRZ, Gmail, SMTP, or other credentials.

## Quick Start (Windows)

1. Install Python 3.11+.
2. Install dependencies: `python -m pip install -r requirements.txt`
3. Copy templates and fill local secrets:
   - `configuration.example.json` -> `configuration.json`
   - `eqsl_settings.example.json` -> `eqsl_settings.json`
   - `qrz.com.example.txt` -> `qrz.com.txt` (optional QRZ credentials fallback)
   - `gmail_app_password.example.txt` -> `gmail_app_password.txt`
4. Run `python upload_adif.py` (or `run_upload_adif.bat`).

## Local Files (Not Committed)

- `configuration.json`
- `eqsl_settings.json`
- `eqsl_sent.json`
- `eqsl_contacts_cache.json`
- `qrz.com.txt`
- `gmail_app_password.txt`
- `eqsl_out/`, `eqsl_assets_cache/`

## Documentation

- `docs/SETUP.md` - installation and configuration
- `docs/EQSL_EMAIL.md` - eQSL email settings and templates
- `docs/TESTING.md` - automated and live tests
- `docs/GALLERY_LICENSES.md` - gallery usage and attribution rules
- `gallery/ATTRIBUTIONS.md` - per-image attribution metadata

## Tests

- Unit tests: `python -m unittest discover -s tests -v`
- CI: Python 3.11/3.13 on Linux and Windows
- The Pillow renderer is portable; System.Drawing fallback is tested only where PowerShell is available
- Live test (real QRZ + Gmail send to yourself): set `RUN_LIVE_EQSL_TEST=1`
- ADIF parsing is tested with synthetic fixtures and compatibility checks for BBLogger-exported generic ADIF files (local fixture path via `BBLOGGER_ADIF_FIXTURE`)

## Privacy & Safety

- Do not commit credentials, local ADIF exports, or sent-email history.
- Only commit gallery images with clear free licenses and attribution metadata.
