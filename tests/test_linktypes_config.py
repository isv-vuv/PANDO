"""Unit, COM, and Qt GUI tests for link-type configuration and Step 5 pre-Visum controls."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.app.app_core.linktypes_config import (
    CONFIG_FILENAME,
    CUSTOM_NET_FILENAME,
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

    def test_step5_ferry_wait_and_linktypes_dialog(self):
        from core.app.app_qt.dialogs.linktypes_dialog import LinkTypesConfigDialog
        from core.app.app_qt.steps.step5_visum import Step5VisumWidget

        with tempfile.TemporaryDirectory() as tmp:
            widget = Step5VisumWidget(localizer, project_path=tmp)
            self.assertAlmostEqual(widget.ferry_wait_spin.value(), 20.0)

            widget.ferry_wait_spin.setValue(35.0)
            ferry_json = Path(tmp) / "ferry_parameters.json"
            self.assertTrue(ferry_json.is_file())
            self.assertAlmostEqual(json.loads(ferry_json.read_text(encoding="utf-8"))["wait_minutes"], 35.0)

            # Edit in LinkTypesConfigDialog and verify preview + get_config
            dlg = LinkTypesConfigDialog(default_linktypes_config(), parent=widget, localizer_obj=localizer)
            self.assertEqual(dlg.tab_widget.count(), 3)
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


if __name__ == "__main__":
    unittest.main()
