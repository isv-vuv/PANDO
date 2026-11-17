"""Dialog for viewing and customizing PANDO Visum link types."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Optional

from core.locales import localizer
from core.app.app_core.linktypes_config import (
    ALL_LANES,
    ALL_SPEEDS,
    ALL_TSYS,
    STRUCTURED_BY_KEY,
    STRUCTURED_CATEGORIES,
    default_linktypes_config,
    generate_linktypes_rows,
    is_default_linktypes_config,
    normalize_linktypes_config,
)
from core.app.app_qt.qt_base import (
    Dialogs,
    Qt,
    QtWidgets,
    app_font,
    escape_mnemonic,
    qfont_bold,
    qt_enum,
)

_Q_DIALOG_BASE = QtWidgets.QDialog if QtWidgets is not None else object

_DIALOG_STYLE = """
    QDialog {
        background-color: #ffffff;
    }
    QTabWidget::pane {
        border: 1px solid #e2e8f0;
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
        border: 1px solid #e2e8f0;
        border-bottom: none;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
        padding: 6px 14px;
        margin-right: 3px;
        margin-bottom: -1px;
        font-size: 11px;
    }
    QTabBar::tab:first {
        margin-left: 6px;
    }
    QTabBar::tab:hover {
        background-color: #e2e8f0;
        color: #0f172a;
    }
    QTabBar::tab:selected {
        background-color: #ffffff;
        color: #2563eb;
        font-weight: bold;
        border-color: #e2e8f0;
        border-bottom: 2px solid #2563eb;
    }
    QGroupBox {
        font-weight: bold;
        color: #1e293b;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        margin-top: 10px;
        padding-top: 10px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 4px;
    }
    QListWidget, QTableWidget {
        border: 1px solid #cbd5e1;
        border-radius: 4px;
        background-color: #ffffff;
        alternate-background-color: #f8fafc;
        gridline-color: #e2e8f0;
    }
    QHeaderView::section {
        background-color: #f1f5f9;
        color: #334155;
        font-weight: bold;
        border: none;
        border-right: 1px solid #cbd5e1;
        border-bottom: 1px solid #cbd5e1;
        padding: 4px 6px;
    }
