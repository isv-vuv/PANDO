import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from core.app.app_core.model_pipeline import (
    MODEL_DESCRIPTIONS,
    ModelPipeline,
    _resolve_output_path,
    inspect_model_parameters,
    materialize_runtime_model,
)
from core.app.app_core.processing import ProcessingRunResult


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, "core", "scripts", "qgis", "models")
SCRIPT_DIR = os.path.join(ROOT, "core", "scripts", "qgis", "scripts")


class _Registration:
    def __init__(self):
        self.unregistered = False

    def unregister(self):
        self.unregistered = True


class ModelPipelineTests(unittest.TestCase):
    def test_descriptions_match_real_model3_parameter_names(self):
        with tempfile.TemporaryDirectory() as project:
            pipeline = ModelPipeline(project, model_directory=MODEL_DIR)
            context = self._initial_context()
            for description in MODEL_DESCRIPTIONS:
                if description.key == "model5":
                    context.update(
                        poi_points="/phase-c/points.gpkg",
                        poi_polygons="/phase-c/polygons.gpkg",
                    )
                parameters, output_bindings = pipeline._parameters_for(description, context)
                available = inspect_model_parameters(os.path.join(MODEL_DIR, description.filename))
                self.assertTrue(set(parameters).issubset(available), description.filename)
                for output, _actual, path in output_bindings:
                    context[output.context_key] = path

    def test_chain_passes_results_and_calls_phase_c_between_models_4_and_5(self):
        calls = []
        registration = _Registration()

        def registrar(scripts, **_kwargs):
            self.assertIn("script:central_place_categorisation", scripts)
            self.assertIn("script:osmpoly_generator", scripts)
            self.assertIn("script:raster_neighborhood_max", scripts)
            return registration

        def runner(config, **_kwargs):
            calls.append((os.path.basename(config.model_path), dict(config.parameters)))
            # QGIS may return uppercase/display-style output keys; alias matching
            # must still feed the following model.
            return ProcessingRunResult(outputs={key.upper(): value for key, value in config.parameters.items()})

        def phase_c(context):
            self.assertIn("poly_paia1", context)
            self.assertIn("poly_study_area_paia1ia2", context)
            calls.append(("phase_c", dict(context)))
            return {"poi_points": "/phase-c/points.gpkg", "poi_polygons": "/phase-c/polygons.gpkg"}

        with tempfile.TemporaryDirectory() as project:
            result = ModelPipeline(
                project,
                model_directory=MODEL_DIR,
                script_directory=SCRIPT_DIR,
                runner=runner,
                script_registrar=registrar,
            ).run(self._initial_context(), phase_c_hook=phase_c)

        names = [call[0] for call in calls]
        self.assertEqual(
            names,
            [
                "Model1_DataPrep.model3",
                "Model2_ZoneClass.model3",
                "Model3_GridGen.model3",
                "Model3-4_GridAssign.model3",
                "Model4_TierAssign.model3",
                "phase_c",
                "Model5_UrbanCentrality.model3",
                "Model6_ZoneAssembler.model3",
            ],
        )
        model5_parameters = calls[6][1]
        self.assertEqual(model5_parameters["osm_points"], "/phase-c/points.gpkg")
        self.assertEqual(model5_parameters["osm_polygons"], "/phase-c/polygons.gpkg")
        model6_parameters = calls[7][1]
        self.assertEqual(model6_parameters["zone_adm2_typeno_model2"], result.context["zone_adm2_typeno"])
        self.assertEqual(model6_parameters["zone_paia1ia2_model5"], result.context["zone_pa_ia1_ia2"])
        self.assertTrue(
            os.path.normpath(result.context["zones"]).endswith(
                os.path.join("model6_ZoneAssembler", "zones.gpkg")
            )
        )
        self.assertTrue(registration.unregistered)

    def test_reusable_model_outputs_skip_runner_and_restore_context(self):
        registration = _Registration()
        model1 = next(item for item in MODEL_DESCRIPTIONS if item.key == "model1")
        reused = {"adm0": "/saved/adm0.gpkg", "pop_raster_corr": "/saved/pop.tif"}
        callbacks = []

        def runner(*_args, **_kwargs):
            self.fail("A reusable model must not invoke the QGIS runner")

        result = ModelPipeline(
            "/project",
            model_directory=MODEL_DIR,
            runner=runner,
            script_registrar=lambda *_args, **_kwargs: registration,
            descriptions=(model1,),
        ).run(
            {},
            reusable_outputs={"model1": reused},
            on_model_reused=lambda description, index, total, context: callbacks.append(
                (description.key, index, total, dict(context))
            ),
        )

        self.assertEqual(result.context["adm0"], "/saved/adm0.gpkg")
        self.assertEqual(callbacks[0][:3], ("model1", 1, 1))
        self.assertTrue(registration.unregistered)

    def test_default_model_and_script_directories_use_core_qgis_layout(self):
        with tempfile.TemporaryDirectory() as project:
            pipeline = ModelPipeline(project)

        self.assertEqual(pipeline.model_directory, MODEL_DIR)
        self.assertEqual(pipeline.script_directory, SCRIPT_DIR)
        self.assertTrue(os.path.isfile(os.path.join(SCRIPT_DIR, "Model2_CityCategorisation.py")))
        self.assertTrue(os.path.isfile(os.path.join(SCRIPT_DIR, "Model4_Export_poly.py")))
        self.assertTrue(os.path.isfile(os.path.join(SCRIPT_DIR, "Model5_RasterNeighborhood.py")))

    def test_model5_uses_portable_neighborhood_maximum_without_grass(self):
        model_path = os.path.join(MODEL_DIR, "Model5_UrbanCentrality.model3")
        with open(model_path, encoding="utf-8") as model_file:
            model_text = model_file.read()

        self.assertIn("script:raster_neighborhood_max", model_text)
        self.assertNotIn('value="grass:r.neighbors"', model_text)

    def test_model1_uses_qgis4_extent_format_and_valid_zero_dummy_rasters(self):
        root = ET.parse(os.path.join(MODEL_DIR, "Model1_DataPrep.model3")).getroot()

        def parameter_value(component_id, parameter_name):
            component = next(item for item in root.iter("Option") if item.get("name") == component_id)
            params = next(item for item in component if item.get("name") == "params")
            parameter = next(item for item in params if item.get("name") == parameter_name)
            value = next(item for item in parameter.iter("Option") if item.get("name") in {"expression", "static_value"})
            return value.get("value")

        extent_expression = parameter_value("gdal:warpreproject_1", "TARGET_EXTENT")
        self.assertNotIn("_EXTENT", extent_expression)
        for coordinate in ("X_MIN", "X_MAX", "Y_MIN", "Y_MAX"):
            self.assertIn(coordinate, extent_expression)
        self.assertEqual(parameter_value("gdal:rasterize_4", "EXTRA"), "-at")
        self.assertEqual(parameter_value("gdal:rasterize_3", "DATA_TYPE"), "6")
        self.assertEqual(parameter_value("gdal:rasterize_4", "DATA_TYPE"), "6")
        self.assertEqual(parameter_value("gdal:rastercalculator_3", "NO_DATA"), "-9999")

        refactor = next(item for item in root.iter("Option") if item.get("name") == "native:refactorfields_1")
        fid_field = next(
            item
            for item in refactor.iter("Option")
            if any(
                child.get("name") == "name" and child.get("value") == "fid"
                for child in item
            )
        )
        fid_properties = {item.get("name"): item.get("value") for item in fid_field}
        self.assertEqual(fid_properties["type"], "4")
        self.assertEqual(fid_properties["type_name"], "int8")

    def test_model3_output_identifier_fields_have_supported_types(self):
        root = ET.parse(os.path.join(MODEL_DIR, "Model3_GridGen.model3")).getroot()
        refactor = next(item for item in root.iter("Option") if item.get("name") == "native:refactorfields_6")
        fields = {}
        for item in refactor.iter("Option"):
            properties = {child.get("name"): child.get("value") for child in item}
            if properties.get("name") in {"NO_E1", "NO_E0"}:
                fields[properties["name"]] = properties

        self.assertEqual(set(fields), {"NO_E1", "NO_E0"})
        for properties in fields.values():
            self.assertEqual(properties["type"], "2")
            self.assertEqual(properties["type_name"], "integer")

    def test_model6_indexes_both_layers_before_large_population_spatial_join(self):
        root = ET.parse(os.path.join(MODEL_DIR, "Model6_ZoneAssembler.model3")).getroot()

        def child_input(component_id, parameter_name="INPUT"):
            component = next(item for item in root.iter("Option") if item.get("name") == component_id)
            params = next(item for item in component if item.get("name") == "params")
            parameter = next(item for item in params if item.get("name") == parameter_name)
            return next(item for item in parameter.iter("Option") if item.get("name") == "child_id").get("value")

        self.assertEqual(child_input("native:createspatialindex_2"), "native:pixelstopoints_3")
        self.assertEqual(child_input("native:createspatialindex_3"), "native:zonalstatisticsfb_2")
        self.assertEqual(child_input("native:joinattributesbylocation_10"), "native:createspatialindex_2")
        self.assertEqual(
            child_input("native:joinattributesbylocation_10", "JOIN"),
            "native:createspatialindex_3",
        )

    def test_defaults_and_legacy_aliases_are_resolved(self):
        with tempfile.TemporaryDirectory() as project:
            pipeline = ModelPipeline(project, model_directory=MODEL_DIR)
            model2 = next(item for item in MODEL_DESCRIPTIONS if item.key == "model2")
            context = {
                "local_crs": "EPSG:25832",
                "adm2": "/outputs/adm2.gpkg",
                "pop_raster_corr": "/outputs/pop.tif",
                "osm_cities": "/osm/cities.gpkg",
            }
            parameters, _outputs = pipeline._parameters_for(model2, context)

        self.assertEqual(parameters["distance_tolerance_"], 10)
        self.assertEqual(parameters["population_tolerance_"], 10)
        self.assertEqual(parameters["processed_adm2_from_qgis_model_no1"], "/outputs/adm2.gpkg")
        self.assertEqual(parameters["minimum_population_for_metropolitain_regions_level_0_centralities"], 500000)

    def test_legacy_and_nested_configuration_keys_are_accepted(self):
        with tempfile.TemporaryDirectory() as project:
            pipeline = ModelPipeline(project, model_directory=MODEL_DIR)
            model2 = next(item for item in MODEL_DESCRIPTIONS if item.key == "model2")
            parameters, _outputs = pipeline._parameters_for(
                model2,
                {
                    "local_crs": "EPSG:25832",
                    "processed_adm2_from_qgis_model_no1": "/legacy/adm2.gpkg",
                    "pop_raster_corr_layer_from_model_no1": "/legacy/pop.tif",
                    "osm_city_coordinates_of_the_entire_country": "/legacy/cities.gpkg",
                    "Model2_params": {"distance_tolerance_": 17},
                },
            )
        self.assertEqual(parameters["processed_adm2_from_qgis_model_no1"], "/legacy/adm2.gpkg")
        self.assertEqual(parameters["distance_tolerance_"], 17)

    def test_runtime_copy_removes_windows_styles_and_normalises_zone_type(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model2_target = os.path.join(temp_dir, "Model2.model3")
            disabled = materialize_runtime_model(
                os.path.join(MODEL_DIR, "Model2_ZoneClass.model3"),
                model2_target,
                style_directory=os.path.join(temp_dir, "missing-styles"),
            )
            self.assertEqual(disabled, 2)
            with open(model2_target, encoding="utf-8") as model_file:
                model2_text = model_file.read()
            self.assertNotIn(r"C:\Users", model2_text)

            model4_target = os.path.join(temp_dir, "Model4.model3")
            materialize_runtime_model(
                os.path.join(MODEL_DIR, "Model4_TierAssign.model3"),
                model4_target,
                normalise_zone_type=True,
            )
            with open(model4_target, encoding="utf-8") as model_file:
                model4_text = model_file.read()
            self.assertNotIn("Zone Type", model4_text)
            self.assertIn("ZoneType", model4_text)
            self.assertEqual(ET.parse(model4_target).getroot().tag, "Option")

    def test_poly_output_directory_resolves_to_generated_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_directory = os.path.join(temp_dir, "bound_pa_ia1.poly")
            os.makedirs(output_directory)
            generated = os.path.join(output_directory, "poly_paia1.poly")
            with open(generated, "w", encoding="utf-8") as poly_file:
                poly_file.write("poly_paia1\nEND\n")

            resolved = _resolve_output_path(
                output_directory,
                output_directory,
                "poly_paia1",
            )

            self.assertEqual(resolved, output_directory)
            self.assertTrue(os.path.isfile(output_directory))

    def test_post_process_exports_and_styles_reprojects_zones_to_local_crs(self):
        try:
            import geopandas as gpd
            from shapely.geometry import Polygon
        except ImportError:
            self.skipTest("geopandas not installed")

        from core.app.app_core.pipeline import post_process_exports_and_styles

        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = os.path.join(temp_dir, "input")
            model6_dir = os.path.join(temp_dir, "processed", "qgis_output", "model6_ZoneAssembler")
            os.makedirs(input_dir, exist_ok=True)
            os.makedirs(model6_dir, exist_ok=True)

            local_crs = "EPSG:32632"
            with open(os.path.join(input_dir, "local_crs.txt"), "w", encoding="utf-8") as f:
                f.write(local_crs)

            gdf = gpd.GeoDataFrame(
                [{"name": "Zone1"}],
                geometry=[Polygon([(9.0, 48.0), (9.1, 48.0), (9.1, 48.1), (9.0, 48.1)])],
                crs="EPSG:4326",
            )
            zones_path = os.path.join(model6_dir, "zones.gpkg")
            gdf.to_file(zones_path, driver="GPKG")

            post_process_exports_and_styles(temp_dir)

            reprojected_gdf = gpd.read_file(zones_path)
            self.assertEqual(str(reprojected_gdf.crs).upper(), "EPSG:32632")

            visum_shp = os.path.join(temp_dir, "processed", "visum", "shapefile", "Zones", "zones.shp")
            self.assertTrue(os.path.isfile(visum_shp))
            shp_gdf = gpd.read_file(visum_shp)
            self.assertEqual(str(shp_gdf.crs).upper(), "EPSG:32632")

    @staticmethod
    def _initial_context():
        return {
            "iso_country_codes": "DEU",
            "ghs_pop_raster": "/global/ghs.tif",
            "pop_local": "/input/pop_local.gpkg",
            "pop_zero_osm": "/osm/pop_zero.gpkg",
            "gadm_adm0": "/global/adm0.gpkg",
            "gadm_adm1": "/global/adm1.gpkg",
            "gadm_adm2": "/global/adm2.gpkg",
            "gadm_adm3": "/global/adm3.gpkg",
            "local_crs": "EPSG:25832",
            "osm_cities": "/osm/cities.gpkg",
            "center_point": "/input/center.gpkg",
            "zone_type_selected": "/input/zones.gpkg",
        }


if __name__ == "__main__":
    unittest.main()
