from __future__ import annotations

from hashlib import sha256
import subprocess
import sys
import unittest

from study_app.ui.theme import PALETTE, build_stylesheet
import study_app.ui.main_window as facade
from study_app.ui import theme


class ThemeContractTests(unittest.TestCase):
    def test_stylesheet_preserves_palette_and_visible_control_indicators(self) -> None:
        stylesheet = build_stylesheet()

        self.assertIn(PALETTE['blue'], stylesheet)
        self.assertIn('chevron-down.svg', stylesheet)
        self.assertIn('check.svg', stylesheet)
        self.assertNotIn('image: none', stylesheet)

    def test_main_window_keeps_theme_exports_compatible(self) -> None:
        self.assertIs(facade.PALETTE, theme.PALETTE)
        self.assertIs(PALETTE, theme.PALETTE)
        self.assertIs(facade.build_stylesheet, theme.build_stylesheet)
        self.assertEqual(facade.build_stylesheet(), theme.build_stylesheet())

    def test_importing_theme_does_not_load_pyside6(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import study_app.ui.theme; "
                    "assert not any(name == 'PySide6' or name.startswith('PySide6.') "
                    "for name in sys.modules), sorted(name for name in sys.modules "
                    "if name == 'PySide6' or name.startswith('PySide6.'))"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
