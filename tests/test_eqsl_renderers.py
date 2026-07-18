import logging
import tempfile
import shutil
import unittest
from pathlib import Path

import eqsl_service


def _logger() -> logging.Logger:
    logger = logging.getLogger("eqsl_renderer_tests")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)
    return logger


def _sample_qso() -> dict[str, str]:
    return {
        "CALL": "N0CALL",
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
        "OPERATOR": "N0CALL",
        "STATION_CALLSIGN": "N0CALL",
    }


def _render_settings(image_path: Path) -> dict:
    settings = dict(eqsl_service.DEFAULT_EQSL_SETTINGS)
    settings["image_path"] = str(image_path)
    settings["image_url"] = ""
    settings["postcard_output_dir"] = "out"
    settings["postcard_cache_dir"] = "cache"
    settings["dry_run"] = True
    return settings


class EqslRendererTests(unittest.TestCase):
    def _make_source_image(self, path: Path) -> None:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (2400, 1600), (30, 80, 120))
        draw = ImageDraw.Draw(img)
        # Add some shapes so output is not a flat background and compression behaves realistically.
        draw.rectangle((0, 1100, 2400, 1600), fill=(80, 35, 20))
        draw.ellipse((1500, 100, 2250, 850), fill=(230, 200, 120))
        draw.rectangle((150, 250, 1050, 980), fill=(190, 175, 155))
        img.save(path, format="JPEG", quality=92)

    def test_render_postcard_with_pillow_backend(self):
        try:
            from PIL import Image  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"Pillow not available: {exc}")

        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            source = base_dir / "source.jpg"
            self._make_source_image(source)
            settings = _render_settings(source)

            out = eqsl_service.render_postcard_jpg(_sample_qso(), settings, base_dir, _logger())

            self.assertTrue(out.exists(), f"Output not found: {out}")
            self.assertEqual(out.suffix.lower(), ".jpg")
            self.assertGreater(out.stat().st_size, 10_000)

            from PIL import Image

            with Image.open(out) as img:
                self.assertEqual(img.size, (2400, 1600))

    def test_render_postcard_with_system_drawing_backend(self):
        if not (shutil.which("powershell") or shutil.which("pwsh")):
            self.skipTest("PowerShell not available; System.Drawing is Windows-specific")
        try:
            from PIL import Image  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"Pillow not available to create local fixture image: {exc}")

        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            source = base_dir / "source.jpg"
            self._make_source_image(source)
            settings = _render_settings(source)

            out = eqsl_service._render_postcard_jpg_system_drawing(_sample_qso(), settings, base_dir, _logger())

            self.assertTrue(out.exists(), f"Output not found: {out}")
            self.assertEqual(out.suffix.lower(), ".jpg")
            self.assertGreater(out.stat().st_size, 10_000)

    def test_render_dispatch_uses_system_drawing_when_pillow_missing(self):
        if not (shutil.which("powershell") or shutil.which("pwsh")):
            self.skipTest("PowerShell not available; System.Drawing is Windows-specific")
        qso = _sample_qso()

        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            source = base_dir / "source.jpg"
            # Create minimal image via Pillow if available; otherwise skip (dispatch test only).
            try:
                self._make_source_image(source)
            except Exception as exc:  # noqa: BLE001
                self.skipTest(f"Cannot prepare source image: {exc}")

            settings = _render_settings(source)
            called = {"fallback": 0}

            original_fallback = eqsl_service._render_postcard_jpg_system_drawing
            original_import = __import__

            def fake_fallback(*args, **kwargs):
                called["fallback"] += 1
                return original_fallback(*args, **kwargs)

            def fake_import(name, *args, **kwargs):
                if name == "PIL":
                    raise ModuleNotFoundError("No module named 'PIL'")
                return original_import(name, *args, **kwargs)

            try:
                eqsl_service._render_postcard_jpg_system_drawing = fake_fallback
                import builtins

                builtins_import = builtins.__import__
                builtins.__import__ = fake_import
                try:
                    out = eqsl_service.render_postcard_jpg(qso, settings, base_dir, _logger())
                finally:
                    builtins.__import__ = builtins_import
            finally:
                eqsl_service._render_postcard_jpg_system_drawing = original_fallback

            self.assertTrue(out.exists())
            self.assertEqual(called["fallback"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
