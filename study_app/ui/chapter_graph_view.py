"""章节知识图谱只读视图（合同 §5 UI / §12 交互 / §13 降级）。

本模块只提供只读渲染与交互：

- ``ChapterGraphCanvas``：QPainter 只读画布（分层布局、缩放、平移、键盘导航）；
- ``ChapterDetailPanel``：节点详情（合同 §5.3 全字段）；
- ``chapter_graph_panel``：页面式面板工厂（学科选择、图例、异步加载、诊断降级）。

与 query_page 相同的延迟导入模式：核心投影函数在函数体内导入，
测试可以用桩模块替换 ``study_app.core.chapter_graph_projection``。
零业务写入：仅调用 build/list/cache 只读接口，无网络访问，不注入
任何学习助理 action。
"""
from __future__ import annotations

import html
from datetime import date

from PySide6.QtCore import QPointF, QLineF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from study_app.core.async_tasks import AsyncTaskService, TaskStatus

GRAPH_TASK_SLOT = "chapter-graph-panel"

STATE_TEXTS = {
    "unlearned": "未学",
    "partial": "部分学习",
    "learned_unassessed": "已学｜待评估",
    "learned_assessed": "已学",
    "archived": "封存历史",
}

# 状态必须同时通过文字、边框样式与填充明暗表达，不允许只靠颜色。
NODE_VISUALS = {
    "unlearned": {
        "fill": "#ffffff",
        "fill_tone": "light",
        "border_color": "#7a8190",
        "border_tone": "grey",
        "border_style": "solid",
        "border_width": 1,
        "text_color": "#526071",
    },
    "partial": {
        "fill": "#ffffff",
        "fill_tone": "light",
        "border_color": "#b54708",
        "border_tone": "bright",
        "border_style": "dashed",
        "border_width": 2,
        "text_color": "#20252b",
    },
    "learned_unassessed": {
        "fill": "#f8fafc",
        "fill_tone": "light",
        "border_color": "#7fb2ff",
        "border_tone": "bright",
        "border_style": "solid",
        "border_width": 2,
        "text_color": "#20252b",
    },
    "learned_assessed": {
        "fill": "#d9e7ff",
        "fill_tone": "bright",
        "border_color": "#3d6fb2",
        "border_tone": "mid",
        "border_style": "solid",
        "border_width": 2,
        "text_color": "#17263b",
    },
    "archived": {
        "fill": "#e5e7eb",
        "fill_tone": "muted",
        "border_color": "#9aa0a8",
        "border_tone": "grey",
        "border_style": "dashed",
        "border_width": 1,
        "text_color": "#526071",
        "badge": "封存历史",
    },
}

PEN_STYLES = {
    "solid": Qt.PenStyle.SolidLine,
    "dashed": Qt.PenStyle.DashLine,
}

LEGEND_ITEMS = (
    ("unlearned", "未学｜浅色填充＋灰色实线边框"),
    ("partial", "部分学习｜浅色填充＋橙色虚线边框"),
    ("learned_unassessed", "已学｜待评估（亮色实线轮廓）"),
    ("learned_assessed", "已学（浅蓝填充，含掌握度）"),
    ("archived", "封存历史｜低饱和＋封存历史徽标"),
)

RELATION_DISCLAIMER = "说明：关系不代表已掌握；辅助关系不参与拓扑排序。"

DEGRADATION_TEXTS = {
    "structure_missing": "F5 学科结构未安装：无法生成章节图谱（只读诊断，不自动建表）。",
    "structure_empty": "正式结构为空：当前学科没有已登记的章节。",
    "structure_version_missing": "结构版本缺失：该学科暂无可用的正式结构。",
    "subject_archived": "该学科已归档：只读历史视图不可用（无正式结构）。",
    "relations_cycle": "强关系存在历史循环：已回退为原始章节顺序，详见诊断。",
    "registry_missing": "F2 知识点注册表缺失：掌握度与覆盖显示受限。",
    "db_read_failed": "数据库只读读取失败：章节图谱暂不可用。",
}


def mastery_line(node) -> str:
    """掌握度行：有证据给点估计，未学/封存显示“—”，其余显示“待评估”。"""
    point = getattr(node, "mastery_point", None)
    if point is None:
        if node.learning_state in {"unlearned", "archived"}:
            return "掌握度 —"
        return "掌握度 待评估"
    percent = f"{float(point) * 100:.0f}%"
    interval = getattr(node, "mastery_interval", None)
    if interval:
        low = f"{float(interval[0]) * 100:.0f}%"
        high = f"{float(interval[1]) * 100:.0f}%"
        return f"掌握度 {percent}（区间 [{low}, {high}]）"
    return f"掌握度 {percent}"


def mastery_band(point) -> str:
    """把连续掌握概率转为便于扫读的阶段标签。"""
    if point is None:
        return "待评估"
    value = min(max(float(point), 0.0), 1.0)
    if value < 0.40:
        return "基础薄弱"
    if value < 0.60:
        return "发展中"
    if value < 0.80:
        return "基本熟悉"
    if value < 0.92:
        return "熟练"
    return "高掌握"


def confidence_dots(confidence) -> str:
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        value = 0.0
    value = min(max(value, 0.0), 1.0)
    filled = int(value * 3 + 0.5)
    return "●" * filled + "○" * (3 - filled)


def confidence_label(confidence) -> str:
    try:
        value = min(max(float(confidence), 0.0), 1.0)
    except (TypeError, ValueError):
        value = 0.0
    if value < 0.34:
        return "较低"
    if value < 0.67:
        return "中等"
    return "较高"


