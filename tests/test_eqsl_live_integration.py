import logging
import os
import re
import tempfile
import unittest
from pathlib import Path

import requests

import eqsl_service
import upload_adif


def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    masked_local = (local[:1] + "***") if local else "***"
    return f"{masked_local}@{domain}"


class EqslLiveIntegrationTests(unittest.TestCase):
    def test_live_send_eqsl_to_self_via_qrz_lookup(self):
        if os.environ.get("RUN_LIVE_EQSL_TEST") != "1":
            self.skipTest("Set RUN_LIVE_EQSL_TEST=1 to run live QRZ+Gmail integration send")

        base_dir = Path(__file__).resolve().parents[1]
        qrz_file = base_dir / "qrz.com.txt"
        gmail_pass_file = base_dir / "gmail_app_password.txt"
        if not qrz_file.exists():
            self.skipTest("qrz.com.txt not found")
        if not gmail_pass_file.exists():
            self.skipTest("gmail_app_password.txt not found")

        lines = qrz_file.read_text(encoding="utf-8-sig").splitlines()
        if len(lines) < 2:
            self.fail("qrz.com.txt must contain username on line 1 and password on line 2")
        qrz_username = lines[0].strip()
        qrz_password = lines[1].strip()
        self.assertTrue(qrz_username)
        self.assertTrue(qrz_password)

        logger = logging.getLogger("eqsl_live_integration")
        if not logger.handlers:
            logger.addHandler(logging.StreamHandler())
        logger.setLevel(logging.INFO)

        with requests.Session() as session:
            cfg = {
                "username": qrz_username,
                "password": qrz_password,
                "login_url": "https://www.qrz.com/login",
                "twofactor_code": "",
                "trust_device": False,
            }
            upload_adif.login(session, cfg, logger)

            contacts_cache = {"schema_version": 1, "emails_by_callsign": {}}
            lookup_settings = {
                "qrz_lookup_enabled": True,
                "qrz_lookup_timeout_sec": 20,
            }
            recipient_email = eqsl_service.lookup_email_via_qrz_html(
                session, qrz_username, lookup_settings, logger, contacts_cache
            )

        self.assertRegex(
            recipient_email,
            r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$",
            "QRZ lookup did not return a valid email",
        )

        qso = {
            "CALL": qrz_username,
            "QSO_DATE": "20260225",
            "TIME_ON": "120000",
            "TIME_OFF": "120100",
            "BAND": "20m",
            "MODE": "MFSK",
            "SUBMODE": "FT4",
            "RST_SENT": "-05",
            "RST_RCVD": "-12",
            "GRIDSQUARE": "JN54PR",
            "MY_GRIDSQUARE": "JN54PR",
            "TX_PWR": "100",
            "QRB": "0",
            "OPERATOR": qrz_username,
            "STATION_CALLSIGN": qrz_username,
        }

        settings = dict(eqsl_service.DEFAULT_EQSL_SETTINGS)
        settings["dry_run"] = False
        settings["sender_email"] = recipient_email
        settings["sender_name"] = qrz_username
        settings["gmail_app_password_file"] = gmail_pass_file.name
        settings["postcard_output_dir"] = "eqsl_out/live_test"
        settings["postcard_cache_dir"] = "eqsl_assets_cache/live_test"

        postcard_path = eqsl_service.render_postcard_jpg(qso, settings, base_dir, logger)
        self.assertTrue(postcard_path.exists(), f"Postcard not created: {postcard_path}")
        self.assertEqual(postcard_path.suffix.lower(), ".jpg")

        message_id = eqsl_service.send_email_with_attachment(
            recipient_email=recipient_email,
            qso=qso,
            postcard_path=postcard_path,
            settings=settings,
            base_dir=base_dir,
        )
        self.assertTrue(message_id)
        self.assertRegex(message_id, r"^<.+>$")

        print(
            "LIVE eQSL sent to self:",
            _mask_email(recipient_email),
            "callsign:",
            qrz_username,
            "message-id:",
            message_id,
            "postcard:",
            postcard_path,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
