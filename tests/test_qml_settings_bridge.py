from __future__ import annotations

import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from study_app.core.async_tasks import AsyncTaskService
from study_app.quick.self_test import wait_until
from study_app.quick.settings_bridge import SettingsBridge


class Preferences:
    backup_root = Path("isolated-backups")

    def __init__(self):
        self.saved = 0
        self.fail_reload = False
        self.credentials = []

    def load(self):
        if self.saved and self.fail_reload:
            raise RuntimeError("api_key=private-secret C:/private/database.sqlite")
        return {"llm": {"provider": "openai", "providerLabel": "OpenAI", "hasCredential": bool(self.credentials), "credentialScope": "isolated-openai-scope"}}

    def save(self, payload):
        self.saved += 1
        return {"saved": True}

    def set_credential(self, provider, key, clear=False, expected_scope=None):
        assert expected_scope == "isolated-openai-scope"
        self.credentials.append((provider, key, clear, threading.get_ident()))
        self.saved += 1
        return {"saved": True}

    def check_integrity(self):
        return {"message": "数据库完整性通过"}


class Subjects:
    def __init__(self):
        self.committed = []
        self.loads = 0

    def load(self):
        self.loads += 1
        return {"subjects": [{"subjectKey": "stable", "name": "隔离学科", "lifecycleStatus": "active"}]}

    def preview(self, kind, **kwargs):
        return {"token": "preview-token", "name": kwargs.get("name"), "kind": kind}

    def confirm(self, token, typed_name, confirmed):
        if typed_name != "隔离学科" or not confirmed:
            raise ValueError("accurate full name required")
        self.committed.append(token)
        return {"saved": True, "subjectKey": "stable"}


@pytest.fixture
def settings_bridge():
    app = QApplication.instance() or QApplication(["settings-tests"])
    tasks = AsyncTaskService(max_workers=1)
    settings, subjects = Preferences(), Subjects()
    bridge = SettingsBridge(settings, subjects, tasks, credential_prompt=lambda *_: "isolated-test-key")
    yield app, bridge, settings, subjects, tasks
    bridge.close()
    tasks.close()
    tasks.wait_for_shutdown()


def ready(bridge):
    wait_until(lambda: not bridge.busy, timeout_ms=3000)


def test_static_metaobject_and_real_worker_boundary(settings_bridge):
    _, bridge, _, subjects, _ = settings_bridge
    static = type(bridge).staticMetaObject
    for signature in ("_poll()", "leave_page()", "invalidateForDataChange()", "promptCredential(QString)"):
        assert static.indexOfSlot(signature) >= 0
    gui = threading.get_ident()
    callers = []
    original = subjects.load
    subjects.load = lambda: (callers.append(threading.get_ident()), original())[1]
    bridge.run("load", {})
    ready(bridge)
    assert bridge.subjectsData["subjects"][0]["subjectKey"] == "stable"
    assert callers and callers[0] != gui
    assert bridge.metaObject().methodCount() == static.methodCount()


def test_review_token_full_name_checkbox_and_one_use(settings_bridge):
    _, bridge, _, subjects, _ = settings_bridge
    bridge.run("subject.confirm", {"token": "forged", "typedName": "隔离学科", "confirmed": True})
    assert bridge.error and not subjects.committed
    bridge.run("subject.preview", {"kind": "remove", "name": "隔离学科", "subjectKey": "stable"})
    ready(bridge)
    bridge.run("subject.confirm", {"token": "preview-token", "typedName": "隔离学科"})
    assert bridge.error and not subjects.committed
    bridge.run("subject.confirm", {"token": "preview-token", "typedName": "错误学科", "confirmed": True})
    ready(bridge)
    assert bridge.error and not subjects.committed and not bridge.subjectPreview
    bridge.run("subject.preview", {"kind": "remove", "name": "隔离学科"})
    ready(bridge)
    bridge.run("subject.confirm", {"token": "preview-token", "typedName": "隔离学科", "confirmed": True})
    ready(bridge)
    assert bridge.receipt["saved"] and subjects.committed == ["preview-token"]
    assert not bridge.subjectPreview
    bridge.run("subject.confirm", {"token": "preview-token", "typedName": "隔离学科", "confirmed": True})
    assert len(subjects.committed) == 1


def test_save_followed_by_failed_refresh_is_reported_saved(settings_bridge):
    _, bridge, settings, _, _ = settings_bridge
    settings.fail_reload = True
    bridge.run("settings.save", {})
    ready(bridge)
    assert settings.saved == 1 and bridge.receipt["saved"] and not bridge.error
    assert "已保存" in bridge.message and "勿重复提交" in bridge.message
    assert "private" not in repr((bridge.settingsData, bridge.message, bridge.error))


