# Repository Guidelines

## Project Structure & Module Organization
- `upload_adif.py`: QRZ login + ADIF upload entrypoint, then optional eQSL email flow.
- `eqsl_service.py`: ADIF parsing, QRZ email lookup, postcard rendering, SMTP send, sent-state store.
- `tests/`: unit tests and optional live integration test (`RUN_LIVE_EQSL_TEST=1`).
- `*.example.json` / `*.example.txt`: safe templates for local config/secrets.
- `gallery/`: reusable postcard images (free-license only) plus attribution docs.

Keep runtime files local only: `configuration.json`, `eqsl_settings.json`, `eqsl_sent.json`, `qrz.com.txt`, `gmail_app_password.txt`.

## Build, Test, and Development Commands
- `python -m pip install requests Pillow`: runtime dependencies (`Pillow` optional but recommended).
- `python upload_adif.py`: run QRZ upload and eQSL flow.
- `python -m unittest discover -s tests -v`: run automated tests.
- `python -m py_compile upload_adif.py eqsl_service.py`: quick syntax check.

Use `run_upload_adif.bat` for Windows launch from Explorer.

## Coding Style & Naming Conventions
- Python 3, PEP 8, 4 spaces, `snake_case` for functions/variables, `UPPER_CASE` for constants.
- Prefer pure helpers and dependency injection (see `process_eqsl_records(...)`) to keep tests simple.
- Add logging for network/retry behavior (QRZ/SMTP/proxy fallback) instead of silent failures.

## Testing Guidelines
- Framework: `unittest` (no pytest dependency required).
- Test files: `tests/test_*.py`.
- Keep live-network tests opt-in and guarded by env vars.
- Mock SMTP/QRZ in unit tests; only use real sends in explicit local runs.

## Commit & Pull Request Guidelines
- Use descriptive commits (e.g. `eqsl: add SMTP throttle backoff and batch pauses`).
- Separate code, docs, and gallery/license updates when practical.
- PRs should include test results and note any new config keys or file formats.

## Security & Data Handling
- Never commit personal credentials, local ADIF paths, or sent-email history.
- Only commit gallery images with clear free licenses, and update `gallery/ATTRIBUTIONS.md`.
