from __future__ import annotations

from study_app.data.database import list_recent_records
from study_app.ui.record_view_model import recent_record_display_lines


def recent_records_card(limit: int = 6):
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    title = QLabel(f"最近学习记录（{limit} 条）")
    title.setObjectName("CardTitle")
    layout.addWidget(title)

    records = list_recent_records(limit=limit)
    if not records:
        layout.addWidget(QLabel("暂无学习记录。"))
    for item, line in zip(records, recent_record_display_lines(records)):
        label = QLabel(line)
        label.setObjectName("ListTitle")
        label.setWordWrap(True)
        note = QLabel((item.get("note") or "")[:90])
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(label)
        if item.get("note"):
            layout.addWidget(note)
    return card

def records_page(add_record_callback):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(24, 22, 24, 24)
    layout.setSpacing(16)

    top = QHBoxLayout()
    heading = QLabel("学习记录")
    heading.setObjectName("HeroTitle")
    top.addWidget(heading)
    top.addStretch()
    add_button = QPushButton("新增记录")
    add_button.setObjectName("PrimaryButton")
    add_button.clicked.connect(add_record_callback)
    top.addWidget(add_button)
    layout.addLayout(top)

    hint = QLabel("这里集中查看最近记录、题目尝试和附件引用。后续会继续加入筛选与编辑。")
    hint.setObjectName("Muted")
    layout.addWidget(hint)
    recent_card = recent_records_card(limit=10)
    layout.addWidget(recent_card)
    layout.addStretch()

    def refresh_records():
        nonlocal recent_card
        scroll_bar = scroll.verticalScrollBar()
        old_value = scroll_bar.value()
        position = layout.indexOf(recent_card)
        replacement = recent_records_card(limit=10)
        recent_card.close()
        layout.removeWidget(recent_card)
        layout.insertWidget(position, replacement)
        recent_card.deleteLater()
        recent_card = replacement
        scroll_bar.setValue(min(old_value, scroll_bar.maximum()))
        QTimer.singleShot(
            0,
            lambda: scroll_bar.setValue(min(old_value, scroll_bar.maximum())),
        )

    scroll._refresh_records = refresh_records
    scroll.setWidget(content)
    return scroll
