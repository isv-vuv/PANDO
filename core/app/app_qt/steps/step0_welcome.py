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
    project_file_open_requested = pyqtSignal() if pyqtSignal is not None else None
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
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        # 1. Header Section
        header, _title_label, _ = create_step_header(
            self.localizer.get_string("step0_title"),
            current_step=0,
            total_steps=5,
            localizer=self.localizer,
            parent=container,
        )
        layout.addWidget(header)

        # Language selection switcher under header
        lang_layout = QtWidgets.QHBoxLayout()
        lang_layout.setContentsMargins(0, 0, 0, 0)
        lang_layout.setSpacing(8)
        lang_label = QtWidgets.QLabel(self.localizer.get_string("step0_label_language"), container)
        lang_label.setFont(app_font(10))
        lang_combo = QtWidgets.QComboBox(container)
        lang_combo.setFont(app_font(10))
        lang_combo.addItem("Deutsch (DE)", "de")
        lang_combo.addItem("English (EN)", "en")

        current_lang = self.localizer.get_current_language() if hasattr(self.localizer, "get_current_language") else "de"
        idx = lang_combo.findData(current_lang)
        if idx >= 0:
            lang_combo.setCurrentIndex(idx)

        def _on_lang_changed(index):
            code = lang_combo.itemData(index)
            if code and self.language_switch_requested:
                self.language_switch_requested.emit(code)

        lang_combo.currentIndexChanged.connect(_on_lang_changed)
        lang_layout.addStretch(1)
        lang_layout.addWidget(lang_label)
        lang_layout.addWidget(lang_combo)
        layout.addLayout(lang_layout)

        # 2. Process Steps GroupBox
        steps_group = QtWidgets.QGroupBox(self.localizer.get_string("step0_steps_group_title"), container)
        steps_group.setFont(app_font(10))
        steps_layout = QtWidgets.QVBoxLayout(steps_group)
        steps_layout.setContentsMargins(16, 12, 16, 12)
        steps_layout.setSpacing(8)

        def _clean_title(raw_text: str, num: int) -> str:
            if ":" in raw_text:
                title = raw_text.split(":", 1)[1].strip()
            else:
                title = raw_text.strip()
            return f"{num}. {title}"

        steps_data = [
            _clean_title(self.localizer.get_string("step1_title"), 1),
            _clean_title(self.localizer.get_string("step3_title"), 2),
            _clean_title(self.localizer.get_string("step4_title"), 3),
            _clean_title(self.localizer.get_string("step5_title"), 4),
            _clean_title(self.localizer.get_string("step6_title"), 5),
        ]
        for step_text in steps_data:
            lbl = QtWidgets.QLabel(step_text, steps_group)
            lbl.setFont(app_font(10))
            steps_layout.addWidget(lbl)

        layout.addWidget(steps_group)

        # 3. Disclaimer GroupBox
        disclaimer_group = QtWidgets.QGroupBox(self.localizer.get_string("step0_disclaimer_title"), container)
        disclaimer_group.setFont(app_font(10))
        disc_layout = QtWidgets.QVBoxLayout(disclaimer_group)
        disc_layout.setContentsMargins(16, 12, 16, 12)

        disc_body = QtWidgets.QLabel(
            self.localizer.get_string("step0_disclaimer_body"),
            disclaimer_group,
        )
        disc_body.setFont(app_font(10))
        disc_body.setWordWrap(True)
        disc_layout.addWidget(disc_body)

        layout.addWidget(disclaimer_group)

        # 4. Collapsible Urban-Act Details GroupBox
        funding_group = QtWidgets.QGroupBox(self.localizer.get_string("step0_urban_act_group_title"), container)
        funding_group.setFont(app_font(10))
        funding_layout = QtWidgets.QVBoxLayout(funding_group)
        funding_layout.setContentsMargins(16, 12, 16, 12)

        toggle_btn = QtWidgets.QPushButton(self.localizer.get_string("step0_urban_act_toggle_expand"), funding_group)
        toggle_btn.setFont(app_font(10))
        toggle_btn.setFlat(True)
        toggle_btn.setStyleSheet("QPushButton { text-align: left; font-style: italic; }")
        funding_layout.addWidget(toggle_btn)

        details_widget = QtWidgets.QWidget(funding_group)
        details_layout = QtWidgets.QVBoxLayout(details_widget)
        details_layout.setContentsMargins(0, 10, 0, 0)
        details_layout.setSpacing(12)

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

        # Logos on white background (no extra frame box)
        logo_path = os.path.join(tool_root(), "core", "app", "Urban-Act.png")
        if os.path.isfile(logo_path):
            pixmap = QtGui.QPixmap(logo_path)
            if not pixmap.isNull():
                img_label = QtWidgets.QLabel(details_widget)
                img_label.setStyleSheet("background-color: #ffffff; padding: 12px;")
                scaled_pixmap = pixmap.scaled(
                    750,
                    200,
                    qt_enum(Qt, "KeepAspectRatio", "AspectRatioMode"),
                    qt_enum(Qt, "SmoothTransformation", "TransformationMode"),
                )
                img_label.setPixmap(scaled_pixmap)
                img_label.setAlignment(qt_enum(Qt, "AlignCenter", "AlignmentFlag"))
                details_layout.addWidget(img_label)

        details_widget.setVisible(False)
        funding_layout.addWidget(details_widget)

        def _toggle_details():
            is_vis = details_widget.isVisible()
            details_widget.setVisible(not is_vis)
            toggle_btn.setText(
                self.localizer.get_string("step0_urban_act_toggle_collapse")
                if not is_vis
                else self.localizer.get_string("step0_urban_act_toggle_expand")
            )

        toggle_btn.clicked.connect(_toggle_details)
        layout.addWidget(funding_group)

        # 5. Contact GroupBox
        contact_group = QtWidgets.QGroupBox(self.localizer.get_string("step0_contact_group_title"), container)
        contact_group.setFont(app_font(10))
        contact_layout = QtWidgets.QVBoxLayout(contact_group)
        contact_layout.setContentsMargins(16, 12, 16, 12)
        contact_layout.setSpacing(6)

        prompt_lbl = QtWidgets.QLabel(self.localizer.get_string("step0_contact_prompt"), contact_group)
        prompt_lbl.setFont(app_font(10))
        prompt_lbl.setWordWrap(True)
        contact_layout.addWidget(prompt_lbl)

        proj_lbl = QtWidgets.QLabel(self.localizer.get_string("step0_contact_project_link"), contact_group)
        proj_lbl.setFont(app_font(10))
        proj_lbl.setOpenExternalLinks(True)
        contact_layout.addWidget(proj_lbl)

        person1_lbl = QtWidgets.QLabel(self.localizer.get_string("step0_contact_person1"), contact_group)
        person1_lbl.setFont(app_font(10))
        person1_lbl.setOpenExternalLinks(True)
        contact_layout.addWidget(person1_lbl)

        person2_lbl = QtWidgets.QLabel(self.localizer.get_string("step0_contact_person2"), contact_group)
        person2_lbl.setFont(app_font(10))
        person2_lbl.setOpenExternalLinks(True)
        contact_layout.addWidget(person2_lbl)

        layout.addWidget(contact_group)

        # Add vertical stretch to keep boxes aligned to top
        layout.addStretch(1)

        # 5. Vertically Stacked Action Buttons
        btn_layout = QtWidgets.QVBoxLayout()
        btn_layout.setSpacing(10)

        btn_open_folder = QtWidgets.QPushButton(self.localizer.get_string("step0_button_open_folder"), container)
        btn_open_folder.setFont(app_font(10))
        btn_open_folder.setFixedHeight(36)
        btn_open_folder.clicked.connect(self._handle_open_folder)
        btn_layout.addWidget(btn_open_folder)

        btn_open_file = QtWidgets.QPushButton(self.localizer.get_string("step0_button_open_file"), container)
        btn_open_file.setFont(app_font(10))
        btn_open_file.setFixedHeight(36)
        btn_open_file.clicked.connect(self._handle_open_file)
        btn_layout.addWidget(btn_open_file)

        btn_start = QtWidgets.QPushButton(self.localizer.get_string("step0_button_start"), container)
        btn_start.setFont(app_font(10))
        btn_start.setFixedHeight(40)
        btn_start.clicked.connect(self._handle_start)
        btn_layout.addWidget(btn_start)

        layout.addLayout(btn_layout)

    def _handle_start(self) -> None:
        if self.start_requested is not None:
            self.start_requested.emit()

    def _handle_open_folder(self) -> None:
        if self.project_folder_open_requested is not None:
            self.project_folder_open_requested.emit()

    def _handle_open_file(self) -> None:
        if self.project_file_open_requested is not None:
            self.project_file_open_requested.emit()
