from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


class OCRCapabilitiesTests(unittest.TestCase):
    def test_usable_path_installation_wins_over_incomplete_managed_installation(self) -> None:
        from study_app import capabilities
        from study_app.core import subject_pdf_pipeline

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            managed_executable = root / "managed" / "tesseract" / "tesseract.exe"
            managed_executable.parent.mkdir(parents=True)
            managed_executable.touch()
            path_executable = root / "bin" / "tesseract.exe"
            path_executable.parent.mkdir()
            path_executable.touch()
            path_tessdata = path_executable.parent / "tessdata"
            path_tessdata.mkdir()
            (path_tessdata / "chi_sim.traineddata").touch()

            with (
                patch.object(capabilities, "DATA_DIR", root / "managed"),
                patch.object(capabilities, "TESSDATA_DIR", root / "managed" / "tessdata"),
                patch.object(capabilities.shutil, "which", return_value=str(path_executable)),
                patch.dict("os.environ", {"TESSDATA_PREFIX": ""}),
            ):
                self.assertEqual(capabilities.find_tesseract_executable(), managed_executable)
                runtime = capabilities.resolve_tesseract_runtime()
                status = capabilities.detect_optional_capabilities()["ocr"]
                pdf_runtime = subject_pdf_pipeline._local_tesseract_runtime()

        self.assertEqual(runtime.executable, path_executable)
        self.assertEqual(pdf_runtime, (path_executable, path_tessdata, "chi_sim"))
        self.assertTrue(status["available"])
        self.assertEqual(status["executable"], str(path_executable))

    def test_managed_executable_is_shared_by_diagnostics_and_ocr(self) -> None:
        from study_app import capabilities
        from study_app.core import subject_pdf_pipeline
        from study_app.data import text_extractor

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            executable = data_dir / "tesseract" / "tesseract.exe"
            executable.parent.mkdir()
            executable.touch()
            tessdata = data_dir / "tesseract" / "tessdata"
            tessdata.mkdir()
            (tessdata / "chi_sim.traineddata").touch()
            (tessdata / "eng.traineddata").touch()

            with (
                patch.object(capabilities, "DATA_DIR", data_dir),
                patch.object(capabilities, "TESSDATA_DIR", tessdata),
                patch.object(capabilities.shutil, "which", return_value=None),
                patch.dict("os.environ", {"TESSDATA_PREFIX": ""}),
            ):
                status = capabilities.detect_optional_capabilities()["ocr"]
                self.assertEqual(capabilities.find_tesseract_executable(), executable)
                self.assertEqual(subject_pdf_pipeline._local_tesseract_runtime(), (executable, tessdata, "chi_sim+eng"))
                (tessdata / "chi_sim.traineddata").unlink()
                with self.assertRaisesRegex(RuntimeError, "缺少简体中文 OCR 语言包"):
                    subject_pdf_pipeline._local_tesseract_runtime()
                missing_language_status = capabilities.detect_optional_capabilities()["ocr"]

            self.assertTrue(status["available"])
            self.assertTrue(status["executable_found"])
            self.assertTrue(status["language_data_found"])
            self.assertIsNone(status["runtime_ready"])
            self.assertEqual(status["language"], "chi_sim+eng")
            self.assertIn(str(executable), status["detail"])
            self.assertIn("尚未验证", status["detail"])
            self.assertFalse(missing_language_status["available"])
            self.assertTrue(missing_language_status["executable_found"])
            self.assertFalse(missing_language_status["language_data_found"])
            self.assertFalse(missing_language_status["runtime_ready"])
            self.assertIn("缺少简体中文", missing_language_status["detail"])
            self.assertIs(text_extractor.resolve_tesseract_runtime, capabilities.resolve_tesseract_runtime)
            self.assertIs(text_extractor.find_tesseract_executable, capabilities.find_tesseract_executable)
            self.assertIs(subject_pdf_pipeline.resolve_tesseract_runtime, capabilities.resolve_tesseract_runtime)

    def test_path_executable_and_missing_engine_message(self) -> None:
        from study_app import capabilities
        from study_app.data import text_extractor

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            executable = Path(directory) / "bin" / "tesseract.exe"
            executable.parent.mkdir()
            executable.touch()
            with (
                patch.object(capabilities, "DATA_DIR", data_dir),
                patch.object(capabilities.shutil, "which", return_value=str(executable)),
            ):
                self.assertEqual(capabilities.find_tesseract_executable(), executable)

        with patch.object(text_extractor, "resolve_tesseract_runtime", side_effect=RuntimeError("未找到本地 Tesseract 可执行文件")):
            text, warning = text_extractor._extract_image_text(Path("unused.png"), 100)
        self.assertEqual(text, "")
        self.assertIn("未找到本地 Tesseract 可执行文件", warning)
        self.assertNotIn("pytesseract", warning)

    def test_cli_remains_available_without_python_ocr_binding(self) -> None:
        from study_app.capabilities import TesseractRuntime
        from study_app.data import text_extractor

        runtime = TesseractRuntime(Path("tesseract.exe"), Path("tessdata"), "chi_sim")
        with (
            patch.object(text_extractor, "resolve_tesseract_runtime", return_value=runtime),
            patch.dict("sys.modules", {"pytesseract": None}),
            patch.object(text_extractor, "_extract_image_text_with_cli", return_value=("识别文字", "")) as cli,
        ):
            result = text_extractor._extract_image_text(Path("image.png"), 100)
        self.assertEqual(result, ("识别文字", ""))
        cli.assert_called_once_with(Path("image.png"), runtime, 100)

    def test_chinese_only_managed_data_is_used_by_image_routes(self) -> None:
        from study_app import capabilities
        from study_app.data import text_extractor

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            executable = data_dir / "tesseract" / "tesseract.exe"
            executable.parent.mkdir()
            executable.touch()
            tessdata = executable.parent / "tessdata"
            tessdata.mkdir()
            (tessdata / "chi_sim.traineddata").touch()

            fake_pytesseract = ModuleType("pytesseract")
            fake_pytesseract.pytesseract = SimpleNamespace(tesseract_cmd="")
            fake_pytesseract.image_to_string = Mock(return_value="中文识别")
            fake_pil = ModuleType("PIL")
            fake_image = ModuleType("PIL.Image")
            fake_image.open = Mock(return_value=object())
            fake_pil.Image = fake_image

            with (
                patch.object(capabilities, "DATA_DIR", data_dir),
                patch.object(capabilities, "TESSDATA_DIR", tessdata),
                patch.object(capabilities.shutil, "which", return_value=None),
                patch.dict("os.environ", {"TESSDATA_PREFIX": ""}),
            ):
                runtime = capabilities.resolve_tesseract_runtime()
                status = capabilities.detect_optional_capabilities()["ocr"]
                with (
                    patch.dict("sys.modules", {"pytesseract": fake_pytesseract, "PIL": fake_pil, "PIL.Image": fake_image}),
                    patch.object(text_extractor, "preprocess_image_for_ocr", side_effect=lambda image: image),
                ):
                    self.assertEqual(text_extractor._extract_image_text(Path("image.png"), 100), ("中文识别", ""))

                completed = SimpleNamespace(stdout="中文识别", stderr="", returncode=0)
                with (
                    patch.object(text_extractor, "preprocess_image_file_for_ocr", return_value=None),
                    patch.object(text_extractor.subprocess, "run", return_value=completed) as run,
                ):
                    self.assertEqual(
                        text_extractor._extract_image_text_with_cli(Path("image.png"), runtime, 100),
                        ("中文识别", ""),
                    )

            self.assertEqual(runtime.language, "chi_sim")
            self.assertEqual(status["language"], "chi_sim")
            self.assertEqual(status["tessdata_dir"], str(tessdata))
            self.assertEqual(fake_pytesseract.pytesseract.tesseract_cmd, str(executable))
            self.assertEqual(fake_pytesseract.image_to_string.call_args.kwargs["lang"], "chi_sim")
            self.assertIn(str(tessdata), fake_pytesseract.image_to_string.call_args.kwargs["config"])
            self.assertEqual(
                run.call_args.args[0],
                [str(executable), "image.png", "stdout", "--tessdata-dir", str(tessdata), "-l", "chi_sim", "--psm", "6"],
            )


if __name__ == "__main__":
    unittest.main()
