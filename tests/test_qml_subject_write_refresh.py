"""Committed subject changes must refresh the page already being displayed."""
import threading

import pytest
from PySide6.QtTest import QTest

from study_app.quick.self_test import wait_until
from test_qml_window import quick_window, find_item


pytestmark = pytest.mark.parametrize('quick_window', ['phase2'], indirect=True)


@pytest.mark.parametrize('route', ['plan', 'knowledge'])
@pytest.mark.parametrize('start_empty', [False, True])
@pytest.mark.parametrize('hold_write', [False, True])
def test_subject_commit_after_leaving_settings_refreshes_visible_page(quick_window, route, start_empty, hold_write):
    _, window, bridge, engine, db_path, _ = quick_window
    if start_empty:
        bridge.navigate('settings')
        work = bridge.settings
        wait_until(lambda: not work.busy and bool(work.subjectsData))
        row = work.subjectsData['subjects'][0]
        work.run('subject.preview', {'kind': 'remove', 'subjectKey': row['subjectKey']})
        wait_until(lambda: not work.busy)
        work.run('subject.confirm', {'token': work.subjectPreview['token'],
                                    'typedName': row['name'], 'confirmed': True})
        wait_until(lambda: not work.busy)
        assert work.subjectResult.get('saved')
    bridge.navigate(route)
    target = bridge.workspace if route == 'plan' else bridge.insights
    def subjects():
        return (target.planData.get('catalog', {}).get('subjects', []) if route == 'plan'
                else target.knowledgeData.get('subjects', []))
    wait_until(lambda: not target.busy and bool(target.planData if route == 'plan' else target.knowledgeData))
    find_item(window, 'planningPane' if route == 'plan' else 'knowledgePage')
    initial = len(subjects())
    if start_empty:
        assert initial == 0
    bridge.navigate('settings')
    work = bridge.settings
    wait_until(lambda: not work.busy and bool(work.settingsData))
    find_item(window, 'settingsPage')
    wait_until(lambda: not work.busy and work._page_refresh._handle is None)
    work.run('subject.preview', {'kind': 'introduce', 'name': '切页保存核验',
                                 'outline': '第一章 / 基础', 'subjectKey': ''})
    wait_until(lambda: not work.busy)
    assert work.subjectPreview.get('token')
    # Control scheduling only: the actual service, SQL transaction, QML data
    # and notifications are unchanged. Keep the write running during entry.
    entered, release = threading.Event(), threading.Event()
    original = work._subjects.confirm
    def confirm(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    if hold_write:
        work._subjects.confirm = confirm
    try:
        work.run('subject.confirm', {'token': work.subjectPreview['token'],
                                    'typedName': '切页保存核验', 'confirmed': True})
        if hold_write:
            assert entered.wait(3)
        bridge.navigate(route)
        find_item(window, 'planningPane' if route == 'plan' else 'knowledgePage')
        release.set()
        wait_until(lambda: not work.busy and not target.busy and not bridge.busy)
        QTest.qWait(200)
        assert work.subjectResult.get('saved')
        assert not work.error
        assert len(subjects()) == initial + 1
        if route == 'plan':
            assert find_item(window, 'planScope').property('count') == initial + 2
        assert not engine._qml_warnings
    finally:
        release.set()


def test_home_llm_mode_write_after_navigation_refreshes_visible_settings(quick_window):
    _, window, bridge, engine, db_path, _ = quick_window
    bridge.navigate('settings')
    settings = bridge.settings
    wait_until(lambda: not settings.busy and bool(settings.settingsData))
    find_item(window, 'preferencesPane')
    original_llm = dict(settings.settingsData['llm'])
    expected = not original_llm['enabled']
    bridge.navigate('home')
    find_item(window, 'homePage').setProperty('detailsExpanded', True)
    daily = bridge.dailySummary
    wait_until(lambda: not daily.busy and bool(daily.summaryData))
    entered, release = threading.Event(), threading.Event()
    original = daily._service.set_llm_mode
    def set_mode(enabled):
        entered.set()
        assert release.wait(10)
        return original(enabled)
    daily._service.set_llm_mode = set_mode
    try:
        daily.run('setLlmMode', {'enabled': expected})
        assert entered.wait(3)
        bridge.navigate('settings')
        find_item(window, 'preferencesPane')
        release.set()
        wait_until(lambda: not daily.busy and not settings.busy and not bridge.busy)
        QTest.qWait(200)
        from study_app.ai.providers import load_llm_settings
        assert load_llm_settings(db_path=db_path).enabled is expected
        assert settings.settingsData['llm']['enabled'] is expected
        assert find_item(window, 'settingsLlmEnabled').property('checked') is expected
        for key in ('provider', 'model', 'singleCallTokenLimit', 'hasCredential'):
            assert settings.settingsData['llm'][key] == original_llm[key]
        assert not engine._qml_warnings
    finally:
        release.set()