def test_native_credential_input_stays_in_python_and_write_is_on_worker(settings_bridge):
    _, bridge, settings, _, _ = settings_bridge
    bridge.run("load", {})
    ready(bridge)
    bridge.promptCredential("other")
    assert bridge.error and not settings.credentials
    bridge.run("settings.credential", {"provider": "openai", "key": "qml-must-not-send-this"})
    assert bridge.error and not settings.credentials
    gui = threading.get_ident()
    bridge.promptCredential("openai")
    ready(bridge)
    assert settings.credentials[0][:3] == ("openai", "isolated-test-key", False)
    assert settings.credentials[0][3] != gui
    assert bridge.settingsData["llm"]["hasCredential"]
    assert "isolated-test-key" not in repr((bridge.settingsData, bridge.receipt, bridge.error, bridge.message))
    bridge.clearCredential("openai", False)
    assert len(settings.credentials) == 1
    bridge.clearCredential("openai", True)
    ready(bridge)
    assert settings.credentials[1][:3] == ("openai", "", True)


def test_cancelled_or_failed_credential_prompt_preserves_existing_key(settings_bridge):
    _, bridge, settings, _, _ = settings_bridge
    bridge.run("load", {})
    ready(bridge)
    bridge._credential_prompt = lambda *_: None
    bridge.promptCredential("openai")
    assert not bridge.busy and not settings.credentials and "取消" in bridge.message
    def fail(*_):
        raise RuntimeError("private-secret")
    bridge._credential_prompt = fail
    bridge.promptCredential("openai")
    assert not bridge.busy and not settings.credentials and bridge.error
    assert "private-secret" not in bridge.error


def test_native_dialog_cannot_rebind_key_if_endpoint_changes_during_input(settings_bridge, tmp_path):
    from test_qml_settings_service import _service, _valid_payload
    from study_app.ai.providers import load_llm_settings

    _, bridge, _, _, _ = settings_bridge
    service = _service(tmp_path)
    bridge._settings = service
    service.save(_valid_payload(provider="custom", custom_url="https://endpoint-a.example.test/v1"))
    bridge.run("load", {})
    ready(bridge)

    def prompt(*_):
        # Simulate another writer while the native modal input is open.
        service.save(_valid_payload(provider="custom", custom_url="https://endpoint-b.example.test/v1"))
        service.set_credential("custom", "isolated-endpoint-b-key")
        return "isolated-dialog-for-endpoint-a-key"

    bridge._credential_prompt = prompt
    bridge.promptCredential("custom")
    ready(bridge)
    assert bridge.error
    saved = load_llm_settings(db_path=service.db_path)
    assert saved.base_url == "https://endpoint-b.example.test/v1"
    assert saved.api_key == "isolated-endpoint-b-key"
    assert "isolated-dialog" not in repr((bridge.settingsData, bridge.receipt, bridge.error))


def test_slow_preview_leaving_discards_result_and_queues_new_page(settings_bridge):
    _, bridge, _, subjects, _ = settings_bridge
    started, release, heartbeat = threading.Event(), threading.Event(), threading.Event()
    def slow(*_, **__):
        started.set()
        assert release.wait(3)
        return {"token": "stale"}
    subjects.preview = slow
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(heartbeat.set)
    try:
        bridge.run("subject.preview", {"kind": "remove"})
        assert started.wait(2)
        timer.start(10)
        wait_until(heartbeat.is_set, timeout_ms=1000)
        bridge.leave_page()
        bridge.run("load", {})
    finally:
        release.set()
    ready(bridge)
    assert not bridge.subjectPreview and subjects.loads == 1


def test_cancellation_waits_for_work_before_unlocking(settings_bridge):
    _, bridge, _, subjects, _ = settings_bridge
    started, release = threading.Event(), threading.Event()
    original = subjects.preview
    def slow(*a, **kw):
        started.set()
        assert release.wait(3)
        return original(*a, **kw)
    subjects.preview = slow
    try:
        bridge.run("subject.preview", {"kind": "introduce", "name": "隔离学科"})
        assert started.wait(2) and bridge.canCancel
        bridge.cancel()
        bridge._poll()
        assert bridge.busy and not bridge.canCancel
    finally:
        release.set()
    ready(bridge)
    assert not bridge.subjectPreview
    bridge.close()
    bridge.run("load", {})
    assert subjects.loads == 0


def test_native_prompt_abi_does_not_persist_and_zeroes_buffers(monkeypatch):
    import ctypes
    import study_app.quick.credential_prompt as native
    class Prompt:
        def __call__(self, info, target, reserved, error, username, username_length, password, password_length, save, flags):
            assert flags & 0x2 and flags & 0x40000 and flags & 0x100000
            self.password = password
            self.username = username
            assert username.value == "OpenAI" and not password.value
            password.value = "native-private-key"
            return 0
    prompt = Prompt()
    class Library:
        CredUIPromptForCredentialsW = prompt
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: Library())
    assert native.prompt_credential("OpenAI") == "native-private-key"
    assert prompt.password.value == "" and prompt.username.value == ""
