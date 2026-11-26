import unittest
from pathlib import Path
from unittest.mock import patch

from config import _resolve_tesseract_cmd


class TesseractConfigurationTests(unittest.TestCase):
    def test_explicit_tesseract_command_takes_precedence(self):
        with patch("config.Path.is_file", return_value=True), patch(
            "config.shutil.which", return_value="path-tesseract"
        ):
            result = _resolve_tesseract_cmd(r"C:\custom path\tesseract.exe")

        self.assertEqual(result, r"C:\custom path\tesseract.exe")

    def test_standard_windows_install_is_discovered(self):
        with patch("config.Path.is_file", return_value=True), patch(
            "config.shutil.which", return_value=None
        ):
            result = _resolve_tesseract_cmd("")

        self.assertEqual(
            result, str(Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
        )

    def test_invalid_override_falls_back_to_installed_windows_executable(self):
        with patch("config.Path.is_file", side_effect=[False, True]), patch(
            "config.shutil.which", return_value=None
        ):
            result = _resolve_tesseract_cmd("invalid-tesseract-path")

        self.assertEqual(
            result, str(Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
        )

    def test_path_install_is_used_when_standard_windows_path_is_absent(self):
        with patch("config.Path.is_file", return_value=False), patch(
            "config.shutil.which", return_value="path-tesseract"
        ) as which:
            result = _resolve_tesseract_cmd("")

        self.assertEqual(result, "path-tesseract")
        which.assert_called_once_with("tesseract")

    def test_missing_install_returns_no_command_for_clear_runtime_error(self):
        with patch("config.Path.is_file", return_value=False), patch(
            "config.shutil.which", return_value=None
        ):
            self.assertIsNone(_resolve_tesseract_cmd(""))


if __name__ == "__main__":
    unittest.main()
