import json
import logging
import os
import random
import re
import smtplib
import subprocess
import shutil
import time
import uuid
import hashlib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

EQSL_SETTINGS_FILENAME = "eqsl_settings.json"

DEFAULT_EQSL_SETTINGS: Dict[str, Any] = {
    "enabled": False,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_ssl": True,
    "sender_email": "",
    "sender_name": "",
    "gmail_app_password_file": "gmail_app_password.txt",
    "subject_template": "eQSL {station_callsign} -> {call} {qso_date_display} {time_on_utc}",
    "body_template": (
        "Hello {call},\n\n"
        "Thank you for the QSO with {station_callsign}.\n"
        "QSO: {qso_date_display} {time_on_utc} UTC | {band} | {mode_display}\n"
        "RST: TX {rst_sent} / RX {rst_rcvd}\n"
        "Locator: {my_gridsquare} -> {gridsquare}\n"
        "{extra_qso_line}\n\n"
        "Attached is your personalized electronic QSL card (eQSL).\n"
        "{image_attribution_line}\n"
        "73 from {station_callsign}\n"
    ),
    "image_url": "https://commons.wikimedia.org/wiki/Special:FilePath/Pieve_di_Cento_-_Palazzo_Comunale.jpg",
    "image_path": "",
    "image_urls": [],
    "image_paths": [],
    "image_dirs": [],
    "randomize_image": True,
    "image_attribution": (
        "Photo: Ingo Mehling, Wikimedia Commons, CC BY-SA 4.0 - "
        "https://commons.wikimedia.org/wiki/File:Pieve_di_Cento_-_Palazzo_Comunale.jpg"
    ),
    "postcard_output_dir": "eqsl_out",
    "postcard_cache_dir": "eqsl_assets_cache",
    "jpeg_quality": 90,
    "postcard_text": {
        "x": 48,
        "y": 48,
        "font_size": 42,
        "line_spacing": 8,
        "auto_scale": True,
        "scale_multiplier": 1.7,
        "text_color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 2,
        "box_padding": 22,
        "box_fill_rgba": [0, 0, 0, 130],
    },
    "postcard_footer_line": "Thanks for the contact from Pieve di Cento, Italy",
    "qrz_lookup_enabled": True,
    "qrz_lookup_timeout_sec": 20,
    "qrz_contacts_cache_file": "eqsl_contacts_cache.json",
    "sent_store_file": "eqsl_sent.json",
    "max_emails_per_run": 50,
    "anti_block_enabled": True,
    "delay_sec_between_emails": 2.0,
    "delay_jitter_sec": 1.0,
    "batch_size": 10,
    "batch_pause_sec": 60.0,
    "max_emails_per_hour": 40,
    "hour_window_sec": 3600.0,
    "smtp_throttle_backoff_sec": 300.0,
    "stop_run_on_smtp_throttle": True,
    "max_consecutive_send_errors": 3,
    "dry_run": True,
}

DEFAULT_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    )
}


def _request_with_proxy_fallback(
    session: requests.Session,
    method: str,
    url: str,
    *,
    logger: logging.Logger | None = None,
    **kwargs: Any,
):
    """Retry once with trust_env=False when env proxy settings are broken."""
    try:
        return session.request(method, url, **kwargs)
    except requests.exceptions.ProxyError as exc:
        if not getattr(session, "trust_env", True):
            raise
        if logger:
            logger.warning(
                "Proxy env error during %s %s; retrying with trust_env=False: %s",
                method.upper(),
                url,
                exc,
            )
        session.trust_env = False
        return session.request(method, url, **kwargs)


