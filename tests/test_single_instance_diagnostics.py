from __future__ import annotations

import unittest
from unittest.mock import Mock


class SingleInstanceDiagnosticsTests(unittest.TestCase):
    def test_callback_failure_does_not_hide_following_server_failure(self) -> None:
        from study_app.single_instance import WAKE_MESSAGE, WakeServer

        callback = Mock(side_effect=RuntimeError("PRIVATE_CALLBACK_DETAIL"))
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.recv.return_value = WAKE_MESSAGE
        listener = Mock()
        listener.accept.side_effect = [(client, ("127.0.0.1", 1)), OSError("socket failed")]
        server = WakeServer(callback)
        server.socket = listener
        server.running = True

        with self.assertLogs("study_app.single_instance", level="ERROR") as captured:
            server._serve()

        callback.assert_called_once_with()
        self.assertEqual(listener.accept.call_count, 2)
        diagnostic = "\n".join(captured.output)
        self.assertIn("RuntimeError", diagnostic)
        self.assertIn("OSError", diagnostic)
        self.assertNotIn("PRIVATE_CALLBACK_DETAIL", diagnostic)
        self.assertNotIn("socket failed", diagnostic)


if __name__ == "__main__":
    unittest.main()
