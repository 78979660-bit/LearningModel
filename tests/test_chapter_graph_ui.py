"""章节图谱 UI 测试（headless）。

核心投影模块 ``study_app.core.chapter_graph_projection`` 由并行任务实现；
本文件通过 monkeypatch 向 ``sys.modules`` 注入桩模块（query_page 异步
测试同款模式），UI 测试不依赖真实核心。集成真相由核心套件与后续
端到端验证承担。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
)

AS_OF = date(2026, 9, 20)


# ---------------------------------------------------------------------------
# 桩投影模块
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StubNode:
    module_key: str
    display_name: str
    module_order: int
    topological_rank: int
    learning_state: str
    mastery_point: float | None = None
    mastery_interval: tuple | None = None
    coverage_count: int = 0
    topic_count: int = 0
    evidence_confidence: float = 0.0
    diagnostics: tuple = ()


@dataclass(frozen=True)
class StubEdge:
    from_module_key: str
    to_module_key: str
    relation_type: str
    source: str = "user_confirmed"
    evidence_refs: tuple = ()
    diagnostics: tuple = ()


@dataclass(frozen=True)
class StubSnapshot:
    schema_version: str
    subject_key: str
    structure_version: str
    catalog_revision: int
    as_of_date: str
    nodes: tuple
    edges: tuple
    diagnostics: tuple = ()

    @property
    def relations_present(self) -> bool:
        return any(
            edge.relation_type == "strong_prerequisite" for edge in self.edges
        )


class StubCache:
    def __init__(self):
        self._items: dict[tuple, object] = {}

    def get(self, key):
        return self._items.get(tuple(key))

    def put(self, key, snapshot):
        self._items[tuple(key)] = snapshot


class StubProjectionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def make_stub_module() -> ModuleType:
    module = ModuleType("study_app.core.chapter_graph_projection")
    module.ChapterNode = StubNode
    module.ChapterEdge = StubEdge
    module.ChapterGraphSnapshot = StubSnapshot
    module.ChapterGraphCache = StubCache
    module.calls: list[tuple] = []
    # 零写入探针：只有业务写操作才允许触碰；只读流程必须保持零调用。
    module.forbidden_write = Mock(name="forbidden_write")
    module.SUBJECTS: tuple = ()
    module.HANDLERS: dict = {}

    def list_graph_subjects(db_path, include_archived=False):
        module.calls.append(("list_graph_subjects", str(db_path), bool(include_archived)))
        return tuple(
            item
            for item in module.SUBJECTS
            if include_archived or item.get("lifecycle_status") == "active"
        )

    def snapshot_cache_key(subject_key, *, db_path, as_of_date):
        return (str(subject_key), str(db_path), str(as_of_date))

    def build_chapter_graph_snapshot(
        subject_key, *, db_path, as_of_date, include_archived=False
    ):
        module.calls.append(
            ("build", str(subject_key), str(db_path), str(as_of_date), bool(include_archived))
        )
        handler = module.HANDLERS.get(subject_key)
        if handler is None:
            raise StubProjectionError("structure_missing", f"{subject_key} 无正式结构")
        return handler(
            subject_key,
            db_path=db_path,
            as_of_date=as_of_date,
            include_archived=include_archived,
        )

    module.list_graph_subjects = list_graph_subjects
    module.snapshot_cache_key = snapshot_cache_key
    module.build_chapter_graph_snapshot = build_chapter_graph_snapshot
    return module


@pytest.fixture()
def stub_module(monkeypatch):
    module = make_stub_module()
    import study_app.core as core_pkg

    monkeypatch.setitem(sys.modules, "study_app.core.chapter_graph_projection", module)
    monkeypatch.setattr(core_pkg, "chapter_graph_projection", module, raising=False)
    return module

def subject(key: str, name: str, status: str = "active", has_structure: bool = True):
    return {
        "subject_key": key,
        "display_name": name,
        "lifecycle_status": status,
        "has_current_structure": has_structure,
    }


def sample_snapshot(
    subject_key: str = "subj:discrete", prefix: str = "module:v1"
) -> StubSnapshot:
    k1, k2, k3, k4, k5 = (f"{prefix}:m{i}" for i in range(1, 6))
    nodes = (
        StubNode(k2, "命题逻辑", 1, 1, "learned_unassessed",
                 None, None, 4, 4, 0.7, ()),
        StubNode(k5, "历史专题", 4, 4, "archived",
                 None, None, 0, 0, 0.0, ("structure_topics_unbound",)),
        StubNode(k1, "集合与关系", 0, 0, "learned_assessed",
                 0.42, (0.31, 0.55), 3, 8, 0.8, ("evidence_source=学习记录",)),
        StubNode(k4, "代数系统", 3, 3, "unlearned",
                 None, None, 0, 2, 0.0, ()),
        StubNode(k3, "图论基础", 2, 2, "partial",
                 None, None, 1, 3, 0.5, ("recent_evidence=讲义第4章",)),
    )
    edges = (
        StubEdge(k1, k2, "strong_prerequisite", "user_confirmed", ("ref-1",), ()),
        StubEdge(k2, k3, "strong_prerequisite", "adopted_manifest", (), ()),
        StubEdge(k3, k5, "supporting_relation", "derived_topic_evidence", (), ()),
    )
    return StubSnapshot(
        "chapter-graph-v1", subject_key, "structure:v1:one", 3,
        AS_OF.isoformat(), nodes, edges, (),
    )


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def pump_until(qapp, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Qt UI did not reach expected state")


def build_panel(stub_module, **kwargs):
    from study_app.ui.chapter_graph_view import chapter_graph_panel

    return chapter_graph_panel(
        parent_callable=lambda: SimpleNamespace(today=AS_OF),
        db_path="stub-db.sqlite",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 画布
# ---------------------------------------------------------------------------
class TestChapterGraphCanvas:
    def test_layout_is_deterministic_and_layered(self, qapp, stub_module):
        from study_app.ui.chapter_graph_view import ChapterGraphCanvas

        canvas = ChapterGraphCanvas()
        first = sample_snapshot()
        second = sample_snapshot()
        canvas.apply_snapshot(first)
        model_one = canvas.layout_model()
        canvas.apply_snapshot(second)
        model_two = canvas.layout_model()
        assert model_one == model_two
        # 列 = 拓扑层级；孤立章节（无强边）附加到最后一列，按 module_order。
        assert model_one["columns"] == {
            0: ["module:v1:m1"],
            1: ["module:v1:m2"],
            2: ["module:v1:m3"],
            3: ["module:v1:m4", "module:v1:m5"],
        }
        rects = model_one["rects"]
        assert rects["module:v1:m1"][0] < rects["module:v1:m2"][0] < rects["module:v1:m3"][0]
        assert rects["module:v1:m4"][0] == rects["module:v1:m5"][0]
        assert rects["module:v1:m4"][1] < rects["module:v1:m5"][1]
        assert rects["module:v1:m1"][2:] == (196.0, 108.0)
        canvas.deleteLater()

    def test_zoom_clamps_at_extremes_and_reset_restores(self, qapp, stub_module):
        from study_app.ui.chapter_graph_view import ChapterGraphCanvas

        canvas = ChapterGraphCanvas()
        canvas.resize(640, 480)
        canvas.apply_snapshot(sample_snapshot())
        canvas.zoom_step(1e9)
        assert canvas.zoom() == canvas.MAX_ZOOM
        canvas.zoom_step(1e9)
        assert canvas.zoom() == canvas.MAX_ZOOM  # 无数值溢出
        canvas.zoom_step(1e-9)
        assert canvas.zoom() == canvas.MIN_ZOOM
        assert canvas.node_detail_level("module:v1:m1") == "minimal"
        canvas.zoom_step(1e-9)
        assert canvas.zoom() == canvas.MIN_ZOOM
        canvas.reset_view()
        assert canvas.zoom() == 1.0
        assert canvas.node_detail_level("module:v1:m1") == "full"
        # 键盘 +/- 同样受钳制
        for _ in range(30):
            QTest.keyClick(canvas, Qt.Key.Key_Plus)
        assert canvas.zoom() <= canvas.MAX_ZOOM
        for _ in range(30):
            QTest.keyClick(canvas, Qt.Key.Key_Minus)
        assert canvas.zoom() >= canvas.MIN_ZOOM
        QTest.keyClick(canvas, Qt.Key.Key_R)
        assert canvas.zoom() == 1.0
        canvas.deleteLater()

    def test_blank_click_clears_selection(self, qapp, stub_module):
        from study_app.ui.chapter_graph_view import ChapterGraphCanvas

        canvas = ChapterGraphCanvas()
        canvas.resize(640, 480)
        canvas.show()
        canvas.apply_snapshot(sample_snapshot())
        canvas.focus_module("module:v1:m1")
        assert canvas.selected_module() == "module:v1:m1"
        QTest.mouseClick(
            canvas,
            Qt.MouseButton.LeftButton,
            pos=QPoint(5, 5),
        )
        assert canvas.selected_module() is None
        canvas.deleteLater()

    def test_fit_all_fits_bounding_rect_into_viewport(self, qapp, stub_module):
        from study_app.ui.chapter_graph_view import ChapterGraphCanvas

        canvas = ChapterGraphCanvas()
        canvas.resize(640, 420)
        canvas.apply_snapshot(sample_snapshot())
        canvas.fit_all()
        assert canvas.MIN_ZOOM <= canvas.zoom() <= canvas.MAX_ZOOM
        for key in canvas.module_keys():
            rect = canvas.node_device_rect(key)
            assert rect is not None
            assert rect.left() >= 0 and rect.top() >= 0
            assert rect.right() <= canvas.width() and rect.bottom() <= canvas.height()
        # 放大后再适配全部，仍全部可见
        canvas.zoom_step(1e9)
        canvas.fit_all()
        for key in canvas.module_keys():
            rect = canvas.node_device_rect(key)
            assert rect.right() <= canvas.width() and rect.bottom() <= canvas.height()
        canvas.deleteLater()

    def test_keyboard_cycles_nodes_and_enter_emits_activation(self, qapp, stub_module):
        from study_app.ui.chapter_graph_view import ChapterGraphCanvas

        canvas = ChapterGraphCanvas()
        canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        canvas.apply_snapshot(sample_snapshot())
        selections: list[str] = []
        activations: list[str] = []
        canvas.selection_changed.connect(selections.append)
        canvas.node_activated.connect(activations.append)

        canvas.setFocus()
        assert canvas.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert canvas.selected_module() is None
        QTest.keyClick(canvas, Qt.Key.Key_Tab)
        assert canvas.selected_module() == "module:v1:m1"
        QTest.keyClick(canvas, Qt.Key.Key_Tab)
        assert canvas.selected_module() == "module:v1:m2"
        QTest.keyClick(canvas, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
        assert canvas.selected_module() == "module:v1:m1"
        QTest.keyClick(canvas, Qt.Key.Key_Enter)
        assert activations == ["module:v1:m1"]
        assert selections[0] == "module:v1:m1"
        QTest.keyClick(canvas, Qt.Key.Key_Escape)
        assert canvas.selected_module() is None
        assert canvas.module_keys() == (
            "module:v1:m1", "module:v1:m2", "module:v1:m3", "module:v1:m4", "module:v1:m5",
        )
        canvas.deleteLater()

    def test_state_text_and_border_styles_differ_per_state(self, qapp, stub_module):
        from study_app.ui.chapter_graph_view import ChapterGraphCanvas, build_graph_legend

        canvas = ChapterGraphCanvas()
        canvas.apply_snapshot(sample_snapshot())
        visuals = {key: canvas.node_visual(key) for key in canvas.module_keys()}
        assert {v["state_text"] for v in visuals.values()} == {
            "未学", "部分学习", "已学｜待评估", "已学", "封存历史",
        }
        signatures = {
            key: (
                v["fill_tone"],
                v["border_tone"],
                v["border_style"],
                v["border_width"],
            )
            for key, v in visuals.items()
        }
        assert len(set(signatures.values())) == 5  # 文字之外，样式亦五者互异
        assert visuals["module:v1:m1"]["mastery_text"].startswith("掌握度 42%")
        assert visuals["module:v1:m2"]["mastery_text"] == "掌握度 待评估"
        assert visuals["module:v1:m4"]["mastery_text"] == "掌握度 —"
        assert visuals["module:v1:m5"]["badge"] == "封存历史"
        assert visuals["module:v1:m1"]["confidence_dots"] == "●●○"
        assert visuals["module:v1:m4"]["confidence_dots"] == "○○○"
        assert visuals["module:v1:m1"]["coverage_text"] == "覆盖 3/8"

        legend = build_graph_legend()
        legend_text = "\n".join(
            label.text() for label in legend.findChildren(QLabel)
        )
        for text in ("未学", "部分学习", "已学｜待评估", "已学", "封存历史", "实线", "虚线"):
            assert text in legend_text
        canvas.deleteLater()
        legend.deleteLater()


# ---------------------------------------------------------------------------
# 详情面板
# ---------------------------------------------------------------------------
class TestChapterDetailPanel:
    def test_detail_panel_renders_full_fields(self, qapp, stub_module):
        from study_app.ui.chapter_graph_view import ChapterDetailPanel

        snapshot = sample_snapshot()
        panel = ChapterDetailPanel()
        by_key = {node.module_key: node for node in snapshot.nodes}

        panel.show_node(by_key["module:v1:m1"], snapshot)
        text = panel.toPlainText()
        for expected in (
            "集合与关系",
            "已学",
            "掌握估计",
            "42% · 发展中",
            "估计范围 31%–55%",
            "学习覆盖",
            "3/8 个知识点 · 38%",
            "证据质量",
            "较高 · 80%",
            "前置：无",
            "后续：命题逻辑",
        ):
            assert expected in text
        for technical in (
            "module:v1:m1",
            "subj:discrete",
            "structure:v1:one",
            "user_confirmed",
            "evidence_source=",
        ):
            assert technical not in text

        panel.show_node(by_key["module:v1:m2"], snapshot)
        text = panel.toPlainText()
        assert "已学｜待评估" in text
        assert "待评估" in text
        assert "前置：集合与关系" in text
        assert "后续：图论基础" in text

        panel.show_node(by_key["module:v1:m4"], snapshot)
        assert "—" in panel.toPlainText()

        panel.show_node(by_key["module:v1:m3"], snapshot)
        text = panel.toPlainText()
        assert "讲义第4章" in text

        panel.show_node(by_key["module:v1:m5"], snapshot)
        text = panel.toPlainText()
        assert "封存历史" in text
        assert "structure_topics_unbound" not in text

        panel.show_empty("尚无选中章节")
        assert panel.toPlainText() == "尚无选中章节"
        assert panel.isReadOnly()
        panel.deleteLater()


# ---------------------------------------------------------------------------
# 面板（异步 + 降级 + 生命周期）
# ---------------------------------------------------------------------------
class TestChapterGraphPanel:
    def test_subject_combo_populated_active_first_and_archived_toggle(
        self, qapp, stub_module
    ):
        stub_module.SUBJECTS = (
            subject("subj:old", "旧学科", status="archived"),
            subject("subj:zoo", "动物学"),
            subject("subj:abc", "离散数学"),
        )
        stub_module.HANDLERS = {
            key: (lambda k, **_kw: sample_snapshot(k))
            for key in ("subj:old", "subj:zoo", "subj:abc")
        }
        panel = build_panel(stub_module)
        try:
            combo = panel.findChild(QComboBox, "GraphSubjectCombo")
            check = panel.findChild(QCheckBox, "GraphArchivedCheck")
            # 活动学科优先，其后按显示名稳定排序；归档学科默认隐藏
            assert [combo.itemData(i) for i in range(combo.count())] == [
                "subj:zoo", "subj:abc"
            ]
            pump_until(
                qapp, lambda: panel._graph_canvas.snapshot_subject_key() == "subj:zoo"
            )
            check.setChecked(True)
            assert [combo.itemData(i) for i in range(combo.count())] == [
                "subj:zoo", "subj:abc", "subj:old"
            ]
            assert combo.itemText(2).endswith("（已归档）")
            # 归档开关不改变当前选择
            assert combo.currentData() == "subj:zoo"
        finally:
            panel.close()
            qapp.processEvents()

    def test_switch_subject_clears_canvas_then_repopulates_without_residue(
        self, qapp, stub_module
    ):
        started = threading.Event()
        release = threading.Event()

        def blocking_handler(_key, **_kwargs):
            started.set()
            release.wait(2)
            return sample_snapshot("subj:block", prefix="module:block")

        stub_module.SUBJECTS = (
            subject("subj:warm", "甲学科"),
            subject("subj:block", "乙学科"),
            subject("subj:fresh", "丙学科"),
        )
        stub_module.HANDLERS = {
            "subj:warm": lambda _key, **_kw: sample_snapshot("subj:warm", prefix="module:warm"),
            "subj:block": blocking_handler,
            "subj:fresh": lambda _key, **_kw: sample_snapshot("subj:fresh", prefix="module:fresh"),
        }
        panel = build_panel(stub_module)
        try:
            combo = panel.findChild(QComboBox, "GraphSubjectCombo")
            canvas = panel._graph_canvas
            loading = panel.findChild(QLabel, "GraphLoadingLabel")
            pump_until(qapp, lambda: canvas.snapshot_subject_key() is not None)
            combo.setCurrentIndex(combo.findData("subj:block"))
            assert started.wait(2)
            combo.setCurrentIndex(combo.findData("subj:fresh"))
            # 切换时刻：旧图立即清空，不残留上一学科节点
            keys_now = canvas.module_keys()
            assert keys_now == () or all(k.startswith("module:fresh") for k in keys_now)
            assert loading.isVisibleTo(panel) or canvas.snapshot_subject_key() == "subj:fresh"
            release.set()
            pump_until(qapp, lambda: canvas.snapshot_subject_key() == "subj:fresh")
            keys = canvas.module_keys()
            assert keys and all(key.startswith("module:fresh:") for key in keys)
            assert "module:warm:m1" not in keys  # 历史学科节点无残留
            time.sleep(0.05)
            qapp.processEvents()
            # 迟到的阻塞学科结果被丢弃（STALE + 快照键复核）
            assert canvas.snapshot_subject_key() == "subj:fresh"
            assert "module:block:m1" not in canvas.module_keys()
            assert not loading.isVisibleTo(panel)
        finally:
            release.set()
            panel.close()
            qapp.processEvents()

    def test_loading_label_visible_while_snapshot_in_flight(self, qapp, stub_module):
        started = threading.Event()
        release = threading.Event()

        def blocking_handler(_key, **_kwargs):
            started.set()
            release.wait(2)
            return sample_snapshot()

        stub_module.SUBJECTS = (subject("subj:discrete", "离散数学"),)
        stub_module.HANDLERS = {"subj:discrete": blocking_handler}
        panel = build_panel(stub_module)
        try:
            canvas = panel._graph_canvas
            loading = panel.findChild(QLabel, "GraphLoadingLabel")
            assert started.wait(2)
            assert loading.isVisibleTo(panel)
            assert canvas.module_keys() == ()
            release.set()
            pump_until(qapp, lambda: canvas.snapshot_subject_key() == "subj:discrete")
            assert not loading.isVisibleTo(panel)
            assert len(canvas.module_keys()) == 5
        finally:
            release.set()
            panel.close()
            qapp.processEvents()

    def test_structure_missing_shows_degradation_label(self, qapp, stub_module):
        stub_module.SUBJECTS = (subject("subj:broken", "无结构学科"),)
        stub_module.HANDLERS = {
            "subj:broken": lambda _key, **_kw: (_ for _ in ()).throw(
                StubProjectionError("structure_missing", "F5 未安装")
            )
        }
        panel = build_panel(stub_module)
        try:
            error_label = panel.findChild(QLabel, "GraphErrorLabel")
            pump_until(qapp, lambda: error_label.isVisibleTo(panel))
            assert "F5 学科结构未安装" in error_label.text()
            assert panel._graph_canvas.module_keys() == ()
        finally:
            panel.close()
            qapp.processEvents()

    def test_cache_reuses_snapshot_without_rebuild(self, qapp, stub_module):
        stub_module.SUBJECTS = (
            subject("subj:discrete", "离散数学"),
            subject("subj:zoo", "动物学"),
        )
        stub_module.HANDLERS = {
            key: (lambda k, **_kw: sample_snapshot(k))
            for key in ("subj:discrete", "subj:zoo")
        }
        panel = build_panel(stub_module)
        try:
            combo = panel.findChild(QComboBox, "GraphSubjectCombo")
            canvas = panel._graph_canvas
            pump_until(qapp, lambda: canvas.snapshot_subject_key() == "subj:zoo")
            builds = [c for c in stub_module.calls if c[0] == "build"]
            assert len(builds) == 1
            combo.setCurrentIndex(combo.findData("subj:discrete"))
            pump_until(qapp, lambda: canvas.snapshot_subject_key() == "subj:discrete")
            combo.setCurrentIndex(combo.findData("subj:zoo"))
            pump_until(qapp, lambda: canvas.snapshot_subject_key() == "subj:zoo")
            builds = [c for c in stub_module.calls if c[0] == "build"]
            assert len(builds) == 2  # 动物学命中缓存，未重建
        finally:
            panel.close()
            qapp.processEvents()

    def test_zero_business_writes(self, qapp, stub_module):
        stub_module.SUBJECTS = (
            subject("subj:discrete", "离散数学"),
            subject("subj:old", "旧学科", status="archived"),
        )
        stub_module.HANDLERS = {
            "subj:discrete": lambda _key, **_kw: sample_snapshot("subj:discrete"),
            "subj:old": lambda _key, **_kw: sample_snapshot("subj:old"),
        }
        combo = QComboBox()
        combo.setObjectName("GraphSubjectCombo")
        check = QCheckBox("含封存历史")
        check.setObjectName("GraphArchivedCheck")
        panel = build_panel(stub_module, subject_combo=combo, archived_check=check)
        try:
            canvas = panel._graph_canvas
            pump_until(qapp, lambda: canvas.snapshot_subject_key() == "subj:discrete")
            canvas.setFocus()
            QTest.keyClick(canvas, Qt.Key.Key_Tab)
            panel.findChild(QPushButton, "GraphFitButton").click()
            panel.findChild(QPushButton, "GraphResetButton").click()
            check.setChecked(True)
            combo.setCurrentIndex(combo.findData("subj:old"))
            pump_until(qapp, lambda: canvas.snapshot_subject_key() == "subj:old")
            stub_module.forbidden_write.assert_not_called()
            operations = {call[0] for call in stub_module.calls}
            assert operations <= {"list_graph_subjects", "build"}
        finally:
            panel.close()
            qapp.processEvents()

    def test_page_close_invalidates_inflight_service(self, qapp, stub_module):
        started = threading.Event()
        release = threading.Event()

        def blocking_handler(_key, **_kwargs):
            started.set()
            release.wait(2)
            return sample_snapshot()

        stub_module.SUBJECTS = (subject("subj:discrete", "离散数学"),)
        stub_module.HANDLERS = {"subj:discrete": blocking_handler}
        panel = build_panel(stub_module)
        try:
            assert started.wait(2)
            canvas = panel._graph_canvas
            panel.close()
            assert panel._graph_lifecycle["open"] is False
            service = panel._graph_task_service
            with pytest.raises(RuntimeError):
                service.submit(("after-close",), lambda: None, slot="chapter-graph-panel")
            release.set()
            time.sleep(0.05)
            qapp.processEvents()
            assert canvas.module_keys() == ()  # 关闭后迟到结果不会应用
        finally:
            release.set()
            qapp.processEvents()

    def test_external_combo_widgets_are_reused(self, qapp, stub_module):
        stub_module.SUBJECTS = (subject("subj:discrete", "离散数学"),)
        stub_module.HANDLERS = {
            "subj:discrete": lambda _key, **_kw: sample_snapshot("subj:discrete")
        }
        combo = QComboBox()
        combo.setObjectName("GraphSubjectCombo")
        check = QCheckBox("含封存历史")
        check.setObjectName("GraphArchivedCheck")
        panel = build_panel(stub_module, subject_combo=combo, archived_check=check)
        try:
            assert combo.count() == 1
            assert combo.itemData(0) == "subj:discrete"
            assert panel.objectName() == "ChapterGraphPanel"
            canvas = panel._graph_canvas
            pump_until(qapp, lambda: canvas.snapshot_subject_key() == "subj:discrete")
            legend = panel.findChild(QFrame, "GraphLegend")
            assert legend is not None
            assert legend.window().isHidden()  # 独立图例浮层默认收起，不占画布高度
            splitter = panel.findChild(QSplitter, "GraphWorkspaceSplitter")
            assert splitter is not None
            assert splitter.orientation() == Qt.Orientation.Horizontal
            assert canvas.minimumHeight() == 240
            assert panel.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
            search = panel.findChild(QLineEdit, "GraphSearchEdit")
            assert search is not None
            assert panel.findChild(QComboBox, "GraphStateFilter") is not None
            assert panel.findChild(QLabel, "GraphDiagnosticsLabel") is not None
            assert any(
                label.text() == "当前学科" for label in panel.findChildren(QLabel)
            )
            assert panel.findChild(QLabel, "GraphSubjectHeader").isHidden()
            detail = panel.findChild(QTextEdit, "GraphDetailPanel")
            canvas.setFocus()
            for _ in range(3):
                QTest.keyClick(canvas, Qt.Key.Key_Tab)
                if canvas.selected_module() is not None:
                    break
            assert canvas.selected_module() == "module:v1:m1"
            assert "集合与关系" in detail.toPlainText()
            assert "module:v1:m1" not in detail.toPlainText()
            assert not detail.isHidden()
            QTest.mouseClick(
                canvas,
                Qt.MouseButton.LeftButton,
                pos=QPoint(5, 5),
            )
            assert canvas.selected_module() is None
            assert detail.isHidden()
        finally:
            panel.close()
            qapp.processEvents()


if __name__ == "__main__":
    pytest.main([__file__])
