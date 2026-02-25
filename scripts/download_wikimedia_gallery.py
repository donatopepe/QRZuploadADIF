from __future__ import annotations

import html
import json
import re
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

import requests

API_URL = "https://commons.wikimedia.org/w/api.php"
ROOT_CATEGORIES = [
    "Category:Architecture in Pieve di Cento",
    "Category:Streets in Pieve di Cento",
    "Category:Urban squares in Pieve di Cento",
    "Category:Pieve_di_Cento",
]
MAX_SUBCATEGORY_DEPTH = 2
USER_AGENT = "QRZuploadADIF/1.0 (gallery downloader)"
OUTPUT_DIR = Path("gallery") / "pieve_di_cento_landscapes"
ATTRIBUTIONS_MD = Path("gallery") / "ATTRIBUTIONS.md"
INDEX_TSV = OUTPUT_DIR / "INDEX.tsv"
RASTER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_DOWNLOADS = 24
DOWNLOAD_DELAY_SEC = 0.35


def _log(message: str) -> None:
    print(message, flush=True)


def _strip_html(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"(?is)<br\s*/?>", " | ", text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _sanitize_filename(name: str) -> str:
    name = name.replace("/", "_").replace("\\", "_").strip()
    name = re.sub(r"[^\w.\- ]+", "_", name, flags=re.ASCII)
    name = re.sub(r"\s+", "_", name)
    return name[:200] or "image"


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _api_get(session: requests.Session, **params: Any) -> dict[str, Any]:
    base = {"action": "query", "format": "json"}
    base.update({k: v for k, v in params.items() if v is not None})
    resp = session.get(API_URL, params=base, timeout=45)
    resp.raise_for_status()
    return resp.json()


def _collect_files(session: requests.Session, root_categories: list[str], max_depth: int) -> list[str]:
    queue: deque[tuple[str, int]] = deque((cat, 0) for cat in root_categories)
    seen_categories = set(root_categories)
    seen_files: set[str] = set()

    while queue:
        category, depth = queue.popleft()
        cmcontinue: str | None = None
        while True:
            data = _api_get(
                session,
                list="categorymembers",
                cmtitle=category,
                cmlimit="500",
                cmcontinue=cmcontinue if cmcontinue else None,
            )
            for member in data.get("query", {}).get("categorymembers", []):
                title = str(member.get("title", ""))
                ns = int(member.get("ns", -1))
                if ns == 6 and title.startswith("File:"):
                    seen_files.add(title)
                elif ns == 14 and depth < max_depth and title not in seen_categories:
                    seen_categories.add(title)
                    queue.append((title, depth + 1))
            cmcontinue = data.get("continue", {}).get("cmcontinue")
            if not cmcontinue:
                break

    return sorted(seen_files)


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _file_metadata_records(session: requests.Session, titles: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for group in _chunk(titles, 50):
        data = _api_get(
            session,
            prop="imageinfo",
            titles="|".join(group),
            iiprop="url|size|extmetadata",
            iiurlwidth="2400",
        )
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            title = str(page.get("title", ""))
            if not title.startswith("File:"):
                continue
            suffix = Path(title[5:]).suffix.lower()
            if suffix not in RASTER_EXTENSIONS:
                continue

            imageinfo = (page.get("imageinfo") or [])
            if not imageinfo:
                continue
            info = imageinfo[0]
            meta = info.get("extmetadata") or {}

            license_short = _strip_html(str((meta.get("LicenseShortName") or {}).get("value", "")))
            if not license_short:
                continue

            record = {
                "pageid": page.get("pageid", 0),
                "title": title,
                "basename": title[5:],
                "source_page": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
                "download_url": info.get("thumburl") or info.get("url"),
                "original_url": info.get("url"),
                "width": info.get("width"),
                "height": info.get("height"),
                "thumbwidth": info.get("thumbwidth"),
                "thumbheight": info.get("thumbheight"),
                "license_short": license_short,
                "license_url": _strip_html(str((meta.get("LicenseUrl") or {}).get("value", ""))),
                "artist": _strip_html(str((meta.get("Artist") or {}).get("value", ""))),
                "credit": _strip_html(str((meta.get("Credit") or {}).get("value", ""))),
                "description": _strip_html(str((meta.get("ImageDescription") or {}).get("value", ""))),
                "attribution_required": _strip_html(str((meta.get("AttributionRequired") or {}).get("value", ""))),
            }
            if record["download_url"]:
                results.append(record)
    return results


def _score_landscape_candidate(rec: dict[str, Any]) -> tuple[int, list[str]]:
    title = str(rec.get("title", "")).lower()
    desc = str(rec.get("description", "")).lower()
    artist = str(rec.get("artist", "")).lower()
    text = " ".join([title, desc, artist])

    if "pieve" not in text:
        return (-999, ["no-pieve"])

    score = 0
    reasons: list[str] = []

    positive = {
        "piazza": 6,
        "via ": 5,
        "portico": 6,
        "palazzo": 6,
        "rocca": 6,
        "chiesa": 5,
        "church": 5,
        "street": 4,
        "square": 4,
        "stazione": 4,
        "tramway station": 5,
        "porta ": 4,
        "panoramio": 2,
        "facciata": 3,
        "facade": 3,
        "night": 2,
        "centro": 4,
    }
    for token, pts in positive.items():
        if token in text:
            score += pts
            reasons.append(f"+{token}")

    negative = {
        "lamborghini": -20,
        "tractor": -20,
        "tracteur": -20,
        "traktor": -20,
        "red ronnie": -20,
        "ronnie": -15,
        "ritratto": -12,
        "portrait": -12,
        "firma": -8,
        "lettera": -8,
        "licenza": -8,
        "agenda": -8,
        "album-": -8,
        "scolo bisana": -25,
        "lavori-scolo-bisana": -25,
        "mosca": -10,
        "cavicchi": -10,
        "zacchini": -10,
        "funerali": -20,
        "bustine di zucchero": -20,
        "museo delle storie": -5,
    }
    for token, pts in negative.items():
        if token in text:
            score += pts
            reasons.append(f"{pts}:{token}")

    try:
        w = int(rec.get("width") or 0)
        h = int(rec.get("height") or 0)
        if w and h:
            if w >= h:
                score += 3
                reasons.append("+landscape")
            else:
                score -= 2
                reasons.append("-portrait")
    except Exception:  # noqa: BLE001
        pass

    return (score, reasons)


def _select_postcard_candidates(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for rec in records:
        score, reasons = _score_landscape_candidate(rec)
        rec["landscape_score"] = score
        rec["landscape_reasons"] = reasons
        if score >= 5:
            scored.append(rec)

    scored.sort(
        key=lambda r: (
            int(r.get("landscape_score", 0)),
            int(r.get("width") or 0) * int(r.get("height") or 0),
            str(r.get("title", "")),
        ),
        reverse=True,
    )
    return scored[:limit]


def _download_file(session: requests.Session, url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with session.get(url, stream=True, timeout=120) as resp:
                if resp.status_code == 429:
                    raise requests.HTTPError("429 Too Many Requests", response=resp)
                resp.raise_for_status()
                with destination.open("wb") as fp:
                    for chunk in resp.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            fp.write(chunk)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if destination.exists() and destination.stat().st_size == 0:
                destination.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    if last_exc:
        raise last_exc


def _write_outputs(records: list[dict[str, Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []
    md_lines: list[str] = [
        "# Gallery Attributions",
        "",
        "Free-license images downloaded from Wikimedia Commons for postcard backgrounds.",
        "",
        "Selection preference: landscapes / urban scenery (streets, squares, architecture) in Pieve di Cento.",
        "",
        "Source categories (Wikimedia Commons):",
        "",
        *[f"- `{cat}`" for cat in ROOT_CATEGORIES],
        "",
        "| Local File | Commons File | Author | License | Source |",
        "|---|---|---|---|---|",
    ]

    for rec in sorted(records, key=lambda r: str(r["local_name"]).lower()):
        rows.append(
            "\t".join(
                [
                    str(rec["local_name"]),
                    str(rec["title"]),
                    str(rec.get("artist", "")),
                    str(rec.get("license_short", "")),
                    str(rec.get("landscape_score", "")),
                    str(rec.get("license_url", "")),
                    str(rec.get("source_page", "")),
                    str(rec.get("original_url", "")),
                    str(rec.get("description", "")),
                ]
            )
        )
        md_lines.append(
            "| "
            + " | ".join(
                [
                    f"`gallery/{OUTPUT_DIR.name}/{rec['local_name']}`",
                    rec["title"].replace("|", " "),
                    (str(rec.get("artist", "")) or "-").replace("|", " "),
                    (str(rec.get("license_short", "")) or "-").replace("|", " "),
                    f"[link]({rec['source_page']})",
                ]
            )
            + " |"
        )

    INDEX_TSV.write_text(
        "local_name\ttitle\tartist\tlicense_short\tlandscape_score\tlicense_url\tsource_page\toriginal_url\tdescription\n"
        + "\n".join(rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )
    ATTRIBUTIONS_MD.write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def main() -> int:
    with _session() as session:
        _log("Scanning Commons categories ...")
        for cat in ROOT_CATEGORIES:
            _log(f" - {cat}")
        file_titles = _collect_files(session, ROOT_CATEGORIES, MAX_SUBCATEGORY_DEPTH)
        _log(f"Found {len(file_titles)} file entries before filtering")

        records = _file_metadata_records(session, file_titles)
        _log(f"Eligible raster images with license metadata: {len(records)}")
        records = _select_postcard_candidates(records, MAX_DOWNLOADS)
        _log(f"Selected postcard candidates (landscape-biased): {len(records)}")

        local_names_seen: set[str] = set()
        for rec in records:
            stem = _sanitize_filename(Path(str(rec["basename"])).stem)
            suffix = Path(str(rec["basename"])).suffix.lower()
            local_name = _sanitize_filename(f"{rec['pageid']}_{stem}{suffix}")
            if local_name in local_names_seen:
                local_name = _sanitize_filename(f"{rec['pageid']}_{stem}_{abs(hash(rec['title'])) % 10000}{suffix}")
            local_names_seen.add(local_name)
            rec["local_name"] = local_name

            destination = OUTPUT_DIR / local_name
            try:
                _download_file(session, str(rec["download_url"]), destination)
            except Exception as exc:  # noqa: BLE001
                _log(f"SKIP download failed: {rec['title']} -> {exc}")
                rec["download_failed"] = True
                continue
            rec["download_failed"] = False
            if destination.exists():
                rec["bytes"] = destination.stat().st_size
            time.sleep(DOWNLOAD_DELAY_SEC)

        downloaded = [r for r in records if not r.get("download_failed")]
        _write_outputs(downloaded)
        _log(f"Downloaded {len(downloaded)} images into {OUTPUT_DIR}")
        _log(f"Wrote attribution files: {ATTRIBUTIONS_MD}, {INDEX_TSV}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
