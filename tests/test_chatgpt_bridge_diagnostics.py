from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class ChatGPTBridgeDiagnosticsTests(unittest.TestCase):
    def test_cancel_command_nonzero_exit_is_not_reported_as_success(self) -> None:
        from study_app.integrations import chatgpt_desktop_bridge as bridge

        completed = SimpleNamespace(returncode=1, stdout="", stderr="fatal internal path")
        with (
            patch.object(bridge, "_ACTIVE_PDF_PROCESSES", set()),
            patch.object(bridge.subprocess, "run", return_value=completed),
            patch.object(bridge, "_trace") as trace,
        ):
            result = bridge.cancel_chatgpt_pdf_generation()

        self.assertFalse(result.success)
        self.assertEqual(result.code, "cancel_failed")
        self.assertNotIn("fatal internal path", result.message)
        trace.assert_called_once()

    def test_cancel_uses_shared_process_deadline_and_rejects_unreaped_processes(self) -> None:
        from study_app.integrations import chatgpt_desktop_bridge as bridge

        clock = [100.0]

        class StuckProcess:
            def __init__(self):
                self.wait_timeouts = []
                self.terminated = False
                self.killed = False

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

            def wait(self, timeout):
                self.wait_timeouts.append(timeout)
                clock[0] += timeout
                raise subprocess.TimeoutExpired("pdf-helper", timeout)

        processes = [StuckProcess() for _ in range(5)]
        completed = SimpleNamespace(returncode=0, stdout="0", stderr="")
        with (
            patch.object(bridge, "_ACTIVE_PDF_PROCESSES", set(processes)),
            patch.object(bridge.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(bridge.subprocess, "run", return_value=completed),
        ):
            result = bridge.cancel_chatgpt_pdf_generation()

        self.assertFalse(result.success)
        self.assertEqual(result.code, "cancel_failed")
        self.assertIn("已确认结束 0 个", result.message)
        self.assertNotIn("已确认结束 5 个", result.message)
        self.assertTrue(all(process.terminated for process in processes))
        self.assertTrue(all(process.killed for process in processes))
        total_wait = sum(
            timeout
            for process in processes
            for timeout in process.wait_timeouts
        )
        self.assertLessEqual(total_wait, 2.0)

    def test_cleanup_removes_only_chatgpt_temp_artifacts(self) -> None:
        from study_app.integrations import chatgpt_desktop_bridge as bridge

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = [
                root / "study-chatgpt-prompt-private.txt",
                root / "study-chatgpt-placeholders-private.txt",
                root / "study-chatgpt-download-keywords-private.txt",
            ]
            for artifact in artifacts:
                artifact.write_text("PRIVATE_PROMPT", encoding="utf-8")
            for name in ("study-chatgpt-download-private", "study-chatgpt-capture-private"):
                staging = root / name
                staging.mkdir()
                (staging / "private.txt").write_text("PRIVATE_PROMPT", encoding="utf-8")
                artifacts.append(staging)
            unrelated = root / "unrelated.txt"
            unrelated.write_text("keep", encoding="utf-8")

            with patch.object(bridge.tempfile, "gettempdir", return_value=directory):
                removed = bridge.cleanup_chatgpt_temp_artifacts()

            self.assertEqual(removed, len(artifacts))
            self.assertTrue(all(not artifact.exists() for artifact in artifacts))
            self.assertTrue(unrelated.is_file())

    def test_shutdown_gate_blocks_new_pdf_work_after_poll_delay(self) -> None:
        from study_app.integrations import chatgpt_desktop_bridge as bridge

        bridge.request_chatgpt_pdf_shutdown()
        try:
            with (
                patch.object(bridge.tempfile, "mkdtemp") as make_staging,
                patch.object(bridge.subprocess, "Popen") as popen,
            ):
                generated = bridge.generate_pdf_with_chatgpt("PRIVATE_PROMPT")
                captured = bridge.capture_current_chatgpt_pdf()
            with patch.object(bridge, "capture_current_chatgpt_pdf") as capture:
                waited = bridge._wait_for_fresh_chatgpt_pdf(timeout=30, job_id="job")

            self.assertEqual(generated.code, "cancelled")
            self.assertEqual(captured.code, "cancelled")
            self.assertEqual(waited.code, "cancelled")
            make_staging.assert_not_called()
            popen.assert_not_called()
            capture.assert_not_called()

            bridge._PDF_SHUTDOWN_EVENT.clear()
            with (
                patch.object(
                    bridge.time,
                    "sleep",
                    side_effect=lambda _seconds: bridge.request_chatgpt_pdf_shutdown(),
                ),
                patch.object(bridge, "capture_current_chatgpt_pdf") as delayed_capture,
            ):
                delayed = bridge._wait_for_fresh_chatgpt_pdf(timeout=30, job_id="job")
            self.assertEqual(delayed.code, "cancelled")
            delayed_capture.assert_not_called()
        finally:
            bridge._PDF_SHUTDOWN_EVENT.clear()

    def test_shutdown_waits_until_process_is_started_and_registered(self) -> None:
        from study_app.integrations import chatgpt_desktop_bridge as bridge

        popen_entered = threading.Event()
        allow_popen = threading.Event()
        communicating = threading.Event()
        finish_process = threading.Event()
        shutdown_done = threading.Event()

        class BlockingProcess:
            pid = 123
            returncode = 0

            def communicate(self, timeout):
                communicating.set()
                finish_process.wait(2)
                return ('{"success": false, "code": "bridge_error"}', "")

            def poll(self):
                return 0 if finish_process.is_set() else None

        process = BlockingProcess()

        def start_process(*_args, **_kwargs):
            popen_entered.set()
            allow_popen.wait(2)
            return process

        def shutdown():
            bridge.request_chatgpt_pdf_shutdown()
            shutdown_done.set()

        bridge._PDF_SHUTDOWN_EVENT.clear()
        with (
            patch.object(bridge, "cleanup_generated_pdfs"),
            patch.object(bridge.subprocess, "Popen", side_effect=start_process),
        ):
            generator = threading.Thread(
                target=bridge.generate_pdf_with_chatgpt,
                args=("PRIVATE_PROMPT",),
            )
            generator.start()
            self.assertTrue(popen_entered.wait(1))
            stopper = threading.Thread(target=shutdown)
            stopper.start()
            self.assertFalse(shutdown_done.wait(0.05))

            allow_popen.set()
            self.assertTrue(communicating.wait(1))
            self.assertTrue(shutdown_done.wait(1))
            self.assertIn(process, bridge._ACTIVE_PDF_PROCESSES)

            finish_process.set()
            generator.join(1)
            stopper.join(1)

        bridge._PDF_SHUTDOWN_EVENT.clear()
        self.assertFalse(generator.is_alive())
        self.assertFalse(stopper.is_alive())

    def test_communicate_error_stops_and_reaps_generate_and_capture_processes(self) -> None:
        from study_app.integrations import chatgpt_desktop_bridge as bridge

        class BrokenProcess:
            pid = 123
            returncode = None

            def __init__(self):
                self.terminated = False
                self.reaped = False

            def communicate(self, timeout):
                raise OSError("PRIVATE_COMMUNICATION_DETAIL")

            def poll(self):
                return 1 if self.reaped else None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout):
                self.reaped = True
                self.returncode = 1
                return 1

        generate_process = BrokenProcess()
        capture_process = BrokenProcess()
        bridge._PDF_SHUTDOWN_EVENT.clear()
        with (
            patch.object(bridge, "cleanup_generated_pdfs"),
            patch.object(bridge, "_trace") as trace,
            patch.object(
                bridge.subprocess,
                "Popen",
                side_effect=[generate_process, capture_process],
            ),
        ):
            generated = bridge.generate_pdf_with_chatgpt("PRIVATE_PROMPT")
            captured = bridge.capture_current_chatgpt_pdf()

        self.assertEqual(generated.code, "bridge_error")
        self.assertEqual(captured.code, "bridge_error")
        diagnostic = f"{generated.message}\n{captured.message}\n{trace.call_args_list}"
        self.assertNotIn("PRIVATE_COMMUNICATION_DETAIL", diagnostic)
        for process in (generate_process, capture_process):
            self.assertTrue(process.terminated)
            self.assertTrue(process.reaped)
            self.assertNotIn(process, bridge._ACTIVE_PDF_PROCESSES)

    def test_capture_shutdown_waits_until_process_is_registered(self) -> None:
        from study_app.integrations import chatgpt_desktop_bridge as bridge

        popen_entered = threading.Event()
        allow_popen = threading.Event()
        communicating = threading.Event()
        finish_process = threading.Event()
        shutdown_done = threading.Event()

        class BlockingProcess:
            pid = 124
            returncode = 0

            def communicate(self, timeout):
                communicating.set()
                finish_process.wait(2)
                return ('{"success": false, "code": "capture_failed"}', "")

            def poll(self):
                return 0 if finish_process.is_set() else None

        process = BlockingProcess()

        def start_process(*_args, **_kwargs):
            popen_entered.set()
            allow_popen.wait(2)
            return process

        def shutdown():
            bridge.request_chatgpt_pdf_shutdown()
            shutdown_done.set()

        bridge._PDF_SHUTDOWN_EVENT.clear()
        with (
            patch.object(bridge, "cleanup_generated_pdfs"),
            patch.object(bridge.subprocess, "Popen", side_effect=start_process),
        ):
            capture = threading.Thread(target=bridge.capture_current_chatgpt_pdf)
            capture.start()
            self.assertTrue(popen_entered.wait(1))
            stopper = threading.Thread(target=shutdown)
            stopper.start()
            self.assertFalse(shutdown_done.wait(0.05))

            allow_popen.set()
            self.assertTrue(communicating.wait(1))
            self.assertTrue(shutdown_done.wait(1))
            self.assertIn(process, bridge._ACTIVE_PDF_PROCESSES)

            finish_process.set()
            capture.join(1)
            stopper.join(1)

        bridge._PDF_SHUTDOWN_EVENT.clear()
        self.assertFalse(capture.is_alive())
        self.assertFalse(stopper.is_alive())

    def test_cancel_powershell_uses_only_remaining_absolute_deadline(self) -> None:
        from study_app.integrations import chatgpt_desktop_bridge as bridge

        clock = [100.0]
        completed = SimpleNamespace(returncode=0, stdout="0", stderr="")
        with (
            patch.object(bridge, "_ACTIVE_PDF_PROCESSES", set()),
            patch.object(bridge.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(bridge.subprocess, "run", return_value=completed) as run,
        ):
            result = bridge.cancel_chatgpt_pdf_generation(deadline=103.0)

        self.assertTrue(result.success)
        self.assertGreater(run.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(run.call_args.kwargs["timeout"], 3.0)

    def test_unreaped_communicate_error_remains_registered_for_cancel(self) -> None:
        from study_app.integrations import chatgpt_desktop_bridge as bridge

        process = Mock(pid=123, returncode=None)
        process.communicate.side_effect = OSError("PRIVATE_COMMUNICATION_DETAIL")
        process.poll.return_value = None
        process.terminate.side_effect = OSError("terminate failed")
        process.wait.side_effect = subprocess.TimeoutExpired("wait", 2)
        process.kill.side_effect = OSError("kill failed")
        bridge._PDF_SHUTDOWN_EVENT.clear()
        with (
            patch.object(bridge, "cleanup_generated_pdfs"),
            patch.object(bridge.subprocess, "Popen", return_value=process),
        ):
            result = bridge.generate_pdf_with_chatgpt("PRIVATE_PROMPT")

        self.assertEqual(result.code, "bridge_error")
        self.assertIn(process, bridge._ACTIVE_PDF_PROCESSES)
        bridge._ACTIVE_PDF_PROCESSES.discard(process)

    def test_unreaped_timeout_remains_registered_for_generate_and_capture(self) -> None:
        from study_app.integrations import chatgpt_desktop_bridge as bridge

        def broken_process():
            process = Mock(pid=123, returncode=None)
            process.communicate.side_effect = subprocess.TimeoutExpired("communicate", 2)
            process.poll.return_value = None
            process.terminate.side_effect = OSError("terminate failed")
            process.wait.side_effect = subprocess.TimeoutExpired("wait", 2)
            process.kill.side_effect = OSError("kill failed")
            return process

        generate_process = broken_process()
        capture_process = broken_process()
        bridge._PDF_SHUTDOWN_EVENT.clear()
        with (
            patch.object(bridge, "cleanup_generated_pdfs"),
            patch.object(
                bridge.subprocess,
                "Popen",
                side_effect=[generate_process, capture_process],
            ),
        ):
            generated = bridge.generate_pdf_with_chatgpt("PRIVATE_PROMPT")
            captured = bridge.capture_current_chatgpt_pdf()

        self.assertEqual(generated.code, "pdf_link_not_found")
        self.assertEqual(captured.code, "pdf_link_not_found")
        self.assertIn(generate_process, bridge._ACTIVE_PDF_PROCESSES)
        self.assertIn(capture_process, bridge._ACTIVE_PDF_PROCESSES)
        bridge._ACTIVE_PDF_PROCESSES.difference_update(
            {generate_process, capture_process}
        )


if __name__ == "__main__":
    unittest.main()