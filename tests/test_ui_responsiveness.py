import time
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_window_shell_contract import WindowShellContractTests


def test_slow_refresh_allows_navigation_deduplicates_and_preserves_pages():
    app = QApplication.instance() or QApplication([])
    window, context = WindowShellContractTests().build_window()
    with context:
        window.show()
        app.processEvents()
        pages = [window.stack.widget(i) for i in range(5)]
        loaded = WindowShellContractTests.state()
        def slow_load():
            time.sleep(0.15)
            return loaded
        with patch('study_app.ui.window_shell.load_dashboard_state', side_effect=slow_load) as load:
            window.request_dashboard_refresh()
            handle = window._refresh_handle
            window.request_dashboard_refresh()
            assert window._refresh_handle is handle
            window.switch_page(2)
            assert window.stack.currentIndex() == 2
            assert not window._refresh_button.isEnabled()
            deadline = time.monotonic() + 3
            while window._refresh_handle is not None and time.monotonic() < deadline:
                QTest.qWait(10)
            assert window._refresh_handle is None
            assert load.call_count == 1
            assert window.state is loaded
            assert window._refresh_button.isEnabled()
            assert [window.stack.widget(i) for i in range(5)] == pages
            assert window.stack.currentIndex() == 2
        window.close()


def test_refresh_failure_restores_action_and_old_state():
    app = QApplication.instance() or QApplication([])
    window, context = WindowShellContractTests().build_window()
    with context:
        old_state = window.state
        with patch('study_app.ui.window_shell.load_dashboard_state', side_effect=RuntimeError('test')):
            window.request_dashboard_refresh()
            deadline = time.monotonic() + 3
            while window._refresh_handle is not None and time.monotonic() < deadline:
                QTest.qWait(10)
            assert window.state is old_state
            assert window._refresh_button.isEnabled()
            assert '失败' in window.statusBar().currentMessage()
        window.close()
