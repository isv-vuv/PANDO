"""Qt implementation of Step 0: Welcome Screen for Urban-Act / PANDO."""

from __future__ import annotations

import os
from typing import Optional

from core.app.app_core.project import tool_root
from core.app.app_qt.qt_base import (
    Qt,
    QtGui,
    QtWidgets,
    app_font,
    create_step_header,
    pyqtSignal,
    qfont_bold,
    qt_enum,
    require_qgis_qt,
)

_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object


class Step0WelcomeWidget(_Q_WIDGET_BASE):
    """Welcome screen widget (Step 0) for PANDO."""

    start_requested = pyqtSignal() if pyqtSignal is not None else None
    project_folder_open_requested = pyqtSignal() if pyqtSignal is not None else None
    language_switch_requested = pyqtSignal(str) if pyqtSignal is not None else None

    def __init__(self, localizer, parent: Optional[object] = None):
        require_qgis_qt()
        super().__init__(parent)
        self.localizer = localizer
        self._build_ui()

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))
        root_layout.addWidget(scroll)

        container = QtWidgets.QWidget(scroll)
        scroll.setWidget(container)

        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(12)

        # 1. Header Section with Step Title and Compact Language Switcher
        header_row = QtWidgets.QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)

        header, _title_label, step_label = create_step_header(
            self.localizer.get_string("step0_title"),
            current_step=0,
            total_steps=6,
            localizer=self.localizer,
            parent=container,
        )
        header_row.addWidget(header, 1)

        # Compact segmented language switch: [ DE ] [ EN ]
        lang_bar = QtWidgets.QFrame(container)
        lang_bar_layout = QtWidgets.QHBoxLayout(lang_bar)
        lang_bar_layout.setContentsMargins(0, 0, 0, 0)
        lang_bar_layout.setSpacing(4)

        current_lang = (
            self.localizer.get_current_language()
            if hasattr(self.localizer, "get_current_language")
            else "de"
        )

        lang_btn_style = """
            QPushButton {
                background-color: #ffffff;
                color: #475569;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 2px 4px;
                font-size: 11px;
                font-weight: bold;
                min-width: 32px;
                max-width: 32px;
                min-height: 22px;
                max-height: 22px;
            }
            QPushButton:hover {
                background-color: #f1f5f9;
                color: #0f172a;
            }
            QPushButton:checked {
                background-color: #2563eb;
                color: #ffffff;
                border-color: #1d4ed8;
            }
        """

        btn_de = QtWidgets.QPushButton("DE", lang_bar)
        btn_de.setFont(app_font(9, qfont_bold() if current_lang == "de" else None))
        btn_de.setCheckable(True)
        btn_de.setChecked(current_lang == "de")
        btn_de.setStyleSheet(lang_btn_style)
        btn_de.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))

        btn_en = QtWidgets.QPushButton("EN", lang_bar)
        btn_en.setFont(app_font(9, qfont_bold() if current_lang == "en" else None))
        btn_en.setCheckable(True)
        btn_en.setChecked(current_lang == "en")
        btn_en.setStyleSheet(lang_btn_style)
        btn_en.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))

        lang_group = QtWidgets.QButtonGroup(self)
        lang_group.addButton(btn_de)
        lang_group.addButton(btn_en)

        def _on_switch_de():
            if current_lang != "de" and self.language_switch_requested:
                self.language_switch_requested.emit("de")

        def _on_switch_en():
            if current_lang != "en" and self.language_switch_requested:
                self.language_switch_requested.emit("en")

        btn_de.clicked.connect(_on_switch_de)
        btn_en.clicked.connect(_on_switch_en)

        lang_bar_layout.addWidget(btn_de)
        lang_bar_layout.addWidget(btn_en)
        header_row.addWidget(lang_bar, 0, qt_enum(Qt, "AlignVCenter", "AlignmentFlag"))

        layout.addLayout(header_row)

        # 2. Process Steps (Integrated in GroupBox)
        steps_card = QtWidgets.QGroupBox(self.localizer.get_string("step0_steps_group_title"), container)
        steps_layout = QtWidgets.QVBoxLayout(steps_card)
        steps_layout.setContentsMargins(14, 12, 14, 12)
        steps_layout.setSpacing(8)

        def _clean_title(raw_text: str, num: int) -> str:
            if ":" in raw_text:
                title = raw_text.split(":", 1)[1].strip()
            else:
                title = raw_text.strip()
            return f"<b>{num}.</b> {title}"

        # Dynamically load the exact titles from steps 1 to 6
        steps_grid = QtWidgets.QGridLayout()
        steps_grid.setContentsMargins(4, 2, 4, 2)
        steps_grid.setHorizontalSpacing(24)
        steps_grid.setVerticalSpacing(6)
        steps_grid.setColumnStretch(0, 1)
        steps_grid.setColumnStretch(1, 1)

        raw_titles = [
            self.localizer.get_string("step1_title"),
            self.localizer.get_string("step2_title"),
            self.localizer.get_string("step3_title"),
            self.localizer.get_string("step4_title"),
            self.localizer.get_string("step5_title"),
            self.localizer.get_string("step6_title"),
        ]

        for i, raw_title in enumerate(raw_titles):
            col = 0 if i < 3 else 1
            row = i if i < 3 else i - 3
            lbl = QtWidgets.QLabel(_clean_title(raw_title, i + 1), steps_card)
            lbl.setFont(app_font(9))
            lbl.setWordWrap(True)
            steps_grid.addWidget(lbl, row, col)

        steps_layout.addLayout(steps_grid)
        layout.addWidget(steps_card)

        # 3. Disclaimer Box (Clean GroupBox)
        disc_box = QtWidgets.QGroupBox(self.localizer.get_string("step0_disclaimer_title", default="Hinweis"), container)
        disc_layout = QtWidgets.QVBoxLayout(disc_box)
        disc_layout.setContentsMargins(14, 10, 14, 12)

        disc_lbl = QtWidgets.QLabel(
            self.localizer.get_string("step0_disclaimer_body"),
            disc_box,
        )
        disc_lbl.setFont(app_font(9))
        disc_lbl.setStyleSheet("color: #475569;")
        disc_lbl.setWordWrap(True)
        disc_layout.addWidget(disc_lbl)

        layout.addWidget(disc_box)

        # 4. Contact Section (Clean GroupBox)
        contact_box = QtWidgets.QGroupBox(self.localizer.get_string("step0_contact_group_title", default="Kontakt"), container)
        contact_layout = QtWidgets.QVBoxLayout(contact_box)
        contact_layout.setContentsMargins(14, 10, 14, 12)
        contact_layout.setSpacing(6)

        links_row = QtWidgets.QHBoxLayout()
        links_row.setContentsMargins(0, 0, 0, 0)
        links_row.setSpacing(16)

        proj_lbl = QtWidgets.QLabel(self.localizer.get_string("step0_contact_project_link"), contact_box)
        proj_lbl.setFont(app_font(9))
        proj_lbl.setOpenExternalLinks(True)
        links_row.addWidget(proj_lbl)

        person1_lbl = QtWidgets.QLabel(self.localizer.get_string("step0_contact_person1"), contact_box)
        person1_lbl.setFont(app_font(9))
        person1_lbl.setOpenExternalLinks(True)
        links_row.addWidget(person1_lbl)

        person2_lbl = QtWidgets.QLabel(self.localizer.get_string("step0_contact_person2"), contact_box)
        person2_lbl.setFont(app_font(9))
        person2_lbl.setOpenExternalLinks(True)
        links_row.addWidget(person2_lbl)

        links_row.addStretch(1)
        contact_layout.addLayout(links_row)

        prompt_text = self.localizer.get_string("step0_contact_prompt")
        contact_prompt_lbl = QtWidgets.QLabel(prompt_text, contact_box)
        contact_prompt_lbl.setFont(app_font(9))
        contact_prompt_lbl.setStyleSheet("color: #64748b;")
        contact_prompt_lbl.setWordWrap(True)
        contact_layout.addWidget(contact_prompt_lbl)

        layout.addWidget(contact_box)

        # 5. Collapsible Urban-Act Details (Native collapsible arrow)
        urban_act_text = self.localizer.get_string("step0_urban_act_group_title")
        toggle_btn = QtWidgets.QToolButton(container)
        toggle_btn.setText(urban_act_text)
        toggle_btn.setToolButtonStyle(qt_enum(Qt, "ToolButtonTextBesideIcon", "ToolButtonStyle"))
        toggle_btn.setArrowType(qt_enum(Qt, "RightArrow", "ArrowType"))
        toggle_btn.setFont(app_font(10, qfont_bold()))
        toggle_btn.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
        toggle_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; color: #1e293b; padding: 4px 2px; } "
            "QToolButton:hover { color: #2563eb; }"
        )
        layout.addWidget(toggle_btn)

        details_widget = QtWidgets.QGroupBox(container)
        details_layout = QtWidgets.QVBoxLayout(details_widget)
        details_layout.setContentsMargins(14, 10, 14, 12)
        details_layout.setSpacing(8)

        # Title
        p_title = QtWidgets.QLabel(
            self.localizer.get_string("step0_urban_act_title"),
            details_widget,
        )
        p_title.setFont(app_font(10, qfont_bold()))
        p_title.setWordWrap(True)
        details_layout.addWidget(p_title)

        # Description Header & Text
        desc_hdr = QtWidgets.QLabel(self.localizer.get_string("step0_urban_act_desc_hdr"), details_widget)
        desc_hdr.setFont(app_font(10, qfont_bold()))
        details_layout.addWidget(desc_hdr)

        desc_text = QtWidgets.QLabel(
            self.localizer.get_string("step0_urban_act_desc_body"),
            details_widget,
        )
        desc_text.setFont(app_font(10))
        desc_text.setWordWrap(True)
        details_layout.addWidget(desc_text)

        # Goal Header & Text
        goal_hdr = QtWidgets.QLabel(self.localizer.get_string("step0_urban_act_goal_hdr"), details_widget)
        goal_hdr.setFont(app_font(10, qfont_bold()))
        details_layout.addWidget(goal_hdr)

        goal_text = QtWidgets.QLabel(
            self.localizer.get_string("step0_urban_act_goal_body"),
            details_widget,
        )
        goal_text.setFont(app_font(10))
        goal_text.setWordWrap(True)
        details_layout.addWidget(goal_text)

        # Links
        links_lbl = QtWidgets.QLabel(
            self.localizer.get_string("step0_urban_act_links"),
            details_widget,
        )
        links_lbl.setFont(app_font(10))
        links_lbl.setOpenExternalLinks(True)
        links_lbl.setWordWrap(True)
        details_layout.addWidget(links_lbl)

        # Logos on clean background
        logo_path = os.path.join(tool_root(), "core", "app", "icons", "Urban-Act.png")
        if not os.path.isfile(logo_path):
            logo_path = os.path.join(tool_root(), "core", "app", "Urban-Act.png")
        if os.path.isfile(logo_path):
            pixmap = QtGui.QPixmap(logo_path)
            if not pixmap.isNull():
                img_label = QtWidgets.QLabel(details_widget)
                img_label.setStyleSheet("background-color: #ffffff; padding: 8px;")
                scaled_pixmap = pixmap.scaled(
                    750,
                    180,
                    qt_enum(Qt, "KeepAspectRatio", "AspectRatioMode"),
                    qt_enum(Qt, "SmoothTransformation", "TransformationMode"),
                )
                img_label.setPixmap(scaled_pixmap)
                img_label.setAlignment(qt_enum(Qt, "AlignCenter", "AlignmentFlag"))
                details_layout.addWidget(img_label)

        details_widget.setVisible(False)
        layout.addWidget(details_widget)

        def _toggle_details():
            is_vis = details_widget.isVisible()
            details_widget.setVisible(not is_vis)
            toggle_btn.setArrowType(
                qt_enum(Qt, "DownArrow", "ArrowType") if not is_vis else qt_enum(Qt, "RightArrow", "ArrowType")
            )

        toggle_btn.clicked.connect(_toggle_details)

        # Vertical stretch to keep content nicely aligned
        layout.addStretch(1)

        # 6. Action Buttons Section
        btn_layout = QtWidgets.QVBoxLayout()
        btn_layout.setSpacing(8)

        btn_open_folder = QtWidgets.QPushButton(
            self.localizer.get_string("step0_button_open_folder"), container
        )
        btn_open_folder.setFont(app_font(10))
        btn_open_folder.setFixedHeight(36)
        btn_open_folder.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
        btn_open_folder.clicked.connect(self._handle_open_folder)
        btn_layout.addWidget(btn_open_folder)

        # Primary Start Button
        btn_start = QtWidgets.QPushButton(self.localizer.get_string("step0_button_start"), container)
        btn_start.setFont(app_font(10, qfont_bold()))
        btn_start.setFixedHeight(38)
        btn_start.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
        btn_start.setDefault(True)
        btn_start.clicked.connect(self._handle_start)
        btn_layout.addWidget(btn_start)

        layout.addLayout(btn_layout)

    def _handle_start(self) -> None:
        if self.start_requested is not None:
            self.start_requested.emit()

    def _handle_open_folder(self) -> None:
        if self.project_folder_open_requested is not None:
            self.project_folder_open_requested.emit()
