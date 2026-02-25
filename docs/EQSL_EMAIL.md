# Email eQSL Guide

## What the eQSL service does

For each QSO in the ADIF:

1. builds a unique QSO key
2. skips if already present in `eqsl_sent.json`
3. reads `EMAIL` from ADIF, otherwise tries QRZ HTML lookup
4. renders a postcard JPG with QSO details
5. sends an email with the JPG attachment
6. stores success in `eqsl_sent.json`

## Important Settings (`eqsl_settings.json`)

- `enabled`: enable/disable eQSL flow
- `sender_email`, `sender_name`
- `gmail_app_password_file`
- `image_dirs`, `image_paths`, `image_urls`
- `randomize_image`
- `postcard_footer_line`
- `postcard_text.*` (size, padding, colors, scaling)
- `qrz_lookup_enabled`
- `anti_block_*`, `batch_*`, `max_emails_per_hour`
- `dry_run` (start with `true`)

## Gmail

Use a Gmail App Password (not your normal account password).

- SMTP host: `smtp.gmail.com`
- SSL port: `465` (default in template)

The code strips spaces from the App Password automatically.
