"""Step 5 (Link Types & Ferry Parameters) widget and shared 5-sub-page editor."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Optional

from core.locales import localizer
from core.app.app_core.workflow_state import StepId
from core.app.app_core.linktypes_config import (
    ALL_LANES,
    ALL_SPEEDS,
    ALL_TSYS,
    STRUCTURED_BY_KEY,
    STRUCTURED_CATEGORIES,
    default_linktypes_config,
    generate_linktypes_rows,
    is_default_linktypes_config,
    load_linktypes_config,
    normalize_linktypes_config,
    reset_linktypes_config,
    save_linktypes_config,
)
from core.app.app_qt.qt_base import (
    Qt,
    QtCore,
    QtGui,
    QtWidgets,
    app_font,
    create_step_header,
    escape_mnemonic,
    pyqtSignal,
    qfont_bold,
    qt_enum,
)

_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object
_Q_OBJECT_BASE = QtCore.QObject if QtCore is not None else object
_Q_DELEGATE_BASE = QtWidgets.QStyledItemDelegate if QtWidgets is not None else object

CAP_TABLE_LANES: tuple[int, ...] = (1, 2, 3, 4)


def _apply_balanced_column_widths(table: QtWidgets.QTableWidget) -> None:
    """Make all columns equal width unless content requires more space, then scale proportionally."""
    if table is None or table.columnCount() <= 0:
        return
    col_count = table.columnCount()
    header = table.horizontalHeader()
    if table.property("_resizing_cols"):
        return
    table.setProperty("_resizing_cols", True)
    try:
        table.resizeColumnsToContents()
        avail = max(0, table.viewport().width())
        needed = [
            max(header.sectionSizeHint(c), table.columnWidth(c), 48) + 12
            for c in range(col_count)
        ]
        if avail <= col_count * 48:
            return
        equal_w = avail / col_count
        if max(needed) <= equal_w:
            stretch = qt_enum(QtWidgets.QHeaderView, "Stretch", "ResizeMode")
            for c in range(col_count):
                header.setSectionResizeMode(c, stretch)
            return

        interactive = qt_enum(QtWidgets.QHeaderView, "Interactive", "ResizeMode")
        header.setStretchLastSection(False)
        for c in range(col_count):
            header.setSectionResizeMode(c, interactive)
        raw_w = [max(equal_w, float(n)) for n in needed]
        total_raw = sum(raw_w)
        if sum(needed) <= avail and total_raw > 0:
            widths = [max(48, int(avail * w / total_raw)) for w in raw_w]
            diff = avail - sum(widths)
            if widths and diff != 0:
                widths[0] = max(48, widths[0] + diff)
        else:
            widths = needed
        for c, width in enumerate(widths):
            table.setColumnWidth(c, width)
    finally:
        table.setProperty("_resizing_cols", False)


class _TableResizeWatcher(_Q_OBJECT_BASE):
    """Re-balance table column widths whenever the table viewport resizes."""

    def __init__(self, table: QtWidgets.QTableWidget):
        super().__init__(table)
        self._table = table
        table.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event is not None and event.type() == qt_enum(QtCore.QEvent, "Resize", "Type"):
            _apply_balanced_column_widths(self._table)
        return super().eventFilter(obj, event)


class _CategoryItemDelegate(_Q_DELEGATE_BASE):
    """Two-line styled card delegate for hierarchical OSM categories."""

    def sizeHint(self, option, index):
        return QtCore.QSize(160, 42)

    def paint(self, painter, option, index):
        if QtWidgets is None:
            return
        painter.save()
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        user_role = qt_enum(Qt, "UserRole", "ItemDataRole")
        is_selected = bool(opt.state & qt_enum(QtWidgets.QStyle, "State_Selected", "StateFlag"))
        is_hover = bool(opt.state & qt_enum(QtWidgets.QStyle, "State_MouseOver", "StateFlag"))
        is_ramp = bool(index.data(user_role + 2))
        enabled = bool(index.data(user_role + 3))

        rect = opt.rect.adjusted(2, 1, -2, -1)
        bg = QtGui.QColor("#eff6ff") if is_selected else (QtGui.QColor("#f8fafc") if is_hover else QtGui.QColor("#ffffff"))
        border = QtGui.QColor("#3b82f6") if is_selected else QtGui.QColor("#e2e8f0")
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing if hasattr(QtGui.QPainter, "RenderHint") else QtGui.QPainter.Antialiasing)
        painter.setBrush(bg)
        painter.setPen(QtGui.QPen(border, 1.2 if is_selected else 1.0))
        painter.drawRoundedRect(rect, 5, 5)

        x_cursor = rect.left() + (18 if is_ramp else 8)
        if is_ramp:
            # Crisp vector L-connector for _link sub-categories
            conn_pen = QtGui.QPen(QtGui.QColor("#94a3b8"), 1.3)
            conn_pen.setCapStyle(qt_enum(Qt, "RoundCap", "PenCapStyle"))
            conn_pen.setJoinStyle(qt_enum(Qt, "RoundJoin", "PenJoinStyle"))
            painter.setPen(conn_pen)
            cx = rect.left() + 9
            cy = rect.top() + 14
            painter.drawPolyline(QtGui.QPolygon([
                QtCore.QPoint(cx, rect.top() + 5),
                QtCore.QPoint(cx, cy),
                QtCore.QPoint(cx + 6, cy),
            ]))

        # Crisp vector status badge (checkmark or cross)
        badge_size = 13
        badge_rect = QtCore.QRect(x_cursor, rect.top() + 5, badge_size, badge_size)
        badge_bg = QtGui.QColor("#dbeafe" if enabled else "#f1f5f9")
        badge_border = QtGui.QColor("#3b82f6" if enabled else "#cbd5e1")
        painter.setBrush(badge_bg)
        painter.setPen(QtGui.QPen(badge_border, 1.0))
        painter.drawEllipse(badge_rect)

        icon_pen = QtGui.QPen(QtGui.QColor("#1d4ed8" if enabled else "#94a3b8"), 1.5)
        icon_pen.setCapStyle(qt_enum(Qt, "RoundCap", "PenCapStyle"))
        icon_pen.setJoinStyle(qt_enum(Qt, "RoundJoin", "PenJoinStyle"))
        painter.setPen(icon_pen)
        bx, by = badge_rect.left(), badge_rect.top()
        if enabled:
            painter.drawPolyline(QtGui.QPolygon([
                QtCore.QPoint(bx + 3, by + 7),
                QtCore.QPoint(bx + 6, by + 10),
                QtCore.QPoint(bx + 10, by + 4),
            ]))
        else:
            painter.drawLine(bx + 4, by + 4, bx + 9, by + 9)
            painter.drawLine(bx + 9, by + 4, bx + 4, by + 9)

        text_left = badge_rect.right() + 7
        text_width = max(10, rect.right() - 8 - text_left)
        title = str(index.data(user_role + 4) or index.data() or "")
        subtitle = str(index.data(user_role + 1) or "")

        title_font = app_font(9, qfont_bold())
        sub_font = app_font(8)
        title_color = QtGui.QColor("#1e40af" if is_selected else ("#0f172a" if enabled else "#94a3b8"))
        sub_color = QtGui.QColor("#3b82f6" if is_selected else ("#64748b" if enabled else "#cbd5e1"))

        painter.setFont(title_font)
        painter.setPen(title_color)
        title_box = QtCore.QRect(text_left, rect.top() + 3, text_width, 17)
        painter.drawText(title_box, qt_enum(Qt, "AlignLeft", "AlignmentFlag") | qt_enum(Qt, "AlignVCenter", "AlignmentFlag"), title)

        painter.setFont(sub_font)
        painter.setPen(sub_color)
        sub_box = QtCore.QRect(text_left, rect.top() + 20, text_width, 16)
        elided_sub = painter.fontMetrics().elidedText(subtitle, qt_enum(Qt, "ElideRight", "TextElideMode"), sub_box.width())
        painter.drawText(sub_box, qt_enum(Qt, "AlignLeft", "AlignmentFlag") | qt_enum(Qt, "AlignVCenter", "AlignmentFlag"), elided_sub)
        painter.restore()

# Exact fixed transport systems & modes defined in
# core/scripts/visum/importer/PANDO_Importer/PANDO_Importer.net ($MODE & $TSYS)
FIXED_IMPORTER_TSYS: tuple[tuple[str, str, str, str, str], ...] = (
    ("CAR", "CAR", "Car", "PrT", "1.0"),
    ("HGV", "HGV", "HGV", "PrT", "2.0"),
    ("BIKE", "BIKE", "Bicycle", "PrT", "1.0"),
    ("WALK", "WALK", "Pedestrian", "PrT", "1.0"),
    ("PuT", "BUS", "Bus", "PuT", "1.0"),
    ("PuT", "MINIBUS", "Minibus", "PuT", "1.0"),
    ("PuT", "TRAM", "Tram", "PuT", "1.0"),
    ("PuT", "LRT", "Light Rail", "PuT", "1.0"),
    ("PuT", "SUBWAY", "Subway", "PuT", "1.0"),
    ("PuT", "TRAIN", "Train", "PuT", "1.0"),
    ("PuT", "PUTW", "PuT Walk", "PuTWalk", "1.0"),
)

_EDITOR_STYLE = """
    QTabWidget::pane {
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        background-color: #ffffff;
    }
    QTabBar {
        background: transparent;
        qproperty-drawBase: 0;
    }
    QTabBar::tab {
        background-color: #f1f5f9;
        color: #475569;
        border: 1px solid #cbd5e1;
        border-bottom: none;
        border-top-left-radius: 5px;
        border-top-right-radius: 5px;
        padding: 7px 14px;
        margin-right: 4px;
        margin-bottom: -1px;
        font-size: 11px;
    }
    QTabBar::tab:first {
        margin-left: 4px;
    }
    QTabBar::tab:hover {
        background-color: #e2e8f0;
        color: #0f172a;
    }
    QTabBar::tab:selected {
        background-color: #ffffff;
        color: #2563eb;
        font-weight: bold;
        border-color: #cbd5e1;
        border-bottom: 2px solid #2563eb;
    }
    QGroupBox {
        font-weight: bold;
        color: #1e293b;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        background-color: #ffffff;
        margin-top: 10px;
        padding-top: 10px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 5px;
    }
    QListWidget, QTableWidget {
        border: 1px solid #cbd5e1;
        border-radius: 4px;
        background-color: #ffffff;
        alternate-background-color: #f8fafc;
        gridline-color: #e2e8f0;
        selection-background-color: #dbeafe;
        selection-color: #0f172a;
    }
    QHeaderView::section {
        background-color: #f1f5f9;
        color: #334155;
        font-weight: bold;
        border: none;
        border-right: 1px solid #cbd5e1;
        border-bottom: 1px solid #cbd5e1;
        padding: 5px 7px;
    }
