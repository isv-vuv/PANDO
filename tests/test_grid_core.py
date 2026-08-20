import unittest

from core.app.app_core.grid import (
    assign_cell,
    assign_cells_along_lonlat_path,
    build_step3_payload,
    create_selected_cells,
    generate_grid_map_data,
    grid_params_are_dirty,
    has_selected_cells,
    restore_grid_geometries,
    restore_selected_cells,
    selection_is_exclusive,
    subgrid_division_for_area,
    subgrid_map_coords_for_cell,
    toggle_cell,
)


def _geo_dependencies_available():
    try:
        import pyproj  # noqa: F401
        import shapely  # noqa: F401
    except ImportError:
        return False
    return True


requires_geo_dependencies = unittest.skipUnless(_geo_dependencies_available(), "pyproj/shapely not installed")


class GridCoreTests(unittest.TestCase):
    def test_toggle_cell_state_logic_without_geo_dependencies(self):
        cell = {"id": 7, "area_type": None}
        selected_cells = create_selected_cells()

        result = toggle_cell(cell, selected_cells, "IA2")

        self.assertTrue(result.selected)
        self.assertEqual(cell["area_type"], "IA2")
        self.assertEqual(selected_cells["IA2"], {7})
        self.assertTrue(selection_is_exclusive(selected_cells))

    def test_toggle_cell_assigns_one_mode_and_deselects_same_mode_without_geo_dependencies(self):
        cell = {"id": 7, "area_type": None}
        selected_cells = create_selected_cells()

        selected = toggle_cell(cell, selected_cells, "PA")
        self.assertTrue(selected.selected)
        self.assertEqual(cell["area_type"], "PA")
        self.assertEqual(selected_cells["PA"], {7})
        self.assertTrue(has_selected_cells(selected_cells))

        deselected = toggle_cell(cell, selected_cells, "PA")
        self.assertFalse(deselected.selected)
        self.assertIsNone(cell["area_type"])
        self.assertFalse(has_selected_cells(selected_cells))
        self.assertTrue(selection_is_exclusive(selected_cells))

    def test_toggle_cell_moves_between_modes_without_duplicate_assignment(self):
        cell = {"id": 7, "area_type": None}
        selected_cells = create_selected_cells()

        toggle_cell(cell, selected_cells, "PA")
        toggle_cell(cell, selected_cells, "IA1")
        toggle_cell(cell, selected_cells, "IA2")

        self.assertEqual(cell["area_type"], "IA2")
        self.assertEqual(selected_cells["PA"], set())
        self.assertEqual(selected_cells["IA1"], set())
        self.assertEqual(selected_cells["IA2"], {7})
        self.assertTrue(selection_is_exclusive(selected_cells))

    def test_assign_cell_keeps_cell_selected_when_drag_crosses_same_cell_again(self):
        cell = {"id": 7, "area_type": None}
        selected_cells = create_selected_cells()

        first = assign_cell(cell, selected_cells, "PA")
        second = assign_cell(cell, selected_cells, "PA")

        self.assertTrue(first.selected)
        self.assertTrue(second.selected)
        self.assertEqual(cell["area_type"], "PA")
        self.assertEqual(selected_cells["PA"], {7})
        self.assertTrue(selection_is_exclusive(selected_cells))

    def test_assign_cell_drag_moves_cell_between_modes_exclusively(self):
        cell = {"id": 7, "area_type": None}
        selected_cells = create_selected_cells()

        assign_cell(cell, selected_cells, "PA")
        result = assign_cell(cell, selected_cells, "IA1")

        self.assertTrue(result.selected)
        self.assertEqual(cell["area_type"], "IA1")
        self.assertEqual(selected_cells["PA"], set())
        self.assertEqual(selected_cells["IA1"], {7})
        self.assertTrue(selection_is_exclusive(selected_cells))

    @requires_geo_dependencies
    def test_assign_cells_along_path_selects_all_crossed_cells_exclusively(self):
        from shapely.geometry import Polygon

        grid = [
            {"id": 1, "area_type": None, "shapely_poly_wgs84": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])},
            {"id": 2, "area_type": "PA", "shapely_poly_wgs84": Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])},
            {"id": 3, "area_type": None, "shapely_poly_wgs84": Polygon([(2, 0), (3, 0), (3, 1), (2, 1)])},
        ]
        selected_cells = create_selected_cells()
        selected_cells["PA"].add(2)

        results = assign_cells_along_lonlat_path(grid, selected_cells, "IA1", 0.5, 0.5, 2.5, 0.5)

        self.assertEqual([result.cell_id for result in results], [1, 2, 3])
        self.assertEqual(selected_cells["PA"], set())
        self.assertEqual(selected_cells["IA1"], {1, 2, 3})
        self.assertTrue(all(cell["area_type"] == "IA1" for cell in grid))
        self.assertTrue(selection_is_exclusive(selected_cells))

    def test_selection_exclusivity_detects_parallel_assignment(self):
        selected_cells = create_selected_cells()
        selected_cells["PA"].add(3)
        selected_cells["IA1"].add(3)

        self.assertFalse(selection_is_exclusive(selected_cells))

    def test_restore_selected_cells_rehydrates_sets_and_cell_display_types(self):
        grid = [
            {"id": 1, "area_type": None},
            {"id": 2, "area_type": "PA"},
            {"id": 3, "area_type": "IA1"},
        ]

        selected = restore_selected_cells(grid, {"PA": [1], "IA1": [2], "IA2": [99]})

        self.assertEqual(selected, {"PA": {1}, "IA1": {2}, "IA2": set()})
        self.assertEqual([cell["area_type"] for cell in grid], ["PA", "IA1", None])

    @requires_geo_dependencies
    def test_restore_grid_geometries_rehydrates_saved_wkt(self):
        grid = [
            {
                "id": 1,
                "shapely_poly_wgs84": "POLYGON ((9 48, 10 48, 10 49, 9 49, 9 48))",
            }
        ]

        restored = restore_grid_geometries(grid)

        polygon = restored[0]["shapely_poly_wgs84"]
        self.assertEqual(polygon.geom_type, "Polygon")
        self.assertFalse(polygon.is_empty)

    @requires_geo_dependencies
    def test_restore_grid_geometries_can_rebuild_from_map_coordinates(self):
        grid = [
            {
                "id": 1,
                "shapely_poly_wgs84": "not valid WKT",
                "wgs84_coords_map": [[48, 9], [48, 10], [49, 10], [49, 9]],
            }
        ]

        polygon = restore_grid_geometries(grid)[0]["shapely_poly_wgs84"]

        self.assertEqual(list(polygon.exterior.coords)[0], (9.0, 48.0))
        self.assertFalse(polygon.is_empty)

    def test_grid_params_dirty_helper(self):
        self.assertTrue(grid_params_are_dirty((3500, 20), None))
        self.assertTrue(grid_params_are_dirty(None, (3500, 20)))
        self.assertTrue(grid_params_are_dirty((3000, 20), (3500, 20)))
        self.assertTrue(grid_params_are_dirty((3500, 15), (3500, 20)))
        self.assertFalse(grid_params_are_dirty((3500, 20), (3500, 20)))

    @requires_geo_dependencies
    def test_generate_grid_data_contains_contract_fields(self):
        grid = generate_grid_map_data(48.78, 9.18, 1000, 2)

        self.assertGreater(len(grid), 0)
        first_cell = grid[0]
        self.assertEqual(first_cell["id"], 0)
        self.assertEqual(len(first_cell["wgs84_coords_map"]), 4)
        self.assertIn("shapely_poly_wgs84", first_cell)
        self.assertIn("utm_coords", first_cell)
        self.assertIn("utm_crs", first_cell)
        self.assertIsNone(first_cell["area_type"])

    @requires_geo_dependencies
    def test_subgrid_uses_area_divisions(self):
        grid = generate_grid_map_data(48.78, 9.18, 1000, 2)
        cell = grid[0]

        self.assertEqual(subgrid_division_for_area("PA"), 9)
        self.assertEqual(subgrid_division_for_area("IA1"), 3)
        self.assertEqual(subgrid_division_for_area("IA2"), 1)
        self.assertEqual(len(subgrid_map_coords_for_cell(cell, 9)), 81)
        self.assertEqual(len(subgrid_map_coords_for_cell(cell, 3)), 9)
        self.assertEqual(len(subgrid_map_coords_for_cell(cell, 1)), 1)

    @requires_geo_dependencies
    def test_subgrid_ia2_boundary_division(self):
        grid = generate_grid_map_data(48.78, 9.18, 1000, 2)
        cell = grid[0]
        cell["area_type"] = "IA2"

        division = subgrid_division_for_area("IA2")
        self.assertEqual(division, 1)

        coords = subgrid_map_coords_for_cell(cell, division)
        self.assertEqual(len(coords), 1)
        self.assertEqual(len(coords[0]), 4)

    def test_build_step3_payload_keeps_public_keys(self):
        selected_cells = create_selected_cells()
        grid = []

        payload = build_step3_payload("loc", 3500, 20, selected_cells, grid)

        self.assertEqual(set(payload), {"selected_loc", "cell_size_m", "radius_km", "selected_cells", "grid_map_data"})
        self.assertIs(payload["selected_cells"], selected_cells)
        self.assertIs(payload["grid_map_data"], grid)

    def test_parse_radius_km_preserves_kilometers(self):
        from core.app.app_qt.steps.step3_grid_area import Step3GridAreaWidget
        widget = Step3GridAreaWidget.__new__(Step3GridAreaWidget)
        self.assertEqual(widget._parse_radius_km("30"), 30.0)
        self.assertEqual(widget._parse_radius_km("50,5"), 50.5)
        self.assertEqual(widget._parse_radius_km("100"), 100.0)
        self.assertEqual(widget._parse_radius_km("150"), 150.0)
        self.assertEqual(widget._parse_radius_km("250"), 250.0)
        self.assertIsNone(widget._parse_radius_km("-10"))
        self.assertIsNone(widget._parse_radius_km("abc"))

    def test_oa_mode_and_adm2_layer_structure(self):
        from core.app.app_qt.steps.step3_grid_area import Step3GridAreaWidget
        widget = Step3GridAreaWidget.__new__(Step3GridAreaWidget)
        widget.current_mode = "OA"
        widget.grid_map_data = [{"id": 1, "area_type": "PA", "wgs84_coords_map": [(46.5, 6.6), (46.6, 6.6), (46.6, 6.7), (46.5, 6.7)]}]
    @requires_geo_dependencies
    def test_transfer_selected_cells_preserves_area_assignments(self):
        from core.app.app_core.grid import transfer_selected_cells
        grid1 = generate_grid_map_data(48.78, 9.18, 1000, 2)
        # Mark center cell as PA
        grid1[0]["area_type"] = "PA"
        # Mark another cell as IA1
        if len(grid1) > 1:
            grid1[1]["area_type"] = "IA1"

        # Shift location slightly (e.g. 300m)
        grid2 = generate_grid_map_data(48.782, 9.182, 1000, 2)
        selected_cells2 = transfer_selected_cells(grid1, grid2)

        self.assertTrue(len(selected_cells2["PA"]) >= 1)
        self.assertTrue(any(c.get("area_type") == "PA" for c in grid2))

    @requires_geo_dependencies
    def test_transfer_selected_cells_preserves_relative_pattern_on_shift(self):
        from core.app.app_core.grid import transfer_selected_cells
        grid1 = generate_grid_map_data(48.78, 9.18, 1000, 3)
        # Find cell at center (0,0) and neighbors
        center_cell = next(c for c in grid1 if c.get("rel_coord") == (0, 0))
        center_cell["area_type"] = "PA"
        right_cell = next(c for c in grid1 if c.get("rel_coord") == (1, 0))
        right_cell["area_type"] = "IA1"

        # Shift center position significantly (e.g. 5km away)
        grid2 = generate_grid_map_data(48.82, 9.22, 1000, 3)
        selected_cells2 = transfer_selected_cells(grid1, grid2)

        new_center = next(c for c in grid2 if c.get("rel_coord") == (0, 0))
        new_right = next(c for c in grid2 if c.get("rel_coord") == (1, 0))

        self.assertEqual(new_center.get("area_type"), "PA")
        self.assertEqual(new_right.get("area_type"), "IA1")
        self.assertIn(new_center["id"], selected_cells2["PA"])
        self.assertIn(new_right["id"], selected_cells2["IA1"])

    def test_floating_map_toolbar_structure(self):
        from core.app.app_qt.steps.step3_grid_area import FloatingMapToolbar
        toolbar = FloatingMapToolbar.__new__(FloatingMapToolbar)
        self.assertIsNotNone(toolbar)


if __name__ == "__main__":
    unittest.main()
