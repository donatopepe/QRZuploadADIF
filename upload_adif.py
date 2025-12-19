import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict

import requests

CONFIG_FILE = Path(__file__).with_name("configuration.json")
DEFAULT_LOG = Path(__file__).with_name("upload_adif.log")
DEFAULT_CONFIG: Dict[str, Any] = {
    "login_url": "https://www.qrz.com/login",
    "adif_url": "https://logbook.qrz.com/adif",
    "username": "",
    "password": "",
    "book_id": "",
    "sbook": 0,
    "adif_path": "",
    "allow_duplicates": False,
    "email_report": False,
    "log_path": str(DEFAULT_LOG),
    "twofactor_code": "",
    "trust_device": False,
}


def load_config() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return dict(DEFAULT_CONFIG)
    # Handle possible BOM in the JSON file on Windows (utf-8-sig).
    text = CONFIG_FILE.read_text(encoding="utf-8-sig")
    cfg = json.loads(text)

    changed = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = value
            changed = True
    if changed:
        save_config(cfg)
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def ensure_config_fields(cfg: Dict[str, Any]) -> Dict[str, Any]:
    changed = False

    def prompt(field: str, msg: str) -> str:
        nonlocal changed
        if cfg.get(field):
            return cfg[field]
        cfg[field] = input(msg).strip()
        changed = True
        return cfg[field]

    prompt("username", "Username QRZ: ")
    prompt("password", "Password QRZ: ")
    prompt("book_id", "Book ID (bid): ")
    if cfg.get("sbook") is None:
        cfg["sbook"] = 0
        changed = True
    prompt("adif_path", "Percorso file ADIF (.adi/.adif): ")
    if cfg.get("log_path") is None:
        cfg["log_path"] = str(DEFAULT_LOG)
        changed = True
    if changed:
        save_config(cfg)
    return cfg


def get_log_path(cfg: Dict[str, Any]) -> Path:
    log_path = cfg.get("log_path")
    if log_path:
        return Path(log_path).expanduser()
    return DEFAULT_LOG


def setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("qrz_upload")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _parse_login_ticket(html: str) -> str:
    """Extract loginTicket value from the login page JS, if present."""
    m = re.search(r"loginTicket'\\s*:\\s*'([a-f0-9]+)'", html, flags=re.IGNORECASE)
    return m.group(1) if m else ""


def _try_handshake(session: requests.Session, ticket: str, cfg: Dict[str, Any], logger: logging.Logger) -> None:
    """Best-effort login-handshake flow (step 1 + step 2)."""
    if not ticket:
        return
    try:
        logger.info("Attempting login handshake (step 1)")
        step1 = session.post(
            "https://www.qrz.com/login-handshake",
            data={"loginTicket": ticket, "username": cfg["username"], "step": 1},
            timeout=30,
        )
        step1.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Handshake step 1 skipped: %s", exc)
        return

    try:
        logger.info("Attempting login handshake (step 2)")
        step2 = session.post(
            "https://www.qrz.com/login-handshake",
            data={"loginTicket": ticket, "username": cfg["username"], "password": cfg["password"], "step": 2},
            timeout=30,
        )
        step2.raise_for_status()
        if step2.headers.get("Content-Type", "").startswith("application/json"):
            payload = step2.json()
            if payload.get("error"):
                raise RuntimeError(payload.get("message") or "Handshake error")
            if payload.get("twofactor"):
                logger.info("Server requests 2FA; a code will be posted with the final login form if provided.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Handshake step 2 skipped: %s", exc)


def login(session: requests.Session, cfg: Dict[str, Any], logger: logging.Logger) -> None:
    login_url = cfg.get("login_url") or "https://www.qrz.com/login"
    logger.info("Fetching login page: %s", login_url)
    resp = session.get(login_url, timeout=30)
    resp.raise_for_status()

    login_ticket = _parse_login_ticket(resp.text)
    _try_handshake(session, login_ticket, cfg, logger)

    hidden = dict(re.findall(r'name=["\']([^"\']+)["\']\s+value=["\']([^"\']*)["\']', resp.text))
    payload = {**hidden, "username": cfg["username"], "password": cfg["password"]}
    payload.setdefault("login", "Login")
    if cfg.get("twofactor_code"):
        payload["2fcode"] = cfg["twofactor_code"]
    if cfg.get("trust_device"):
        payload["trustdevice"] = "yes"

    logger.info("Submitting login form")
    post = session.post(login_url, data=payload, timeout=30, headers={"Referer": login_url})
    post.raise_for_status()

    text = post.text.lower()
    if "logout" not in text and "log out" not in text:
        # Fallback: probe logbook page to confirm session.
        probe = session.get("https://logbook.qrz.com/logbook", timeout=30)
        probe.raise_for_status()
        ptext = probe.text.lower()
        if "logout" not in ptext and "log out" not in ptext:
            logger.error("Login failed: logout marker not found")
            raise RuntimeError("Login failed: cannot detect authenticated session.")
    logger.info("Login successful")


def upload_adif(session: requests.Session, cfg: Dict[str, Any], logger: logging.Logger) -> None:
    adif_url = cfg.get("adif_url") or "https://logbook.qrz.com/adif"
    adif_path = Path(cfg["adif_path"])
    if not adif_path.exists():
        logger.error("File ADIF non trovato: %s", adif_path)
        raise FileNotFoundError(f"File ADIF non trovato: {adif_path}")

    data = {
        "bid": str(cfg.get("book_id", "")),
        "sbook": str(cfg.get("sbook", 0)),
        "op": "upfile",
    }
    if cfg.get("allow_duplicates"):
        data["dupok"] = "1"
    if cfg.get("email_report"):
        data["ereport"] = "1"

    logger.info("Uploading %s to %s (bid=%s, sbook=%s)", adif_path.name, adif_url, data["bid"], data["sbook"])
    with adif_path.open("rb") as fp:
        files = {"upload_file": (adif_path.name, fp, "application/octet-stream")}
        resp = session.post(adif_url, data=data, files=files, timeout=120)
    resp.raise_for_status()

    try:
        payload = resp.json()
    except ValueError:
        logger.error("Risposta non JSON: %s", resp.text[:500])
        raise RuntimeError(f"Risposta non JSON: {resp.text[:500]}")

    status = payload.get("status")
    if status != "ok":
        logger.error("Upload fallito: %s", payload)
        raise RuntimeError(f"Upload fallito: {payload}")

    logger.info("Upload completato: %s", payload)
    print("Upload completato:", payload)


def main():
    cfg = load_config()
    cfg = ensure_config_fields(cfg)
    log_path = get_log_path(cfg)
    logger = setup_logger(log_path)
    logger.info("Inizio procedura upload")

    with requests.Session() as session:
        login(session, cfg, logger)
        upload_adif(session, cfg, logger)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        try:
            logger = logging.getLogger("qrz_upload")
            if logger.handlers:
                logger.exception("Errore durante l'esecuzione")
        except Exception:
            pass
        sys.exit(1)
