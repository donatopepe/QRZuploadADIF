import tempfile
import unittest
from pathlib import Path
from unittest import mock

import upload_adif


class UploadAdifCredentialsTests(unittest.TestCase):
    def test_apply_qrz_credentials_file_loads_missing_username_and_password(self):
        with tempfile.TemporaryDirectory() as td:
            cred = Path(td) / "qrz.com.txt"
            cred.write_text("TESTCALL\nsecret-pass\n", encoding="utf-8")
            cfg = {"username": "", "password": ""}

            with mock.patch.object(upload_adif, "QRZ_CREDENTIALS_FILE", cred):
                upload_adif.apply_qrz_credentials_file(cfg)

        self.assertEqual(cfg["username"], "TESTCALL")
        self.assertEqual(cfg["password"], "secret-pass")

    def test_apply_qrz_credentials_file_does_not_override_existing_values(self):
        with tempfile.TemporaryDirectory() as td:
            cred = Path(td) / "qrz.com.txt"
            cred.write_text("OTHERUSER\nother-pass\n", encoding="utf-8")
            cfg = {"username": "KEEPUSER", "password": "KEEPPASS"}

            with mock.patch.object(upload_adif, "QRZ_CREDENTIALS_FILE", cred):
                upload_adif.apply_qrz_credentials_file(cfg)

        self.assertEqual(cfg["username"], "KEEPUSER")
        self.assertEqual(cfg["password"], "KEEPPASS")

    def test_apply_qrz_credentials_file_raises_on_invalid_format(self):
        with tempfile.TemporaryDirectory() as td:
            cred = Path(td) / "qrz.com.txt"
            cred.write_text("only-one-line\n", encoding="utf-8")
            cfg = {"username": "", "password": ""}

            with mock.patch.object(upload_adif, "QRZ_CREDENTIALS_FILE", cred):
                with self.assertRaises(ValueError):
                    upload_adif.apply_qrz_credentials_file(cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
