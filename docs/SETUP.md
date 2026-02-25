# Setup Guide

## Requirements

- Windows + Python 3.11+ (recommended)
- QRZ account with Logbook access
- Gmail account with 2-Step Verification enabled (for App Password)

## Install

```powershell
python -m pip install requests Pillow
```

`Pillow` is recommended for postcard rendering. If unavailable, the project can fall back to `System.Drawing` on Windows.

## Configure Local Files

Create local files from templates:

- `configuration.example.json` -> `configuration.json`
- `eqsl_settings.example.json` -> `eqsl_settings.json`
- `qrz.com.example.txt` -> `qrz.com.txt` (optional fallback credentials)
- `gmail_app_password.example.txt` -> `gmail_app_password.txt`

## Run

```powershell
python upload_adif.py
```

The script uploads the ADIF to QRZ and then (if enabled) processes eQSL email sending.