"""


class LinkTypesEditorWidget(_Q_WIDGET_BASE):
    """Reusable 5-sub-page editor for PANDO link types and ferry parameters."""

    config_changed = pyqtSignal() if pyqtSignal is not None else None
    ferry_wait_changed = pyqtSignal(float) if pyqtSignal is not None else None

    def __init__(
        self,
        config: Optional[Mapping[str, Any]] = None,
        project_path: Optional[str] = None,
        localizer_obj: Optional[object] = None,
        parent: Optional[object] = None,
        *,
        show_footer: bool = True,
    ):
        if QtWidgets is None:
            return
        super().__init__(parent)
        self.loc = localizer_obj or localizer
        self.project_path = project_path or ""
        self._config = normalize_linktypes_config(config)
        self._syncing = False
        self._loading_ferry = False
        self._show_footer = bool(show_footer)

        self._lane_checks: dict[int, QtWidgets.QCheckBox] = {}
        self._speed_checks: dict[int, QtWidgets.QCheckBox] = {}
        self._tsys_chip_map: dict[str, QtWidgets.QPushButton] = {}
        self._table_watchers: list[_TableResizeWatcher] = []
        self._preview_rows: list[dict[str, Any]] = []

        self.setStyleSheet(_EDITOR_STYLE)
        self._build_ui()
        self._load_all_views()

    # ------------------------------------------------------------------
    # Public state helpers
    # ------------------------------------------------------------------
    def get_config(self) -> dict[str, Any]:
        """Commit pending table edits and return a normalized config copy."""
        self._commit_capacity_table()
        self._commit_impedance_table()
        self._commit_special_table()
        return normalize_linktypes_config(copy.deepcopy(self._config))

    def set_config(self, config: Mapping[str, Any]) -> None:
        self._config = normalize_linktypes_config(config)
        self._load_all_views()

    def set_project_path(self, project_path: str) -> None:
        self.project_path = project_path or ""
        self.set_config(load_linktypes_config(self.project_path))
        self._load_ferry_wait()

    def reset_defaults(self) -> None:
        self._config = default_linktypes_config()
        self._load_all_views()
        self._emit_changed()

    def save_to_project(self, project_path: Optional[str] = None) -> None:
        target = project_path or self.project_path
        if not target:
            return
        cfg = self.get_config()
        save_linktypes_config(target, cfg)
        from core.scripts.visum.scripts.ferry_wait import save_wait_minutes

        save_wait_minutes(target, float(self.ferry_wait_spin.value()))

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.tab_widget = QtWidgets.QTabWidget(self)
        self.tab_widget.addTab(
            self._build_subpage1_overview(),
            escape_mnemonic(self.loc.get_string("linktypes_subpage1_tab", default="1. Übersicht & Feste Modi")),
        )
        self.tab_widget.addTab(
            self._build_subpage2_categories(),
            escape_mnemonic(self.loc.get_string("linktypes_subpage2_tab", default="2. OSM-Kategorien & Stufen")),
        )
        self.tab_widget.addTab(
            self._build_subpage3_capacities_and_impedances(),
            escape_mnemonic(self.loc.get_string("linktypes_subpage3_tab", default="3. Kapazitäten & Widerstände")),
        )
        self.tab_widget.addTab(
            self._build_subpage4_special_and_ferry(),
            escape_mnemonic(self.loc.get_string("linktypes_subpage4_tab", default="4. Sonderstreckentypen & Fähren")),
        )
        self.tab_widget.addTab(
            self._build_subpage5_preview(),
            escape_mnemonic(self.loc.get_string("linktypes_subpage5_tab", default="5. Vorschau ($LINKTYPE)")),
        )
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tab_widget, 1)

        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(10)
        self.summary_label = QtWidgets.QLabel("", self)
        self.summary_label.setFont(app_font(9))
        self.summary_label.setStyleSheet("color: #475569;")
        footer.addWidget(self.summary_label, 1)

        self.reset_button = QtWidgets.QPushButton(
            self.loc.get_string("linktypes_btn_reset", default="Auf Standard zurücksetzen"),
            self,
        )
        self.reset_button.setFont(app_font(9))
        self.reset_button.setStyleSheet(
            "QPushButton:enabled { background-color: #ffffff; color: #334155; border: 1px solid #cbd5e1; border-radius: 4px; padding: 5px 14px; } "
            "QPushButton:enabled:hover { background-color: #f1f5f9; border-color: #94a3b8; } "
            "QPushButton:disabled { background-color: #f8fafc; color: #94a3b8; border: 1px solid #e2e8f0; border-radius: 4px; padding: 5px 14px; }"
        )
        self.reset_button.clicked.connect(self.reset_defaults)
        footer.addWidget(self.reset_button)

        if self._show_footer:
            root.addLayout(footer)
        else:
            self.summary_label.hide()
            self.reset_button.hide()

    # --- Sub-Page 1: Overview & Fixed Modes -----------------------------
    def _build_subpage1_overview(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea(self.tab_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))

        page = QtWidgets.QWidget(scroll)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # Card 1: What is customizable
        custom_box = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string("linktypes_overview_customizable_title", default="Was hier angepasst werden kann")
            ),
            page,
        )
        custom_layout = QtWidgets.QVBoxLayout(custom_box)
        custom_layout.setContentsMargins(12, 10, 12, 10)
        custom_lbl = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_overview_customizable_body",
                default=(
                    "<ul><li><b>Unterseite 2 – OSM-Kategorien &amp; Ausprägungen:</b> Kategorien, Fahrstreifen (1–4) und V0PrT-Stufen wählen.</li>"
                    "<li><b>Unterseite 3 – Kapazitäten &amp; Widerstandsfaktoren:</b> Kapazitätsmatrix und getrennte Widerstände für Pkw, Rad und Fuß bearbeiten.</li>"
                    "<li><b>Unterseite 4 – Sonderstreckentypen &amp; Fähren:</b> Fähr-Wartezeit je Einstieg und Sonderstreckentypen (900–994) konfigurieren.</li>"
                    "<li><b>Unterseite 5 – Vorschau ($LINKTYPE):</b> Vollständige Vorschau mit Doppelklick-Sprung zu den Detail-Unterseiten.</li></ul>"
                ),
            ),
            custom_box,
        )
        custom_lbl.setFont(app_font(9))
        custom_lbl.setWordWrap(True)
        custom_lbl.setStyleSheet("color: #334155;")
        custom_layout.addWidget(custom_lbl)
        layout.addWidget(custom_box)

        # Card 2: Fixed Importer Modes & TSys
        modes_box = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string(
                    "linktypes_overview_modes_title",
                    default="Fest vorgegebene Modi & Verkehrssysteme (PANDO_Importer.net)",
                )
            ),
            page,
        )
        modes_layout = QtWidgets.QVBoxLayout(modes_box)
        modes_layout.setContentsMargins(12, 10, 12, 10)
        modes_layout.setSpacing(8)

        modes_desc = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_overview_modes_desc",
                default=(
                    "Die verfügbaren Modi und Verkehrssystem-Codes (TSys) sind im Visum-Importer (PANDO_Importer.net) "
                    "definiert. Welche Verkehrssysteme je Streckentyp zugelassen sind (TSysSet), können Sie auf den "
                    "Unterseiten 2 und 4 frei anpassen."
                ),
            ),
            modes_box,
        )
        modes_desc.setFont(app_font(9))
        modes_desc.setWordWrap(True)
        modes_desc.setStyleSheet("color: #475569;")
        modes_layout.addWidget(modes_desc)

        self.modes_table = QtWidgets.QTableWidget(len(FIXED_IMPORTER_TSYS), 5, modes_box)
        self.modes_table.setAlternatingRowColors(True)
        self.modes_table.setEditTriggers(qt_enum(QtWidgets.QAbstractItemView, "NoEditTriggers", "EditTrigger"))
        self.modes_table.setSelectionBehavior(qt_enum(QtWidgets.QAbstractItemView, "SelectRows", "SelectionBehavior"))
        self.modes_table.verticalHeader().setVisible(False)
        self.modes_table.setHorizontalHeaderLabels([
            self.loc.get_string("linktypes_overview_mode_col", default="Modus"),
            self.loc.get_string("linktypes_overview_tsys_col", default="TSys-Code"),
            self.loc.get_string("linktypes_overview_name_col", default="Bezeichnung im Importer"),
            self.loc.get_string("linktypes_overview_type_col", default="Kategorie"),
            self.loc.get_string("linktypes_overview_pcu_col", default="PKW-E [Fz]"),
        ])
        for row_idx, row_vals in enumerate(FIXED_IMPORTER_TSYS):
            for col_idx, val in enumerate(row_vals):
                item = QtWidgets.QTableWidgetItem(val)
                if col_idx in (1, 3, 4):
                    item.setTextAlignment(qt_enum(Qt, "AlignCenter", "AlignmentFlag"))
                self.modes_table.setItem(row_idx, col_idx, item)
        self._table_watchers.append(_TableResizeWatcher(self.modes_table))
        _apply_balanced_column_widths(self.modes_table)
        row_h = self.modes_table.verticalHeader().defaultSectionSize()
        self.modes_table.setMinimumHeight(min(320, max(220, row_h * len(FIXED_IMPORTER_TSYS) + 34)))
        modes_layout.addWidget(self.modes_table)
        layout.addWidget(modes_box)

        # Card 3: Preview & Jump navigation explanation
        prev_box = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string("linktypes_overview_preview_hint_title", default="Navigation zur Vorschau")
            ),
            page,
        )
        prev_layout = QtWidgets.QHBoxLayout(prev_box)
        prev_layout.setContentsMargins(12, 10, 12, 10)
        prev_layout.setSpacing(12)

        prev_lbl = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_overview_preview_hint_body",
                default=(
                    "Auf Unterseite 5 (\"5. Vorschau ($LINKTYPE)\") sehen Sie die finale Liste aller Streckentypen "
                    "mit getrennten Spalten für Pkw-, Rad- und Fußwiderstände. Doppelklick auf eine Zelle springt direkt "
                    "in die zugehörige Detail-Unterseite."
                ),
            ),
            prev_box,
        )
        prev_lbl.setFont(app_font(9))
        prev_lbl.setWordWrap(True)
        prev_lbl.setStyleSheet("color: #334155;")
        prev_layout.addWidget(prev_lbl, 1)

        btn_go_preview = QtWidgets.QPushButton(
            self.loc.get_string("linktypes_btn_go_preview", default="Zur Vorschau ($LINKTYPE) →"),
            prev_box,
        )
        btn_go_preview.setFont(app_font(9, qfont_bold()))
        btn_go_preview.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: #ffffff; border: 1px solid #1d4ed8; border-radius: 4px; padding: 6px 14px; } "
            "QPushButton:hover { background-color: #1d4ed8; }"
        )
        btn_go_preview.clicked.connect(lambda: self.tab_widget.setCurrentIndex(4))
        prev_layout.addWidget(btn_go_preview)
        layout.addWidget(prev_box)

        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    # --- Sub-Page 2: OSM Categories & Variants --------------------------
    def _build_subpage2_categories(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self.tab_widget)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        splitter = QtWidgets.QSplitter(qt_enum(Qt, "Horizontal", "Orientation"), page)
        splitter.setChildrenCollapsible(False)

        # Left panel: Hierarchical OSM Categories list
        left_box = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string("linktypes_lbl_categories", default="OSM-Straßenkategorien (hierarchisch)")
            ),
            page,
        )
        left_layout = QtWidgets.QVBoxLayout(left_box)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(6)

        self.category_list = QtWidgets.QListWidget(left_box)
        self.category_list.setMinimumWidth(240)
        self.category_list.setSpacing(2)
        self.category_list.setHorizontalScrollBarPolicy(qt_enum(Qt, "ScrollBarAlwaysOff", "ScrollBarPolicy"))
        self.category_list.setStyleSheet(
            "QListWidget { border: none; background: transparent; outline: none; } "
            "QListWidget::item { background: transparent; border: none; }"
        )
        self._category_delegate = _CategoryItemDelegate(self.category_list)
        self.category_list.setItemDelegate(self._category_delegate)
        for spec in STRUCTURED_CATEGORIES:
            item = QtWidgets.QListWidgetItem(spec.key)
            item.setData(qt_enum(Qt, "UserRole", "ItemDataRole"), spec.key)
            self.category_list.addItem(item)
        self.category_list.currentRowChanged.connect(self._on_category_selected)
        left_layout.addWidget(self.category_list, 1)
        splitter.addWidget(left_box)

        # Right panel: Selected category details cleanly organized
        right_scroll = QtWidgets.QScrollArea(page)
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(qt_enum(Qt, "ScrollBarAlwaysOff", "ScrollBarPolicy"))
        right_scroll.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))

        right_widget = QtWidgets.QWidget(right_scroll)
        right_layout = QtWidgets.QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 2, 8, 8)
        right_layout.setSpacing(10)

        # Header card for category
        cat_hdr_box = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string("linktypes_lbl_cat_details", default="Ausprägungen der gewählten Kategorie")
            ),
            right_widget,
        )
        cat_hdr_layout = QtWidgets.QVBoxLayout(cat_hdr_box)
        cat_hdr_layout.setContentsMargins(12, 8, 12, 10)
        cat_hdr_layout.setSpacing(6)

        hdr_top = QtWidgets.QHBoxLayout()
        self.cat_title_label = QtWidgets.QLabel("", cat_hdr_box)
        self.cat_title_label.setFont(app_font(11, qfont_bold()))
        self.cat_title_label.setStyleSheet("color: #0f172a;")
        hdr_top.addWidget(self.cat_title_label, 1)

        self.cat_enabled_check = QtWidgets.QCheckBox(
            self.loc.get_string("linktypes_lbl_cat_enabled", default="Kategorie aktiv"),
            cat_hdr_box,
        )
        self.cat_enabled_check.setFont(app_font(9, qfont_bold()))
        self.cat_enabled_check.toggled.connect(self._on_category_detail_changed)
        hdr_top.addWidget(self.cat_enabled_check)
        cat_hdr_layout.addLayout(hdr_top)

        self.cat_osm_ref_label = QtWidgets.QLabel("", cat_hdr_box)
        self.cat_osm_ref_label.setFont(app_font(8))
        self.cat_osm_ref_label.setWordWrap(True)
        self.cat_osm_ref_label.setStyleSheet("color: #64748b;")
        cat_hdr_layout.addWidget(self.cat_osm_ref_label)
        right_layout.addWidget(cat_hdr_box)

        # Lane classes
        lanes_group = QtWidgets.QGroupBox(
            escape_mnemonic(self.loc.get_string("linktypes_lbl_lanes", default="Fahrstreifen-Klassen (je Richtung)")),
            right_widget,
        )
        lanes_row = QtWidgets.QHBoxLayout(lanes_group)
        lanes_row.setContentsMargins(12, 10, 12, 10)
        lanes_row.setSpacing(16)
        for lane in ALL_LANES:
            cb = QtWidgets.QCheckBox(f"{lane} Lane{'s' if lane > 1 else ''}", lanes_group)
            cb.toggled.connect(self._on_category_detail_changed)
            self._lane_checks[lane] = cb
            lanes_row.addWidget(cb)
        lanes_row.addStretch()
        right_layout.addWidget(lanes_group)

        # Speed brackets
        speeds_group = QtWidgets.QGroupBox(
            escape_mnemonic(self.loc.get_string("linktypes_lbl_speeds", default="Geschwindigkeitsstufen V0PrT [km/h]")),
            right_widget,
        )
        speeds_vbox = QtWidgets.QVBoxLayout(speeds_group)
        speeds_vbox.setContentsMargins(12, 10, 12, 10)
        speeds_vbox.setSpacing(8)
        speeds_grid = QtWidgets.QGridLayout()
        speeds_grid.setSpacing(8)
        for idx, spd in enumerate(ALL_SPEEDS):
            cb = QtWidgets.QCheckBox(f"{spd} km/h", speeds_group)
            cb.toggled.connect(self._on_category_detail_changed)
            self._speed_checks[spd] = cb
            speeds_grid.addWidget(cb, idx // 4, idx % 4)
        speeds_vbox.addLayout(speeds_grid)

        spd_btns = QtWidgets.QHBoxLayout()
        btn_spd_default = QtWidgets.QPushButton(
            self.loc.get_string("linktypes_btn_cat_default", default="Standard für Kategorie"),
            speeds_group,
        )
        btn_spd_default.clicked.connect(self._reset_current_category)
        btn_spd_all = QtWidgets.QPushButton(
            self.loc.get_string("linktypes_btn_select_all", default="Alle Stufen"),
            speeds_group,
        )
        btn_spd_all.clicked.connect(lambda: self._set_all_speeds_current(True))
        spd_btns.addWidget(btn_spd_default)
        spd_btns.addWidget(btn_spd_all)
        spd_btns.addStretch()
        speeds_vbox.addLayout(spd_btns)
        right_layout.addWidget(speeds_group)

        # Editable Allowed Transport Systems chips
        tsys_group = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string(
                    "linktypes_lbl_tsys",
                    default="Zugelassene Verkehrssysteme (TSysSet – anklickbar zum Anpassen)",
                )
            ),
            right_widget,
        )
        tsys_row = QtWidgets.QHBoxLayout(tsys_group)
        tsys_row.setContentsMargins(12, 10, 12, 10)
        tsys_row.setSpacing(6)
        self._tsys_chip_map = {}
        for tsys_code in ALL_TSYS:
            btn = QtWidgets.QPushButton(tsys_code, tsys_group)
            btn.setCheckable(True)
            btn.setFont(app_font(8, qfont_bold()))
            btn.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
            btn.setStyleSheet(
                "QPushButton { background-color: #f8fafc; color: #94a3b8; border: 1px solid #e2e8f0; border-radius: 4px; padding: 4px 10px; } "
                "QPushButton:hover { border-color: #93c5fd; color: #1e40af; } "
                "QPushButton:checked { background-color: #dbeafe; color: #1e40af; border: 1px solid #60a5fa; }"
            )
            btn.toggled.connect(self._on_category_detail_changed)
            tsys_row.addWidget(btn)
            self._tsys_chip_map[tsys_code] = btn
        tsys_row.addStretch()
        right_layout.addWidget(tsys_group)

        right_layout.addStretch()
        right_scroll.setWidget(right_widget)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 640])
        layout.addWidget(splitter, 1)

        # Bottom bus-lane option
        self.bus_lanes_check = QtWidgets.QCheckBox(
            self.loc.get_string(
                "linktypes_lbl_include_bus",
                default="Optionale Busspur-Streckentypen (Pr/Se/Te/Un +Bus) für Szenario-Analysen beibehalten",
            ),
            page,
        )
        self.bus_lanes_check.setFont(app_font(9))
        self.bus_lanes_check.toggled.connect(self._on_bus_lanes_toggled)
        layout.addWidget(self.bus_lanes_check)
        return page

    # --- Sub-Page 3: Capacities (Top) & Impedance Factors (Bottom) -------
    def _build_subpage3_capacities_and_impedances(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self.tab_widget)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        splitter = QtWidgets.QSplitter(qt_enum(Qt, "Vertical", "Orientation"), page)
        splitter.setChildrenCollapsible(False)

        # TOP: Capacity matrix CapPrT [veh/h]
        cap_group = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string(
                    "linktypes_group_capacity",
                    default="Kapazitätsmatrix CapPrT [Fz/h] nach V0PrT [km/h] und Fahrstreifenanzahl",
                )
            ),
            page,
        )
        cap_vbox = QtWidgets.QVBoxLayout(cap_group)
        cap_vbox.setContentsMargins(10, 10, 10, 10)
        cap_vbox.setSpacing(6)

        cap_hdr = QtWidgets.QHBoxLayout()
        cap_hint = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_capacity_hint",
                default="Stündliche Kapazitäten [Fz/h] je Fahrstreifenklasse und V0PrT-Stufe [km/h] (Doppelklick zum Bearbeiten).",
            ),
            cap_group,
        )
        cap_hint.setFont(app_font(8))
        cap_hint.setStyleSheet("color: #475569;")
        cap_hdr.addWidget(cap_hint, 1)

        self.scale_lanes_check = QtWidgets.QCheckBox(
            self.loc.get_string(
                "linktypes_chk_scale_lanes",
                default="2–4 Lanes aus 1-Lane-Kapazität berechnen (× L)",
            ),
            cap_group,
        )
        self.scale_lanes_check.setFont(app_font(8, qfont_bold()))
        self.scale_lanes_check.toggled.connect(self._on_scale_lanes_toggled)
        cap_hdr.addWidget(self.scale_lanes_check)
        cap_vbox.addLayout(cap_hdr)

        self.cap_table = QtWidgets.QTableWidget(len(ALL_SPEEDS), 4, cap_group)
        self.cap_table.setAlternatingRowColors(True)
        self.cap_table.setHorizontalHeaderLabels([
            "1 Lane [Fz/h]",
            "2 Lanes [Fz/h]",
            "3 Lanes [Fz/h]",
            "4 Lanes [Fz/h]",
        ])
        self.cap_table.setVerticalHeaderLabels([f"{spd} km/h" for spd in ALL_SPEEDS])
        self._table_watchers.append(_TableResizeWatcher(self.cap_table))
        _apply_balanced_column_widths(self.cap_table)
        self.cap_table.itemChanged.connect(self._on_cap_item_changed)
        cap_vbox.addWidget(self.cap_table, 1)
        splitter.addWidget(cap_group)

        # BOTTOM: Category Impedance Factors (separate cells for Car, Bike, Walk)
        imp_group = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string(
                    "linktypes_lbl_impedance",
                    default="Widerstandsfaktoren (LinkTypeImpedanceFactor) – getrennt je Modus",
                )
            ),
            page,
        )
        imp_vbox = QtWidgets.QVBoxLayout(imp_group)
        imp_vbox.setContentsMargins(10, 10, 10, 10)
        imp_vbox.setSpacing(6)

        imp_hint = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_impedance_hint",
                default="Separater Widerstandsfaktor pro OSM-Kategorie für Pkw (Car), Fahrrad (Bike) und Fuß (Walk). Doppelklick auf eine Zelle zum Bearbeiten.",
            ),
            imp_group,
        )
        imp_hint.setFont(app_font(8))
        imp_hint.setStyleSheet("color: #475569;")
        imp_vbox.addWidget(imp_hint)

        self.imp_table = QtWidgets.QTableWidget(len(STRUCTURED_CATEGORIES), 6, imp_group)
        self.imp_table.setAlternatingRowColors(True)
        self.imp_table.verticalHeader().setVisible(False)
        self.imp_table.setHorizontalHeaderLabels([
            self.loc.get_string("linktypes_col_category", default="Kategorie"),
            self.loc.get_string("linktypes_col_abbr", default="Kürzel"),
            "GType [-]",
            self.loc.get_string("linktypes_col_imp_car", default="Imp Pkw (Car) [-]"),
            self.loc.get_string("linktypes_col_imp_bike", default="Imp Rad (Bike) [-]"),
            self.loc.get_string("linktypes_col_imp_walk", default="Imp Fuß (Walk) [-]"),
        ])
        self._table_watchers.append(_TableResizeWatcher(self.imp_table))
        self.imp_table.itemChanged.connect(self._on_imp_item_changed)
        imp_vbox.addWidget(self.imp_table, 1)
        splitter.addWidget(imp_group)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        return page

    # --- Sub-Page 4: Special Link Types & Ferry Wait Time ----------------
    def _build_subpage4_special_and_ferry(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self.tab_widget)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Ferry parameters box
        ferry_box = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string("linktypes_group_ferry", default="Fähr-Parameter (Ferry & Ferry_Access)")
            ),
            page,
        )
        ferry_row = QtWidgets.QHBoxLayout(ferry_box)
        ferry_row.setContentsMargins(12, 10, 12, 10)
        ferry_row.setSpacing(10)

        self.ferry_wait_label = QtWidgets.QLabel(
            self.loc.get_string("ferry_wait_minutes", default="Wartezeit je Fährnutzung"),
            ferry_box,
        )
        self.ferry_wait_label.setFont(app_font(9, qfont_bold()))
        ferry_row.addWidget(self.ferry_wait_label)

        self.ferry_wait_spin = QtWidgets.QDoubleSpinBox(ferry_box)
        self.ferry_wait_spin.setRange(0.0, 10080.0)
        self.ferry_wait_spin.setDecimals(1)
        self.ferry_wait_spin.setSingleStep(5.0)
        self.ferry_wait_spin.setSuffix(" min")
        self.ferry_wait_spin.setFixedWidth(105)
        self.ferry_wait_spin.setValue(20.0)
        self.ferry_wait_spin.valueChanged.connect(self._on_ferry_wait_spin_changed)
        ferry_row.addWidget(self.ferry_wait_spin)

        ferry_hint = QtWidgets.QLabel(
            self.loc.get_string(
                "step5_ferry_wait_hint",
                default="Ferry und Ferry_Access mit 20 km/h (inkl. Fuß); Wartezeit einmalig bei Auffahrt auf die Fähre bzw. Ferry_Access.",
            ),
            ferry_box,
        )
        ferry_hint.setFont(app_font(8))
        ferry_hint.setStyleSheet("color: #475569;")
        ferry_hint.setWordWrap(True)
        ferry_row.addWidget(ferry_hint, 1)
        layout.addWidget(ferry_box)

        # Special Link Types table (with SEPARATE Imp Car / Imp Bike / Imp Walk cells)
        special_group = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string(
                    "linktypes_group_special",
                    default="Sonder- & Nebennetztypen (900–994, inkl. Wohn-/Erschließungswege, Schiene, Ferry & Ferry_Access)",
                )
            ),
            page,
        )
        sp_vbox = QtWidgets.QVBoxLayout(special_group)
        sp_vbox.setContentsMargins(10, 10, 10, 10)
        sp_vbox.setSpacing(6)

        sp_hint = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_special_hint",
                default="Alle Sonderstreckentypen inkl. getrennter Widerstandsfaktoren für Pkw (Car), Fahrrad (Bike) und Fuß (Walk).",
            ),
            special_group,
        )
        sp_hint.setFont(app_font(8))
        sp_hint.setStyleSheet("color: #475569;")
        sp_vbox.addWidget(sp_hint)

        self.special_table = QtWidgets.QTableWidget(0, 10, special_group)
        self.special_table.setAlternatingRowColors(True)
        self.special_table.verticalHeader().setVisible(False)
        self.special_table.setHorizontalHeaderLabels([
            "No",
            "Name",
            "GType [-]",
            "TSysSet",
            "V0PrT [km/h]",
            "CapPrT [Fz/h]",
            self.loc.get_string("linktypes_col_imp_car", default="Imp Pkw (Car) [-]"),
            self.loc.get_string("linktypes_col_imp_bike", default="Imp Rad (Bike) [-]"),
            self.loc.get_string("linktypes_col_imp_walk", default="Imp Fuß (Walk) [-]"),
            self.loc.get_string("linktypes_col_desc", default="Beschreibung"),
        ])
        self._table_watchers.append(_TableResizeWatcher(self.special_table))
        self.special_table.itemChanged.connect(self._on_special_item_changed)
        sp_vbox.addWidget(self.special_table, 1)
        layout.addWidget(special_group, 1)

        self._load_ferry_wait()
        return page

    # --- Sub-Page 5: Full Preview ($LINKTYPE) with Double-Click Jump ------
    def _build_subpage5_preview(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self.tab_widget)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint_banner = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_preview_dblclick_hint",
                default="Tipp: Doppelklick auf eine Zelle öffnet direkt die zugehörige Detail-Unterseite (Stufen, Kapazität, Imp Car/Bike/Walk oder Sonderstreckentyp).",
            ),
            page,
        )
        hint_banner.setFont(app_font(8, qfont_bold()))
        hint_banner.setWordWrap(True)
        hint_banner.setStyleSheet(
            "background-color: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; border-radius: 4px; padding: 6px 10px;"
        )
        layout.addWidget(hint_banner)

        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QtWidgets.QLabel(self.loc.get_string("linktypes_lbl_filter", default="Filter:"), page))
        self.preview_filter_edit = QtWidgets.QLineEdit(page)
        self.preview_filter_edit.setPlaceholderText("Mo, Pr L2, Ferry, 940, BIKE...")
        self.preview_filter_edit.textChanged.connect(self._refresh_preview_table)
        bar.addWidget(self.preview_filter_edit, 1)

        self.preview_count_label = QtWidgets.QLabel("", page)
        self.preview_count_label.setFont(app_font(9, qfont_bold()))
        self.preview_count_label.setStyleSheet("color: #2563eb;")
        bar.addWidget(self.preview_count_label)
        layout.addLayout(bar)

        self.preview_table = QtWidgets.QTableWidget(0, 12, page)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setEditTriggers(qt_enum(QtWidgets.QAbstractItemView, "NoEditTriggers", "EditTrigger"))
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.setHorizontalHeaderLabels([
            "TypNo",
            "GType [-]",
            "Name",
            self.loc.get_string("linktypes_col_category", default="Kategorie"),
            "Rank [-]",
            "Lanes [-]",
            "CapPrT [Fz/h]",
            "V0PrT [km/h]",
            "TSysSet",
            self.loc.get_string("linktypes_col_imp_car", default="Imp Pkw (Car) [-]"),
            self.loc.get_string("linktypes_col_imp_bike", default="Imp Rad (Bike) [-]"),
            self.loc.get_string("linktypes_col_imp_walk", default="Imp Fuß (Walk) [-]"),
        ])
        self._table_watchers.append(_TableResizeWatcher(self.preview_table))
        self.preview_table.cellDoubleClicked.connect(self._on_preview_cell_double_clicked)
        layout.addWidget(self.preview_table, 1)
        return page

    # ------------------------------------------------------------------
    # Load / Populate Views
    # ------------------------------------------------------------------
    def _load_ferry_wait(self) -> None:
        self._loading_ferry = True
        try:
            wait_val = 20.0
            if self.project_path:
                from core.scripts.visum.scripts.ferry_wait import load_wait_minutes

                try:
                    wait_val = float(load_wait_minutes(self.project_path))
                except Exception:
                    wait_val = 20.0
            if hasattr(self, "ferry_wait_spin"):
                self.ferry_wait_spin.setValue(wait_val)
        finally:
            self._loading_ferry = False

    def _on_ferry_wait_spin_changed(self, value: float) -> None:
        if self._loading_ferry:
            return
        if self.project_path:
            try:
                from core.scripts.visum.scripts.ferry_wait import save_wait_minutes

                save_wait_minutes(self.project_path, float(value))
            except Exception:
                pass
        if self.ferry_wait_changed is not None:
            self.ferry_wait_changed.emit(float(value))

    def _load_all_views(self) -> None:
        self._syncing = True
        try:
            self.bus_lanes_check.setChecked(bool(self._config.get("include_bus_lanes", True)))
            self.scale_lanes_check.setChecked(bool(self._config.get("scale_lanes_from_single", True)))
            self._refresh_category_list_labels()
            if self.category_list.currentRow() < 0 and self.category_list.count() > 0:
                self.category_list.setCurrentRow(0)
            else:
                self._load_selected_category()
            self._populate_capacity_table()
            self._populate_impedance_table()
            self._populate_special_table()
        finally:
            self._syncing = False
        self._refresh_preview_table()
        self._refresh_summary()

    def _current_category_key(self) -> Optional[str]:
        item = self.category_list.currentItem()
        if item is None:
            return None
        return item.data(qt_enum(Qt, "UserRole", "ItemDataRole"))

    def _format_category_subtitle(self, spec, cat_cfg: Mapping[str, Any]) -> str:
        lanes = sorted({int(x) for x in cat_cfg.get("lanes", ())})
        lanes_str = ",".join(str(x) for x in lanes) if lanes else "—"
        speeds = sorted({int(x) for x in cat_cfg.get("speeds", ())}, reverse=True)
        if not speeds:
            spd_str = "—"
        elif len(speeds) == 1:
            spd_str = f"{speeds[0]}"
        else:
            spd_str = f"{speeds[0]}–{speeds[-1]}"
        tsys_count = len(cat_cfg.get("tsys", ()))
        return f"{spec.abbr} · L {lanes_str} · V {spd_str} km/h · {tsys_count} TSys"

    def _refresh_category_list_labels(self) -> None:
        user_role = qt_enum(Qt, "UserRole", "ItemDataRole")
        for i in range(self.category_list.count()):
            item = self.category_list.item(i)
            key = item.data(user_role)
            spec = STRUCTURED_BY_KEY[key]
            cat_cfg = self._config["categories"][key]
            enabled = bool(cat_cfg["enabled"])
            title = spec.key
            subtitle = self._format_category_subtitle(spec, cat_cfg)
            item.setText(f"{title} ({subtitle})")
            item.setData(user_role + 1, subtitle)
            item.setData(user_role + 2, bool(spec.is_ramp))
            item.setData(user_role + 3, enabled)
            item.setData(user_role + 4, title)
        self.category_list.viewport().update()

    def _on_category_selected(self, _row: int) -> None:
        self._load_selected_category()

    def _load_selected_category(self) -> None:
        key = self._current_category_key()
        if not key:
            return
        self._syncing = True
        try:
            spec = STRUCTURED_BY_KEY[key]
            cat_cfg = self._config["categories"][key]
            self.cat_title_label.setText(f"{spec.key} ({spec.abbr}) — Rank {spec.rank}, GType {spec.gtype}")
            self.cat_osm_ref_label.setText(spec.osm_ref)
            self.cat_enabled_check.setChecked(bool(cat_cfg["enabled"]))

            for lane, cb in self._lane_checks.items():
                cb.setEnabled(lane in spec.default_lanes)
                cb.setChecked(lane in cat_cfg["lanes"])

            for spd, cb in self._speed_checks.items():
                cb.setChecked(spd in cat_cfg["speeds"])

            active_tsys = set(cat_cfg["tsys"])
            for code, btn in self._tsys_chip_map.items():
                btn.setChecked(code in active_tsys)
        finally:
            self._syncing = False

    def _on_category_detail_changed(self) -> None:
        if self._syncing:
            return
        key = self._current_category_key()
        if not key:
            return
        spec = STRUCTURED_BY_KEY[key]
        cat_cfg = self._config["categories"][key]
        cat_cfg["enabled"] = bool(self.cat_enabled_check.isChecked())
        cat_cfg["lanes"] = [l for l in spec.default_lanes if self._lane_checks[l].isChecked()]
        cat_cfg["speeds"] = [s for s in ALL_SPEEDS if self._speed_checks[s].isChecked()]
        cat_cfg["tsys"] = [code for code, btn in self._tsys_chip_map.items() if btn.isChecked()]

        self._refresh_category_list_labels()
        self._refresh_preview_table()
        self._refresh_summary()
        self._emit_changed()

    def _reset_current_category(self) -> None:
        key = self._current_category_key()
        if not key:
            return
        defaults = default_linktypes_config()["categories"][key]
        self._config["categories"][key] = defaults
        self._load_selected_category()
        self._refresh_category_list_labels()
        self._populate_impedance_table()
        self._refresh_preview_table()
        self._refresh_summary()
        self._emit_changed()

    def _set_all_speeds_current(self, checked: bool) -> None:
        self._syncing = True
        try:
            for cb in self._speed_checks.values():
                cb.setChecked(checked)
        finally:
            self._syncing = False
        self._on_category_detail_changed()

    def _on_bus_lanes_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._config["include_bus_lanes"] = bool(checked)
        self._refresh_preview_table()
        self._refresh_summary()
        self._emit_changed()

    # --- Capacity Matrix (Sub-Page 3 Top) --------------------------------
    def _populate_capacity_table(self) -> None:
        self._syncing = True
        try:
            caps = self._config["capacities"]
            auto_scale = bool(self._config.get("scale_lanes_from_single", True))
            ro_flags = qt_enum(Qt, "ItemIsSelectable", "ItemFlag") | qt_enum(Qt, "ItemIsEnabled", "ItemFlag")
            for row, spd in enumerate(ALL_SPEEDS):
                spd_map = caps.get(str(spd), {})
                base = int(spd_map.get("1", 0))
                for col, lanes in enumerate(CAP_TABLE_LANES):
                    if auto_scale and lanes >= 2:
                        val = int(base * lanes)
                        spd_map[str(lanes)] = val
                    else:
                        val = int(spd_map.get(str(lanes), 0))
                    item = QtWidgets.QTableWidgetItem(str(val))
                    item.setTextAlignment(qt_enum(Qt, "AlignCenter", "AlignmentFlag"))
                    if auto_scale and lanes >= 2:
                        item.setFlags(ro_flags)
                        item.setBackground(QtGui.QColor("#f1f5f9"))
                        item.setForeground(QtGui.QColor("#64748b"))
                    self.cap_table.setItem(row, col, item)
            _apply_balanced_column_widths(self.cap_table)
        finally:
            self._syncing = False

    def _on_cap_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._syncing:
            return
        spd = ALL_SPEEDS[item.row()]
        lanes = CAP_TABLE_LANES[item.column()]
        try:
            val = max(0, int(float(item.text().strip().replace(",", "."))))
        except ValueError:
            val = int(self._config["capacities"][str(spd)][str(lanes)])
            self._syncing = True
            item.setText(str(val))
            self._syncing = False
            return
        self._config["capacities"][str(spd)][str(lanes)] = val
        if lanes == 1 and bool(self._config.get("scale_lanes_from_single", True)):
            self._syncing = True
            try:
                for col_idx, mult_lanes in enumerate(CAP_TABLE_LANES):
                    if mult_lanes >= 2:
                        scaled = int(val * mult_lanes)
                        self._config["capacities"][str(spd)][str(mult_lanes)] = scaled
                        target_item = self.cap_table.item(item.row(), col_idx)
                        if target_item is not None:
                            target_item.setText(str(scaled))
            finally:
                self._syncing = False
        self._refresh_preview_table()
        self._refresh_summary()
        self._emit_changed()

    def _commit_capacity_table(self) -> None:
        caps = self._config["capacities"]
        auto_scale = bool(self._config.get("scale_lanes_from_single", True))
        for row, spd in enumerate(ALL_SPEEDS):
            for col, lanes in enumerate(CAP_TABLE_LANES):
                if auto_scale and lanes >= 2:
                    continue
                it = self.cap_table.item(row, col)
                if it is None:
                    continue
                try:
                    caps[str(spd)][str(lanes)] = max(0, int(float(it.text().strip().replace(",", "."))))
                except ValueError:
                    pass
            if auto_scale:
                base = int(caps[str(spd)]["1"])
                for lanes in (2, 3, 4):
                    caps[str(spd)][str(lanes)] = int(base * lanes)

    def _on_scale_lanes_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._commit_capacity_table()
        self._config["scale_lanes_from_single"] = bool(checked)
        self._populate_capacity_table()
        self._refresh_preview_table()
        self._refresh_summary()
        self._emit_changed()

    # --- Impedance Factors Table (Sub-Page 3 Bottom) ---------------------
    def _populate_impedance_table(self) -> None:
        self._syncing = True
        try:
            self.imp_table.setRowCount(len(STRUCTURED_CATEGORIES))
            ro_flags = qt_enum(Qt, "ItemIsSelectable", "ItemFlag") | qt_enum(Qt, "ItemIsEnabled", "ItemFlag")
            for row, spec in enumerate(STRUCTURED_CATEGORIES):
                cat_cfg = self._config["categories"][spec.key]
                ro_vals = (spec.key, spec.abbr, str(spec.gtype))
                for col, text in enumerate(ro_vals):
                    item = QtWidgets.QTableWidgetItem(text)
                    item.setFlags(ro_flags)
                    if col > 0:
                        item.setTextAlignment(qt_enum(Qt, "AlignCenter", "AlignmentFlag"))
                    self.imp_table.setItem(row, col, item)
                for col, field_key in ((3, "imp_car"), (4, "imp_bike"), (5, "imp_ped")):
                    item = QtWidgets.QTableWidgetItem(f"{float(cat_cfg[field_key]):g}")
                    item.setTextAlignment(qt_enum(Qt, "AlignCenter", "AlignmentFlag"))
                    self.imp_table.setItem(row, col, item)
            _apply_balanced_column_widths(self.imp_table)
        finally:
            self._syncing = False

    def _apply_impedance_row(self, row: int) -> None:
        if not (0 <= row < len(STRUCTURED_CATEGORIES)):
            return
        cat_cfg = self._config["categories"][STRUCTURED_CATEGORIES[row].key]
        for col, field_key in ((3, "imp_car"), (4, "imp_bike"), (5, "imp_ped")):
            item = self.imp_table.item(row, col)
            if item is None:
                continue
            try:
                cat_cfg[field_key] = max(0.01, round(float(item.text().strip().replace(",", ".")), 4))
            except ValueError:
                pass

    def _on_imp_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._syncing:
            return
        if item.column() < 3:
            return
        self._apply_impedance_row(item.row())
        self._refresh_preview_table()
        self._refresh_summary()
        self._emit_changed()

    def _commit_impedance_table(self) -> None:
        for r in range(len(STRUCTURED_CATEGORIES)):
            self._apply_impedance_row(r)

    # --- Special Link Types Table (Sub-Page 4) ---------------------------
    def _populate_special_table(self) -> None:
        self._syncing = True
        try:
            specials = self._config["special"]
            self.special_table.setRowCount(len(specials))
            ro_flags = qt_enum(Qt, "ItemIsSelectable", "ItemFlag") | qt_enum(Qt, "ItemIsEnabled", "ItemFlag")
            for row, sp in enumerate(specials):
                vals = (
                    str(sp["no"]),
                    str(sp["name"]),
                    str(sp["gtype"]),
                    ",".join(sp["tsys"]),
                    str(sp["v0"]),
                    str(sp["cap"]),
                    f"{sp['imp_car']:g}",
                    f"{sp['imp_bike']:g}",
                    f"{sp['imp_ped']:g}",
                    str(sp.get("osm_ref", "")),
                )
                for col, text in enumerate(vals):
                    item = QtWidgets.QTableWidgetItem(text)
                    if col not in (3, 4, 5, 6, 7, 8):
                        item.setFlags(ro_flags)
                    if col in (0, 2, 4, 5, 6, 7, 8):
                        item.setTextAlignment(qt_enum(Qt, "AlignCenter", "AlignmentFlag"))
                    self.special_table.setItem(row, col, item)
            _apply_balanced_column_widths(self.special_table)
        finally:
            self._syncing = False

    def _apply_special_row(self, row: int) -> None:
        if not (0 <= row < len(self._config["special"])):
            return
        sp = self._config["special"][row]
        tsys_item = self.special_table.item(row, 3)
        if tsys_item is not None:
            tokens = [t.strip().upper() for t in tsys_item.text().split(",") if t.strip()]
            sp["tsys"] = tokens
        for col, key, is_int in (
            (4, "v0", True),
            (5, "cap", True),
            (6, "imp_car", False),
            (7, "imp_bike", False),
            (8, "imp_ped", False),
        ):
            item = self.special_table.item(row, col)
            if item is None:
                continue
            try:
                raw = float(item.text().strip().replace(",", "."))
                sp[key] = max(0, int(raw)) if is_int else max(0.01, round(raw, 4))
            except ValueError:
                pass

    def _on_special_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._syncing:
            return
        if item.column() < 3 or item.column() > 8:
            return
        self._apply_special_row(item.row())
        self._refresh_preview_table()
        self._refresh_summary()
        self._emit_changed()

    def _commit_special_table(self) -> None:
        for r in range(len(self._config["special"])):
            self._apply_special_row(r)

    # --- Tab switching & Preview Table (Sub-Page 5) ----------------------
    def _on_tab_changed(self, idx: int) -> None:
        if idx == 4:
            self._commit_capacity_table()
            self._commit_impedance_table()
            self._commit_special_table()
            self._refresh_preview_table()

    def _refresh_preview_table(self) -> None:
        rows = generate_linktypes_rows(self._config)
        needle = self.preview_filter_edit.text().strip().lower() if hasattr(self, "preview_filter_edit") else ""
        if needle:
            filtered = [
                r
                for r in rows
                if needle in str(r["no"])
                or needle in r["name"].lower()
                or needle in r["category"].lower()
                or needle in r["tsys"].lower()
            ]
        else:
            filtered = rows

        self._preview_rows = list(filtered)
        self.preview_count_label.setText(
            self.loc.get_string(
                "linktypes_preview_count",
                count=len(filtered),
                total=len(rows),
                default=f"{len(filtered)} / {len(rows)} aktive Streckentypen",
            )
        )
        self.preview_table.setRowCount(len(filtered))
        for idx, r in enumerate(filtered):
            vals = (
                str(r["no"]),
                str(r["gtype"]),
                str(r["name"]),
                str(r["category"]),
                str(r["rank"]),
                str(r["lanes"]),
                str(r["cap"]),
                str(r["v0"]),
                str(r["tsys"]),
                f"{r['imp_car']:g}",
                f"{r['imp_bike']:g}",
                f"{r['imp_ped']:g}",
            )
            for col, text in enumerate(vals):
                item = QtWidgets.QTableWidgetItem(text)
                if col in (0, 1, 4, 5, 6, 7, 9, 10, 11):
                    item.setTextAlignment(qt_enum(Qt, "AlignCenter", "AlignmentFlag"))
                self.preview_table.setItem(idx, col, item)
        _apply_balanced_column_widths(self.preview_table)

    def _on_preview_cell_double_clicked(self, row: int, col: int) -> None:
        """Jump from a double-clicked preview cell to its corresponding detail sub-page."""
        if not (0 <= row < len(self._preview_rows)):
            return
        row_data = self._preview_rows[row]
        cat_name = str(row_data.get("category", ""))

        # Special Link Types -> Sub-Page 4 (index 3)
        if cat_name == "Special":
            self.tab_widget.setCurrentIndex(3)
            target_no = int(row_data["no"])
            sp_row = next(
                (i for i, sp in enumerate(self._config["special"]) if int(sp["no"]) == target_no),
                None,
            )
            if sp_row is not None:
                # Map preview col -> special_table col
                col_map = {6: 5, 7: 4, 8: 3, 9: 6, 10: 7, 11: 8}
                sp_col = col_map.get(col, 4)
                item = self.special_table.item(sp_row, sp_col)
                if item is not None:
                    self.special_table.setCurrentCell(sp_row, sp_col)
                    self.special_table.scrollToItem(item)
                self.special_table.setFocus()
            return

        # CapPrT -> Sub-Page 3 Capacity Matrix (index 2)
        if col == 6:
            self.tab_widget.setCurrentIndex(2)
            try:
                spd_idx = ALL_SPEEDS.index(int(row_data["v0"]))
                lane_idx = CAP_TABLE_LANES.index(int(row_data["lanes"]))
            except ValueError:
                spd_idx, lane_idx = 0, 0
            item = self.cap_table.item(spd_idx, lane_idx)
            if item is not None:
                self.cap_table.setCurrentCell(spd_idx, lane_idx)
                self.cap_table.scrollToItem(item)
            self.cap_table.setFocus()
            return

        # Imp Car (9), Imp Bike (10), Imp Walk (11) -> Sub-Page 3 Impedance Table (index 2)
        if col in (9, 10, 11):
            self.tab_widget.setCurrentIndex(2)
            imp_row = next(
                (i for i, spec in enumerate(STRUCTURED_CATEGORIES) if spec.key == cat_name),
                0,
            )
            imp_col = col - 6  # 9->3 (Car), 10->4 (Bike), 11->5 (Walk)
            item = self.imp_table.item(imp_row, imp_col)
            if item is not None:
                self.imp_table.setCurrentCell(imp_row, imp_col)
                self.imp_table.scrollToItem(item)
            self.imp_table.setFocus()
            return

        # Categories, Lanes, Speeds, TypNo, Rank -> Sub-Page 2 (index 1)
        self.tab_widget.setCurrentIndex(1)
        cat_row = next(
            (i for i, spec in enumerate(STRUCTURED_CATEGORIES) if spec.key == cat_name),
            None,
        )
        if cat_row is not None:
            self.category_list.setCurrentRow(cat_row)
            if col == 5:
                lane_cb = self._lane_checks.get(int(row_data["lanes"]))
                if lane_cb is not None:
                    lane_cb.setFocus()
                    return
            if col == 7:
                speed_cb = self._speed_checks.get(int(row_data["v0"]))
                if speed_cb is not None:
                    speed_cb.setFocus()
                    return
            self.category_list.setFocus()

    def _refresh_summary(self) -> None:
        rows = generate_linktypes_rows(self._config)
        is_def = is_default_linktypes_config(self._config)
        if is_def:
            text = self.loc.get_string(
                "linktypes_summary_default",
                count=len(rows),
                default=f"Standard-Voreinstellung aktiv ({len(rows)} aktive Streckentypen)",
            )
        else:
            text = self.loc.get_string(
                "linktypes_summary_custom",
                count=len(rows),
                default=f"Benutzerdefinierte Konfiguration ({len(rows)} aktive Streckentypen)",
            )
        self.summary_label.setText(text)
        self.reset_button.setEnabled(not is_def)

    def _emit_changed(self) -> None:
        if self.config_changed is not None:
            self.config_changed.emit()


class LinkTypesStepWidget(_Q_WIDGET_BASE):
    """Dedicated wizard page for configuring link types and ferry parameters."""

    next_requested = pyqtSignal() if pyqtSignal is not None else None
    status_changed = pyqtSignal(str) if pyqtSignal is not None else None

    def __init__(
        self,
        localizer_obj=None,
        parent=None,
        *,
        project_path: Optional[str] = None,
    ):
        if QtWidgets is None:
            return
        super().__init__(parent)
        self.localizer = localizer_obj or localizer
        self.project_path = project_path or ""
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(16, 10, 16, 10)
        main_layout.setSpacing(8)

        self.header, _title_label, _step_label = create_step_header(
            self.localizer.get_string(
                "step5_linktypes_title",
                default="Schritt 5: Streckentypen und Fährparameter",
            ),
            current_step=StepId.LINKTYPES,
            localizer=self.localizer,
            parent=self,
        )
        main_layout.addWidget(self.header)

        self.desc_label = QtWidgets.QLabel(
            self.localizer.get_string(
                "step5_linktypes_desc",
                default="Legen Sie vor der Visum-Verarbeitung Straßenkategorien, Fahrstreifenklassen, Geschwindigkeitsstufen, Kapazitäten, getrennte Widerstandsfaktoren (Pkw, Rad, Fuß), Sonderstreckentypen und Fährparameter fest.",
            ),
            self,
        )
        self.desc_label.setFont(app_font(9))
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet("color: #475569; padding: 2px 2px;")
        main_layout.addWidget(self.desc_label)

        initial_cfg = load_linktypes_config(self.project_path) if self.project_path else default_linktypes_config()
        self.editor = LinkTypesEditorWidget(
            config=initial_cfg,
            project_path=self.project_path,
            localizer_obj=self.localizer,
            parent=self,
            show_footer=True,
        )
        self.editor.config_changed.connect(self._on_editor_changed)
        main_layout.addWidget(self.editor, 1)

        # Convenient aliases matching LinkTypesConfigDialog / tests
        self.tab_widget = self.editor.tab_widget
        self.reset_button = self.editor.reset_button
        self.summary_label = self.editor.summary_label
        self.ferry_wait_spin = self.editor.ferry_wait_spin
        self.cap_table = self.editor.cap_table
        self.imp_table = self.editor.imp_table
        self.special_table = self.editor.special_table
        self.preview_table = self.editor.preview_table
        self.modes_table = self.editor.modes_table
        self.category_list = self.editor.category_list

    def set_project_path(self, path: str) -> None:
        self.project_path = path or ""
        self.editor.set_project_path(self.project_path)

    def get_config(self) -> dict[str, Any]:
        return self.editor.get_config()

    def save_all(self) -> bool:
        """Persist current link-type configuration and ferry wait time to project directory."""
        rows = generate_linktypes_rows(self.editor.get_config())
        if not rows:
            return False
        if self.project_path:
            self.editor.save_to_project(self.project_path)
        return True

    def _on_editor_changed(self) -> None:
        if self.project_path:
            self.editor.save_to_project(self.project_path)
        if self.status_changed is not None:
            self.status_changed.emit(self.editor.summary_label.text())


Step5LinkTypesWidget = LinkTypesStepWidget
