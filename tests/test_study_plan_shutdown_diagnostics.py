from __future__ import annotations

import unittest
from unittest.mock import Mock, patch


class StudyPlanShutdownDiagnosticsTests(unittest.TestCase):
    def test_cancel_and_worker_wait_share_one_shutdown_deadline(self) -> None:
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult
        from study_app.ui import study_plan_page as page

        clock = [100.0]
        worker = Mock()
        page._CHATGPT_BRIDGE_WORKERS.add(worker)
        page._CHATGPT_BRIDGE_SHUTDOWN_STARTED = False

        def consume_budget(*, deadline):
            clock[0] = deadline
            return ChatGPTBridgeResult(True, "cancelled", "ok")

        try:
            with (
                patch.object(page.time, "monotonic", side_effect=lambda: clock[0]),
                patch(
                    "study_app.integrations.chatgpt_desktop_bridge.request_chatgpt_pdf_shutdown"
                ),
                patch(
                    "study_app.integrations.chatgpt_desktop_bridge.cancel_chatgpt_pdf_generation",
                    side_effect=consume_budget,
                ),
                patch(
                    "study_app.integrations.chatgpt_desktop_bridge.cleanup_chatgpt_temp_artifacts"
                ),
                self.assertLogs("study_app.ui.study_plan_page", level="ERROR"),
            ):
                page._shutdown_chatgpt_bridge_workers()

            worker.wait.assert_not_called()
        finally:
            page._CHATGPT_BRIDGE_WORKERS.discard(worker)
            page._CHATGPT_BRIDGE_SHUTDOWN_STARTED = False

    def test_shutdown_without_worker_still_closes_registration_gate(self) -> None:
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult
        from study_app.ui import study_plan_page as page

        page._CHATGPT_BRIDGE_WORKERS.clear()
        page._CHATGPT_BRIDGE_SHUTDOWN_STARTED = False
        try:
            with (
                patch(
                    "study_app.integrations.chatgpt_desktop_bridge.request_chatgpt_pdf_shutdown"
                ) as request_shutdown,
                patch(
                    "study_app.integrations.chatgpt_desktop_bridge.cancel_chatgpt_pdf_generation",
                    return_value=ChatGPTBridgeResult(True, "cancelled", "ok"),
                ),
                patch(
                    "study_app.integrations.chatgpt_desktop_bridge.cleanup_chatgpt_temp_artifacts"
                ),
            ):
                page._shutdown_chatgpt_bridge_workers()

            self.assertTrue(page._CHATGPT_BRIDGE_SHUTDOWN_STARTED)
            request_shutdown.assert_called_once_with()
        finally:
            page._CHATGPT_BRIDGE_SHUTDOWN_STARTED = False

    def test_shutdown_is_idempotent_and_reports_cancel_failure(self) -> None:
        from study_app.integrations.chatgpt_desktop_bridge import ChatGPTBridgeResult
        from study_app.ui import study_plan_page as page

        worker = Mock()
        worker.wait.return_value = True
        page._CHATGPT_BRIDGE_WORKERS.add(worker)
        page._CHATGPT_BRIDGE_SHUTDOWN_STARTED = False
        try:
            with (
                patch.object(page.threading, "Thread") as thread_factory,
                patch(
                    "study_app.integrations.chatgpt_desktop_bridge.cancel_chatgpt_pdf_generation",
                    return_value=ChatGPTBridgeResult(
                        False,
                        "cancel_failed",
                        "PRIVATE_CANCEL_DETAIL",
                    ),
                ) as cancel,
                patch(
                    "study_app.integrations.chatgpt_desktop_bridge.cleanup_chatgpt_temp_artifacts",
                    create=True,
                ) as cleanup,
                patch(
                    "study_app.integrations.chatgpt_desktop_bridge.request_chatgpt_pdf_shutdown",
                    create=True,
                ) as request_shutdown,
                self.assertLogs(
                    "study_app.ui.study_plan_page", level="ERROR"
                ) as captured,
            ):
                page._shutdown_chatgpt_bridge_workers()
                page._shutdown_chatgpt_bridge_workers()

                thread_factory.assert_not_called()
                request_shutdown.assert_called_once_with()
                cancel.assert_called_once()
                self.assertIn("deadline", cancel.call_args.kwargs)
                worker.wait.assert_called_once()
                wait_ms = worker.wait.call_args.args[0]
                self.assertGreater(wait_ms, 0)
                self.assertLessEqual(wait_ms, 2000)
                cleanup.assert_called_once_with()

            diagnostic = "\n".join(captured.output)
            self.assertIn("cancel_failed", diagnostic)
            self.assertNotIn("PRIVATE_CANCEL_DETAIL", diagnostic)
        finally:
            page._CHATGPT_BRIDGE_WORKERS.discard(worker)
            page._CHATGPT_BRIDGE_SHUTDOWN_STARTED = False


if __name__ == "__main__":
    unittest.main()
