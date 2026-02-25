import json
import logging
import os
import re
import tempfile
import unittest
from pathlib import Path

import eqsl_service
import requests


def _logger() -> logging.Logger:
    logger = logging.getLogger("eqsl_service_tests")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)
    return logger


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, html_by_url):
        self.html_by_url = html_by_url
        self.calls = []
        self.trust_env = True

    def request(self, method, url, timeout=20, headers=None, **kwargs):
        self.calls.append({"method": method, "url": url, "timeout": timeout, "headers": headers or {}, "kwargs": kwargs})
        return FakeResponse(self.html_by_url.get(url, ""), 200)


class EqslServiceTests(unittest.TestCase):
    def test_parse_adif_records_minimal(self):
        adif = """ADIF Export\n<EOH>\n<CALL:6>N0CALL<QSO_DATE:8>20260225<TIME_ON:6>101500<MODE:4>MFSK<SUBMODE:3>FT4<BAND:3>20m<EMAIL:15>test@example.com<EOR>\n<CALL:6>VR2VGM<QSO_DATE:8>20260225<TIME_ON:6>102000<MODE:3>FT8<BAND:3>40m<EOR>\n"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sample.adi"
            p.write_text(adif, encoding="utf-8")
            rows = eqsl_service.parse_adif_records(p)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["CALL"], "N0CALL")
        self.assertEqual(rows[0]["SUBMODE"], "FT4")
        self.assertEqual(rows[0]["EMAIL"], "test@example.com")
        self.assertEqual(rows[1]["CALL"], "VR2VGM")
        self.assertNotIn("EMAIL", rows[1])

    def test_qso_key_uses_radio_identity_fields(self):
        qso = {
            "CALL": "vr2vgm",
            "QSO_DATE": "20260225",
            "TIME_ON": "091530",
            "BAND": "10m",
            "MODE": "FT8",
            "SUBMODE": "FT4",
        }
        self.assertEqual(eqsl_service.qso_key(qso), "VR2VGM|20260225|091530|10m|FT8")

    def test_build_postcard_lines_personalizes_useful_radio_fields(self):
        qso = {
            "CALL": "VR2VGM",
            "QSO_DATE": "20260225",
            "TIME_ON": "091530",
            "BAND": "10m",
            "MODE": "MFSK",
            "SUBMODE": "FT4",
            "RST_SENT": "-07",
            "RST_RCVD": "-12",
            "STATION_CALLSIGN": "N0CALL",
            "MY_GRIDSQUARE": "JN54PR",
            "GRIDSQUARE": "OL72",
            "QRB": "9158",
            "TX_PWR": "100",
        }
        lines = eqsl_service.build_postcard_lines(qso, eqsl_service.DEFAULT_EQSL_SETTINGS)
        text = "\n".join(lines)

        self.assertIn("N0CALL", text)
        self.assertIn("VR2VGM", text)
        self.assertIn("10m", text)
        self.assertIn("MFSK/FT4", text)
        self.assertIn("RST TX/RX: -07 / -12", text)
        self.assertIn("JN54PR", text)
        self.assertIn("OL72", text)
        self.assertIn("QRB 9158 km", text)
        self.assertIn("Pwr 100 W", text)

    def test_extract_first_email_plain_mailto(self):
        html = '<div><a href="mailto:test.user+radio@example.net">mail</a></div>'
        self.assertEqual(eqsl_service._extract_first_email(html), "test.user+radio@example.net")

    def test_decode_qrz_qmail(self):
        obfuscated = "e93a6432e81m9o3c8.8l0i0aam7g5@2mag3v423r4v!61"
        decoded = eqsl_service._decode_qrz_qmail(obfuscated)
        self.assertRegex(decoded, r"^[^@]+@[^@]+\.[^@]+$")
        self.assertEqual(len(decoded), 16)

    def test_extract_first_email_from_saved_qrz_html_fixture(self):
        fixture = Path("Documenti") / "VR2VGM - Callsign Lookup by QRZ Ham Radio.html"
        if not fixture.exists():
            self.skipTest("QRZ HTML fixture not present")

        html = fixture.read_text(encoding="utf-8", errors="ignore")
        email = eqsl_service._extract_first_email(html)

        self.assertRegex(email, r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
        self.assertNotIn("!", email)

        m = re.search(r"var\s+qmail\s*=\s*'([^']+)'", html)
        self.assertIsNotNone(m, "Fixture should contain qmail obfuscation")
        decoded = eqsl_service._decode_qrz_qmail(m.group(1))
        self.assertEqual(email, decoded)

    def test_lookup_email_via_qrz_html_uses_cache(self):
        html = "<html><a href='mailto:cached@test.net'>x</a></html>"
        session = FakeSession({"https://www.qrz.com/db/VR2VGM": html})
        contacts_cache = {"schema_version": 1, "emails_by_callsign": {}}
        settings = {"qrz_lookup_enabled": True, "qrz_lookup_timeout_sec": 5}

        first = eqsl_service.lookup_email_via_qrz_html(session, "VR2VGM", settings, _logger(), contacts_cache)
        second = eqsl_service.lookup_email_via_qrz_html(session, "VR2VGM", settings, _logger(), contacts_cache)

        self.assertEqual(first, "cached@test.net")
        self.assertEqual(second, "cached@test.net")
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(contacts_cache["emails_by_callsign"]["VR2VGM"]["source"], "qrz_html")

    def test_lookup_email_via_qrz_html_persists_contacts_cache_on_update(self):
        html = "<html><a href='mailto:persisted@test.net'>x</a></html>"
        session = FakeSession({"https://www.qrz.com/db/VR2VGM": html})
        contacts_cache = {"schema_version": 1, "emails_by_callsign": {}}
        settings = {"qrz_lookup_enabled": True, "qrz_lookup_timeout_sec": 5}
        persisted_snapshots = []

        def persist_fn(payload):
            persisted_snapshots.append(json.loads(json.dumps(payload)))

        email = eqsl_service.lookup_email_via_qrz_html(
            session,
            "VR2VGM",
            settings,
            _logger(),
            contacts_cache,
            persist_contacts_cache_fn=persist_fn,
        )

        self.assertEqual(email, "persisted@test.net")
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(len(persisted_snapshots), 1)
        self.assertEqual(
            persisted_snapshots[0]["emails_by_callsign"]["VR2VGM"]["email"],
            "persisted@test.net",
        )

    def test_lookup_email_via_qrz_html_retries_after_proxy_error(self):
        class ProxyThenOkSession:
            def __init__(self):
                self.trust_env = True
                self.calls = 0

            def request(self, method, url, timeout=20, headers=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise requests.exceptions.ProxyError("proxy refused")
                return FakeResponse("<html><a href='mailto:retry@test.net'>ok</a></html>", 200)

        session = ProxyThenOkSession()
        contacts_cache = {"schema_version": 1, "emails_by_callsign": {}}
        settings = {"qrz_lookup_enabled": True, "qrz_lookup_timeout_sec": 5}

        email = eqsl_service.lookup_email_via_qrz_html(session, "VR2VGM", settings, _logger(), contacts_cache)

        self.assertEqual(email, "retry@test.net")
        self.assertEqual(session.calls, 2)
        self.assertFalse(session.trust_env)

    def test_load_or_create_json_creates_and_merges_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cfg.json"
            default = {"a": 1, "nested": {"x": 1, "y": 2}}
            created = eqsl_service.load_or_create_json(p, default)
            self.assertEqual(created["nested"]["y"], 2)

            p.write_text(json.dumps({"a": 9, "nested": {"x": 7}}), encoding="utf-8")
            merged = eqsl_service.load_or_create_json(p, default)
            self.assertEqual(merged["a"], 9)
            self.assertEqual(merged["nested"]["x"], 7)
            self.assertEqual(merged["nested"]["y"], 2)

    def test_process_eqsl_records_sends_and_is_idempotent(self):
        qsos = [
            {
                "CALL": "VR2VGM",
                "QSO_DATE": "20260225",
                "TIME_ON": "091530",
                "BAND": "10m",
                "MODE": "FT8",
            }
        ]
        settings = {"dry_run": False, "delay_sec_between_emails": 0, "max_emails_per_run": 0}
        sent_store = {"schema_version": 1, "sent_by_qso_key": {}}
        calls = {"lookup": 0, "render": 0, "send": 0}

        with tempfile.TemporaryDirectory() as td:
            postcard_path = Path(td) / "qso.jpg"
            postcard_path.write_bytes(b"jpg")

            def lookup(qso):
                calls["lookup"] += 1
                return "ham@example.net", "qrz_html"

            def render(qso):
                calls["render"] += 1
                return postcard_path

            def send(recipient, qso, path):
                calls["send"] += 1
                self.assertEqual(recipient, "ham@example.net")
                self.assertEqual(path, postcard_path)
                return "<msg@test>"

            summary1 = eqsl_service.process_eqsl_records(
                qsos=qsos,
                settings=settings,
                sent_store=sent_store,
                logger=_logger(),
                lookup_email_fn=lookup,
                render_postcard_fn=render,
                send_email_fn=send,
                sleep_fn=lambda _: None,
            )
            summary2 = eqsl_service.process_eqsl_records(
                qsos=qsos,
                settings=settings,
                sent_store=sent_store,
                logger=_logger(),
                lookup_email_fn=lookup,
                render_postcard_fn=render,
                send_email_fn=send,
                sleep_fn=lambda _: None,
            )

        self.assertEqual(summary1["sent"], 1)
        self.assertEqual(summary1["already_sent"], 0)
        self.assertEqual(summary2["sent"], 0)
        self.assertEqual(summary2["already_sent"], 1)
        self.assertEqual(calls["send"], 1)
        self.assertIn(eqsl_service.qso_key(qsos[0]), sent_store["sent_by_qso_key"])

    def test_process_eqsl_records_dry_run_does_not_mark_sent(self):
        qsos = [{"CALL": "K1ABC", "QSO_DATE": "20260225", "TIME_ON": "101500", "BAND": "20m", "MODE": "FT8"}]
        settings = {"dry_run": True, "delay_sec_between_emails": 0, "max_emails_per_run": 0}
        sent_store = {"schema_version": 1, "sent_by_qso_key": {}}

        with tempfile.TemporaryDirectory() as td:
            postcard = Path(td) / "p.jpg"
            postcard.write_bytes(b"x")
            summary = eqsl_service.process_eqsl_records(
                qsos=qsos,
                settings=settings,
                sent_store=sent_store,
                logger=_logger(),
                lookup_email_fn=lambda q: ("k1abc@example.org", "adif"),
                render_postcard_fn=lambda q: postcard,
                send_email_fn=lambda recipient, qso, path: "<unused>",
                sleep_fn=lambda _: None,
            )

        self.assertEqual(summary["dry_run"], 1)
        self.assertEqual(summary["sent"], 0)
        self.assertEqual(sent_store["sent_by_qso_key"], {})

    def test_process_eqsl_records_sends_two_different_qsos_to_same_email(self):
        qsos = [
            {"CALL": "K1ABC", "QSO_DATE": "20260225", "TIME_ON": "101500", "BAND": "20m", "MODE": "FT8"},
            {"CALL": "K1ABC", "QSO_DATE": "20260225", "TIME_ON": "101600", "BAND": "20m", "MODE": "FT8"},
        ]
        settings = {"dry_run": False, "delay_sec_between_emails": 0, "max_emails_per_run": 0}
        sent_store = {"schema_version": 1, "sent_by_qso_key": {}}
        sent_calls = []

        with tempfile.TemporaryDirectory() as td:
            postcard = Path(td) / "p.jpg"
            postcard.write_bytes(b"x")

            def lookup(qso):
                return "same-recipient@example.org", "qrz_html"

            def render(qso):
                return postcard

            def send(recipient, qso, path):
                sent_calls.append((recipient, qso["TIME_ON"]))
                return f"<{qso['TIME_ON']}@test>"

            summary = eqsl_service.process_eqsl_records(
                qsos=qsos,
                settings=settings,
                sent_store=sent_store,
                logger=_logger(),
                lookup_email_fn=lookup,
                render_postcard_fn=render,
                send_email_fn=send,
                sleep_fn=lambda _: None,
            )

        self.assertEqual(summary["sent"], 2)
        self.assertEqual(summary["already_sent"], 0)
        self.assertEqual(len(sent_calls), 2)
        self.assertTrue(all(recipient == "same-recipient@example.org" for recipient, _ in sent_calls))
        self.assertEqual(len(sent_store["sent_by_qso_key"]), 2)

    def test_process_eqsl_records_persists_sent_store_after_each_send(self):
        qsos = [
            {"CALL": "K1ABC", "QSO_DATE": "20260225", "TIME_ON": "101500", "BAND": "20m", "MODE": "FT8"},
            {"CALL": "K1ABC", "QSO_DATE": "20260225", "TIME_ON": "101600", "BAND": "20m", "MODE": "FT8"},
        ]
        settings = {"dry_run": False, "delay_sec_between_emails": 0, "max_emails_per_run": 0}
        sent_store = {"schema_version": 1, "sent_by_qso_key": {}}
        persisted_counts: list[int] = []

        with tempfile.TemporaryDirectory() as td:
            postcard = Path(td) / "p.jpg"
            postcard.write_bytes(b"x")

            def persist(payload):
                persisted_counts.append(len(payload.get("sent_by_qso_key", {})))

            summary = eqsl_service.process_eqsl_records(
                qsos=qsos,
                settings=settings,
                sent_store=sent_store,
                logger=_logger(),
                lookup_email_fn=lambda q: ("same@example.org", "qrz_html"),
                render_postcard_fn=lambda q: postcard,
                send_email_fn=lambda recipient, qso, path: f"<{qso['TIME_ON']}@test>",
                persist_sent_store_fn=persist,
                sleep_fn=lambda _: None,
            )

        self.assertEqual(summary["sent"], 2)
        self.assertEqual(persisted_counts, [1, 2])

    def test_process_eqsl_records_handles_missing_email_and_max_per_run(self):
        qsos = [
            {"CALL": "A1", "QSO_DATE": "20260225", "TIME_ON": "100000", "BAND": "20m", "MODE": "FT8"},
            {"CALL": "A2", "QSO_DATE": "20260225", "TIME_ON": "100100", "BAND": "20m", "MODE": "FT8"},
            {"CALL": "A3", "QSO_DATE": "20260225", "TIME_ON": "100200", "BAND": "20m", "MODE": "FT8"},
        ]
        settings = {"dry_run": False, "delay_sec_between_emails": 0, "max_emails_per_run": 1}
        sent_store = {"schema_version": 1, "sent_by_qso_key": {}}
        lookup_calls = []

        with tempfile.TemporaryDirectory() as td:
            postcard = Path(td) / "p.jpg"
            postcard.write_bytes(b"x")

            def lookup(qso):
                lookup_calls.append(qso["CALL"])
                if qso["CALL"] == "A1":
                    return "", ""
                return f"{qso['CALL'].lower()}@example.org", "qrz_html"

            summary = eqsl_service.process_eqsl_records(
                qsos=qsos,
                settings=settings,
                sent_store=sent_store,
                logger=_logger(),
                lookup_email_fn=lookup,
                render_postcard_fn=lambda q: postcard,
                send_email_fn=lambda recipient, qso, path: "<id@test>",
                sleep_fn=lambda _: None,
            )

        self.assertEqual(summary["no_email"], 1)
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(len(sent_store["sent_by_qso_key"]), 1)
        self.assertEqual(lookup_calls, ["A1", "A2"])

    def test_parse_real_bb_logger_adif_fixture_if_present(self):
        real_adif_env = os.environ.get("BBLOGGER_ADIF_FIXTURE", "").strip()
        if not real_adif_env:
            self.skipTest("Set BBLOGGER_ADIF_FIXTURE to run local real-ADIF parsing test")
        real_adif = Path(real_adif_env)
        if not real_adif.exists():
            self.skipTest(f"Local BBLogger ADIF fixture not present: {real_adif}")

        rows = eqsl_service.parse_adif_records(real_adif)
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(sum(1 for r in rows if r.get("EMAIL")), 0)
        self.assertTrue(all("CALL" in r and "QSO_DATE" in r and "TIME_ON" in r for r in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