"""


class LinkTypesConfigDialog(_Q_DIALOG_BASE):
    """Modal configuration dialog replacing Excel-based link-type editing."""

    def __init__(
        self,
        config: Optional[Mapping[str, Any]] = None,
        parent: Optional[object] = None,
        localizer_obj: Optional[object] = None,
    ):
        if QtWidgets is None:
            return
        super().__init__(parent)
        self.loc = localizer_obj or localizer
        self._config = normalize_linktypes_config(config)
        self._syncing = False
        self._lane_checks: dict[int, QtWidgets.QCheckBox] = {}
        self._speed_checks: dict[int, QtWidgets.QCheckBox] = {}
        self._tsys_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._imp_spins: dict[str, QtWidgets.QDoubleSpinBox] = {}

        self.setWindowTitle(
            self.loc.get_string(
                "linktypes_dialog_title",
                default="Streckentypen konfigurieren (OSM-Bracketing & Visum)",
            )
        )
        self.setMinimumSize(940, 650)
        self.resize(1080, 720)
        self.setStyleSheet(_DIALOG_STYLE)

        self._build_ui()
        self._load_all_views()

    def get_config(self) -> dict[str, Any]:
        """Return normalized copy of the currently edited configuration."""
        self._commit_special_table()
        self._commit_capacity_table()
        return normalize_linktypes_config(copy.deepcopy(self._config))

    def _build_ui(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(18, 14, 18, 14)
        main_layout.setSpacing(10)

        # Header
        title_lbl = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_dialog_header",
                default="Visum-Streckentypen & Kapazitätsmethodik konfigurieren",
            ),
            self,
        )
        title_lbl.setFont(app_font(12, qfont_bold()))
        title_lbl.setStyleSheet("color: #0f172a;")
        main_layout.addWidget(title_lbl)

        desc_lbl = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_dialog_desc",
                default=(
                    "Voreingestellt ist die Standard-Master-Methodik (analog LINKTYPE_Visum.xlsm). "
                    "Änderungen gelten für die Visum-Neustrukturierung (Schritt 3) und das OSM-Bracketing."
                ),
            ),
            self,
        )
        desc_lbl.setFont(app_font(9))
        desc_lbl.setStyleSheet("color: #475569;")
        desc_lbl.setWordWrap(True)
        main_layout.addWidget(desc_lbl)

        # Tabs
        self.tab_widget = QtWidgets.QTabWidget(self)
        self.tab_widget.addTab(
            self._build_categories_tab(),
            escape_mnemonic(
                self.loc.get_string("linktypes_tab_categories", default="1. Straßenkategorien & Stufen")
            ),
        )
        self.tab_widget.addTab(
            self._build_capacities_and_special_tab(),
            escape_mnemonic(
                self.loc.get_string("linktypes_tab_capacities", default="2. Kapazitäten & Sonderstreckentypen")
            ),
        )
        self.tab_widget.addTab(
            self._build_preview_tab(),
            escape_mnemonic(
                self.loc.get_string("linktypes_tab_preview", default="3. Vorschau ($LINKTYPE)")
            ),
        )
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self.tab_widget, 1)

        # Footer
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
            "QPushButton { background-color: #ffffff; color: #334155; border: 1px solid #cbd5e1; border-radius: 4px; padding: 6px 14px; } "
            "QPushButton:hover { background-color: #f1f5f9; border-color: #94a3b8; }"
        )
        self.reset_button.clicked.connect(self._on_reset_defaults)
        footer.addWidget(self.reset_button)

        self.cancel_button = QtWidgets.QPushButton(
            self.loc.get_string("button_cancel", default="Abbrechen"),
            self,
        )
        self.cancel_button.setFont(app_font(9))
        self.cancel_button.setStyleSheet(
            "QPushButton { background-color: #ffffff; color: #334155; border: 1px solid #cbd5e1; border-radius: 4px; padding: 6px 16px; } "
            "QPushButton:hover { background-color: #f1f5f9; border-color: #94a3b8; }"
        )
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)

        self.save_button = QtWidgets.QPushButton(
            self.loc.get_string("linktypes_btn_save", default="Speichern & Übernehmen"),
            self,
        )
        self.save_button.setFont(app_font(9, qfont_bold()))
        self.save_button.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: #ffffff; font-weight: bold; border-radius: 4px; padding: 6px 18px; border: 1px solid #1d4ed8; } "
            "QPushButton:hover { background-color: #1d4ed8; } "
            "QPushButton:pressed { background-color: #1e40af; }"
        )
        self.save_button.clicked.connect(self._on_accept)
        footer.addWidget(self.save_button)

        main_layout.addLayout(footer)

    def _build_categories_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self.tab_widget)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        splitter = QtWidgets.QSplitter(qt_enum(Qt, "Horizontal", "Orientation"), page)
        splitter.setChildrenCollapsible(False)

        # Left: category list
        left_box = QtWidgets.QWidget(splitter)
        left_layout = QtWidgets.QVBoxLayout(left_box)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(6)

        cat_hdr = QtWidgets.QLabel(
            self.loc.get_string("linktypes_lbl_categories", default="Straßenkategorien (hierarchisch)"),
            left_box,
        )
        cat_hdr.setFont(app_font(9, qfont_bold()))
        left_layout.addWidget(cat_hdr)

        self.category_list = QtWidgets.QListWidget(left_box)
        self.category_list.setMinimumWidth(250)
        self.category_list.currentRowChanged.connect(self._on_category_selected)
        left_layout.addWidget(self.category_list, 1)

        splitter.addWidget(left_box)

        # Right: category details
        right_scroll = QtWidgets.QScrollArea(splitter)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))

        right_widget = QtWidgets.QWidget(right_scroll)
        right_layout = QtWidgets.QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 0, 4, 0)
        right_layout.setSpacing(10)

        # Category Header + Enable
        top_row = QtWidgets.QHBoxLayout()
        self.cat_title_label = QtWidgets.QLabel("", right_widget)
        self.cat_title_label.setFont(app_font(11, qfont_bold()))
        self.cat_title_label.setStyleSheet("color: #0f172a;")
        top_row.addWidget(self.cat_title_label, 1)

        self.cat_enabled_check = QtWidgets.QCheckBox(
            self.loc.get_string("linktypes_lbl_cat_enabled", default="Kategorie aktiv"),
            right_widget,
        )
        self.cat_enabled_check.setFont(app_font(9, qfont_bold()))
        self.cat_enabled_check.toggled.connect(self._on_category_detail_changed)
        top_row.addWidget(self.cat_enabled_check)
        right_layout.addLayout(top_row)

        self.cat_meta_label = QtWidgets.QLabel("", right_widget)
        self.cat_meta_label.setFont(app_font(8))
        self.cat_meta_label.setStyleSheet("color: #64748b;")
        self.cat_meta_label.setWordWrap(True)
        right_layout.addWidget(self.cat_meta_label)

        # Lanes box
        lanes_group = QtWidgets.QGroupBox(
            escape_mnemonic(self.loc.get_string("linktypes_lbl_lanes", default="Fahrstreifen-Klassen (je Richtung)")),
            right_widget,
        )
        lanes_layout = QtWidgets.QHBoxLayout(lanes_group)
        for lane in ALL_LANES:
            cb = QtWidgets.QCheckBox(f"{lane} Lane" if lane == 1 else f"{lane} Lanes", lanes_group)
            cb.toggled.connect(self._on_category_detail_changed)
            self._lane_checks[lane] = cb
            lanes_layout.addWidget(cb)
        lanes_layout.addStretch()
        right_layout.addWidget(lanes_group)

        # Speeds box
        speeds_group = QtWidgets.QGroupBox(
            escape_mnemonic(self.loc.get_string("linktypes_lbl_speeds", default="Geschwindigkeitsstufen V0PrT [km/h]")),
            right_widget,
        )
        speeds_vbox = QtWidgets.QVBoxLayout(speeds_group)
        speeds_grid = QtWidgets.QGridLayout()
        speeds_grid.setSpacing(6)
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

        # Modes (TSYSSET) box
        tsys_group = QtWidgets.QGroupBox(
            escape_mnemonic(self.loc.get_string("linktypes_lbl_tsys", default="Zugelassene Verkehrssysteme (TSysSet)")),
            right_widget,
        )
        tsys_grid = QtWidgets.QGridLayout(tsys_group)
        tsys_grid.setSpacing(6)
        for idx, code in enumerate(ALL_TSYS):
            cb = QtWidgets.QCheckBox(code, tsys_group)
            cb.toggled.connect(self._on_category_detail_changed)
            self._tsys_checks[code] = cb
            tsys_grid.addWidget(cb, idx // 4, idx % 4)
        right_layout.addWidget(tsys_group)

        # Impedance factors box
        imp_group = QtWidgets.QGroupBox(
            escape_mnemonic(self.loc.get_string("linktypes_lbl_impedance", default="Widerstandsfaktoren (LinkTypeImpedanceFactor)")),
            right_widget,
        )
        imp_layout = QtWidgets.QHBoxLayout(imp_group)
        for key, label in (("imp_car", "Pkw (Car):"), ("imp_bike", "Rad (Bike):"), ("imp_ped", "Fuß (Walk):")) :
            lbl = QtWidgets.QLabel(label, imp_group)
            spin = QtWidgets.QDoubleSpinBox(imp_group)
            spin.setRange(0.01, 99.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.1)
            spin.setFixedWidth(85)
            spin.valueChanged.connect(self._on_category_detail_changed)
            self._imp_spins[key] = spin
            imp_layout.addWidget(lbl)
            imp_layout.addWidget(spin)
            imp_layout.addSpacing(12)
        imp_layout.addStretch()
        right_layout.addWidget(imp_group)

        right_layout.addStretch()
        right_scroll.setWidget(right_widget)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        # Bottom bus-lane option
        self.bus_lanes_check = QtWidgets.QCheckBox(
            self.loc.get_string(
                "linktypes_lbl_include_bus",
                default="Optionale Busspur-Streckentypen (Pr/Se/Te/Un +Bus) für Szenario-Analysen beibehalten",
            ),
            page,
        )
        self.bus_lanes_check.toggled.connect(self._on_bus_lanes_toggled)
        layout.addWidget(self.bus_lanes_check)

        return page

    def _build_capacities_and_special_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self.tab_widget)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        splitter = QtWidgets.QSplitter(qt_enum(Qt, "Vertical", "Orientation"), page)
        splitter.setChildrenCollapsible(False)

        # Top: Capacity lookup matrix
        cap_group = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string(
                    "linktypes_group_capacity",
                    default="Kapazitätsmatrix CapPrT [Fz/h] nach Geschwindigkeit und Fahrstreifenanzahl",
                )
            ),
            splitter,
        )
        cap_vbox = QtWidgets.QVBoxLayout(cap_group)
        cap_toolbar = QtWidgets.QHBoxLayout()
        cap_hint = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_capacity_hint",
                default="Werte entsprechen dem 'Dictionary'-Blatt aus LINKTYPE_Visum.xlsm (Doppelklick zum Bearbeiten).",
            ),
            cap_group,
        )
        cap_hint.setFont(app_font(8))
        cap_hint.setStyleSheet("color: #475569;")
        btn_scale_lanes = QtWidgets.QPushButton(
            self.loc.get_string(
                "linktypes_btn_scale_lanes",
                default="2–4 Lanes aus 1-Lane-Kapazität skalieren (× L)",
            ),
            cap_group,
        )
        btn_scale_lanes.clicked.connect(self._scale_capacities_from_1_lane)
        cap_toolbar.addWidget(cap_hint, 1)
        cap_toolbar.addWidget(btn_scale_lanes)
        cap_vbox.addLayout(cap_toolbar)

        self.cap_table = QtWidgets.QTableWidget(len(ALL_SPEEDS), 5, cap_group)
        self.cap_table.setHorizontalHeaderLabels([
            "V0PrT [km/h]",
            "1 Lane [Fz/h]",
            "2 Lanes [Fz/h]",
            "3 Lanes [Fz/h]",
            "4 Lanes [Fz/h]",
        ])
        self.cap_table.verticalHeader().setVisible(False)
        self.cap_table.setAlternatingRowColors(True)
        self.cap_table.horizontalHeader().setStretchLastSection(True)
        self.cap_table.cellChanged.connect(self._on_cap_cell_changed)
        cap_vbox.addWidget(self.cap_table, 1)
        splitter.addWidget(cap_group)

        # Bottom: Special types 900..994
        sp_group = QtWidgets.QGroupBox(
            escape_mnemonic(
                self.loc.get_string(
                    "linktypes_group_special",
                    default="Sonder- & Nebennetztypen (inkl. Wohn-/Erschließungswege, Schiene, Ferry & Ferry_Access)",
                )
            ),
            splitter,
        )
        sp_vbox = QtWidgets.QVBoxLayout(sp_group)
        self.special_table = QtWidgets.QTableWidget(0, 8, sp_group)
        self.special_table.setHorizontalHeaderLabels([
            "No",
            "Name",
            "GTYPE",
            "V0PrT [km/h]",
            "CapPrT",
            "TSysSet",
            "Imp Car",
            "Imp Bike / Walk",
        ])
        self.special_table.verticalHeader().setVisible(False)
        self.special_table.setAlternatingRowColors(True)
        self.special_table.horizontalHeader().setStretchLastSection(True)
        self.special_table.cellChanged.connect(self._on_special_cell_changed)
        sp_vbox.addWidget(self.special_table, 1)
        splitter.addWidget(sp_group)

        splitter.setSizes([280, 260])
        layout.addWidget(splitter, 1)
        return page

    def _build_preview_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self.tab_widget)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        filter_row = QtWidgets.QHBoxLayout()
        lbl_filter = QtWidgets.QLabel(
            self.loc.get_string("linktypes_lbl_filter", default="Filter:"),
            page,
        )
        self.preview_filter_edit = QtWidgets.QLineEdit(page)
        self.preview_filter_edit.setPlaceholderText("z. B. Ferry, Mo, Link Pr, 940 …")
        self.preview_filter_edit.textChanged.connect(self._refresh_preview_table)
        self.preview_count_label = QtWidgets.QLabel("", page)
        self.preview_count_label.setFont(app_font(9, qfont_bold()))
        filter_row.addWidget(lbl_filter)
        filter_row.addWidget(self.preview_filter_edit, 1)
        filter_row.addWidget(self.preview_count_label)
        layout.addLayout(filter_row)

        self.preview_table = QtWidgets.QTableWidget(0, 10, page)
        self.preview_table.setHorizontalHeaderLabels([
            "TypNo",
            "GTYPE",
            "Name",
            "Rank",
            "Lanes",
            "CapPrT",
            "V0PrT",
            "TSysSet",
            "Imp Car",
            "Imp Bike / Walk",
        ])
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.setEditTriggers(qt_enum(QtWidgets.QAbstractItemView, "NoEditTriggers", "EditTrigger"))
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.preview_table, 1)
        return page

    def _load_all_views(self) -> None:
        self._syncing = True
        try:
            current_row = max(0, self.category_list.currentRow())
            self.category_list.clear()
            for spec in STRUCTURED_CATEGORIES:
                item = QtWidgets.QListWidgetItem(self._format_category_list_label(spec.key))
                item.setData(qt_enum(Qt, "UserRole", "ItemDataRole"), spec.key)
                self.category_list.addItem(item)
            self.bus_lanes_check.setChecked(bool(self._config.get("include_bus_lanes", True)))
            self._populate_capacity_table()
            self._populate_special_table()
        finally:
            self._syncing = False

        if self.category_list.count() > 0:
            idx = min(current_row, self.category_list.count() - 1)
            self.category_list.setCurrentRow(idx)
            self._on_category_selected(idx)
        self._refresh_preview_table()
        self._refresh_summary()

    def _format_category_list_label(self, key: str) -> str:
        spec = STRUCTURED_BY_KEY[key]
        cat_cfg = self._config["categories"][key]
        if not cat_cfg.get("enabled", True) or not cat_cfg["lanes"] or not cat_cfg["speeds"]:
            return f"[Aus] {spec.key} ({spec.abbr})"
        lanes_str = ",".join(str(x) for x in cat_cfg["lanes"])
        vmin, vmax = min(cat_cfg["speeds"]), max(cat_cfg["speeds"])
        return f"{spec.key} ({spec.abbr}) — L:{lanes_str} | V:{vmin}–{vmax}"

    def _current_category_key(self) -> Optional[str]:
        item = self.category_list.currentItem()
        if item is None:
            return None
        return item.data(qt_enum(Qt, "UserRole", "ItemDataRole"))

    def _on_category_selected(self, row: int) -> None:
        if row < 0:
            return
        item = self.category_list.item(row)
        if item is None:
            return
        key = item.data(qt_enum(Qt, "UserRole", "ItemDataRole"))
        spec = STRUCTURED_BY_KEY[key]
        cat_cfg = self._config["categories"][key]

        self._syncing = True
        try:
            self.cat_title_label.setText(f"{spec.key}  (Kürzel: {spec.abbr} | GType: {spec.gtype} | Rang: {spec.rank})")
            self.cat_meta_label.setText(spec.osm_ref)
            self.cat_enabled_check.setChecked(bool(cat_cfg.get("enabled", True)))
            active_lanes = set(cat_cfg["lanes"])
            for lane, cb in self._lane_checks.items():
                cb.setChecked(lane in active_lanes)
            active_speeds = set(cat_cfg["speeds"])
            for spd, cb in self._speed_checks.items():
                cb.setChecked(spd in active_speeds)
            active_tsys = {t.upper() for t in cat_cfg["tsys"]}
            for code, cb in self._tsys_checks.items():
                cb.setChecked(code in active_tsys)
            for imp_key, spin in self._imp_spins.items():
                spin.setValue(float(cat_cfg[imp_key]))
        finally:
            self._syncing = False

    def _on_category_detail_changed(self) -> None:
        if self._syncing:
            return
        key = self._current_category_key()
        if not key:
            return
        cat_cfg = self._config["categories"][key]
        cat_cfg["enabled"] = self.cat_enabled_check.isChecked()
        cat_cfg["lanes"] = sorted([l for l, cb in self._lane_checks.items() if cb.isChecked()], reverse=True)
        cat_cfg["speeds"] = sorted([s for s, cb in self._speed_checks.items() if cb.isChecked()], reverse=True)
        cat_cfg["tsys"] = [code for code in ALL_TSYS if self._tsys_checks[code].isChecked()]
        for imp_key, spin in self._imp_spins.items():
            cat_cfg[imp_key] = round(float(spin.value()), 2)

        item = self.category_list.currentItem()
        if item is not None:
            item.setText(self._format_category_list_label(key))
        self._refresh_summary()

    def _reset_current_category(self) -> None:
        key = self._current_category_key()
        if not key:
            return
        defaults = default_linktypes_config()["categories"][key]
        self._config["categories"][key] = defaults
        row = self.category_list.currentRow()
        self.category_list.item(row).setText(self._format_category_list_label(key))
        self._on_category_selected(row)
        self._refresh_summary()

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
        self._refresh_summary()

    def _populate_capacity_table(self) -> None:
        caps = self._config["capacities"]
        for r, spd in enumerate(ALL_SPEEDS):
            spd_item = QtWidgets.QTableWidgetItem(f"{spd} km/h")
            spd_item.setFlags(spd_item.flags() & ~qt_enum(Qt, "ItemIsEditable", "ItemFlag"))
            self.cap_table.setItem(r, 0, spd_item)
            for c, lanes in enumerate((1, 2, 3, 4), start=1):
                val = int(caps[str(spd)][str(lanes)])
                cell = QtWidgets.QTableWidgetItem(str(val))
                cell.setTextAlignment(qt_enum(Qt, "AlignRight", "AlignmentFlag") | qt_enum(Qt, "AlignVCenter", "AlignmentFlag"))
                self.cap_table.setItem(r, c, cell)
        self.cap_table.resizeColumnsToContents()

    def _on_cap_cell_changed(self, row: int, col: int) -> None:
        if self._syncing or col == 0 or not (0 <= row < len(ALL_SPEEDS)):
            return
        item = self.cap_table.item(row, col)
        if item is None:
            return
        spd = str(ALL_SPEEDS[row])
        lanes = str(col)
        try:
            val = max(0, int(float(item.text().strip().replace(",", "."))))
        except ValueError:
            val = int(self._config["capacities"][spd][lanes])
            self._syncing = True
            item.setText(str(val))
            self._syncing = False
            return
        self._config["capacities"][spd][lanes] = val
        self._refresh_summary()

    def _commit_capacity_table(self) -> None:
        for row, spd in enumerate(ALL_SPEEDS):
            for col in (1, 2, 3, 4):
                item = self.cap_table.item(row, col)
                if item is not None:
                    try:
                        self._config["capacities"][str(spd)][str(col)] = max(0, int(float(item.text().strip().replace(",", "."))))
                    except ValueError:
                        pass

    def _scale_capacities_from_1_lane(self) -> None:
        self._commit_capacity_table()
        self._syncing = True
        try:
            for row, spd in enumerate(ALL_SPEEDS):
                sk = str(spd)
                base = int(self._config["capacities"][sk]["1"])
                for lanes in (2, 3, 4):
                    scaled = base * lanes
                    self._config["capacities"][sk][str(lanes)] = scaled
                    self.cap_table.item(row, lanes).setText(str(scaled))
        finally:
            self._syncing = False
        self._refresh_summary()

    def _populate_special_table(self) -> None:
        specials = self._config["special"]
        self.special_table.setRowCount(len(specials))
        ro_flag = qt_enum(Qt, "ItemIsEditable", "ItemFlag")
        for r, sp in enumerate(specials):
            items = [
                (str(sp["no"]), False),
                (str(sp["name"]), False),
                (str(sp["gtype"]), False),
                (str(sp["v0"]), True),
                (str(sp["cap"]), True),
                (",".join(sp["tsys"]), True),
                (f"{float(sp['imp_car']):g}", True),
                (f"{float(sp['imp_bike']):g} / {float(sp['imp_ped']):g}", True),
            ]
            for c, (text, editable) in enumerate(items):
                it = QtWidgets.QTableWidgetItem(text)
                if not editable:
                    it.setFlags(it.flags() & ~ro_flag)
                self.special_table.setItem(r, c, it)
        self.special_table.resizeColumnsToContents()

    def _on_special_cell_changed(self, row: int, col: int) -> None:
        if self._syncing or not (0 <= row < len(self._config["special"])):
            return
        self._apply_special_row(row)
        self._refresh_summary()

    def _apply_special_row(self, row: int) -> None:
        sp = self._config["special"][row]
        try:
            v0_item = self.special_table.item(row, 3)
            if v0_item is not None:
                sp["v0"] = max(0, int(float(v0_item.text().strip())))
        except ValueError:
            pass
        try:
            cap_item = self.special_table.item(row, 4)
            if cap_item is not None:
                sp["cap"] = max(0, int(float(cap_item.text().strip())))
        except ValueError:
            pass
        tsys_item = self.special_table.item(row, 5)
        if tsys_item is not None:
            sp["tsys"] = [t.strip().upper() for t in tsys_item.text().split(",") if t.strip()]
        imp_car_item = self.special_table.item(row, 6)
        if imp_car_item is not None:
            try:
                sp["imp_car"] = max(0.01, float(imp_car_item.text().strip().replace(",", ".")))
            except ValueError:
                pass
        imp_bw_item = self.special_table.item(row, 7)
        if imp_bw_item is not None and "/" in imp_bw_item.text():
            parts = [p.strip().replace(",", ".") for p in imp_bw_item.text().split("/", 1)]
            try:
                sp["imp_bike"] = max(0.01, float(parts[0]))
                sp["imp_ped"] = max(0.01, float(parts[1]))
            except ValueError:
                pass

    def _commit_special_table(self) -> None:
        for r in range(len(self._config["special"])):
            self._apply_special_row(r)

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 2:
            self._commit_capacity_table()
            self._commit_special_table()
            self._refresh_preview_table()

    def _refresh_preview_table(self) -> None:
        rows = generate_linktypes_rows(self._config)
        needle = self.preview_filter_edit.text().strip().lower() if hasattr(self, "preview_filter_edit") else ""
        if needle:
            filtered = [
                r for r in rows
                if needle in str(r["no"]) or needle in r["name"].lower() or needle in r["tsys"].lower()
            ]
        else:
            filtered = rows

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
                str(r["rank"]),
                str(r["lanes"]),
                str(r["cap"]),
                f"{r['v0']} km/h",
                str(r["tsys"]),
                f"{r['imp_car']:g}",
                f"{r['imp_bike']:g} / {r['imp_ped']:g}",
            )
            for col, text in enumerate(vals):
                self.preview_table.setItem(idx, col, QtWidgets.QTableWidgetItem(text))
        self.preview_table.resizeColumnsToContents()

    def _refresh_summary(self) -> None:
        rows = generate_linktypes_rows(self._config)
        is_def = is_default_linktypes_config(self._config)
        if is_def:
            text = self.loc.get_string(
                "linktypes_summary_default",
                count=len(rows),
                default=f"Standard-Voreinstellung aktiv ({len(rows)} Streckentypen, identisch zu master_linktypes.net)",
            )
        else:
            text = self.loc.get_string(
                "linktypes_summary_custom",
                count=len(rows),
                default=f"Benutzerdefinierte Konfiguration ({len(rows)} aktive Streckentypen)",
            )
        self.summary_label.setText(text)
        self.reset_button.setEnabled(not is_def)

    def _on_reset_defaults(self) -> None:
        self._config = default_linktypes_config()
        self._load_all_views()

    def _on_accept(self) -> None:
        rows = generate_linktypes_rows(self.get_config())
        if not rows:
            Dialogs.warning(
                self,
                self.loc.get_string("linktypes_err_empty_title", default="Keine Streckentypen"),
                self.loc.get_string("linktypes_err_empty_body", default="Bitte mindestens eine Streckentyp-Klasse aktivieren."),
            )
            return
        self.accept()
