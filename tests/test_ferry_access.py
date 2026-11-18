import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "core/scripts/visum/scripts/ferry_access.py"
spec = importlib.util.spec_from_file_location("ferry_access", SCRIPT)
ferry_access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ferry_access)


def _visum_available() -> bool:
    try:
        import win32com.client as com

        com.Dispatch("Visum.Visum.260")
        return True
    except Exception:
        return False


class FerryAccessUnitTests(unittest.TestCase):
    def test_classify_ferry_nodes_degree_terminals_water_and_road_access(self):
        nodes_df = pd.DataFrame([
            {"NO": 1, "XCOORD": 0.0, "YCOORD": 0.0, "OSM_NODE_ID": "101", "OSM_FerryTerminal": False},
            {"NO": 2, "XCOORD": 100.0, "YCOORD": 0.0, "OSM_NODE_ID": "102", "OSM_FerryTerminal": False},
            {"NO": 3, "XCOORD": 200.0, "YCOORD": 0.0, "OSM_NODE_ID": "103", "OSM_FerryTerminal": True},
            {"NO": 4, "XCOORD": 200.0, "YCOORD": 100.0, "OSM_NODE_ID": "104", "OSM_FerryTerminal": True},
            {"NO": 5, "XCOORD": 200.0, "YCOORD": -100.0, "OSM_NODE_ID": "105", "OSM_FerryTerminal": False},
            {"NO": 6, "XCOORD": 300.0, "YCOORD": 100.0, "OSM_NODE_ID": "106", "OSM_FerryTerminal": False},
            {"NO": 7, "XCOORD": 300.0, "YCOORD": -100.0, "OSM_NODE_ID": "107", "OSM_FerryTerminal": False},
            {"NO": 8, "XCOORD": 400.0, "YCOORD": 100.0, "OSM_NODE_ID": "108", "OSM_FerryTerminal": False},
        ])
        links_df = pd.DataFrame([
            # Dead-end ferry link (1 <-> 2) and water junction at 2 (2 <-> 3, 2 <-> 5) -> deg(1)=1, deg(2)=3
            {"FROMNODENO": 1, "TONODENO": 2, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 2, "TONODENO": 1, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 2, "TONODENO": 3, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 3, "TONODENO": 2, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 2, "TONODENO": 5, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 5, "TONODENO": 2, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            # Pier 3 also connects to 4 (deg(3)=2, tagged terminal, no road) -> Isolated_Ferry=True
            {"FROMNODENO": 3, "TONODENO": 4, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 4, "TONODENO": 3, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            # Pier 4 already has incoming & outgoing CAR+WALK road link (4 <-> 6) -> Isolated_Ferry=False
            {"FROMNODENO": 4, "TONODENO": 6, "TYPENO": 30, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 6, "TONODENO": 4, "TYPENO": 30, "TSYSSET": "CAR,WALK"},
            # Node 5 is deg=1 ferry node with only a CAR-only (no WALK) road -> still Isolated_Ferry=True
            {"FROMNODENO": 5, "TONODENO": 7, "TYPENO": 10, "TSYSSET": "CAR"},
            {"FROMNODENO": 7, "TONODENO": 5, "TYPENO": 10, "TSYSSET": "CAR"},
            # Road 6 <-> 8 with CAR,WALK -> 6 and 8 are Road_Target_Node=True
            {"FROMNODENO": 6, "TONODENO": 8, "TYPENO": 30, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 8, "TONODENO": 6, "TYPENO": 30, "TSYSSET": "CAR,WALK"},
        ])

        out = ferry_access.classify_ferry_nodes(nodes_df, links_df).set_index("NO")

        self.assertTrue(out.loc[1, "Ferry_Node"])
        self.assertEqual(out.loc[1, "Ferry_Degree"], 1)
        self.assertTrue(out.loc[1, "Isolated_Ferry"])

        self.assertTrue(out.loc[2, "Ferry_Node"])
        self.assertEqual(out.loc[2, "Ferry_Degree"], 3)
        self.assertFalse(out.loc[2, "OSM_FerryTerminal"])
        self.assertFalse(out.loc[2, "Isolated_Ferry"])

        self.assertTrue(out.loc[3, "Ferry_Node"])
        self.assertEqual(out.loc[3, "Ferry_Degree"], 2)
        self.assertTrue(out.loc[3, "OSM_FerryTerminal"])
        self.assertTrue(out.loc[3, "Isolated_Ferry"])

        self.assertTrue(out.loc[4, "Ferry_Node"])
        self.assertFalse(out.loc[4, "Isolated_Ferry"])
        self.assertFalse(out.loc[4, "Road_Target_Node"])

        self.assertTrue(out.loc[5, "Isolated_Ferry"])
        self.assertFalse(out.loc[7, "Road_Target_Node"])
        self.assertTrue(out.loc[6, "Road_Target_Node"])
        self.assertTrue(out.loc[8, "Road_Target_Node"])

    def test_offset_terminal_snaps_to_single_nearest_ferry_node_within_200m(self):
        nodes_df = pd.DataFrame([
            {"NO": 10, "XCOORD": 1000.0, "YCOORD": 1000.0, "OSM_NODE_ID": "10", "OSM_FerryTerminal": False},
            {"NO": 11, "XCOORD": 1060.0, "YCOORD": 1000.0, "OSM_NODE_ID": "11", "OSM_FerryTerminal": False},
            {"NO": 12, "XCOORD": 2000.0, "YCOORD": 1000.0, "OSM_NODE_ID": "12", "OSM_FerryTerminal": False},
            {"NO": 13, "XCOORD": 3000.0, "YCOORD": 1000.0, "OSM_NODE_ID": "13", "OSM_FerryTerminal": False},
            {"NO": 14, "XCOORD": 1000.0, "YCOORD": 2000.0, "OSM_NODE_ID": "14", "OSM_FerryTerminal": False},
            # Non-ferry tagged node within 50m of ferry node 10
            {"NO": 99, "XCOORD": 950.0, "YCOORD": 1000.0, "OSM_NODE_ID": "99", "OSM_FerryTerminal": True},
        ])
        links_df = pd.DataFrame([
            # Node 10 (pier, deg=2) and Node 11 (water junction, deg=3)
            {"FROMNODENO": 10, "TONODENO": 11, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 11, "TONODENO": 10, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 10, "TONODENO": 14, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 14, "TONODENO": 10, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 11, "TONODENO": 12, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 12, "TONODENO": 11, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 11, "TONODENO": 13, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 13, "TONODENO": 11, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            # Make 12 & 13 multi-degree water nodes too so only 10/11 are under test
            {"FROMNODENO": 12, "TONODENO": 13, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 13, "TONODENO": 12, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
        ])

        out = ferry_access.classify_ferry_nodes(
            nodes_df,
            links_df,
            osm_terminal_xy={"far_away": (2250.0, 1000.0)},  # 250 m from node 12 -> must NOT snap
            snap_distance_m=200.0,
        ).set_index("NO")

        self.assertTrue(out.loc[10, "OSM_FerryTerminal"])
        self.assertTrue(out.loc[10, "Isolated_Ferry"])
        self.assertFalse(out.loc[11, "OSM_FerryTerminal"])
        self.assertFalse(out.loc[11, "Isolated_Ferry"])
        self.assertFalse(out.loc[12, "OSM_FerryTerminal"])
        self.assertFalse(out.loc[99, "Ferry_Node"])
        self.assertFalse(out.loc[99, "Isolated_Ferry"])

    def test_extract_osm_ferry_terminal_info_ignores_bus_stops(self):
        osm_content = """<?xml version="1.0" encoding="utf-8"?>
<osm version="0.6">
  <node id="1" lat="10.67" lon="122.94"><tag k="amenity" v="ferry_terminal"/></node>
  <node id="2" lat="10.68" lon="122.94"/>
  <node id="3" lat="10.69" lon="122.94"/>
  <node id="4" lat="10.70" lon="122.95"><tag k="public_transport" v="stop_position"/><tag k="bus" v="yes"/></node>
  <node id="5" lat="10.671" lon="122.941"><tag k="mooring" v="ferry"/></node>
  <way id="100">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/>
    <tag k="route" v="ferry"/>
  </way>
</osm>
"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sample.osm"
            p.write_text(osm_content, encoding="utf-8")
            term_ids, lonlats, mid_only = ferry_access.extract_osm_ferry_terminal_info(p)

        self.assertEqual(term_ids, {"1", "5"})
        self.assertIn("1", lonlats)
        self.assertIn("5", lonlats)
        self.assertNotIn("4", term_ids)
        self.assertEqual(mid_only, {"2"})

    def test_connect_ferries_mock_uses_target_filter_and_opens_ferry_access_turns(self):
        visum = MagicMock()
        nodes = visum.Net.Nodes
        links = visum.Net.Links
        turns = visum.Net.Turns

        nodes.AttrExists.side_effect = lambda name: name in {"OSM_NODE_ID", "OSM_FerryTerminal", "Road_Target_Node"}
        visum.Net.LinkTypes.GetMultipleAttributes.return_value = [(30, "Primary"), (94, "Ferry"), (95, "Ferry_Access")]
        nodes.GetMultipleAttributes.return_value = [
            (1, 0.0, 0.0, "1", False),
            (2, 100.0, 0.0, "2", True),
            (3, 500.0, 0.0, "3", False),
            (4, 600.0, 0.0, "4", False),
        ]
        nodes.GetMultiAttValues.return_value = [(1, 1), (2, 2), (3, 3), (4, 4)]
        links.GetMultipleAttributes.return_value = [
            (1, 2, 94, "CAR,WALK"),
            (2, 1, 94, "CAR,WALK"),
            (3, 4, 30, "CAR,WALK"),
            (4, 3, 30, "CAR,WALK"),
        ]
        nodes.GetAllActive = [MagicMock(name="node1"), MagicMock(name="node2")]
        nodes.CountActive = 2
        paras = MagicMock()
        nodes.CreateAutomaticNodeConnectionParas.return_value = paras
        new_links = MagicMock(Count=2)
        nodes.ConnectNodes.return_value = new_links

        turns.CountActive = 2
        visum.Net.TSystems.GetMultipleAttributes.return_value = [
            ("CAR", "PRT"),
            ("WALK", "PRT"),
            ("PUTW", "PUTWALK"),
        ]
        turns.GetMultipleAttributes.return_value = [
            ("", "CAR,WALK,PUTW", "CAR,WALK,PUTW", 94, 95),
            ("", "CAR,WALK", "CAR,WALK", 30, 30),
        ]

        stats = ferry_access.connect_ferries_to_road_network(visum)

        self.assertEqual(stats["ferry_nodes"], 2)
        self.assertEqual(stats["isolated_ferries"], 2)
        self.assertEqual(stats["created_links"], 2)
        self.assertEqual(stats["opened_turns"], 1)
        paras.SetAttValue.assert_any_call("MinNewLinksPerSourceNode", 0)
        paras.SetAttValue.assert_any_call("MaxNewLinksPerSourceNode", 1)
        paras.SetAttValue.assert_any_call("MaxDistanceEnabled", True)
        paras.SetAttValue.assert_any_call("MaxDistance", "5000m")
        visum.Filters.NodeFilter().AddCondition.assert_any_call("OP_NONE", False, "Road_Target_Node", "EqualVal", 1)
        turns.SetMultipleAttributes.assert_called_once_with(["TSYSSET"], [["CAR,WALK"], [""]], OnlyActive=True)

    @unittest.skipUnless(_visum_available(), "PTV Visum 260 COM not available")
    def test_connect_ferries_real_visum_com_end_to_end_and_idempotent(self):
        import win32com.client as com

        visum = com.Dispatch("Visum.Visum.260")
        visum.ClearNet()
        wgs84_utm51 = (
            'PROJCS["WGS 84 / UTM zone 51N",GEOGCS["WGS 84",DATUM["WGS_1984",'
            'SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],'
            'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
            'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",123],'
            'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],'
            'PARAMETER["false_northing",0],UNIT["metre",1]]'
        )
        visum.Net.SetProjection(wgs84_utm51, False)

        # Visum default TSystems: B (PUT), F (PUTWALK), P (PRT), R (PRT) -> add CAR and WALK
        visum.Net.AddTSystem("CAR", "PRT")
        visum.Net.AddTSystem("WALK", "PRT")
        visum.Net.AddTSystem("PUTW", "PUTWALK")

        lt30 = visum.Net.AddLinkType(30)
        lt30.SetAttValue("NAME", "Primary")
        lt30.SetAttValue("TSYSSET", "CAR,WALK,PUTW")

        lt94 = visum.Net.AddLinkType(94)
        lt94.SetAttValue("NAME", "Ferry")
        lt94.SetAttValue("TSYSSET", "CAR,WALK,PUTW")

        # Nodes:
        # 1: Isolated multi-route pier (tagged OSM_FerryTerminal=1) at 300 m from road node 10
        # 2: Untagged water junction (deg=3) at 200 m from road node 10 -> MUST NOT connect
        # 3: Connected pier at road node 10
        # 4: Distant dead-end ferry node at 6000 m (> 5 km) -> MUST NOT connect
        # 10, 11: Road nodes
        n1 = visum.Net.AddNode(1, 500000.0, 1180000.0)
        n2 = visum.Net.AddNode(2, 500100.0, 1180000.0)
        n3 = visum.Net.AddNode(3, 500300.0, 1180000.0)
        n4 = visum.Net.AddNode(4, 506500.0, 1180000.0)
        n10 = visum.Net.AddNode(10, 500300.0, 1180300.0)
        n11 = visum.Net.AddNode(11, 500500.0, 1180300.0)

        visum.Net.Nodes.AddUserDefinedAttribute("OSM_FerryTerminal", "OSM_FerryTerminal", "OSM_FerryTerminal", 9, DefVal=False)
        n1.SetAttValue("OSM_FerryTerminal", 1)

        visum.Net.AddLink(1, n1, n2, lt94)
        visum.Net.AddLink(2, n2, n3, lt94)
        visum.Net.AddLink(3, n2, n4, lt94)
        visum.Net.AddLink(4, n1, n4, lt94)  # makes deg(1)=2, deg(2)=3, deg(4)=2 -> untagged 4 is not deg=1
        visum.Net.AddLink(5, n3, n10, lt30)
        visum.Net.AddLink(6, n10, n11, lt30)

        # Also test a distant isolated deg=1 ferry node > 5km away (node 5 -> node 6)
        n5 = visum.Net.AddNode(5, 508000.0, 1180000.0)
        n6 = visum.Net.AddNode(6, 509000.0, 1180000.0)
        visum.Net.AddLink(7, n5, n6, lt94)

        stats = ferry_access.connect_ferries_to_road_network(visum)
        self.assertEqual(stats["created_links"], 2)  # 1 <-> 10 (2 directed links)
        self.assertTrue(visum.Net.Links.LinkExistsByKey(1, 10))
        self.assertFalse(visum.Net.Links.LinkExistsByKey(2, 10))
        self.assertFalse(visum.Net.Links.LinkExistsByKey(5, 11))

        # Check turns involving Ferry_Access link (1 <-> 10) are open for CAR,WALK (and not PUTW)
        turn_2_1_10 = visum.Net.Turns.ItemByKey(2, 1, 10)
        turn_1_10_11 = visum.Net.Turns.ItemByKey(1, 10, 11)
        self.assertEqual(set(str(turn_2_1_10.AttValue("TSYSSET")).split(",")), {"CAR", "WALK"})
        self.assertEqual(set(str(turn_1_10_11.AttValue("TSYSSET")).split(",")), {"CAR", "WALK"})

        # Idempotent second call must not create additional links
        stats2 = ferry_access.connect_ferries_to_road_network(visum)
        self.assertEqual(stats2["created_links"], 0)

    def test_select_same_bank_target_nodes_blocks_across_river_and_water_cone(self):
        import pandas as pd
        from shapely.geometry import LineString

        # Terminal 20486 at (0, 0) connects via ferry link going SE-NE (water vector ~ (1, 0.5))
        # Closer road node 15854 is across a river waterway (distance ~112 m)
        # Same-bank road node 17708 is on the landward side (-200, 80, distance ~215 m)
        classified_df = pd.DataFrame([
            {"NO": 20486, "XCOORD": 0.0, "YCOORD": 0.0, "Isolated_Ferry": 1, "Road_Target_Node": 0},
            {"NO": 20487, "XCOORD": 400.0, "YCOORD": 200.0, "Isolated_Ferry": 0, "Road_Target_Node": 0},
            {"NO": 15854, "XCOORD": 100.0, "YCOORD": -50.0, "Isolated_Ferry": 0, "Road_Target_Node": 1},
            {"NO": 17708, "XCOORD": -200.0, "YCOORD": 80.0, "Isolated_Ferry": 0, "Road_Target_Node": 1},
        ])
        links_df = pd.DataFrame([
            {"FROMNODENO": 20486, "TONODENO": 20487, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
            {"FROMNODENO": 20487, "TONODENO": 20486, "TYPENO": 94, "TSYSSET": "CAR,WALK"},
        ])
        # River bank separator passing between (0, 0) and (100, -50)
        waterway_lines = [LineString([(-50.0, -150.0), (150.0, 150.0)])]
        ferry_wkt_rows = [(20486, 20487, "LINESTRING(0 0, 120 40, 400 200)")]

        targets = ferry_access.select_same_bank_target_nodes(
            classified_df,
            links_df,
            ferry_wkt_rows=ferry_wkt_rows,
            waterway_lines=waterway_lines,
        )
        self.assertEqual(targets.get(20486), 17708)

    def test_resolve_and_extract_ferry_waterways_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            net_dir = Path(tmp) / "processed" / "osm" / "04_network"
            visum_dir = Path(tmp) / "processed" / "visum"
            net_dir.mkdir(parents=True)
            visum_dir.mkdir(parents=True)

            road_osm = net_dir / "road_network_hierarchical_modified.osm"
            road_osm.write_text('<?xml version="1.0"?><osm version="0.6"></osm>', encoding="utf-8")
            ww_osm = net_dir / "ferry_waterways.osm"
            ww_osm.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="10" lat="10.690" lon="122.570"/>
  <node id="11" lat="10.695" lon="122.575"/>
  <way id="50">
    <nd ref="10"/>
    <nd ref="11"/>
    <tag k="waterway" v="river"/>
  </way>
</osm>
""",
                encoding="utf-8",
            )

            files = ferry_access._resolve_waterway_osm_files(road_osm, visum_dir)
            self.assertEqual(files[0].resolve(), ww_osm.resolve())
            lines = ferry_access._extract_waterway_lines_from_osm(ww_osm, "")
            self.assertEqual(len(lines), 1)
            self.assertGreater(lines[0].length, 0.0)


if __name__ == "__main__":
    unittest.main()
