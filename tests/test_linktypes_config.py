"""Unit, COM, and Qt GUI tests for link-type configuration and Step 5 Link-Types wizard."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.app.app_core.linktypes_config import (
    CONFIG_FILENAME,
    CUSTOM_NET_FILENAME,
    apply_linktypes_to_modified_osm,
    default_linktypes_config,
    export_linktypes_net,
    generate_linktypes_rows,
    is_default_linktypes_config,
    load_linktypes_config,
    reset_linktypes_config,
    resolve_project_master_net,
    save_linktypes_config,
)
from core.app.app_core.project import tool_root
from core.locales import localizer
from core.scripts.osm.export_network import calculate_dynamic_brackets, parse_net_link_types


class LinkTypesConfigUnitTests(unittest.TestCase):
    def test_defaults_match_master_linktypes_net_and_brackets(self):
        master_net = Path(tool_root()) / "core" / "scripts" / "visum" / "helper_files" / "master_linktypes.net"
        raw_lines = master_net.read_bytes().decode("cp1252").splitlines()
        master_active = {}
        for line in raw_lines[22:]:
            if not line.strip():
                continue
            parts = line.split(";")
            if parts[2] != "free":
                master_active[int(parts[0])] = (
                    int(parts[1]),
                    parts[2],
                    parts[5],
                    int(parts[6]),
                    int(parts[7]),
                    int(parts[8]),
                )

        rows = generate_linktypes_rows(default_linktypes_config())
        gen_active = {
            r["no"]: (r["gtype"], r["name"], r["tsys"], r["lanes"], r["cap"], r["v0"])
            for r in rows
        }
        self.assertEqual(set(master_active), set(gen_active))
        self.assertEqual(master_active, gen_active)

        with tempfile.TemporaryDirectory() as tmp:
            generated_net = Path(tmp) / "generated_master.net"
            export_linktypes_net(default_linktypes_config(), generated_net)
            b_master = calculate_dynamic_brackets(parse_net_link_types(master_net))
            b_gen = calculate_dynamic_brackets(parse_net_link_types(generated_net))
            self.assertEqual(b_master, b_gen)

    def test_save_custom_resolve_and_reset_project_config(self):
        fallback = Path(tool_root()) / "core" / "scripts" / "visum" / "helper_files" / "master_linktypes.net"
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self.assertTrue(is_default_linktypes_config(load_linktypes_config(project)))
            self.assertEqual(resolve_project_master_net(project, fallback), fallback)

            cfg = default_linktypes_config()
            cfg["categories"]["Motorway"]["lanes"] = [2, 1]
            cfg["categories"]["Motorway"]["speeds"] = [120, 80]
            cfg["categories"]["Primary"]["imp_car"] = 1.15
            cfg["capacities"]["50"]["1"] = 450
            self.assertFalse(is_default_linktypes_config(cfg))

            cfg_path, net_path = save_linktypes_config(project, cfg)
            self.assertTrue(cfg_path.is_file())
            self.assertTrue(net_path.is_file())
            self.assertEqual(cfg_path.name, CONFIG_FILENAME)
            self.assertEqual(net_path.name, CUSTOM_NET_FILENAME)
            self.assertEqual(resolve_project_master_net(project, fallback), net_path)

            brackets = calculate_dynamic_brackets(parse_net_link_types(net_path))
            self.assertEqual(sorted(brackets["Motorway"].keys()), [1, 2])
            self.assertEqual(
                [b["target_speed"] for b in brackets["Motorway"][2]],
                [120, 80],
            )
            loaded = load_linktypes_config(project)
            self.assertEqual(loaded["capacities"]["50"]["1"], 450)
            self.assertAlmostEqual(loaded["categories"]["Primary"]["imp_car"], 1.15)

            reset_linktypes_config(project)
            self.assertFalse(cfg_path.exists())
            self.assertFalse(net_path.exists())
            self.assertEqual(resolve_project_master_net(project, fallback), fallback)

    def test_apply_linktypes_to_modified_osm_reclassifies_brackets(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            net_dir = project / "processed" / "osm" / "04_network"
            net_dir.mkdir(parents=True)
            orig_osm = net_dir / "road_network_hierarchical_original.osm"
            mod_osm = net_dir / "road_network_hierarchical_modified.osm"
            sample_osm = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="10.0" lon="122.0"/>
  <node id="2" lat="10.001" lon="122.0"/>
  <way id="100">
    <nd ref="1"/>
    <nd ref="2"/>
    <tag k="highway" v="motorway"/>
    <tag k="maxspeed" v="130"/>
    <tag k="lanes" v="2"/>
  </way>
</osm>
"""
            orig_osm.write_text(sample_osm, encoding="utf-8")
            mod_osm.write_text(sample_osm, encoding="utf-8")
            # Ensure custom net has newer timestamp than mod_osm
            time.sleep(0.05)

            cfg = default_linktypes_config()
            cfg["categories"]["Motorway"]["speeds"] = [100, 80]
            save_linktypes_config(project, cfg)

            updated = apply_linktypes_to_modified_osm(project, log=lambda *_a: None)
            self.assertTrue(updated)
            mod_text = mod_osm.read_text(encoding="utf-8")
            self.assertIn('k="maxspeed" v="100"', mod_text)

    def test_generated_net_loads_into_real_visum_com(self):
        try:
            from core.scripts.visum.scripts.visum_com import create_visum
            import pythoncom
        except Exception as exc:
            self.skipTest(f"Visum COM unavailable: {exc}")

        pythoncom.CoInitialize()
        visum = None
        try:
            visum = create_visum()
            importer_net = (
                Path(tool_root())
                / "core"
                / "scripts"
                / "visum"
                / "importer"
                / "PANDO_Importer"
                / "PANDO_Importer.net"
            ).resolve()
            visum.IO.LoadNet(str(importer_net), False)

            with tempfile.TemporaryDirectory() as tmp:
                custom_net = (Path(tmp) / CUSTOM_NET_FILENAME).resolve()
                cfg = default_linktypes_config()
                cfg["categories"]["Secondary"]["speeds"] = [80, 60, 40]
                export_linktypes_net(cfg, custom_net)
                visum.IO.LoadNet(str(custom_net), True)

                lt940 = visum.Net.LinkTypes.ItemByKey(940)
                self.assertEqual(lt940.AttValue("NAME"), "Ferry_Access")
                self.assertAlmostEqual(float(lt940.AttValue("V0PRT")), 20.0)
                self.assertAlmostEqual(float(lt940.AttValue("VMAX_PRTSYS(WALK)")), 20.0)
                lt400 = visum.Net.LinkTypes.ItemByKey(400)
                self.assertEqual(lt400.AttValue("NAME"), "Se L2 V80")
        finally:
            if visum is not None:
                try:
                    visum.Exit()
                except Exception:
                    pass


class LinkTypesQtWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.app.app_qt.qt_base import QtWidgets
        if QtWidgets is None:
            raise unittest.SkipTest("PyQt unavailable")
        cls._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
        localizer.load_translations(os.path.join(tool_root(), "core", "locales"))
        localizer.set_language("de")

    def test_step5_linktypes_wizard_5_subpages_and_dblclick_jump(self):
        from core.app.app_qt.qt_base import QtWidgets
        from core.app.app_qt.steps.step5_linktypes import Step5LinkTypesWidget

        with tempfile.TemporaryDirectory() as tmp:
            widget = Step5LinkTypesWidget(localizer, project_path=tmp)
            self.assertEqual(widget.tab_widget.count(), 5)
            self.assertFalse(widget.reset_button.isEnabled())

            # Ensure zero Excel references remain in any visible label/tab text
            for idx in range(widget.tab_widget.count()):
                tab_text = widget.tab_widget.tabText(idx).lower()
                self.assertNotIn("excel", tab_text)
                self.assertNotIn("xlsm", tab_text)
            for lbl in widget.findChildren(QtWidgets.QLabel):
                txt = lbl.text().lower()
                self.assertNotIn("excel", txt)
                self.assertNotIn("xlsm", txt)

            # Sub-Page 1: Fixed importer modes table
            modes_tsys = {widget.modes_table.item(r, 1).text() for r in range(widget.modes_table.rowCount())}
            self.assertTrue({"CAR", "HGV", "BIKE", "WALK", "BUS", "PUTW"}.issubset(modes_tsys))

            # Sub-Page 3: Separate Imp Car / Imp Bike / Imp Walk cells
            self.assertEqual(widget.imp_table.columnCount(), 6)
            widget.imp_table.item(0, 4).setText("8.5")  # Motorway imp_bike
            widget.imp_table.item(0, 5).setText("9.2")  # Motorway imp_ped (Walk)
            cfg = widget.get_config()
            self.assertAlmostEqual(cfg["categories"]["Motorway"]["imp_bike"], 8.5)
            self.assertAlmostEqual(cfg["categories"]["Motorway"]["imp_ped"], 9.2)
            self.assertTrue(widget.reset_button.isEnabled())

            # Sub-Page 4: Ferry Wait Time + Separate Imp Bike / Imp Walk on Special types
            self.assertAlmostEqual(widget.ferry_wait_spin.value(), 20.0)
            widget.ferry_wait_spin.setValue(32.5)
            ferry_json = Path(tmp) / "ferry_parameters.json"
            self.assertTrue(ferry_json.is_file())
            self.assertAlmostEqual(json.loads(ferry_json.read_text(encoding="utf-8"))["wait_minutes"], 32.5)
            self.assertEqual(widget.special_table.columnCount(), 10)
            widget.special_table.item(0, 7).setText("2.4")  # Imp Bike
            widget.special_table.item(0, 8).setText("1.9")  # Imp Walk
            cfg2 = widget.get_config()
            self.assertAlmostEqual(cfg2["special"][0]["imp_bike"], 2.4)
            self.assertAlmostEqual(cfg2["special"][0]["imp_ped"], 1.9)

            # Sub-Page 5: Preview table has 12 cols with separate Imp Car / Bike / Walk + double-click jumps
            widget.tab_widget.setCurrentIndex(4)
            self.assertEqual(widget.preview_table.columnCount(), 12)

            # Jump from TSysSet (col 8) -> Sub-Page 1 (index 0)
            widget.preview_table.cellDoubleClicked.emit(0, 8)
            self.assertEqual(widget.tab_widget.currentIndex(), 0)

            # Jump from Name (col 2) on structured row -> Sub-Page 2 (index 1)
            widget.tab_widget.setCurrentIndex(4)
            widget.preview_table.cellDoubleClicked.emit(0, 2)
            self.assertEqual(widget.tab_widget.currentIndex(), 1)
            self.assertEqual(widget.editor._current_category_key(), "Motorway")

            # Jump from CapPrT (col 6) -> Sub-Page 3 (index 2)
            widget.tab_widget.setCurrentIndex(4)
            widget.preview_table.cellDoubleClicked.emit(0, 6)
            self.assertEqual(widget.tab_widget.currentIndex(), 2)
            self.assertEqual(widget.cap_table.currentRow(), 0)

            # Jump from Imp Rad (Bike) (col 10) -> Sub-Page 3 imp_table col 4
            widget.tab_widget.setCurrentIndex(4)
            widget.preview_table.cellDoubleClicked.emit(0, 10)
            self.assertEqual(widget.tab_widget.currentIndex(), 2)
            self.assertEqual(widget.imp_table.currentColumn(), 4)

            # Jump from Imp Fuß (Walk) (col 11) -> Sub-Page 3 imp_table col 5
            widget.tab_widget.setCurrentIndex(4)
            widget.preview_table.cellDoubleClicked.emit(0, 11)
            self.assertEqual(widget.tab_widget.currentIndex(), 2)
            self.assertEqual(widget.imp_table.currentColumn(), 5)

            # Jump from a Special link type row -> Sub-Page 4 (index 3)
            widget.tab_widget.setCurrentIndex(4)
            special_idx = next(
                i for i, r in enumerate(widget.editor._preview_rows) if r["no"] == 940
            )
            widget.preview_table.cellDoubleClicked.emit(special_idx, 10)
            self.assertEqual(widget.tab_widget.currentIndex(), 3)
            self.assertEqual(widget.special_table.currentColumn(), 7)

            widget.close()

    def test_step6_visum_and_linktypes_dialog(self):
        from core.app.app_qt.dialogs.linktypes_dialog import LinkTypesConfigDialog
        from core.app.app_qt.steps.step5_visum import Step5VisumWidget

        with tempfile.TemporaryDirectory() as tmp:
            widget = Step5VisumWidget(localizer, project_path=tmp)
            self.assertAlmostEqual(widget.ferry_wait_spin.value(), 20.0)

            widget.ferry_wait_spin.setValue(35.0)
            ferry_json = Path(tmp) / "ferry_parameters.json"
            self.assertTrue(ferry_json.is_file())
            self.assertAlmostEqual(json.loads(ferry_json.read_text(encoding="utf-8"))["wait_minutes"], 35.0)

            # Edit in LinkTypesConfigDialog (now 5 sub-pages matching Step 5)
            dlg = LinkTypesConfigDialog(
                default_linktypes_config(),
                parent=widget,
                localizer_obj=localizer,
                project_path=tmp,
            )
            self.assertEqual(dlg.tab_widget.count(), 5)
            self.assertFalse(dlg.reset_button.isEnabled())
            dlg._speed_checks[130].setChecked(False)
            self.assertTrue(dlg.reset_button.isEnabled())
            updated = dlg.get_config()
            self.assertNotIn(130, updated["categories"]["Motorway"]["speeds"])

            save_linktypes_config(tmp, updated)
            widget._refresh_linktypes_status()
            self.assertTrue(widget.linktypes_reset_button.isEnabled())

            widget._reset_linktypes_to_default()
            self.assertFalse(widget.linktypes_reset_button.isEnabled())
            self.assertFalse((Path(tmp) / CONFIG_FILENAME).exists())
            widget.close()
            dlg.close()

    def test_welcome_shows_8_steps_and_main_window_routes_step5_linktypes(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from core.app.app_core.workflow_state import StepId
        from core.app.app_qt import main_window
        from core.app.app_qt.qt_base import QtWidgets
        from core.app.app_qt.steps.step0_welcome import Step0WelcomeWidget
        from core.app.app_qt.steps.step5_linktypes import Step5LinkTypesWidget

        welcome = Step0WelcomeWidget(localizer)
        label_texts = [lbl.text() for lbl in welcome.findChildren(QtWidgets.QLabel)]
        self.assertTrue(any("Schritt 0 von 8" in txt for txt in label_texts))
        self.assertTrue(any("8." in txt for txt in label_texts))
        welcome.close()

        # English Step5LinkTypesWidget also has zero Excel/xlsm references
        localizer.set_language("en")
        try:
            en_widget = Step5LinkTypesWidget(localizer)
            for lbl in en_widget.findChildren(QtWidgets.QLabel):
                self.assertNotIn("excel", lbl.text().lower())
                self.assertNotIn("xlsm", lbl.text().lower())
            en_widget.close()
        finally:
            localizer.set_language("de")

        # MainWindow StepId.LINKTYPES routing
        fake_win = SimpleNamespace(
            state=SimpleNamespace(current_step=StepId.LINKTYPES),
            _show_step5_linktypes=Mock(),
            _show_step5=Mock(),
            _proceed_from_linktypes_to_visum=Mock(),
            _current_step_widget=object(),
        )
        main_window.UrbanActQtMainWindow._show_step_by_id(fake_win, StepId.LINKTYPES)
        fake_win._show_step5_linktypes.assert_called_once()
        main_window.UrbanActQtMainWindow._handle_next_requested(fake_win)
        fake_win._proceed_from_linktypes_to_visum.assert_called_once()


if __name__ == "__main__":
    unittest.main()
