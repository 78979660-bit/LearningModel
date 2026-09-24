from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import logging
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow, QWidget

from study_app.ai.providers import LLMSettings, load_llm_settings, save_llm_settings
from study_app.core.dashboard import DashboardState, load_dashboard_state
from study_app.data.database import delete_settings_by_prefix
from study_app.ui.activity_view import filter_homepage_activity
from study_app.ui.dashboard_widgets import (
    daily_summary_card,
    metric_card,
    risk_card,
    subject_card,
    todo_card,
)
from study_app.ui.query_page import query_page
from study_app.ui.knowledge_page import knowledge_page
from study_app.ui.settings_page import settings_page
from study_app.ui.study_plan_page import study_plan_page
from study_app.ui.theme import PALETTE


def create_main_window(state: DashboardState) -> QMainWindow:
    """Create the Qt main window, loading its class only when a window is needed."""
    return _MainWindowType.build()(state)


MainWindow = create_main_window  # compatibility for existing callers


class _MainWindowType:
    @staticmethod
    @lru_cache(maxsize=1)
    def build():
        from PySide6.QtCore import QObject, Signal, QVariantAnimation, QEasingCurve
        from PySide6.QtWidgets import QMainWindow

        class WakeBridge(QObject):
            wake_requested = Signal()

        class _MainWindow(QMainWindow):
            SIDEBAR_EXPANDED_WIDTH = 220
            SIDEBAR_COLLAPSED_WIDTH = 52
            SIDEBAR_AUTO_COLLAPSE_WIDTH = 1050
            SIDEBAR_ANIMATION_MS = 240
            PAGE_HOME = 0
            PAGE_PLAN = 1
            PAGE_ASSISTANT = 2
            PAGE_KNOWLEDGE = 3
            PAGE_SETTINGS = 4

            def __init__(self, dashboard_state: DashboardState):
                super().__init__()
                self.state = dashboard_state
                self.current_page_index = 0
                self.float_icon = None
                self.tray_icon = None
                self.allow_close = False
                self.sidebar = None
                self._sidebar_collapsed = False
                self._sidebar_user_choice = None
                self._sidebar_nav_buttons = []
                self._sidebar_animation = QVariantAnimation(self)
                self._sidebar_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
                self._sidebar_animation.valueChanged.connect(self._apply_sidebar_width)
                self._refresh_handle = None
                self._refresh_service = None
                self.wake_bridge = WakeBridge(self)
                self.wake_bridge.wake_requested.connect(self.restore_from_tray)
                self.setWindowTitle("学习模型桌面应用")
                self.resize(1120, 760)
                self.setMinimumSize(920, 620)
                self.build_ui()
                self.setup_tray_icon()

            def build_ui(self, page_index: int | None = None):
                from PySide6.QtCore import Qt, QSize
                from PySide6.QtWidgets import QCheckBox, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

                self._sidebar_animation.stop()
                self._sidebar_nav_buttons = []
                self._sidebar_applied = None
                self._sidebar_text_compact = None
                from pathlib import Path
                from PySide6.QtGui import QIcon
                self._sidebar_menu_icon = QIcon(str(Path(__file__).with_name("assets") / "menu.svg"))
                self._sidebar_empty_icon = QIcon()
                self._sidebar_nav_icons = [
                    QIcon(str(Path(__file__).with_name("assets") / f"nav-{name}.svg"))
                    for name in ("home", "plan", "assistant", "graph", "settings")
                ]
                # Warm SVG rendering before the first collapse, not on an animation frame.
                for icon in [self._sidebar_menu_icon, *self._sidebar_nav_icons]:
                    icon.pixmap(QSize(22, 22), self.devicePixelRatioF())
                root = QWidget()
                layout = QHBoxLayout(root)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(0)
                self.stack = QStackedWidget()

                self.sidebar = QWidget()
                self.sidebar.setObjectName("Sidebar")
                self.sidebar.setFixedWidth(self.SIDEBAR_EXPANDED_WIDTH)
                self.sidebar.setStyleSheet("background-color: #172033; color: #ffffff;")
                side_layout = QVBoxLayout(self.sidebar)
                side_layout.setContentsMargins(18, 22, 18, 18)
                side_layout.setSpacing(10)

                sidebar_toggle = QPushButton()
                sidebar_toggle.setIcon(self._sidebar_menu_icon)
                sidebar_toggle.setIconSize(QSize(22, 22))
                sidebar_toggle.setObjectName("SidebarToggle")
                sidebar_toggle.setAccessibleName("收起左侧菜单")
                sidebar_toggle.setToolTip("收起左侧菜单，给主要内容留出更多空间")
                sidebar_toggle.setFixedSize(36, 36)
                sidebar_toggle.setStyleSheet(
                    "QPushButton { background: #24324a; color: #ffffff; border: 0; "
                    "text-align: center; padding: 6px; border-radius: 8px; }"
                    "QPushButton:hover { background: #334155; color: #ffffff; }"
                )
                sidebar_toggle.clicked.connect(self.toggle_sidebar)
                side_layout.addWidget(sidebar_toggle, 0, Qt.AlignmentFlag.AlignLeft)
                self._sidebar_toggle = sidebar_toggle

                sidebar_header = QWidget()
                sidebar_header.setStyleSheet("background-color: #172033;")
                header_layout = QVBoxLayout(sidebar_header)
                header_layout.setContentsMargins(0, 0, 0, 0)
                header_layout.setSpacing(4)
                title = QLabel("学习模型")
                title.setObjectName("SidebarTitle")
                title.setStyleSheet(
                    "background-color: #172033; color: #ffffff; "
                    "font-size: 22px; font-weight: 800;"
                )
                header_layout.addWidget(title)
                subtitle = QLabel("Personal Learning OS")
                subtitle.setObjectName("SidebarSubtle")
                subtitle.setStyleSheet(
                    "background-color: #172033; color: #cbd5e1; font-size: 12px;"
                )
                header_layout.addWidget(subtitle)
                side_layout.addWidget(sidebar_header)
                side_layout.addSpacing(18)
                self._sidebar_header_spacer = side_layout.itemAt(side_layout.count() - 1).spacerItem()
                self._sidebar_header = sidebar_header
                self._sidebar_header_opacity = QGraphicsOpacityEffect(sidebar_header)
                sidebar_header.setGraphicsEffect(self._sidebar_header_opacity)
                sidebar_header.ensurePolished()
                self._sidebar_header_height = sidebar_header.sizeHint().height()
                nav_items = ["主页", "学习计划", "学习助理", "知识图谱", "设置"]
                for index, text in enumerate(nav_items):
                    button = QPushButton(text)
                    button.setObjectName("NavButton")
                    button.setCheckable(True)
                    button.setIconSize(QSize(22, 22))
                    button.setFixedHeight(44)
                    button.setAutoExclusive(True)
                    button.setProperty("fullText", text)
                    button.setAccessibleName(text)
                    button.setToolTip(text)
                    button.setStyleSheet(
                        "QPushButton { background: transparent; color: #dbeafe; border: 0; "
                        "text-align: left; padding: 10px 12px; border-radius: 8px; }"
                        "QPushButton:hover { background: #24324a; color: #ffffff; }"
                        "QPushButton:checked { background: #315ca8; color: #ffffff; font-weight: 700; }"
                        "QPushButton:focus { border: 1px solid #93c5fd; }"
                        "QPushButton[compact=\"true\"] { padding: 10px 1px; text-align: center; font-size: 12px; }"
                    )
                    button.clicked.connect(lambda _checked=False, page_index=index: self.switch_page(page_index))
                    side_layout.addWidget(button)
                    self._sidebar_nav_buttons.append(button)
                side_layout.addStretch()

                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                content = QWidget()
                content_layout = QVBoxLayout(content)
                content_layout.setContentsMargins(30, 28, 30, 30)
                content_layout.setSpacing(20)

                top = QHBoxLayout()
                heading = QLabel("今日概览")
                heading.setObjectName("HeroTitle")
                top.addWidget(heading)
                top.addStretch()
                mode_toggle = QCheckBox("LLM增强模式")
                llm_settings = load_llm_settings()
                mode_toggle.setChecked(llm_settings.enabled)
                mode_toggle.setToolTip("关闭为本地模式；开启后，仅设置页勾选的功能会调用 LLM。")

                def toggle_llm_mode(checked):
                    settings = load_llm_settings()
                    save_llm_settings(LLMSettings(
                        enabled=bool(checked),
                        provider=settings.provider,
                        model=settings.model,
                        custom_base_url=settings.custom_base_url,
                        api_key=settings.api_key,
                        single_call_token_limit=settings.single_call_token_limit,
                        daily_budget_cny=settings.daily_budget_cny,
                        allow_upload_images=settings.allow_upload_images,
                        allow_upload_pdfs=settings.allow_upload_pdfs,
                        enabled_features=settings.enabled_features,
                    ))
                    mode_toggle.setText("LLM增强模式" if checked else "本地模式")

                mode_toggle.setText("LLM增强模式" if llm_settings.enabled else "本地模式")
                mode_toggle.toggled.connect(toggle_llm_mode)
                top.addWidget(mode_toggle)
                hide = QPushButton("隐藏")
                hide.setObjectName("GhostButton")
                hide.clicked.connect(self.hide_to_float_icon)
                top.addWidget(hide)
                refresh = QPushButton("刷新")
                refresh.setObjectName("GhostButton")
                refresh.clicked.connect(self.request_dashboard_refresh)
                self._refresh_button = refresh
                top.addWidget(refresh)
                content_layout.addLayout(top)

                range_label = QLabel(
                    f"{self.state.start.isoformat()} 至 {self.state.today.isoformat()}  |  "
                    f"标杆 {self.state.benchmark:.0f}  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                )
                range_label.setObjectName("Muted")
                content_layout.addWidget(range_label)
                self._home_scroll = scroll
                self._home_content_layout = content_layout
                self._home_range_label = range_label

                from study_app.ui.design_components import disclosure, today_plan_card, readable_page
                readable_page(scroll, content)
                self._today_plan_card = today_plan_card(self.state, self.open_plan_task)
                content_layout.addWidget(self._today_plan_card)
                analysis = QWidget()
                analysis_layout = QVBoxLayout(analysis)
                analysis_layout.setContentsMargins(0, 0, 0, 0)
                analysis_layout.setSpacing(16)
                content_layout.addWidget(disclosure("学习分析与依据", analysis))
                self._home_content_layout = analysis_layout
                homepage_activity = filter_homepage_activity(self.state)
                cards = QHBoxLayout()
                cards.setSpacing(16)
                metric_cards = [
                    metric_card("推荐任务", str(homepage_activity.counts["todos"]), "当前模型推荐；未等同已确认计划"),
                    metric_card("遗忘风险", str(homepage_activity.counts["memory_risks"]), "当前全部活动学科知识点"),
                    metric_card("需要巩固", str(homepage_activity.counts["bkt_alerts"]), "BKT 估计低于阈值"),
                    metric_card("低分学科", str(homepage_activity.counts["low_subjects"]), "低于周期标杆"),
                ]
                for metric in metric_cards:
                    cards.addWidget(metric)
                self._home_metric_cards = metric_cards
                analysis_layout.addLayout(cards)

                self._home_dynamic_cards = [
                    subject_card(self.state),
                    disclosure("学习总结", daily_summary_card(self.state)),
                    disclosure("推荐依据", todo_card(self.state, homepage_activity.todos)),
                    risk_card("遗忘曲线风险", homepage_activity.memory_risks[:8], "memory"),
                    risk_card("BKT 掌握预警", homepage_activity.bkt_alerts[:8], "bkt"),
                ]
                for dynamic_card in self._home_dynamic_cards:
                    analysis_layout.addWidget(dynamic_card)
                content_layout.addStretch()

                scroll.setWidget(content)
                self.stack.addWidget(scroll)
                self.stack.addWidget(study_plan_page(self.state))
                self.stack.addWidget(query_page(self.state))
                self.stack.addWidget(knowledge_page(self.state))
                self.stack.addWidget(settings_page())
                target_page = self.current_page_index if page_index is None else page_index
                target_page = max(0, min(target_page, self.stack.count() - 1))
                self.current_page_index = target_page
                self.stack.setCurrentIndex(target_page)
                self._sidebar_nav_buttons[target_page].setChecked(True)
                layout.addWidget(self.sidebar)
                layout.addWidget(self.stack, 1)
                self.setCentralWidget(root)
                initial_collapsed = (
                    self._sidebar_user_choice
                    if self._sidebar_user_choice is not None
                    else self.width() < self.SIDEBAR_AUTO_COLLAPSE_WIDTH
                )
                self.set_sidebar_collapsed(initial_collapsed)

            def set_sidebar_collapsed(self, collapsed: bool, *, remember: bool = False, animate: bool = False):
                """Slide the existing layout; a reversal starts at its current width."""
                collapsed = bool(collapsed)
                if remember:
                    self._sidebar_user_choice = collapsed
                self._sidebar_collapsed = collapsed
                if self.sidebar is None:
                    return
                if self._sidebar_applied == collapsed and animate:
                    return
                self._sidebar_applied = collapsed

                target_width = (
                    self.SIDEBAR_COLLAPSED_WIDTH
                    if collapsed
                    else self.SIDEBAR_EXPANDED_WIDTH
                )
                self._sidebar_animation.stop()
                self._sidebar_toggle.setAccessibleName(
                    "展开左侧菜单" if collapsed else "收起左侧菜单"
                )
                self._sidebar_toggle.setToolTip(
                    "展开左侧菜单"
                    if collapsed
                    else "收起左侧菜单，给主要内容留出更多空间"
                )
                current_width = self.sidebar.width()
                if animate and self.isVisible() and current_width != target_width:
                    # Reconfiguring a finished animation otherwise emits its old end
                    # value before start(), briefly jumping to the opposite edge.
                    self._sidebar_animation.blockSignals(True)
                    self._sidebar_animation.setDuration(max(80, round(
                        self.SIDEBAR_ANIMATION_MS * abs(target_width - current_width)
                        / (self.SIDEBAR_EXPANDED_WIDTH - self.SIDEBAR_COLLAPSED_WIDTH)
                    )))
                    self._sidebar_animation.setStartValue(current_width)
                    self._sidebar_animation.setEndValue(target_width)
                    self._sidebar_animation.setCurrentTime(0)
                    self._sidebar_animation.blockSignals(False)
                    self._sidebar_animation.start()
                else:
                    self._apply_sidebar_width(target_width)

            def _apply_sidebar_width(self, value):
                width = int(value)
                ratio = max(0.0, min(1.0, (width - self.SIDEBAR_COLLAPSED_WIDTH)
                    / (self.SIDEBAR_EXPANDED_WIDTH - self.SIDEBAR_COLLAPSED_WIDTH)))
                self.sidebar.setFixedWidth(width)
                self._sidebar_header.setMaximumHeight(round(self._sidebar_header_height * ratio))
                self._sidebar_header.setVisible(ratio > 0)
                self._sidebar_header_opacity.setOpacity(max(0.0, (ratio - 0.6) / 0.4))
                self._sidebar_header_spacer.changeSize(0, round(18 * ratio))
                margins = tuple(round(small + (large - small) * ratio)
                    for small, large in zip((7, 12, 7, 12), (18, 22, 18, 18)))
                self.sidebar.layout().setContentsMargins(*margins)
                compact = ratio < 0.5
                if compact != self._sidebar_text_compact:
                    self._sidebar_text_compact = compact
                    for button, icon in zip(self._sidebar_nav_buttons, self._sidebar_nav_icons):
                        button.setText("" if compact else button.property("fullText"))
                        button.setIcon(icon if compact else self._sidebar_empty_icon)
                        button.setProperty("compact", compact)
                        button.style().unpolish(button)
                        button.style().polish(button)

            def toggle_sidebar(self):
                self.set_sidebar_collapsed(
                    not self._sidebar_collapsed,
                    remember=True,
                    animate=True,
                )

            def resizeEvent(self, event):
                super().resizeEvent(event)
                if self.sidebar is None or self._sidebar_user_choice is not None:
                    return
                collapsed = event.size().width() < self.SIDEBAR_AUTO_COLLAPSE_WIDTH
                if self._sidebar_applied != collapsed:
                    self.set_sidebar_collapsed(collapsed)

            def clear_volatile_caches(self):
                delete_settings_by_prefix("daily_summary_cache:")

            def open_plan_task(self, item_id=None):
                self.switch_page(self.PAGE_PLAN)
                open_task = getattr(self.stack.widget(self.PAGE_PLAN), "_open_task", None)
                if callable(open_task):
                    open_task(item_id)

            def switch_page(self, page_index: int):
                if not 0 <= page_index < self.stack.count():
                    return
                if page_index == self.PAGE_HOME and hasattr(self, "_today_plan_card"):
                    self._today_plan_card.refresh()
                self.current_page_index = page_index
                self._sidebar_nav_buttons[page_index].setChecked(True)
                if hasattr(self, "stack"):
                    self.stack.setCurrentIndex(page_index)

            def request_dashboard_refresh(self):
                """User-triggered refresh keeps model computation off the GUI thread."""
                from PySide6.QtCore import QTimer, Qt
                from study_app.core.async_tasks import AsyncTaskService

                if self._refresh_handle is not None:
                    return
                if self._refresh_service is None:
                    self._refresh_service = AsyncTaskService(max_workers=1)
                    self._refresh_timer = QTimer(self)
                    self._refresh_timer.setInterval(30)
                    self._refresh_timer.timeout.connect(self._poll_dashboard_refresh)
                    self.destroyed.connect(self._refresh_service.close)
                if self._refresh_button.hasFocus():
                    self.setFocus(Qt.FocusReason.OtherFocusReason)
                self._refresh_button.setEnabled(False)
                self._refresh_button.setText("刷新中…")
                self.statusBar().showMessage("正在更新学习数据…")
                self._refresh_started = perf_counter()
                self._refresh_handle = self._refresh_service.submit(
                    "dashboard-refresh", load_dashboard_state, timeout_seconds=30,
                )
                self._refresh_timer.start()

            def _poll_dashboard_refresh(self):
                from study_app.core.async_tasks import TaskStatus
                snapshot = self._refresh_handle.snapshot()
                if not snapshot.finished:
                    return
                self._refresh_timer.stop()
                self._refresh_handle = None
                self._refresh_button.setEnabled(True)
                self._refresh_button.setText("刷新")
                if snapshot.status == TaskStatus.SUCCEEDED:
                    started = perf_counter()
                    self._refresh_dashboard_state(snapshot.result)
                    logging.getLogger(__name__).info(
                        "dashboard_refresh total_ms=%.1f apply_ms=%.1f",
                        (perf_counter() - self._refresh_started) * 1000,
                        (perf_counter() - started) * 1000,
                    )
                    self.statusBar().showMessage("学习数据已更新", 3000)
                else:
                    self.statusBar().showMessage("刷新失败或超时，请重试", 5000)

            def refresh_dashboard(self):
                self.current_page_index = self.stack.currentIndex() if hasattr(self, "stack") else self.current_page_index
                self._refresh_dashboard_state(load_dashboard_state())

            def refresh_subject_catalog(self):
                """Recreate subject selectors after an explicit lifecycle change."""
                try:
                    new_state = load_dashboard_state()
                except Exception:
                    self.statusBar().showMessage('学科变更已保存，页面刷新失败，请重启应用。', 10000)
                    return
                if self._refresh_service is not None:
                    self._refresh_timer.stop()
                    self._refresh_service.close()
                    self._refresh_service = None
                    self._refresh_handle = None
                page_index = self.stack.currentIndex()
                for index in range(self.stack.count()):
                    self.stack.widget(index).close()
                old_root = self.takeCentralWidget()
                self.state = new_state
                self.build_ui(page_index)
                old_root.deleteLater()
                self.statusBar().showMessage('学科目录已更新', 5000)

            def _refresh_dashboard_state(self, new_state: DashboardState):
                from PySide6.QtCore import QTimer
                from PySide6.QtWidgets import QLabel

                scroll_bar = self._home_scroll.verticalScrollBar()
                scroll_value = scroll_bar.value()
                self.state = new_state
                self._home_range_label.setText(
                    f"{new_state.start.isoformat()} 至 {new_state.today.isoformat()}  |  "
                    f"标杆 {new_state.benchmark:.0f}  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                )
                activity = filter_homepage_activity(new_state)
                for metric, key in zip(
                    self._home_metric_cards,
                    ("todos", "memory_risks", "bkt_alerts", "low_subjects"),
                ):
                    value_label = metric.findChild(QLabel, "MetricValue")
                    if value_label is not None:
                        value_label.setText(str(activity.counts[key]))
                from study_app.ui.design_components import disclosure
                self._today_plan_card.refresh(new_state)
                new_cards = [
                    subject_card(new_state),
                    disclosure("学习总结", daily_summary_card(new_state)),
                    disclosure("推荐依据", todo_card(new_state, activity.todos)),
                    risk_card("遗忘曲线风险", activity.memory_risks[:8], "memory"),
                    risk_card("BKT 掌握预警", activity.bkt_alerts[:8], "bkt"),
                ]
                for old_card, new_card in zip(self._home_dynamic_cards, new_cards):
                    position = self._home_content_layout.indexOf(old_card)
                    if hasattr(old_card, "toggle") and hasattr(new_card, "toggle"):
                        new_card.toggle.setChecked(old_card.toggle.isChecked())
                    old_card.close()
                    self._home_content_layout.removeWidget(old_card)
                    self._home_content_layout.insertWidget(position, new_card)
                    new_card.show()
                    old_card.deleteLater()
                self._home_dynamic_cards = new_cards
                self._home_content_layout.activate()
                self._home_scroll.widget().adjustSize()
                for page_index in (
                    self.PAGE_PLAN,
                    self.PAGE_ASSISTANT,
                    self.PAGE_KNOWLEDGE,
                ):
                    update_state = getattr(
                        self.stack.widget(page_index), "_set_dashboard_state", None
                    )
                    if callable(update_state):
                        update_state(new_state)
                scroll_bar.setValue(min(scroll_value, scroll_bar.maximum()))
                QTimer.singleShot(
                    0,
                    lambda: scroll_bar.setValue(
                        min(scroll_value, scroll_bar.maximum())
                    ),
                )

            def hide_to_float_icon(self):
                if self.float_icon is None:
                    self.float_icon = create_floating_icon(self)
                    self.float_icon.move(48, 180)
                self.float_icon.show()
                self.hide()

            def restore_from_float_icon(self):
                self.showNormal()
                self.raise_()
                self.activateWindow()
                if self.float_icon is not None:
                    self.float_icon.hide()

            def setup_tray_icon(self):
                from PySide6.QtCore import Qt
                from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
                from PySide6.QtWidgets import QMenu, QSystemTrayIcon

                if self.tray_icon is not None or not QSystemTrayIcon.isSystemTrayAvailable():
                    return
                pixmap = QPixmap(64, 64)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(QColor(PALETTE["blue"]))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(6, 6, 52, 52, 14, 14)
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(24, 18, 16, 16)
                painter.drawRoundedRect(20, 37, 24, 7, 3, 3)
                painter.end()

                menu = QMenu(self)
                open_action = QAction("打开学习面板", self)
                open_action.triggered.connect(self.restore_from_tray)
                reminder_action = QAction("显示今日提醒", self)
                reminder_action.triggered.connect(self.show_tray_summary)
                refresh_action = QAction("刷新模型数据", self)
                refresh_action.triggered.connect(self.request_dashboard_refresh)
                quit_action = QAction("退出程序", self)
                quit_action.triggered.connect(self.quit_application)
                menu.addAction(open_action)
                menu.addAction(reminder_action)
                menu.addAction(refresh_action)
                menu.addSeparator()
                menu.addAction(quit_action)

                self.tray_icon = QSystemTrayIcon(QIcon(pixmap), self)
                self.tray_icon.setToolTip("学习模型")
                self.tray_icon.setContextMenu(menu)
                self.tray_icon.activated.connect(self.on_tray_activated)
                self.tray_icon.show()
                if self.state.todos:
                    self.show_tray_summary()

            def restore_from_tray(self):
                self.showNormal()
                self.raise_()
                self.activateWindow()

            def request_restore(self):
                self.wake_bridge.wake_requested.emit()

            def on_tray_activated(self, reason):
                from PySide6.QtWidgets import QSystemTrayIcon

                if reason in {
                    QSystemTrayIcon.ActivationReason.Trigger,
                    QSystemTrayIcon.ActivationReason.DoubleClick,
                }:
                    self.restore_from_tray()

            def show_tray_summary(self):
                from PySide6.QtWidgets import QSystemTrayIcon

                if self.tray_icon is None:
                    return
                if self.state.todos:
                    top = self.state.todos[0]
                    message = f"{top.kind}: {top.title}\n{top.detail}"
                else:
                    message = "当前没有强预警事项。"
                self.tray_icon.showMessage("学习模型提醒", message, QSystemTrayIcon.MessageIcon.Information, 8000)

            def quit_application(self):
                from PySide6.QtWidgets import QApplication

                self.allow_close = True
                if self.float_icon is not None:
                    self.float_icon.close()
                if self.tray_icon is not None:
                    self.tray_icon.hide()
                QApplication.quit()

            def closeEvent(self, event):
                if self._refresh_service is not None:
                    self._refresh_timer.stop()
                    self._refresh_service.close()
                if getattr(self, 'tray_icon', None) is not None:
                    self.tray_icon.hide()
                    self.tray_icon.deleteLater()
                    self.tray_icon = None
                if getattr(self, 'float_icon', None) is not None:
                    self.float_icon.close()
                    self.float_icon = None
                event.accept()
                from PySide6.QtWidgets import QApplication
                QApplication.quit()
        return _MainWindow

def create_floating_icon(owner: QMainWindow) -> QWidget:
    """Create the floating window; the Qt widget class is cached after first use."""
    return _FloatingIconType.build()(owner)


FloatingIcon = create_floating_icon  # compatibility for existing callers


class _FloatingIconType:
    @staticmethod
    @lru_cache(maxsize=1)
    def build():
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QAction, QColor, QPainter, QPen
        from PySide6.QtWidgets import QMenu, QWidget

        class _FloatingIcon(QWidget):
            def __init__(self, main_window):
                super().__init__()
                self.main_window = main_window
                self.drag_start = QPoint()
                self.setFixedSize(64, 64)
                self.setWindowFlags(
                    Qt.WindowType.FramelessWindowHint
                    | Qt.WindowType.Tool
                    | Qt.WindowType.WindowStaysOnTopHint
                )
                self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                self.setCursor(Qt.CursorShape.OpenHandCursor)

            def paintEvent(self, _event):
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(QColor(PALETTE["blue"]))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(2, 2, 60, 60, 16, 16)

                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#ffffff"), 3))
                painter.drawEllipse(19, 19, 26, 26)
                painter.setPen(QPen(QColor("#bfdbfe"), 3))
                painter.drawArc(12, 12, 40, 40, 30 * 16, 120 * 16)
                painter.setPen(QPen(QColor("#ffffff"), 2))
                painter.drawLine(32, 10, 32, 17)
                painter.drawLine(32, 47, 32, 54)
                painter.drawLine(10, 32, 17, 32)
                painter.drawLine(47, 32, 54, 32)

            def mouseDoubleClickEvent(self, event):
                if event.button() == Qt.MouseButton.LeftButton:
                    self.main_window.restore_from_float_icon()

            def mousePressEvent(self, event):
                if event.button() == Qt.MouseButton.LeftButton:
                    self.drag_start = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)

            def mouseMoveEvent(self, event):
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self.move(event.globalPosition().toPoint() - self.drag_start)

            def mouseReleaseEvent(self, _event):
                self.setCursor(Qt.CursorShape.OpenHandCursor)

            def contextMenuEvent(self, event):
                menu = QMenu(self)
                open_action = QAction("打开面板", self)
                quit_action = QAction("退出程序", self)
                open_action.triggered.connect(self.main_window.restore_from_float_icon)
                quit_action.triggered.connect(self.main_window.quit_application)
                menu.addAction(open_action)
                menu.addSeparator()
                menu.addAction(quit_action)
                menu.exec(event.globalPos())

        return _FloatingIcon