def _evidence_source(node) -> str:
    for item in getattr(node, "diagnostics", ()) or ():
        text = str(item)
        for prefix in ("evidence_source=", "evidence_source:", "来源：", "来源:"):
            if text.startswith(prefix):
                return text[len(prefix):].strip()
    if node.learning_state == "archived":
        return "归档历史只读摘要"
    if node.coverage_count > 0:
        return "现有学习记录（只读聚合）"
    return "无有效覆盖证据"


def _recent_evidence(node) -> str:
    for item in getattr(node, "diagnostics", ()) or ():
        text = str(item)
        for prefix in ("recent_evidence=", "recent_evidence:", "最近证据：", "最近证据:"):
            if text.startswith(prefix):
                return text[len(prefix):].strip()
    if node.coverage_count > 0:
        return f"已有 {node.coverage_count} 个知识点覆盖证据"
    return "暂无有效学习证据"


class ChapterGraphCanvas(QWidget):
    """只读章节图画布：分层布局 + 缩放/平移/键盘导航，零写入。"""

    selection_changed = Signal(str)
    node_activated = Signal(str)
    snapshot_applied = Signal()

    NODE_WIDTH = 196.0
    NODE_HEIGHT = 108.0
    COLUMN_GAP = 88.0
    ROW_GAP = 34.0
    MARGIN = 28.0
    MIN_ZOOM = 0.45
    MAX_ZOOM = 3.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GraphCanvas")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self._snapshot = None
        self._columns: dict[int, tuple[str, ...]] = {}
        self._rects: dict[str, QRectF] = {}
        self._node_by_key: dict[str, object] = {}
        self._order: tuple[str, ...] = ()
        self._edges: tuple = ()
        self._zoom = 1.0
        self._pan = QPointF(self.MARGIN, self.MARGIN)
        self._selected: str | None = None
        self._filter_state = "all"
        self._search_text = ""
        self._panning = False
        self._pan_start = QPointF()
        self._pan_origin = QPointF()

    # ---- 数据 ----------------------------------------------------------------
    def apply_snapshot(self, snapshot) -> None:
        """确定性重排：同一快照永远得到相同坐标。"""
        self._snapshot = snapshot
        self._recompute_layout()
        self._selected = None
        self.reset_view()
        self.update()
        self.selection_changed.emit("")
        self.snapshot_applied.emit()

    def clear(self) -> None:
        self._snapshot = None
        self._columns = {}
        self._rects = {}
        self._node_by_key = {}
        self._order = ()
        self._edges = ()
        self._selected = None
        self.update()
        self.selection_changed.emit("")

    def _recompute_layout(self) -> None:
        self._columns = {}
        self._rects = {}
        self._node_by_key = {}
        self._order = ()
        self._edges = ()
        snapshot = self._snapshot
        if snapshot is None:
            return
        nodes = sorted(
            snapshot.nodes,
            key=lambda n: (n.module_order, str(n.display_name), str(n.module_key)),
        )
        self._edges = tuple(snapshot.edges)
        strong_endpoints: set[str] = set()
        for edge in self._edges:
            if getattr(edge, "relation_type", "") == "strong_prerequisite":
                strong_endpoints.add(edge.from_module_key)
                strong_endpoints.add(edge.to_module_key)
        columns: dict[int, list[str]] = {}
        isolated: list[str] = []
        max_rank = -1
        for node in nodes:
            self._node_by_key[node.module_key] = node
            rank = int(getattr(node, "topological_rank", 0) or 0)
            if node.module_key in strong_endpoints:
                columns.setdefault(rank, []).append(node.module_key)
                max_rank = max(max_rank, rank)
            else:
                isolated.append(node.module_key)
        if isolated:
            # 孤立章节附加在最后一列，按原始课程顺序稳定排列。
            columns.setdefault(max_rank + 1, []).extend(isolated)
        for keys in columns.values():
            keys.sort(
                key=lambda k: (
                    self._node_by_key[k].module_order,
                    str(self._node_by_key[k].display_name),
                    str(k),
                )
            )
        self._columns = {col: tuple(columns[col]) for col in sorted(columns)}
        for col, keys in self._columns.items():
            for row, key in enumerate(keys):
                x = self.MARGIN + col * (self.NODE_WIDTH + self.COLUMN_GAP)
                y = self.MARGIN + row * (self.NODE_HEIGHT + self.ROW_GAP)
                self._rects[key] = QRectF(x, y, self.NODE_WIDTH, self.NODE_HEIGHT)
        self._order = tuple(
            key for _col, keys in sorted(self._columns.items()) for key in keys
        )

    # ---- 查询接口（测试与面板使用） --------------------------------------------
    def layout_model(self) -> dict:
        return {
            "columns": {col: list(self._columns[col]) for col in sorted(self._columns)},
            "rects": {
                key: (rect.x(), rect.y(), rect.width(), rect.height())
                for key, rect in sorted(self._rects.items())
            },
            "order": list(self._order),
        }

    def module_keys(self) -> tuple[str, ...]:
        return tuple(self._order)

    def node(self, module_key: str):
        return self._node_by_key.get(module_key)

    def current_snapshot(self):
        return self._snapshot

    def snapshot_subject_key(self):
        return getattr(self._snapshot, "subject_key", None)

    def selected_module(self):
        return self._selected


    def focusNextPrevChild(self, next: bool) -> bool:  # noqa: N802
        """Tab/Backtab 在画布内循环选择章节，而不是把焦点移出画布（合同 §12）。"""
        if self._order:
            self._cycle(1 if next else -1)
            return True
        return super().focusNextPrevChild(next)
    def node_visual(self, module_key: str) -> dict:
        node = self._node_by_key[module_key]
        visual = dict(NODE_VISUALS[node.learning_state])
        visual["state_text"] = STATE_TEXTS.get(node.learning_state, node.learning_state)
        visual["mastery_text"] = mastery_line(node)
        visual["mastery_band"] = mastery_band(node.mastery_point)
        visual["coverage_text"] = f"覆盖 {node.coverage_count}/{node.topic_count}"
        visual["confidence_dots"] = confidence_dots(node.evidence_confidence)
        visual["display_name"] = node.display_name
        visual["badge"] = visual.get("badge")
        return visual

    def set_state_filter(self, state: str) -> None:
        self._filter_state = str(state or "all")
        self.update()

    def set_search_text(self, text: str) -> None:
        self._search_text = " ".join(str(text or "").lower().split())
        self.update()

    def matching_module_keys(self) -> tuple[str, ...]:
        if not self._search_text:
            return ()
        return tuple(
            key
            for key in self._order
            if self._search_text in str(self._node_by_key[key].display_name).lower()
        )

    def _related_to_selection(self, key: str) -> bool:
        if self._selected is None or key == self._selected:
            return True
        for edge in self._edges:
            endpoints = {edge.from_module_key, edge.to_module_key}
            if self._selected in endpoints and key in endpoints:
                return True
        return False

    def _node_is_dimmed(self, key: str) -> bool:
        node = self._node_by_key[key]
        if self._filter_state != "all" and node.learning_state != self._filter_state:
            return True
        if self._search_text and key not in self.matching_module_keys():
            return True
        if self._selected and not self._related_to_selection(key):
            return True
        return False

    # ---- 视图变换 --------------------------------------------------------------
    def zoom(self) -> float:
        return self._zoom

    def _device_rect(self, rect: QRectF) -> QRectF:
        top_left = self._world_to_device(rect.topLeft())
        return QRectF(
            top_left.x(),
            top_left.y(),
            rect.width() * self._zoom,
            rect.height() * self._zoom,
        )

    def node_device_rect(self, module_key: str):
        rect = self._rects.get(module_key)
        if rect is None:
            return None
        return self._device_rect(rect)

    def node_detail_level(self, module_key: str) -> str:
        """根据节点的实际屏幕尺寸选择文字密度，避免缩小后重叠。"""
        device = self.node_device_rect(module_key)
        if device is None:
            return "minimal"
        if device.width() >= 150 and device.height() >= 88:
            return "full"
        if device.width() >= 95 and device.height() >= 54:
            return "compact"
        return "minimal"

    def _world_to_device(self, point: QPointF) -> QPointF:
        return QPointF(point.x() * self._zoom + self._pan.x(), point.y() * self._zoom + self._pan.y())

    def _device_to_world(self, point: QPointF) -> QPointF:
        if self._zoom == 0:
            return QPointF(0.0, 0.0)
        return QPointF(
            (point.x() - self._pan.x()) / self._zoom,
            (point.y() - self._pan.y()) / self._zoom,
        )

    def zoom_step(self, factor: float, anchor: QPointF | None = None) -> None:
        if anchor is None:
            anchor = QPointF(self.width() / 2.0, self.height() / 2.0)
        target = min(max(self._zoom * float(factor), self.MIN_ZOOM), self.MAX_ZOOM)
        if target == self._zoom:
            return
        world = self._device_to_world(anchor)
        self._zoom = target
        self._pan = QPointF(
            anchor.x() - world.x() * target,
            anchor.y() - world.y() * target,
        )
        self.update()

    def _content_rect(self) -> QRectF:
        content: QRectF | None = None
        for rect in self._rects.values():
            content = QRectF(rect) if content is None else content.united(rect)
        return content if content is not None else QRectF()

    def fit_all(self) -> None:
        if not self._rects:
            return
        content = self._content_rect()
        avail_w = max(self.width() - 2 * self.MARGIN, 1.0)
        avail_h = max(self.height() - 2 * self.MARGIN, 1.0)
        target = min(avail_w / max(content.width(), 1.0), avail_h / max(content.height(), 1.0))
        self._zoom = min(max(target, self.MIN_ZOOM), self.MAX_ZOOM)
        self._center_content(content)

    def reset_view(self) -> None:
        self._zoom = 1.0
        content = self._content_rect()
        if (
            content.width() * self._zoom > max(self.width() - 2 * self.MARGIN, 1)
            or content.height() * self._zoom > max(self.height() - 2 * self.MARGIN, 1)
        ):
            # 内容超出画布时从拓扑起点开始，不把首列默认裁掉。
            self._pan = QPointF(
                self.MARGIN - content.x() * self._zoom,
                self.MARGIN - content.y() * self._zoom,
            )
            self.update()
        else:
            self._center_content(content)

    def _center_content(self, content: QRectF) -> None:
        if content.width() <= 0 and content.height() <= 0:
            self._pan = QPointF(self.MARGIN, self.MARGIN)
        else:
            self._pan = QPointF(
                (self.width() - content.width() * self._zoom) / 2.0 - content.x() * self._zoom,
                (self.height() - content.height() * self._zoom) / 2.0 - content.y() * self._zoom,
            )
        self.update()

    def focus_module(self, module_key: str) -> bool:
        rect = self._rects.get(module_key)
        if rect is None:
            return False
        self._select(module_key)
        center = self._world_to_device(rect.center())
        if not (0 <= center.x() <= self.width() and 0 <= center.y() <= self.height()):
            self._pan = QPointF(
                self.width() / 2.0 - rect.center().x() * self._zoom,
                self.height() / 2.0 - rect.center().y() * self._zoom,
            )
            self.update()
        return True

    # ---- 交互 ------------------------------------------------------------------
    def _select(self, module_key: str | None) -> None:
        if module_key == self._selected:
            return
        self._selected = module_key
        self.update()
        self.selection_changed.emit(module_key or "")

    def _cycle(self, step: int) -> None:
        if not self._order:
            return
        index = self._order.index(self._selected) if self._selected in self._order else -1
        next_index = 0 if index < 0 else (index + step) % len(self._order)
        target = self._order[next_index]
        self._select(target)
        self._ensure_visible(target)

    def _ensure_visible(self, module_key: str) -> None:
        device = self.node_device_rect(module_key)
        if device is None:
            return
        if device.left() < 0 or device.top() < 0 or device.right() > self.width() or device.bottom() > self.height():
            rect = self._rects[module_key]
            self._pan = QPointF(
                self.width() / 2.0 - rect.center().x() * self._zoom,
                self.height() / 2.0 - rect.center().y() * self._zoom,
            )
            self.update()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.zoom_step(factor, event.position())
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            key = self._node_at(event.position())
            if key is not None:
                self._select(key)
                event.accept()
                return
            # 空白处单击即取消选中；随后仍可保持拖动平移。
            self._select(None)
            self._panning = True
            self._pan_start = QPointF(event.position())
            self._pan_origin = QPointF(self._pan)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            pos = event.position()
            self._pan = QPointF(
                self._pan_origin.x() + (pos.x() - self._pan_start.x()),
                self._pan_origin.y() + (pos.y() - self._pan_start.y()),
            )
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._panning and event.button() == Qt.MouseButton.LeftButton:
            self._panning = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.fit_all()
        event.accept()

    def _node_at(self, device_pos: QPointF):
        for key in self._order:
            device = self.node_device_rect(key)
            if device is not None and device.contains(device_pos):
                return key
        return None

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._select(None)
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._selected:
                self.node_activated.emit(self._selected)
            event.accept()
            return
        if key == Qt.Key.Key_Tab:
            step = -1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            self._cycle(step)
            event.accept()
            return
        if key == Qt.Key.Key_Backtab:
            self._cycle(-1)
            event.accept()
            return
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_step(1.2)
            event.accept()
            return
        if key == Qt.Key.Key_Minus:
            self.zoom_step(1 / 1.2)
            event.accept()
            return
        if key == Qt.Key.Key_R:
            self.reset_view()
            event.accept()
            return
        if key == Qt.Key.Key_F:
            self.fit_all()
            event.accept()
            return
        super().keyPressEvent(event)

    # ---- 绘制 ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#f5f6f8"))
        if self._snapshot is None:
            painter.setPen(QPen(QColor("#8a8f98")))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "选择学科后显示章节图谱（只读）",
            )
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for edge in self._edges:
            self._draw_edge(painter, edge)
        for key in self._order:
            self._draw_node(painter, key)
        if self._selected is not None and self._selected in self._rects:
            device = self._device_rect(self._rects[self._selected])
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if self.hasFocus():
                painter.setPen(QPen(QColor("#2f66b3"), 2, Qt.PenStyle.DashLine))
                painter.drawRect(device.adjusted(-7, -7, 7, 7))
            painter.setPen(QPen(QColor("#2f66b3"), 2, Qt.PenStyle.SolidLine))
            painter.drawRect(device.adjusted(-4, -4, 4, 4))

    def _draw_edge(self, painter: QPainter, edge) -> None:
        source = self._rects.get(edge.from_module_key)
        target = self._rects.get(edge.to_module_key)
        if source is None or target is None:
            return
        painter.save()
        if self._selected and self._selected not in {
            edge.from_module_key,
            edge.to_module_key,
        }:
            painter.setOpacity(0.14)
        elif self._node_is_dimmed(edge.from_module_key) and self._node_is_dimmed(
            edge.to_module_key
        ):
            painter.setOpacity(0.18)
        start = self._world_to_device(source.center())
        end = self._world_to_device(target.center())
        if getattr(edge, "relation_type", "") != "strong_prerequisite":
            # 辅助关系：虚线，不带箭头，不参与拓扑排序。
            painter.setPen(QPen(QColor("#8a8f98"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(start, end)
            painter.restore()
            return
        arrow = 10.0
        line = QLineF(start, end)
        length = line.length()
        if length <= arrow:
            painter.restore()
            return
        unit = QPointF((end.x() - start.x()) / length, (end.y() - start.y()) / length)
        trimmed = QPointF(end.x() - unit.x() * arrow, end.y() - unit.y() * arrow)
        painter.setPen(QPen(QColor("#6f9fe8"), 2, Qt.PenStyle.SolidLine))
        painter.drawLine(start, trimmed)
        perp = QPointF(-unit.y(), unit.x())
        p2 = QPointF(
            trimmed.x() + perp.x() * arrow * 0.5,
            trimmed.y() + perp.y() * arrow * 0.5,
        )
        p3 = QPointF(
            trimmed.x() - perp.x() * arrow * 0.5,
            trimmed.y() - perp.y() * arrow * 0.5,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#6f9fe8"))
        painter.drawPolygon(QPolygonF([end, p2, p3]))
        painter.restore()

    def _draw_node(self, painter: QPainter, key: str) -> None:
        visual = self.node_visual(key)
        device = self._device_rect(self._rects[key])
        detail_level = self.node_detail_level(key)
        painter.save()
        painter.setClipRect(device.adjusted(-5, -5, 5, 5))
        if self._node_is_dimmed(key):
            painter.setOpacity(0.24)
        painter.setBrush(QColor(visual["fill"]))
        painter.setPen(
            QPen(
                QColor(visual["border_color"]),
                visual["border_width"],
                PEN_STYLES[visual["border_style"]],
            )
        )
        painter.drawRoundedRect(device, 10.0, 10.0)

        horizontal_padding = 12 if detail_level == "full" else 8
        vertical_padding = 9 if detail_level == "full" else 6
        inner = device.adjusted(
            horizontal_padding,
            vertical_padding,
            -horizontal_padding,
            -vertical_padding,
        )
        name_font = QFont(painter.font())
        name_font.setBold(True)
        if name_font.pointSizeF() > 0 and detail_level != "full":
            name_font.setPointSizeF(max(7.0, name_font.pointSizeF() * 0.82))
        painter.setFont(name_font)
        painter.setPen(QPen(QColor(visual["text_color"])))
        metrics = painter.fontMetrics()
        # 章节名始终只占一行。Windows 显示缩放会改变字体行高，
        # 因此不再依赖“预留两行”的固定像素假设。
        name_height = metrics.height()
        short_name = metrics.elidedText(
            visual["display_name"],
            Qt.TextElideMode.ElideRight,
            max(int(inner.width()), 1),
        )
        painter.drawText(
            QRectF(inner.left(), inner.top(), inner.width(), name_height),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            short_name,
        )

        text_font = QFont(painter.font())
        text_font.setBold(False)
        if text_font.pointSizeF() > 0:
            factor = 0.86 if detail_level == "full" else 0.72
            text_font.setPointSizeF(max(6.5, text_font.pointSizeF() * factor))
        painter.setFont(text_font)
        metrics = painter.fontMetrics()
        y = inner.top() + name_height + 3
        node = self._node_by_key[key]
        if detail_level != "minimal":
            if node.mastery_point is not None:
                prefix = "暂估 " if node.learning_state == "partial" else ""
                mastery_short = f"{prefix}{float(node.mastery_point):.0%}"
            elif node.learning_state in {"unlearned", "archived"}:
                mastery_short = "—"
            else:
                mastery_short = "待评估"
            detail_lines = (f'{visual["state_text"]} · {mastery_short}',)
        else:
            detail_lines = ()
        for text in detail_lines:
            elided = metrics.elidedText(
                text, Qt.TextElideMode.ElideRight, int(inner.width())
            )
            painter.drawText(
                QRectF(inner.left(), y, inner.width(), metrics.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                elided,
            )
            y += metrics.height() + 1

        # 覆盖度单独画为进度条，避免与掌握概率混为一个数。
        coverage = node.coverage_count / node.topic_count if node.topic_count else 0.0
        bar_height = 5 if detail_level == "full" else 4
        bar_y = device.bottom() - (10 if detail_level == "full" else 7)
        bar_rect = QRectF(inner.left(), bar_y, inner.width(), bar_height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#d8dee8"))
        painter.drawRoundedRect(bar_rect, 2.5, 2.5)
        painter.setBrush(QColor("#5b8def"))
        painter.drawRoundedRect(
            QRectF(bar_rect.left(), bar_rect.top(), bar_rect.width() * coverage, bar_rect.height()),
            2.5,
            2.5,
        )
        painter.setPen(QPen(QColor(visual["text_color"])))

        badge = visual.get("badge")
        if badge and detail_level == "full" and device.height() >= 118:
            badge_rect = QRectF(device.right() - 60, device.top() - 9, 60, 18)
            painter.setBrush(QColor("#e5e7eb"))
            painter.setPen(QPen(QColor("#526071"), 1))
            painter.drawRoundedRect(badge_rect, 6.0, 6.0)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge)
        painter.restore()


class ChapterDetailPanel(QTextEdit):
    """节点摘要：只展示学习决策所需的关键信息。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GraphDetailPanel")
        self.setReadOnly(True)
        self.setMinimumWidth(270)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setStyleSheet(
            "QTextEdit { background: #f8fafc; color: #172033; border: 1px solid #d5deeb; "
            "border-radius: 10px; padding: 12px; }"
        )
        self.show_empty("点击章节节点查看摘要。")

    def show_empty(self, message: str) -> None:
        self.setPlainText(message)

    def show_node(self, node, snapshot) -> None:
        state_text = STATE_TEXTS.get(node.learning_state, node.learning_state)
        names = {n.module_key: n.display_name for n in snapshot.nodes}
        predecessors = [
            edge.from_module_key
            for edge in snapshot.edges
            if edge.to_module_key == node.module_key
            and edge.relation_type == "strong_prerequisite"
        ]
        successors = [
            edge.to_module_key
            for edge in snapshot.edges
            if edge.from_module_key == node.module_key
            and edge.relation_type == "strong_prerequisite"
        ]

        def join_keys(keys: list[str]) -> str:
            return "、".join(names.get(key, key) for key in keys) if keys else "无"

        if node.mastery_point is None:
            mastery_value = "待评估" if node.learning_state not in {"unlearned", "archived"} else "—"
            interval_value = "暂无可用评估区间"
        else:
            prefix = "暂估 " if node.learning_state == "partial" else ""
            mastery_value = f"{prefix}{float(node.mastery_point):.0%} · {mastery_band(node.mastery_point)}"
            if node.mastery_interval:
                interval_value = (
                    f"估计范围 {node.mastery_interval[0]:.0%}–{node.mastery_interval[1]:.0%}"
                )
            else:
                interval_value = "估计范围暂不可用"

        topic_count = max(int(node.topic_count), 0)
        coverage_percent = (
            node.coverage_count / topic_count if topic_count else 0.0
        )
        evidence_value = (
            f"{confidence_label(node.evidence_confidence)} · "
            f"{float(node.evidence_confidence):.0%}"
        )

        def esc(value: object) -> str:
            return html.escape(str(value), quote=True)

        path_value = (
            f"前置：{esc(join_keys(predecessors))}<br>"
            f"后续：{esc(join_keys(successors))}"
        )

        def section(label: str, value: str, *, raw: bool = False) -> str:
            rendered = value if raw else esc(value)
            return (
                '<div style="margin:0 0 14px 0;">'
                f'<div style="color:#526071;font-size:13px;margin-bottom:4px;">{esc(label)}</div>'
                f'<div style="font-size:14px;font-weight:600;line-height:1.35;">{rendered}</div>'
                "</div>"
            )

        body = [
            f'<div style="font-size:20px;font-weight:700;margin-bottom:4px;">{esc(node.display_name)}</div>',
            f'<div style="color:#475569;margin-bottom:18px;">{esc(state_text)}</div>',
            section("掌握估计", mastery_value),
            section("不确定性", interval_value),
            section(
                "学习覆盖",
                f"{node.coverage_count}/{topic_count} 个知识点 · {coverage_percent:.0%}",
            ),
            section("证据质量", evidence_value),
            section("学习路径", path_value, raw=True),
            section("最近证据", _recent_evidence(node)),
        ]
        self.setHtml("".join(body))


def build_graph_legend() -> QFrame:
    """图例：状态文字＋边框样式样本＋两种关系线型（非仅颜色）。"""
    frame = QFrame()
    frame.setObjectName("GraphLegend")
    grid = QGridLayout(frame)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(4)
    row = 0
    for state, text in LEGEND_ITEMS:
        visual = NODE_VISUALS[state]
        style = visual["border_style"]
        swatch = QLabel()
        swatch.setFixedSize(22, 14)
        swatch.setAlignment(Qt.AlignmentFlag.AlignCenter)
        swatch.setStyleSheet(
            f"background-color: {visual['fill']};"
            f"border: {visual['border_width']}px {style} {visual['border_color']};"
        )
        label = QLabel(text)
        grid.addWidget(swatch, row, 0)
        grid.addWidget(label, row, 1)
        row += 1
    strong_label = QLabel("强先修关系：实线箭头（参与拓扑排序）")
    supporting_label = QLabel("辅助关系：虚线（不参与拓扑排序）")
    grid.addWidget(strong_label, row, 1)
    grid.addWidget(supporting_label, row + 1, 1)
    return frame


def _degradation_text(error) -> str:
    code = getattr(error, "code", None)
    if code is not None and code in DEGRADATION_TEXTS:
        return DEGRADATION_TEXTS[code]
    message = str(error)
    for known, text in DEGRADATION_TEXTS.items():
        if known in message:
            return text
    return f"章节图谱加载失败：{message}"


def chapter_graph_panel(
    parent_callable=None,
    *,
    subject_combo=None,
    archived_check=None,
    db_path=None,
):
    """页面式章节图谱面板（QScrollArea），只读，零业务写入。

    ``subject_combo`` / ``archived_check`` 允许传入页面级已有控件
    （knowledge_page 集成时复用 GraphSubjectCombo / GraphArchivedCheck），
    未提供时在面板内部创建。
    """
    from PySide6.QtCore import QTimer

    service = AsyncTaskService(max_workers=1)
    lifecycle = {"open": True}
    pending: list[tuple[object, object]] = []
    holders = {"cache": None, "subjects": (), "key": None}
    timer = None

    def projection():
        import study_app.core.chapter_graph_projection as module

        return module

    def resolve_db_path():
        if db_path is not None:
            return db_path
        from study_app.data.database import DEFAULT_DB_PATH

        return DEFAULT_DB_PATH

    def resolve_as_of():
        if callable(parent_callable):
            state = parent_callable()
            today = getattr(state, "today", None)
            if today is not None:
                return today.isoformat() if hasattr(today, "isoformat") else str(today)
        return date.today().isoformat()

    def cache():
        if holders["cache"] is None:
            holders["cache"] = projection().ChapterGraphCache()
        return holders["cache"]

    def close_tasks():
        if not lifecycle["open"]:
            return
        lifecycle["open"] = False
        if timer is not None:
            timer.stop()
        service.invalidate(GRAPH_TASK_SLOT)
        service.close()

    class GraphPanelScrollArea(QScrollArea):
        def closeEvent(self, event):
            close_tasks()
            super().closeEvent(event)

    scroll = GraphPanelScrollArea()
    scroll.setObjectName("ChapterGraphPanel")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.destroyed.connect(close_tasks)

    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)

    header_row = QHBoxLayout()
    if subject_combo is None:
        subject_combo = QComboBox()
        subject_combo.setObjectName("GraphSubjectCombo")
    subject_combo.setMinimumWidth(180)
    subject_combo.setMaximumWidth(280)
    subject_caption = QLabel("当前学科")
    subject_caption.setObjectName("Muted")
    subject_header = QLabel("—", content)
    subject_header.setObjectName("GraphSubjectHeader")
    subject_header.hide()
    structure_label = QLabel("结构版本：—")
    structure_label.setObjectName("GraphStructureVersion")
    catalog_label = QLabel("数据截止日：—｜目录修订：—")
    catalog_label.setObjectName("GraphCatalogRevision")
    if archived_check is None:
        archived_check = QCheckBox("含封存历史")
        archived_check.setObjectName("GraphArchivedCheck")
    structure_label.hide()
    catalog_label.hide()
    for widget in (subject_caption, subject_combo, archived_check):
        header_row.addWidget(widget)
    header_row.addStretch()
    layout.addLayout(header_row)

    toolbar_row = QHBoxLayout()
    search_edit = QLineEdit()
    search_edit.setObjectName("GraphSearchEdit")
    search_edit.setPlaceholderText("搜索章节")
    search_edit.setClearButtonEnabled(True)
    search_edit.setMaximumWidth(220)
    state_filter = QComboBox()
    state_filter.setObjectName("GraphStateFilter")
    for text, value in (
        ("全部状态", "all"),
        ("已学", "learned_assessed"),
        ("已学待评估", "learned_unassessed"),
        ("部分学习", "partial"),
        ("未学", "unlearned"),
    ):
        state_filter.addItem(text, value)
    fit_button = QPushButton("适配全部")
    fit_button.setObjectName("GraphFitButton")
    reset_button = QPushButton("重置视图")
    reset_button.setObjectName("GraphResetButton")
    focus_button = QPushButton("定位选中章节")
    focus_button.setObjectName("GraphFocusButton")
    focus_button.setEnabled(False)
    legend_button = QPushButton("图例")
    legend_button.setObjectName("GraphLegendButton")
    legend_button.setCheckable(True)
    diagnostics_button = QPushButton("数据说明")
    diagnostics_button.setObjectName("GraphDiagnosticsButton")
    diagnostics_button.setCheckable(True)
    loading_label = QLabel("正在加载章节图谱…")
    loading_label.setObjectName("GraphLoadingLabel")
    for widget in (search_edit, state_filter):
        header_row.insertWidget(header_row.count() - 1, widget)
    for widget in (
        fit_button,
        reset_button,
        focus_button,
        legend_button,
        diagnostics_button,
        loading_label,
    ):
        toolbar_row.addWidget(widget)
    toolbar_row.addStretch()
    layout.addLayout(toolbar_row)

    error_label = QLabel("")
    error_label.setObjectName("GraphErrorLabel")
    error_label.setWordWrap(True)
    error_label.hide()
    layout.addWidget(error_label)

    legend = build_graph_legend()
    legend.hide()
    from PySide6.QtWidgets import QDialog
    legend_popup = QDialog(scroll)
    legend_popup.setWindowTitle("图例与状态说明")
    legend_popup.setModal(False)
    legend_layout = QVBoxLayout(legend_popup)
    legend_layout.addWidget(legend)
    legend.show()
    legend_close = QPushButton("关闭")
    legend_close.clicked.connect(legend_popup.close)
    legend_layout.addWidget(legend_close)
    legend_popup.finished.connect(lambda: legend_button.setChecked(False))

    canvas = ChapterGraphCanvas()
    canvas.setMinimumHeight(240)
    canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    detail_panel = ChapterDetailPanel()
    detail_panel.hide()
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setObjectName("GraphWorkspaceSplitter")
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(canvas)
    splitter.addWidget(detail_panel)
    splitter.setStretchFactor(0, 4)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([900, 280])
    layout.addWidget(splitter, 1)

    diagnostics_label = QLabel("")
    diagnostics_label.setObjectName("GraphDiagnosticsLabel")
    diagnostics_label.setWordWrap(True)
    diagnostics_label.hide()
    layout.addWidget(diagnostics_label)

    legend_button.toggled.connect(legend_popup.setVisible)
    diagnostics_button.toggled.connect(
        lambda checked: diagnostics_label.setVisible(
            bool(checked and diagnostics_label.text())
        )
    )
    search_edit.textChanged.connect(canvas.set_search_text)
    state_filter.currentIndexChanged.connect(
        lambda _index: canvas.set_state_filter(state_filter.currentData())
    )

    def focus_search_result() -> None:
        matches = canvas.matching_module_keys()
        if matches:
            canvas.focus_module(matches[0])

    search_edit.returnPressed.connect(focus_search_result)

    def on_selection_changed(module_key: str) -> None:
        if not module_key:
            detail_panel.show_empty("点击章节节点查看详情。")
            detail_panel.hide()
            focus_button.setEnabled(False)
            return
        node = canvas.node(module_key)
        if node is None:
            detail_panel.show_empty("该章节不在当前快照中。")
            focus_button.setEnabled(False)
            return
        detail_panel.show_node(node, canvas.current_snapshot())
        detail_panel.show()
        splitter.setSizes([max(splitter.width() - 280, 400), 280])
        focus_button.setEnabled(True)

    def on_node_activated(module_key: str) -> None:
        # 只读：激活仅定位节点并刷新详情，不触发任何写入。
        canvas.focus_module(module_key)

    canvas.selection_changed.connect(on_selection_changed)
    canvas.node_activated.connect(on_node_activated)
    fit_button.clicked.connect(canvas.fit_all)
    reset_button.clicked.connect(canvas.reset_view)
    focus_button.clicked.connect(
        lambda: canvas.focus_module(canvas.selected_module())
        if canvas.selected_module()
        else None
    )
    subject_combo.currentIndexChanged.connect(lambda _index: load_graph())
    archived_check.toggled.connect(
        lambda _checked: reload_subjects(subject_combo.currentData())
    )

    def show_degradation(error) -> None:
        loading_label.hide()
        error_label.setText(_degradation_text(error))
        error_label.show()

    def reset_header() -> None:
        subject_header.setText("—")
        structure_label.setText("结构版本：—")
        catalog_label.setText("数据截止日：—｜目录修订：—")

    def reload_subjects(preferred=None) -> None:
        if not lifecycle["open"]:
            return
        try:
            subjects = tuple(
                projection().list_graph_subjects(
                    resolve_db_path(),
                    include_archived=archived_check.isChecked(),
                )
            )
        except Exception as error:
            holders["subjects"] = ()
            subject_combo.blockSignals(True)
            subject_combo.clear()
            subject_combo.blockSignals(False)
            reset_header()
            canvas.clear()
            detail_panel.show_empty("章节图谱暂不可用。")
            show_degradation(error)
            return
        holders["subjects"] = subjects
        ordered = sorted(
            subjects,
            key=lambda item: (
                0 if item.get("lifecycle_status") == "active" else 1,
                str(item.get("display_name", "")),
                str(item.get("subject_key", "")),
            ),
        )
        subject_combo.blockSignals(True)
        subject_combo.clear()
        for item in ordered:
            suffix = "" if item.get("lifecycle_status") == "active" else "（已归档）"
            subject_combo.addItem(f"{item.get('display_name', item.get('subject_key'))}{suffix}", item.get("subject_key"))
        if preferred is not None:
            index = subject_combo.findData(preferred)
            if index >= 0:
                subject_combo.setCurrentIndex(index)
        subject_combo.blockSignals(False)
        if subjects:
            load_graph()
        else:
            loading_label.hide()
            reset_header()
            canvas.clear()
            detail_panel.show_empty("暂无可显示的学科。")

    def load_graph() -> None:
        if not lifecycle["open"]:
            return
        subject_key = subject_combo.currentData()
        # 学科切换：立即清空画布，不允许上一学科残留。
        canvas.clear()
        detail_panel.show_empty("正在加载章节图谱…")
        diagnostics_label.hide()
        diagnostics_label.setText("")
        error_label.hide()
        focus_button.setEnabled(False)
        service.invalidate(GRAPH_TASK_SLOT)
        pending.clear()
        if subject_key is None:
            holders["key"] = None
            loading_label.hide()
            reset_header()
            detail_panel.show_empty("请选择学科。")
            return
        as_of = resolve_as_of()
        try:
            key = tuple(
                projection().snapshot_cache_key(
                    subject_key, db_path=resolve_db_path(), as_of_date=as_of
                )
            )
        except Exception as error:
            holders["key"] = None
            show_degradation(error)
            return
        holders["key"] = key
        subject = next(
            (item for item in holders["subjects"] if item.get("subject_key") == subject_key),
            None,
        )
        subject_header.setText(str((subject or {}).get("display_name", subject_key)))
        cached = cache().get(key)
        if cached is not None:
            apply_snapshot(cached)
            return
        loading_label.setVisible(True)
        include_archived = archived_check.isChecked()

        def worker():
            snapshot = projection().build_chapter_graph_snapshot(
                subject_key,
                db_path=resolve_db_path(),
                as_of_date=as_of,
                include_archived=include_archived,
            )
            cache().put(key, snapshot)
            return snapshot

        handle = service.submit(key, worker, slot=GRAPH_TASK_SLOT)
        pending.append((handle, dispatch_snapshot))
        timer.start()

    def dispatch_snapshot(snapshot) -> None:
        if not lifecycle["open"]:
            return
        if snapshot.status in {TaskStatus.STALE, TaskStatus.CANCELLED}:
            return
        if snapshot.key != holders["key"]:
            # 迟到结果：学科/结构/截止日已变化，直接丢弃。
            return
        loading_label.hide()
        if snapshot.status == TaskStatus.SUCCEEDED:
            apply_snapshot(snapshot.result)
        elif snapshot.status in {TaskStatus.FAILED, TaskStatus.TIMED_OUT}:
            show_degradation(
                snapshot.error if snapshot.error is not None else RuntimeError("任务超时")
            )

    def _diagnostics_parts(snapshot) -> tuple[str, str | None]:
        parts = [str(item) for item in (getattr(snapshot, "diagnostics", ()) or ())]
        if not getattr(snapshot, "relations_present", True):
            if "尚无已确认强关系" not in parts:
                parts.append("尚无已确认强关系")
        elif any("relations_missing" in part for part in parts):
            if "尚无已确认强关系" not in parts:
                parts.append("尚无已确认强关系")
        hard = None
        for code, text in DEGRADATION_TEXTS.items():
            if any(code in part for part in parts):
                hard = text
                break
        return "；".join(parts), hard

    def apply_snapshot(snapshot) -> None:
        loading_label.hide()
        subject = next(
            (
                item
                for item in holders["subjects"]
                if item.get("subject_key") == getattr(snapshot, "subject_key", None)
            ),
            None,
        )
        subject_header.setText(
            str((subject or {}).get("display_name", subject_combo.currentText() or "—"))
        )
        structure_label.setText(f"结构版本：{getattr(snapshot, 'structure_version', '—')}")
        catalog_label.setText(
            f"数据截止日：{getattr(snapshot, 'as_of_date', '—')}"
            f"｜目录修订：{getattr(snapshot, 'catalog_revision', '—')}"
        )
        technical = (
            f"结构版本：{getattr(snapshot, 'structure_version', '—')}\n"
            f"数据截止日：{getattr(snapshot, 'as_of_date', '—')}\n"
            f"目录修订：{getattr(snapshot, 'catalog_revision', '—')}"
        )
        subject_header.setToolTip(technical)
        diagnostics_button.setToolTip(technical)
        canvas.apply_snapshot(snapshot)
        text, hard = _diagnostics_parts(snapshot)
        if hard:
            error_label.setText(hard)
            error_label.show()
        if text:
            diagnostics_label.setText(text)
            diagnostics_label.setVisible(diagnostics_button.isChecked())
        else:
            diagnostics_label.hide()
        detail_panel.show_empty("点击章节节点查看详情。")
        detail_panel.hide()

    def refresh_for_state(_new_state=None) -> None:
        if not lifecycle["open"]:
            return
        reload_subjects(subject_combo.currentData())

    def poll_result() -> None:
        if not lifecycle["open"]:
            return
        for entry in list(pending):
            handle, dispatch = entry
            snapshot = handle.snapshot()
            if not snapshot.finished:
                continue
            pending.remove(entry)
            try:
                dispatch(snapshot)
            except Exception:
                loading_label.hide()
        if not pending:
            timer.stop()

    scroll._graph_task_service = service
    scroll._graph_lifecycle = lifecycle
    scroll._close_graph_tasks = close_tasks
    scroll._refresh_for_state = refresh_for_state
    scroll._graph_canvas = canvas
    scroll._graph_splitter = splitter
    scroll._graph_legend = legend

    timer = QTimer(content)
    timer.setInterval(20)
    timer.timeout.connect(poll_result)

    scroll.setWidget(content)
    reload_subjects()
    return scroll