def _download_with_proxy_fallback(url: str, timeout: float, logger: logging.Logger) -> requests.Response:
    try:
        return requests.get(url, timeout=timeout, headers=DEFAULT_HTTP_HEADERS, allow_redirects=True)
    except requests.exceptions.ProxyError as exc:
        logger.warning("Proxy env error during GET %s; retrying direct (trust_env=False): %s", url, exc)
        with requests.Session() as session:
            session.trust_env = False
            return session.get(url, timeout=timeout, headers=DEFAULT_HTTP_HEADERS, allow_redirects=True)


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _merge_defaults(cfg: Dict[str, Any], defaults: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    changed = False
    merged = dict(cfg)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = value
            changed = True
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged_value, inner_changed = _merge_defaults(merged[key], value)
            merged[key] = merged_value
            changed = changed or inner_changed
    return merged, changed


def load_or_create_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        path.write_text(json.dumps(default, indent=2), encoding="utf-8")
        return json.loads(json.dumps(default))

    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    cfg, changed = _merge_defaults(cfg, default)
    if changed:
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def load_eqsl_settings(base_dir: Path) -> tuple[Path, Dict[str, Any]]:
    path = base_dir / EQSL_SETTINGS_FILENAME
    return path, load_or_create_json(path, DEFAULT_EQSL_SETTINGS)


def parse_adif_records(adif_path: Path) -> List[Dict[str, str]]:
    text = adif_path.read_text(encoding="utf-8-sig", errors="replace")
    parts = re.split(r"(?i)<EOR>", text)
    records: List[Dict[str, str]] = []

    for part in parts:
        if not re.search(r"(?i)<CALL\s*:", part):
            continue
        record: Dict[str, str] = {}
        for match in re.finditer(r"(?is)<([A-Z0-9_]+)\s*:\s*\d+(?::[^>]*)?>([^<]*)", part):
            record[match.group(1).upper()] = match.group(2).strip()
        if record:
            records.append(record)
    return records


def qso_key(qso: Dict[str, str]) -> str:
    return "|".join(
        [
            qso.get("CALL", "").upper(),
            qso.get("QSO_DATE", ""),
            qso.get("TIME_ON", ""),
            qso.get("BAND", ""),
            qso.get("MODE", ""),
        ]
    )


def _format_qso_date(date_value: str) -> str:
    if len(date_value) == 8 and date_value.isdigit():
        return f"{date_value[0:4]}-{date_value[4:6]}-{date_value[6:8]}"
    return date_value


def _format_qso_time(time_value: str) -> str:
    if len(time_value) >= 4 and time_value.isdigit():
        hh = time_value[0:2]
        mm = time_value[2:4]
        ss = time_value[4:6] if len(time_value) >= 6 else "00"
        return f"{hh}:{mm}:{ss}"
    return time_value


def qso_mode_display(qso: Dict[str, str]) -> str:
    mode = qso.get("MODE", "")
    submode = qso.get("SUBMODE", "")
    if submode and submode != mode:
        return f"{mode}/{submode}"
    return mode or submode


def build_qso_template_values(qso: Dict[str, str], settings: Dict[str, Any]) -> Dict[str, str]:
    station_callsign = (qso.get("STATION_CALLSIGN") or qso.get("OPERATOR") or settings.get("sender_name") or "").strip()
    station_callsign = station_callsign or "UNKNOWN"
    mode_display = qso_mode_display(qso)

    extra_bits: List[str] = []
    if qso.get("QRB"):
        extra_bits.append(f"QRB {qso['QRB']} km")
    if qso.get("TX_PWR"):
        extra_bits.append(f"Pwr {qso['TX_PWR']} W")
    if qso.get("CONT"):
        extra_bits.append(f"Cont {qso['CONT']}")
    if qso.get("DXCC"):
        extra_bits.append(f"DXCC {qso['DXCC']}")

    values = {
        "call": qso.get("CALL", ""),
        "station_callsign": station_callsign,
        "operator": qso.get("OPERATOR", "").strip(),
        "qso_date": qso.get("QSO_DATE", ""),
        "qso_date_display": _format_qso_date(qso.get("QSO_DATE", "")),
        "time_on": qso.get("TIME_ON", ""),
        "time_on_utc": _format_qso_time(qso.get("TIME_ON", "")),
        "time_off_utc": _format_qso_time(qso.get("TIME_OFF", "")),
        "band": qso.get("BAND", ""),
        "mode": qso.get("MODE", ""),
        "submode": qso.get("SUBMODE", ""),
        "mode_display": mode_display,
        "rst_sent": qso.get("RST_SENT", ""),
        "rst_rcvd": qso.get("RST_RCVD", ""),
        "gridsquare": qso.get("GRIDSQUARE", ""),
        "my_gridsquare": qso.get("MY_GRIDSQUARE", ""),
        "qrb": qso.get("QRB", ""),
        "tx_pwr": qso.get("TX_PWR", ""),
        "name": qso.get("NAME", ""),
        "qth": qso.get("QTH", ""),
        "image_attribution": str(settings.get("image_attribution", "")).strip(),
        "image_attribution_line": (
            f"Image: {str(settings.get('image_attribution', '')).strip()}"
            if str(settings.get("image_attribution", "")).strip()
            else ""
        ),
        "extra_qso_line": " | ".join(extra_bits),
    }
    return values


def build_postcard_lines(qso: Dict[str, str], settings: Dict[str, Any]) -> List[str]:
    v = build_qso_template_values(qso, settings)
    lines = [
        f"eQSL de {v['station_callsign']}  ->  {v['call']}",
        f"QSO {v['qso_date_display']} {v['time_on_utc']} UTC   {v['band']}   {v['mode_display']}",
    ]

    rst_tx = v.get("rst_sent")
    rst_rx = v.get("rst_rcvd")
    if rst_tx or rst_rx:
        lines.append(f"RST TX/RX: {rst_tx or '-'} / {rst_rx or '-'}")

    if v.get("my_gridsquare") or v.get("gridsquare"):
        lines.append(f"Locator: {v.get('my_gridsquare') or '-'} -> {v.get('gridsquare') or '-'}")

    extras = []
    if v.get("qrb"):
        extras.append(f"QRB {v['qrb']} km")
    if v.get("tx_pwr"):
        extras.append(f"Pwr {v['tx_pwr']} W")
    if extras:
        lines.append("   ".join(extras))

    footer_line = str(settings.get("postcard_footer_line", "") or "").strip()
    if footer_line:
        lines.append(footer_line)
    return lines


def load_sent_store(path: Path) -> Dict[str, Any]:
    default = {"schema_version": 1, "sent_by_qso_key": {}}
    return load_or_create_json(path, default)


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON with flush+fsync and atomic replace to reduce data loss on long runs."""
    _ensure_parent(path)
    data = json.dumps(payload, indent=2)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_path, path)
        return
    except PermissionError:
        # Windows may deny atomic replace if another process has the target file open
        # for reading (Explorer preview, AV scan, editor). Fallback to in-place flush.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass

    with path.open("w", encoding="utf-8", newline="\n") as fp:
        fp.write(data)
        fp.flush()
        os.fsync(fp.fileno())


def load_contacts_cache(path: Path) -> Dict[str, Any]:
    default = {"schema_version": 1, "emails_by_callsign": {}}
    return load_or_create_json(path, default)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read_secret_from_file(base_dir: Path, filename: str) -> str:
    secret_path = (base_dir / filename).resolve() if not Path(filename).is_absolute() else Path(filename)
    if not secret_path.exists():
        raise FileNotFoundError(f"File segreto non trovato: {secret_path}")
    return secret_path.read_text(encoding="utf-8-sig").strip()


def _resolve_email_from_qso(qso: Dict[str, str]) -> str:
    email = (qso.get("EMAIL") or "").strip()
    return email


def _extract_first_email(text: str) -> str:
    if not text:
        return ""
    # QRZ often stores the callsign email in an obfuscated qmail JS variable and builds
    # the mailto link client-side via showqem().
    qmail_match = re.search(r"var\s+qmail\s*=\s*'([^']+)'", text, flags=re.IGNORECASE)
    if qmail_match:
        decoded = _decode_qrz_qmail(qmail_match.group(1))
        if decoded:
            return decoded

    mailto = re.search(r"(?i)mailto:([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})", text)
    if mailto:
        return mailto.group(1)

    for match in re.finditer(r"(?i)([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})", text):
        email = match.group(1)
        lower = email.lower()
        if any(
            token in lower
            for token in (
                "qrz.com",
                "example.com",
                "noreply",
                "no-reply",
                "abuse@",
                "support@",
            )
        ):
            continue
        return email
    return ""


def _decode_qrz_qmail(cem: str) -> str:
    """Decode QRZ's qmail obfuscation used by showqem() JS."""
    if not cem or "!" not in cem:
        return ""

    # Mirror QRZ showqem() logic:
    # trailing digits after '!' specify the decoded email length, characters are
    # then read backwards stepping by 2.
    i = len(cem) - 1
    length_chars = ""
    while i > 0:
        ch = cem[i]
        if ch == "!":
            break
        length_chars += ch
        i -= 1
    if i <= 0:
        return ""

    try:
        decoded_len = int(length_chars)
    except ValueError:
        return ""

    i -= 1  # move before '!'
    out_chars: List[str] = []
    for _ in range(decoded_len):
        if i < 0:
            return ""
        out_chars.append(cem[i])
        i -= 2

    email = "".join(out_chars).strip()
    if re.fullmatch(r"(?i)[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", email):
        return email
    return ""


def lookup_email_via_qrz_html(
    session: requests.Session,
    callsign: str,
    settings: Dict[str, Any],
    logger: logging.Logger,
    contacts_cache: Dict[str, Any],
    persist_contacts_cache_fn: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> str:
    cache = contacts_cache.setdefault("emails_by_callsign", {})
    key = callsign.upper()
    cached = cache.get(key, {})
    if isinstance(cached, dict) and cached.get("email"):
        return str(cached["email"]).strip()
    if isinstance(cached, dict) and cached.get("checked") and not settings.get("retry_qrz_not_found", False):
        return ""

    if not settings.get("qrz_lookup_enabled", True):
        return ""

    url = f"https://www.qrz.com/db/{key}"
    timeout = float(settings.get("qrz_lookup_timeout_sec", 20))
    logger.info("QRZ email lookup: %s", url)
    resp = _request_with_proxy_fallback(
        session,
        "GET",
        url,
        logger=logger,
        timeout=timeout,
        headers={"Referer": "https://www.qrz.com/"},
    )
    resp.raise_for_status()
    email = _extract_first_email(resp.text)
    cache[key] = {
        "email": email,
        "checked": True,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "qrz_html",
    }
    if persist_contacts_cache_fn is not None:
        persist_contacts_cache_fn(contacts_cache)
    return email


def _sanitize_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "item"


def _resolve_postcard_source_image_path(base_dir: Path, settings: Dict[str, Any], logger: logging.Logger) -> Path:
    image_path_value = str(settings.get("image_path") or "").strip()
    image_url_value = str(settings.get("image_url") or "").strip()
    image_paths = [str(v).strip() for v in (settings.get("image_paths") or []) if str(v).strip()]
    image_dirs = [str(v).strip() for v in (settings.get("image_dirs") or []) if str(v).strip()]
    image_urls = [str(v).strip() for v in (settings.get("image_urls") or []) if str(v).strip()]

    candidates: List[tuple[str, str]] = []
    if image_dirs:
        exts = {".jpg", ".jpeg", ".png", ".webp"}
        for dir_value in image_dirs:
            dir_path = Path(dir_value)
            if not dir_path.is_absolute():
                dir_path = (base_dir / dir_path).resolve()
            if not dir_path.exists() or not dir_path.is_dir():
                logger.warning("image_dirs entry not found or not a directory: %s", dir_path)
                continue
            for p in dir_path.rglob("*"):
                if p.is_file() and p.suffix.lower() in exts:
                    candidates.append(("path", str(p)))
    if image_paths:
        candidates.extend(("path", v) for v in image_paths)
    if image_urls:
        candidates.extend(("url", v) for v in image_urls)
    # Backward compatibility with legacy single-image settings.
    if image_path_value:
        candidates.append(("path", image_path_value))
    elif image_url_value:
        candidates.append(("url", image_url_value))

    if not candidates:
        raise RuntimeError("Configurare image_path/image_url oppure image_paths/image_urls in eqsl_settings.json")

    # Deduplicate while preserving order.
    deduped: List[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    candidates = deduped

    if len(candidates) > 1 and bool(settings.get("randomize_image", True)):
        kind, selected_value = random.choice(candidates)
    else:
        kind, selected_value = candidates[0]

    if kind == "path":
        source_image_path = Path(selected_value)
        if not source_image_path.is_absolute():
            source_image_path = (base_dir / source_image_path).resolve()
        if not source_image_path.exists():
            raise FileNotFoundError(f"Immagine cartolina non trovata: {source_image_path}")
        logger.info("Immagine cartolina selezionata (file): %s", source_image_path.name)
        return source_image_path

    if kind == "url":
        cache_dir = base_dir / str(settings.get("postcard_cache_dir", "eqsl_assets_cache"))
        _ensure_parent(cache_dir / "dummy")
        url_hash = hashlib.sha1(selected_value.encode("utf-8")).hexdigest()[:12]
        url_basename = Path(selected_value.split("?", 1)[0]).name or "source.jpg"
        safe_name = _sanitize_filename(f"{url_hash}_{url_basename}")
        cache_file = cache_dir / safe_name
        if not cache_file.exists():
            logger.info("Download immagine cartolina: %s", selected_value)
            resp = _download_with_proxy_fallback(selected_value, timeout=45, logger=logger)
            resp.raise_for_status()
            cache_file.write_bytes(resp.content)
        logger.info("Immagine cartolina selezionata (url cached): %s", cache_file.name)
        return cache_file

    raise RuntimeError("Tipo immagine cartolina non supportato")


def _hex_to_rgb(color_hex: str) -> tuple[int, int, int]:
    value = color_hex.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return (255, 255, 255)
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except ValueError:
        return (255, 255, 255)


def _compute_postcard_text_metrics(
    img_width: int,
    img_height: int,
    text_cfg: Dict[str, Any],
) -> Dict[str, int]:
    x = int(text_cfg.get("x", 48))
    y = int(text_cfg.get("y", 48))
    font_size = int(text_cfg.get("font_size", 42))
    line_spacing = int(text_cfg.get("line_spacing", 8))
    stroke_width = int(text_cfg.get("stroke_width", 2))
    box_padding = int(text_cfg.get("box_padding", 22))

    if bool(text_cfg.get("auto_scale", True)):
        # Defaults were tuned on ~2400x1600 images. Scale up for larger photos.
        scale = min(img_width / 2400.0, img_height / 1600.0)
        scale *= float(text_cfg.get("scale_multiplier", 1.7))
        scale = max(1.0, min(scale, 6.0))
        x = int(round(x * scale))
        y = int(round(y * scale))
        font_size = max(font_size, int(round(font_size * scale)))
        line_spacing = max(4, int(round(line_spacing * scale)))
        box_padding = max(8, int(round(box_padding * scale)))
        stroke_width = max(1, int(round(stroke_width * scale)))

    return {
        "x": x,
        "y": y,
        "font_size": font_size,
        "line_spacing": line_spacing,
        "stroke_width": stroke_width,
        "box_padding": box_padding,
    }


def _render_postcard_jpg_system_drawing(
    qso: Dict[str, str],
    settings: Dict[str, Any],
    base_dir: Path,
    logger: logging.Logger,
) -> Path:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise RuntimeError("System.Drawing renderer requires PowerShell (powershell or pwsh).")

    output_dir = base_dir / str(settings.get("postcard_output_dir", "eqsl_out"))
    _ensure_parent(output_dir / "dummy")
    source_image_path = _resolve_postcard_source_image_path(base_dir, settings, logger)

    text_cfg = settings.get("postcard_text", {}) or {}
    metrics = _compute_postcard_text_metrics(2400, 1600, text_cfg)
    x = metrics["x"]
    y = metrics["y"]
    font_size = metrics["font_size"]
    line_spacing = metrics["line_spacing"]
    box_padding = metrics["box_padding"]
    quality = int(settings.get("jpeg_quality", 90))
    text_color = _hex_to_rgb(str(text_cfg.get("text_color", "#FFFFFF")))
    stroke_color = _hex_to_rgb(str(text_cfg.get("stroke_color", "#000000")))
    box_fill = text_cfg.get("box_fill_rgba", [0, 0, 0, 130])
    if not (isinstance(box_fill, list) and len(box_fill) == 4):
        box_fill = [0, 0, 0, 130]

    lines = build_postcard_lines(qso, settings)
    lines_file = output_dir / (_sanitize_filename(qso_key(qso)) + ".txt")
    lines_file.write_text("\n".join(lines), encoding="utf-8")

    outfile_name = _sanitize_filename(
        f"{qso.get('CALL','CALL')}_{qso.get('QSO_DATE','')}_{qso.get('TIME_ON','')}_{qso.get('BAND','')}_{qso_mode_display(qso)}.jpg"
    )
    out_path = output_dir / outfile_name
    src_ps = str(source_image_path).replace("'", "''")
    dst_ps = str(out_path).replace("'", "''")
    lf_ps = str(lines_file).replace("'", "''")

    # PowerShell/.NET fallback for environments without Pillow.
    # Use a simple shadow text effect instead of full stroke for reliability.
    ps_script = (
        "Add-Type -AssemblyName System.Drawing; "
        f"$src = [System.IO.Path]::GetFullPath('{src_ps}'); "
        f"$dst = [System.IO.Path]::GetFullPath('{dst_ps}'); "
        f"$lf = [System.IO.Path]::GetFullPath('{lf_ps}'); "
        "$img = [System.Drawing.Image]::FromFile($src); "
        "$bmp = New-Object System.Drawing.Bitmap $img.Width,$img.Height; "
        "$g = [System.Drawing.Graphics]::FromImage($bmp); "
        "$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality; "
        "$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic; "
        "$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit; "
        "$g.DrawImage($img,0,0,$img.Width,$img.Height); "
        f"$font = New-Object System.Drawing.Font('Arial',{font_size},[System.Drawing.FontStyle]::Bold); "
        "$lines = Get-Content -LiteralPath $lf; "
        "$maxW = 0; "
        "$lineH = [int][Math]::Ceiling($font.GetHeight($g)); "
        "foreach($line in $lines){ $sz = $g.MeasureString($line,$font); if($sz.Width -gt $maxW){ $maxW = [int][Math]::Ceiling($sz.Width) } } "
        f"$x = {x}; $y = {y}; $pad = {box_padding}; $sp = {line_spacing}; "
        "$totalH = ($lines.Count * $lineH) + (([Math]::Max($lines.Count-1,0)) * $sp); "
        "$rect = New-Object System.Drawing.Rectangle ([Math]::Max(0,$x-$pad)),([Math]::Max(0,$y-$pad)),([Math]::Min($bmp.Width-([Math]::Max(0,$x-$pad)),$maxW+$pad*2)),([Math]::Min($bmp.Height-([Math]::Max(0,$y-$pad)),$totalH+$pad*2)); "
        f"$bgBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb({int(box_fill[3])},{int(box_fill[0])},{int(box_fill[1])},{int(box_fill[2])})); "
        "$g.FillRectangle($bgBrush, $rect); "
        f"$shadowBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,{stroke_color[0]},{stroke_color[1]},{stroke_color[2]})); "
        f"$textBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,{text_color[0]},{text_color[1]},{text_color[2]})); "
        "$cy = $y; "
        "foreach($line in $lines){ "
        "  $g.DrawString($line,$font,$shadowBrush,($x+2),($cy+2)); "
        "  $g.DrawString($line,$font,$textBrush,$x,$cy); "
        "  $cy += $lineH + $sp; "
        "} "
        "$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' } | Select-Object -First 1; "
        "$enc = [System.Drawing.Imaging.Encoder]::Quality; "
        "$ep = New-Object System.Drawing.Imaging.EncoderParameters 1; "
        f"$ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter($enc, [long]{quality}); "
        "$bmp.Save($dst, $codec, $ep); "
        "$ep.Dispose(); $textBrush.Dispose(); $shadowBrush.Dispose(); $bgBrush.Dispose(); $font.Dispose(); $g.Dispose(); $bmp.Dispose(); $img.Dispose(); "
        "Write-Output $dst"
    )

    result = subprocess.run(
        [powershell, "-Command", ps_script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Render JPG fallback (System.Drawing) fallito: "
            + (result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}")
        )
    if not out_path.exists():
        raise RuntimeError(f"Render JPG fallback non ha creato il file: {out_path}")
    return out_path


def render_postcard_jpg(
    qso: Dict[str, str],
    settings: Dict[str, Any],
    base_dir: Path,
    logger: logging.Logger,
) -> Path:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pillow non disponibile, uso fallback System.Drawing: %s", exc)
        return _render_postcard_jpg_system_drawing(qso, settings, base_dir, logger)

    output_dir = base_dir / str(settings.get("postcard_output_dir", "eqsl_out"))
    _ensure_parent(output_dir / "dummy")
    source_image_path = _resolve_postcard_source_image_path(base_dir, settings, logger)
    img = Image.open(source_image_path).convert("RGB")

    draw = ImageDraw.Draw(img, "RGBA")
    text_cfg = settings.get("postcard_text", {}) or {}
    metrics = _compute_postcard_text_metrics(img.width, img.height, text_cfg)
    x = metrics["x"]
    y = metrics["y"]
    font_size = metrics["font_size"]
    line_spacing = metrics["line_spacing"]
    text_color = str(text_cfg.get("text_color", "#FFFFFF"))
    stroke_color = str(text_cfg.get("stroke_color", "#000000"))
    stroke_width = metrics["stroke_width"]
    box_padding = metrics["box_padding"]
    box_fill = text_cfg.get("box_fill_rgba", [0, 0, 0, 130])

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

    lines = build_postcard_lines(qso, settings)

    line_positions: List[int] = []
    line_heights: List[int] = []
    bbox_left: Optional[int] = None
    bbox_top: Optional[int] = None
    bbox_right: Optional[int] = None
    bbox_bottom: Optional[int] = None

    cy = y
    for line in lines:
        bbox = draw.textbbox((x, cy), line, font=font, stroke_width=stroke_width)
        line_positions.append(cy)
        line_heights.append(max(1, bbox[3] - bbox[1]))
        bbox_left = bbox[0] if bbox_left is None else min(bbox_left, bbox[0])
        bbox_top = bbox[1] if bbox_top is None else min(bbox_top, bbox[1])
        bbox_right = bbox[2] if bbox_right is None else max(bbox_right, bbox[2])
        bbox_bottom = bbox[3] if bbox_bottom is None else max(bbox_bottom, bbox[3])
        cy += max(1, bbox[3] - bbox[1]) + line_spacing

    if bbox_left is None or bbox_top is None or bbox_right is None or bbox_bottom is None:
        bbox_left, bbox_top, bbox_right, bbox_bottom = x, y, x + 1, y + 1

    box = (
        max(0, bbox_left - box_padding),
        max(0, bbox_top - box_padding),
        min(img.width, bbox_right + box_padding),
        min(img.height, bbox_bottom + box_padding),
    )
    if isinstance(box_fill, list) and len(box_fill) == 4:
        draw.rounded_rectangle(box, radius=12, fill=tuple(int(v) for v in box_fill))

    for idx, line in enumerate(lines):
        draw.text(
            (x, line_positions[idx]),
            line,
            font=font,
            fill=text_color,
            stroke_width=stroke_width,
            stroke_fill=stroke_color,
        )

    outfile_name = _sanitize_filename(
        f"{qso.get('CALL','CALL')}_{qso.get('QSO_DATE','')}_{qso.get('TIME_ON','')}_{qso.get('BAND','')}_{qso_mode_display(qso)}.jpg"
    )
    out_path = output_dir / outfile_name
    img.save(out_path, format="JPEG", quality=int(settings.get("jpeg_quality", 90)), optimize=True)
    return out_path


def _render_template(template: str, values: Dict[str, str]) -> str:
    return template.format_map(_SafeFormatDict(values))


def send_email_with_attachment(
    recipient_email: str,
    qso: Dict[str, str],
    postcard_path: Path,
    settings: Dict[str, Any],
    base_dir: Path,
) -> str:
    sender_email = str(settings.get("sender_email") or "").strip()
    if not sender_email:
        raise RuntimeError("eqsl_settings.json: sender_email obbligatorio")

    sender_name = str(settings.get("sender_name") or qso.get("STATION_CALLSIGN") or "").strip()
    if not sender_name:
        sender_name = sender_email

    password_file = str(settings.get("gmail_app_password_file") or "").strip()
    if not password_file:
        raise RuntimeError("eqsl_settings.json: gmail_app_password_file obbligatorio")
    smtp_password = _read_secret_from_file(base_dir, password_file)
    # Gmail app passwords are often copied as groups of 4 chars with spaces.
    # Normalize all whitespace so both formats work.
    smtp_password = "".join(smtp_password.split())

    values = build_qso_template_values(qso, settings)
    subject = _render_template(str(settings.get("subject_template", "")), values)
    body = _render_template(str(settings.get("body_template", "")), values)

    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{uuid.uuid4()}@local.eqsl>"
    msg.set_content(body)

    with postcard_path.open("rb") as fp:
        data = fp.read()
    msg.add_attachment(data, maintype="image", subtype="jpeg", filename=postcard_path.name)

    host = str(settings.get("smtp_host") or "smtp.gmail.com")
    port = int(settings.get("smtp_port") or 465)
    use_ssl = bool(settings.get("smtp_ssl", True))

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(sender_email, smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(sender_email, smtp_password)
            smtp.send_message(msg)
    return str(msg["Message-ID"])


def _mark_qso_sent(
    sent_store: Dict[str, Any],
    key: str,
    qso: Dict[str, str],
    recipient_email: str,
    source: str,
    postcard_path: Path,
    message_id: str,
) -> None:
    sent_store.setdefault("sent_by_qso_key", {})[key] = {
        "sent_at_utc": datetime.now(timezone.utc).isoformat(),
        "recipient_email": recipient_email,
        "source": source,
        "message_id": message_id,
        "postcard_path": str(postcard_path),
        "call": qso.get("CALL", ""),
        "qso_date": qso.get("QSO_DATE", ""),
        "time_on": qso.get("TIME_ON", ""),
        "band": qso.get("BAND", ""),
        "mode": qso.get("MODE", ""),
        "submode": qso.get("SUBMODE", ""),
    }


def _is_smtp_throttle_error(exc: Exception) -> bool:
    codes = {421, 450, 451, 452, 454}
    msg = str(exc).lower()
    if isinstance(exc, smtplib.SMTPResponseException):
        try:
            if int(exc.smtp_code) in codes:
                return True
        except Exception:
            pass
        try:
            smtp_msg = exc.smtp_error.decode("utf-8", errors="ignore").lower()
            msg = f"{msg} {smtp_msg}"
        except Exception:
            pass

    keywords = (
        "rate limit",
        "too many",
        "try again later",
        "temporarily deferred",
        "temporarily unavailable",
        "daily user sending quota exceeded",
        "user-rate limit exceeded",
        "4.7.0",
        "4.7.1",
        "throttle",
    )
    return any(k in msg for k in keywords)


def _sleep_if_positive(sleep_fn: Callable[[float], None], seconds: float) -> float:
    wait = float(seconds or 0)
    if wait > 0:
        sleep_fn(wait)
        return wait
    return 0.0


def process_eqsl_records(
    qsos: List[Dict[str, str]],
    settings: Dict[str, Any],
    sent_store: Dict[str, Any],
    logger: logging.Logger,
    lookup_email_fn: Callable[[Dict[str, str]], tuple[str, str]],
    render_postcard_fn: Callable[[Dict[str, str]], Path],
    send_email_fn: Callable[[str, Dict[str, str], Path], str],
    persist_sent_store_fn: Optional[Callable[[Dict[str, Any]], None]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.monotonic,
    random_fn: Callable[[], float] = random.random,
) -> Dict[str, int]:
    sent_map = sent_store.setdefault("sent_by_qso_key", {})
    dry_run = bool(settings.get("dry_run", True))
    delay = float(settings.get("delay_sec_between_emails", 0))
    delay_jitter = float(settings.get("delay_jitter_sec", 0) or 0)
    max_per_run = int(settings.get("max_emails_per_run", 0) or 0)
    anti_block_enabled = bool(settings.get("anti_block_enabled", True))
    batch_size = int(settings.get("batch_size", 0) or 0)
    batch_pause_sec = float(settings.get("batch_pause_sec", 0) or 0)
    max_per_hour = int(settings.get("max_emails_per_hour", 0) or 0)
    hour_window_sec = float(settings.get("hour_window_sec", 3600) or 3600)
    smtp_throttle_backoff_sec = float(settings.get("smtp_throttle_backoff_sec", 300) or 0)
    stop_run_on_smtp_throttle = bool(settings.get("stop_run_on_smtp_throttle", True))
    max_consecutive_send_errors = int(settings.get("max_consecutive_send_errors", 0) or 0)

    sent_timestamps: List[float] = []
    consecutive_send_errors = 0

    summary = {
        "total_qso": len(qsos),
        "already_sent": 0,
        "no_email": 0,
        "attempted": 0,
        "sent": 0,
        "dry_run": 0,
        "errors": 0,
        "throttled": 0,
        "rate_pause_count": 0,
        "rate_pause_seconds": 0.0,
    }

    def _prune_send_timestamps(now: float) -> None:
        if hour_window_sec <= 0:
            return
        while sent_timestamps and (now - sent_timestamps[0]) >= hour_window_sec:
            sent_timestamps.pop(0)

    def _anti_block_wait_before_send() -> None:
        if dry_run or not anti_block_enabled:
            return

        # Enforce rolling hourly cap before each send.
        if max_per_hour > 0 and hour_window_sec > 0:
            now = float(time_fn())
            _prune_send_timestamps(now)
            if len(sent_timestamps) >= max_per_hour:
                wait_hour = max(0.0, hour_window_sec - (now - sent_timestamps[0]) + 0.25)
                if wait_hour > 0:
                    logger.warning(
                        "eQSL anti-block: hourly limit reached (%s/%s in %.0fs), sleeping %.1fs",
                        len(sent_timestamps),
                        max_per_hour,
                        hour_window_sec,
                        wait_hour,
                    )
                    summary["rate_pause_count"] += 1
                    summary["rate_pause_seconds"] += _sleep_if_positive(sleep_fn, wait_hour)
                    _prune_send_timestamps(float(time_fn()))

        # Pause every N sent messages to reduce burst behavior.
        if batch_size > 0 and batch_pause_sec > 0 and summary["sent"] > 0 and (summary["sent"] % batch_size) == 0:
            logger.info(
                "eQSL anti-block: batch pause after %s sent messages (sleep %.1fs)",
                summary["sent"],
                batch_pause_sec,
            )
            summary["rate_pause_count"] += 1
            summary["rate_pause_seconds"] += _sleep_if_positive(sleep_fn, batch_pause_sec)

        # Inter-message delay + jitter to avoid mechanical timing patterns.
        if summary["sent"] > 0:
            wait = max(0.0, delay)
            if delay_jitter > 0:
                wait += max(0.0, delay_jitter) * max(0.0, min(1.0, float(random_fn())))
            if wait > 0:
                summary["rate_pause_seconds"] += _sleep_if_positive(sleep_fn, wait)

    for qso in qsos:
        key = qso_key(qso)
        if key in sent_map:
            summary["already_sent"] += 1
            continue

        if max_per_run > 0 and (summary["sent"] + summary["dry_run"]) >= max_per_run:
            logger.info("eQSL: raggiunto max_emails_per_run=%s", max_per_run)
            break

        recipient_email, source = lookup_email_fn(qso)
        if not recipient_email:
            summary["no_email"] += 1
            logger.info("eQSL skip (no email): %s", key)
            continue

        try:
            postcard_path = render_postcard_fn(qso)
            summary["attempted"] += 1
            if dry_run:
                summary["dry_run"] += 1
                logger.info("eQSL dry-run -> %s (%s)", recipient_email, key)
            else:
                _anti_block_wait_before_send()
                message_id = send_email_fn(recipient_email, qso, postcard_path)
                _mark_qso_sent(sent_store, key, qso, recipient_email, source, postcard_path, message_id)
                if persist_sent_store_fn is not None:
                    persist_sent_store_fn(sent_store)
                summary["sent"] += 1
                sent_timestamps.append(float(time_fn()))
                _prune_send_timestamps(sent_timestamps[-1])
                consecutive_send_errors = 0
                logger.info("eQSL sent -> %s (%s)", recipient_email, key)
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            consecutive_send_errors += 1
            logger.exception("eQSL errore per %s: %s", key, exc)

            if not dry_run and anti_block_enabled and _is_smtp_throttle_error(exc):
                summary["throttled"] += 1
                if smtp_throttle_backoff_sec > 0:
                    logger.warning(
                        "eQSL anti-block: SMTP throttle detected, backoff %.1fs",
                        smtp_throttle_backoff_sec,
                    )
                    summary["rate_pause_count"] += 1
                    summary["rate_pause_seconds"] += _sleep_if_positive(sleep_fn, smtp_throttle_backoff_sec)
                if stop_run_on_smtp_throttle:
                    logger.warning("eQSL anti-block: stopping run after SMTP throttle error")
                    break

            if (
                not dry_run
                and anti_block_enabled
                and max_consecutive_send_errors > 0
                and consecutive_send_errors >= max_consecutive_send_errors
            ):
                logger.warning(
                    "eQSL anti-block: stopping run after %s consecutive send errors",
                    consecutive_send_errors,
                )
                break

    return summary


def run_eqsl_for_adif(session: requests.Session, cfg: Dict[str, Any], logger: logging.Logger) -> Dict[str, int]:
    base_dir = Path(__file__).resolve().parent
    settings_path, settings = load_eqsl_settings(base_dir)
    if not settings.get("enabled", False):
        logger.info("eQSL email disabilitato (%s: enabled=false)", settings_path.name)
        return {
            "total_qso": 0,
            "already_sent": 0,
            "no_email": 0,
            "attempted": 0,
            "sent": 0,
            "dry_run": 0,
            "errors": 0,
        }

    adif_path_value = str(cfg.get("adif_path") or "").strip()
    if not adif_path_value:
        raise RuntimeError("adif_path non configurato per processare eQSL")
    adif_path = Path(adif_path_value)
    if not adif_path.exists():
        raise FileNotFoundError(f"File ADIF non trovato per eQSL: {adif_path}")

    qsos = parse_adif_records(adif_path)
    logger.info("eQSL: QSO letti da ADIF: %s", len(qsos))

    sent_store_path = base_dir / str(settings.get("sent_store_file", "eqsl_sent.json"))
    contacts_cache_path = base_dir / str(settings.get("qrz_contacts_cache_file", "eqsl_contacts_cache.json"))
    sent_store = load_sent_store(sent_store_path)
    contacts_cache = load_contacts_cache(contacts_cache_path)

    def lookup_email_fn(qso: Dict[str, str]) -> tuple[str, str]:
        email = _resolve_email_from_qso(qso)
        if email:
            return email, "adif"
        call = (qso.get("CALL") or "").strip()
        if not call:
            return "", ""
        email = lookup_email_via_qrz_html(
            session,
            call,
            settings,
            logger,
            contacts_cache,
            persist_contacts_cache_fn=lambda payload: save_json(contacts_cache_path, payload),
        )
        return email, ("qrz_html" if email else "")

    def render_postcard_fn(qso: Dict[str, str]) -> Path:
        return render_postcard_jpg(qso, settings, base_dir, logger)

    def send_email_fn(recipient_email: str, qso: Dict[str, str], postcard_path: Path) -> str:
        return send_email_with_attachment(recipient_email, qso, postcard_path, settings, base_dir)

    summary = process_eqsl_records(
        qsos=qsos,
        settings=settings,
        sent_store=sent_store,
        logger=logger,
        lookup_email_fn=lookup_email_fn,
        render_postcard_fn=render_postcard_fn,
        send_email_fn=send_email_fn,
        persist_sent_store_fn=lambda payload: save_json(sent_store_path, payload),
    )

    save_json(contacts_cache_path, contacts_cache)
    save_json(sent_store_path, sent_store)

    logger.info(
        "eQSL summary: total=%s already_sent=%s no_email=%s attempted=%s sent=%s dry_run=%s errors=%s",
        summary["total_qso"],
        summary["already_sent"],
        summary["no_email"],
        summary["attempted"],
        summary["sent"],
        summary["dry_run"],
        summary["errors"],
    )
    if summary["sent"] or summary["dry_run"]:
        print("eQSL summary:", summary)
    return summary
