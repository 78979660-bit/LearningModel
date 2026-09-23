from pathlib import Path

PALETTE = {
    "bg": "#f5f6f8",
    "surface": "#ffffff",
    "panel": "#f8fafc",
    "ink": "#20252b",
    "muted": "#526071",
    "line": "#d8dee8",
    "blue": "#2f66b3",
    "blue_soft": "#dbeafe",
    "green": "#067647",
    "green_soft": "#dcfce7",
    "amber": "#b54708",
    "amber_soft": "#fff4d6",
    "red": "#b42318",
    "red_soft": "#fee4e2",
}


def build_stylesheet() -> str:
    assets = Path(__file__).with_name("assets").as_posix()
    return f"""
    QWidget {{
        background: {PALETTE["bg"]};
        color: {PALETTE["ink"]};
        font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI";
        font-size: 15px;
    }}
    #Sidebar {{
        background: #172033;
        color: #ffffff;
    }}
    #SidebarTitle {{
        color: #ffffff;
        font-size: 22px;
        font-weight: 700;
    }}
    #SidebarSubtle {{
        color: #cbd5e1;
        font-size: 12px;
    }}
    QPushButton {{
        background: {PALETTE["surface"]};
        border: 1px solid {PALETTE["line"]};
        border-radius: 8px;
        padding: 9px 14px;
        min-height: 18px;
    }}
    QPushButton:hover {{
        background: {PALETTE["blue_soft"]};
        color: {PALETTE["blue"]};
        border-color: #a8c7ff;
    }}
    QPushButton:pressed {{
        background: #bfdbfe;
    }}
    QPushButton:focus {{ border: 1px solid {PALETTE["blue"]}; }}
    QPushButton:disabled {{
        background: #f1f5f9;
        color: #94a3b8;
        border-color: #e2e8f0;
    }}
    #PrimaryButton {{
        background: {PALETTE["blue"]};
        border: 1px solid {PALETTE["blue"]};
        color: #ffffff;
        font-weight: 700;
        padding: 10px 18px;
    }}
    #PrimaryButton:hover {{
        background: #1d4ed8;
        color: #ffffff;
    }}
    #PrimaryButton:disabled {{
        background: #e5e7eb;
        color: #526071;
        border-color: #d8dee8;
    }}
    #DisclosureButton {{ text-align: left; color: {PALETTE["blue"]}; background: transparent; }}
    #GhostButton {{
        background: transparent;
        color: {PALETTE["muted"]};
        border-color: #dbe3ef;
    }}
    #GhostButton:hover {{
        background: #f8fafc;
        color: {PALETTE["ink"]};
        border-color: #cbd5e1;
    }}
    #DangerButton {{
        background: {PALETTE["red_soft"]};
        color: {PALETTE["red"]};
        border-color: #fecdca;
        font-weight: 700;
    }}
    #DangerButton:hover {{
        background: #fef3f2;
        color: {PALETTE["red"]};
        border-color: #f97066;
    }}
    QLineEdit, QTextEdit, QComboBox, QDateEdit, QListWidget {{
        background: #fbfdff;
        border: 1px solid {PALETTE["line"]};
        border-radius: 8px;
        padding: 9px 11px;
        selection-background-color: {PALETTE["blue_soft"]};
        selection-color: {PALETTE["ink"]};
    }}
    QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QDateEdit:focus, QListWidget:focus {{
        border: 1px solid {PALETTE["blue"]};
        background: #ffffff;
    }}
    QLineEdit:hover, QTextEdit:hover, QComboBox:hover, QDateEdit:hover, QListWidget:hover {{
        border: 1px solid #b9c7dc;
    }}
    QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QDateEdit:disabled {{
        background: #f1f5f9;
        color: #94a3b8;
        border-color: #e2e8f0;
    }}
    QTextEdit {{
        line-height: 145%;
    }}
    QComboBox, QDateEdit {{
        padding-right: 34px;
        min-height: 20px;
    }}
    QComboBox::drop-down, QDateEdit::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 28px;
        border: 0;
        border-left: 1px solid #e2e8f0;
        border-top-right-radius: 8px;
        border-bottom-right-radius: 8px;
        background: #f4f7fb;
    }}
    QComboBox::drop-down:hover, QDateEdit::drop-down:hover {{
        background: {PALETTE["blue_soft"]};
    }}
    QComboBox::down-arrow, QDateEdit::down-arrow {{
        image: url("{assets}/chevron-down.svg");
        width: 14px;
        height: 14px;
    }}
    QComboBox QAbstractItemView {{
        background: #ffffff;
        color: {PALETTE["ink"]};
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        padding: 6px;
        selection-background-color: {PALETTE["blue_soft"]};
        selection-color: {PALETTE["blue"]};
    }}
    QListWidget::item {{
        padding: 8px 8px;
        border: 0;
        border-radius: 6px;
        margin: 2px 0;
    }}
    QListWidget::item:hover {{
        background: #f1f5f9;
    }}
    QListWidget::item:selected {{
        background: {PALETTE["blue_soft"]};
        color: {PALETTE["blue"]};
    }}
    QCheckBox {{
        background: transparent;
        color: {PALETTE["ink"]};
        spacing: 9px;
        min-height: 24px;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 5px;
        border: 1px solid #bfccdc;
        background: #ffffff;
    }}
    QCheckBox::indicator:hover {{
        border-color: {PALETTE["blue"]};
        background: #f8fbff;
    }}
    QCheckBox::indicator:checked {{
        image: url("{assets}/check.svg");
        background: {PALETTE["blue"]};
        border: 1px solid {PALETTE["blue"]};
    }}
    QCheckBox::indicator:disabled {{
        background: #e5e7eb;
        border-color: #cbd5e1;
    }}
    QCheckBox:disabled {{
        color: #94a3b8;
    }}
    QMenu {{
        background: #ffffff;
        border: 1px solid #d8dee8;
        border-radius: 8px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 8px 24px 8px 12px;
        border-radius: 6px;
        background: transparent;
    }}
    QMenu::item:selected {{
        background: {PALETTE["blue_soft"]};
        color: {PALETTE["blue"]};
    }}
    QToolTip {{
        background: #172033;
        color: #ffffff;
        border: 0;
        border-radius: 6px;
        padding: 6px 8px;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: #cbd5e1;
        border-radius: 5px;
        min-height: 36px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: #94a3b8;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
        background: transparent;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: #cbd5e1;
        border-radius: 5px;
        min-width: 36px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: #94a3b8;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
        background: transparent;
    }}
    QScrollArea {{
        background: transparent;
        border: 0;
    }}
    #FormLabel {{
        background: transparent;
        border: 0;
        color: {PALETTE["muted"]};
        font-size: 13px;
        font-weight: 700;
        padding: 7px 0 2px 0;
    }}
    #NavButton {{
        background: transparent;
        color: #dbeafe;
        border: 0;
        text-align: left;
        padding: 10px 12px;
        border-radius: 8px;
    }}
    #NavButton:hover {{
        background: #24324a;
        color: #ffffff;
    }}
    #Card {{
        background: {PALETTE["surface"]};
        border: 1px solid {PALETTE["line"]};
        border-radius: 10px;
    }}
    #CardTitle {{
        background: transparent;
        font-size: 18px;
        font-weight: 700;
    }}
    #RecordHero {{
        background: #f8fbff;
        border: 1px solid #cfe0ff;
        border-radius: 10px;
    }}
    #RecordEyebrow {{
        background: transparent;
        color: {PALETTE["blue"]};
        font-size: 12px;
        font-weight: 700;
    }}
    #RecordHint {{
        background: transparent;
        color: {PALETTE["muted"]};
        font-size: 13px;
        line-height: 150%;
    }}
    #StatusLabel {{
        background: #f8fafc;
        color: {PALETTE["muted"]};
        border: 1px solid {PALETTE["line"]};
        border-radius: 8px;
        padding: 9px 11px;
    }}
    #Muted {{
        background: transparent;
        color: {PALETTE["muted"]};
        font-size: 13px;
    }}
    #HeroTitle {{
        background: transparent;
        font-size: 28px;
        font-weight: 800;
    }}
    #MetricValue {{
        background: transparent;
        font-size: 30px;
        font-weight: 800;
    }}
    #ListTitle {{
        background: transparent;
        font-size: 15px;
        font-weight: 500;
    }}
    #BodyText, #AssistantStatusStrip {{
        background: transparent;
        font-size: 14px;
        font-weight: 400;
    }}
    #AssistantStatusStrip {{ color: {PALETTE["muted"]}; }}
    QSpinBox, QDoubleSpinBox {{
        background: #fbfdff;
        border: 1px solid {PALETTE["line"]};
        border-radius: 8px;
        padding: 7px 24px 7px 10px;
        min-height: 20px;
    }}
    QLabel {{
        background: transparent;
    }}
    #WarningText {{
        background: transparent;
        color: {PALETTE["red"]};
        font-size: 13px;
    }}
    QProgressBar {{
        border: 0;
        border-radius: 4px;
        background: #e6ebf2;
        height: 8px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        border-radius: 4px;
        background: {PALETTE["blue"]};
    }}
    """
