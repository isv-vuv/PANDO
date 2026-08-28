"""UI-independent orchestration of the PANDO QGIS model chain."""

from __future__ import annotations

import os
import re
import json
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from threading import Event
from typing import Callable, Mapping, Optional, Sequence

from core.app.app_core.processing import (
    ProcessingRunResult,
    QgisModelRunConfig,
    register_processing_scripts,
    run_qgis_model,
)
from core.app.app_core.project import tool_root

MODEL_ORDER = ("model1", "model2", "model3", "model3_4", "model4", "model5", "model6")
QGIS_MODELS_SUBDIR = os.path.join("core", "scripts", "qgis", "models")
QGIS_SCRIPTS_SUBDIR = os.path.join("core", "scripts", "qgis", "scripts")

MODEL2_DEFAULTS = {
    "minimum_population_level_0": 500000,
    "minimum_population_level_1": 200000,
    "minimum_population_level_2": 100000,
    "population_tolerance": 10,
    "distance_tolerance": 10,
    "dual_centres_search_radius_km": 5,
    "dual_centres_population_tolerance": 20,
}
MODEL3_DEFAULTS = {"grid_size_e0_m": 4500, "minimum_extent_radius_km": 30}
MODEL5_DEFAULTS = {
    "minimum_distance_level_3_4_m": 500,
    "minimum_distance_level_3_m": 1500,
    "minimum_distance_level_4_m": 700,
    "minimum_intensity_level_3": 10,
    "minimum_intensity_level_4": 7,
}


def load_parameter_defaults(model_name: str, *, root: Optional[str] = None) -> dict[str, object]:
    """Load the shipped machine-readable defaults for Models 2, 3, or 5."""
    model_key = str(model_name).casefold().replace("_", "")
    filenames = {
        "model2": "Model2_defaults.json",
        "model3": "Model3_defaults.json",
        "model5": "Model5_defaults.json",
    }
    if model_key not in filenames:
        raise ValueError(f"No parameter defaults are defined for {model_name}")
    root_path = root or tool_root()
    path = os.path.join(root_path, "core", "scripts", "qgis", "param_defaults", filenames[model_key])
    with open(path, "r", encoding="utf-8") as defaults_file:
        payload = json.load(defaults_file)
    if not isinstance(payload, dict):
        raise ValueError(f"Parameter defaults must be a JSON object: {path}")
    return payload


@dataclass(frozen=True)
class ParameterBinding:
    context_key: str
    aliases: tuple[str, ...]
    default: object = None
    required: bool = True


@dataclass(frozen=True)
class ModelOutput:
    context_key: str
    aliases: tuple[str, ...]
    filename: str


@dataclass(frozen=True)
class ModelDescription:
    key: str
    name: str
    filename: str
    output_directory: str
    inputs: tuple[ParameterBinding, ...]
    outputs: tuple[ModelOutput, ...]
    dependencies: tuple[str, ...] = ()
    required_scripts: tuple[str, ...] = ()


@dataclass
class ModelPipelineResult:
    context: dict[str, object]
    model_results: dict[str, dict[str, object]] = field(default_factory=dict)


def _binding(key: str, actual: str, *aliases: str, default=None, required=True) -> ParameterBinding:
    return ParameterBinding(key, (actual, *aliases), default, required)


def _output(key: str, actual: str, filename: str, *aliases: str) -> ModelOutput:
    return ModelOutput(key, (actual, *aliases), filename)


MODEL_DESCRIPTIONS: tuple[ModelDescription, ...] = (
    ModelDescription(
        "model1", "Modell 1 – Datenvorbereitung", "Model1_DataPrep.model3", "model1_DataPrep",
        (
            _binding("iso_country_codes", "iso_country_code_iso_31661_3_symbols_commaseperated_eg_phl_idn_tha"),
            _binding("ghs_pop_raster", "pop_ghspop_raster_data_worldwide_wgs84_3_arcsec"),
            _binding("pop_local", "pop_reference_for_district_level_correction_eg_barangaytambon_level"),
            _binding("pop_zero_osm", "pop_uninhabited_from_overpass_query"),
            _binding("pop_local_field", "select_pop_field_from_reference_layer", default="POP"),
            *tuple(_binding(f"gadm_adm{i}", f"world_shapefile_adm{i}") for i in range(4)),
        ),
        (
            _output("adm0", "adm0", "adm0.gpkg", "ADM0"),
            _output("adm1", "adm1", "adm1.gpkg", "ADM1"),
            _output("adm2", "adm2", "adm2.gpkg", "ADM2"),
            _output("adm3", "adm3", "adm3.gpkg", "ADM3"),
            _output("pop_raster_corr", "pop_raster_corr", "pop_raster_corr.tif", "Pop_raster_corr"),
        ),
    ),
    ModelDescription(
        "model2", "Modell 2 – Zonenklassifikation", "Model2_ZoneClass.model3", "model2_ZoneClass",
        (
            _binding("local_crs", "local_crs"),
            _binding("adm2", "processed_adm2_from_qgis_model_no1", "adm2"),
            _binding("pop_raster_corr", "pop_raster_corr_layer_from_model_no1"),
            _binding("osm_cities", "osm_city_coordinates_of_the_entire_country"),
            _binding("minimum_population_level_0", "minimum_population_for_metropolitain_regions_level_0_centralities", default=500000),
            _binding("minimum_population_level_1", "minimum_population_for_higherorder_centres_level_1_centralities", default=200000),
            _binding("minimum_population_level_2", "minimum_population_for_middleorder_centres_level_2_centralities", default=100000),
            _binding("population_tolerance", "population_tolerance_", "population_tolerance", default=10),
            _binding("distance_tolerance", "distance_tolerance_", "distance_tolerance", default=10),
            _binding("dual_centres_search_radius_km", "dual_centres_search_radius_km", default=5),
            _binding("dual_centres_population_tolerance", "dual_centres_population_tolerance_", default=20),
        ),
        (
            _output("central_place_points", "central_place_points", "central_place_points.gpkg", "CentralPlacePoints"),
            _output("zone_adm2_typeno", "zone_adm2_typeno", "zone_adm2_typeno.gpkg", "ZoneAdm2Typeno"),
        ),
        ("model1",), ("script:central_place_categorisation",),
    ),
    ModelDescription(
        "model3", "Modell 3 – Grid-Generierung", "Model3_GridGen.model3", "model3_GridGen",
        (
            _binding("center_point", "city_center_location", "center_point"),
            _binding("local_crs", "local_crs"),
            _binding("minimum_extent_radius_km", "minimum_extent_radius_paia1ia2_km", default=30),
            _binding("grid_size_e0_m", "grid_size_e0_m_impacts_distance_between_level_3__4_centralities", default=4500),
            _binding("pop_raster_corr", "pop_raster_corr_from_model_no1"),
        ),
        (_output("e0", "e0", "e0.gpkg", "E0"), _output("e1", "e1", "e1.gpkg", "E1"), _output("e2", "e2", "e2.gpkg", "E2")),
        ("model1",),
    ),
    ModelDescription(
        "model3_4", "Modell 3–4 – Grid-Zuweisung", "Model3-4_GridAssign.model3", "model3-4_GridAssign",
        (_binding("e0", "e0"), _binding("zone_type_selected", "zone_type_selected")),
        (_output("e0_att", "e0_att", "e0_att.gpkg", "E0_Att"),), ("model3",),
    ),
    ModelDescription(
        "model4", "Modell 4 – Tier-Zuweisung", "Model4_TierAssign.model3", "model4_TierAssign",
        (
            _binding("e0_att", "e0", "e0_att"),
            _binding("e1", "e1"), _binding("e2", "e2"),
            _binding("zone_type_field", "select_zone_type_field_must_be_set_to_pa_ia1_ia2_before_manually", default="ZoneType"),
        ),
        (
            _output("extent_paia1", "extent_paia1_visual_check", "extent_paia1.gpkg"),
            _output("extent_study_area", "extent_study_area_paia1ia2_visual_check", "extent_study_area.gpkg"),
            _output("grid_zone_pa_ia1_ia2", "grid_zone_pa_ia1_ia2", "grid_zone_pa_ia1_ia2.gpkg"),
            _output("mainzone_pa_ia1_ia2", "mainzone_pa_ia1_ia2", "mainzone_pa_ia1_ia2.gpkg"),
            _output("poly_paia1", "poly_paia1", "bound_pa_ia1.poly"),
            _output("poly_study_area_paia1ia2", "poly_study_area_paia1ia2", "bound_pa_ia1_ia2.poly"),
        ),
        ("model3_4",), ("script:osmpoly_generator",),
    ),
    ModelDescription(
        "model5", "Modell 5 – Urbane Zentralität", "Model5_UrbanCentrality.model3", "model5_UrbanCentrality",
        (
            _binding("grid_zone_pa_ia1_ia2", "grid_cells_zone_pa_ia1_ia2"),
            _binding("local_crs", "local_crs"),
            _binding("poi_points", "osm_points_prepared"),
            _binding("minimum_distance_level_3_4_m", "minimum_distance_between_level_3_and_level_4_centralities_m", default=500),
            _binding("minimum_distance_level_3_m", "minimum_distance_between_level_3_centralities_if_grid_seperation_is_insufficient_m", default=1500),
            _binding("minimum_distance_level_4_m", "minimum_distance_between_level_4_centralities_if_grid_seperation_is_insufficient_m", default=700),
            _binding("minimum_intensity_level_3", "minimum_intensity_threshold_for_level_3_centralities_total_intensity_values_range_from_0_to_25", default=10),
            _binding("minimum_intensity_level_4", "minimum_intensity_threshold_for_level_4_centralities_total_intensity_values_range_from_0_to_25", default=7),
        ),
        tuple(
            [_output("zone_pa_ia1_ia2", "zone_pa_ia1_ia2", "zone_pa_ia1_ia2.gpkg")]
            + [
                _output(f"sector_{sector}_{kind}", f"sector_{sector}_{'intensity_analysis' if kind == 'intensity' else 'points'}",
                        f"sector_{sector}_{kind}.{'tif' if kind == 'intensity' else 'gpkg'}")
                for sector in ("all", "education", "government", "healthcare", "leisure", "retail")
                for kind in ("points", "intensity")
            ]
        ),
        ("model4",), ("script:raster_neighborhood_max",),
    ),
    ModelDescription(
        "model6", "Modell 6 – Zonenassemblierung", "Model6_ZoneAssembler.model3", "model6_ZoneAssembler",
        (
            _binding("adm1", "adm1_mainzone_model1"),
            _binding("local_crs", "local_crs"),
            _binding("mainzone_pa_ia1_ia2", "mainzone_paia1ia2_model4"),
            _binding("osm_cities", "osm_city_coordinates_of_the_entire_country"),
            _binding("pop_raster_corr", "pop_raster_corr"),
            _binding("zone_adm2_typeno", "zone_adm2_typeno_model2"),
            _binding("zone_pa_ia1_ia2", "zone_paia1ia2_model5"),
        ),
        (
            _output("zones", "zones", "zones.gpkg"),
            _output("mainzones", "mainzones", "mainzones.gpkg"),
            _output("zone_centroids", "zone_centroids", "zone_centroids.gpkg"),
        ),
        ("model1", "model2", "model4", "model5"),
    ),
)


def inspect_model_parameters(model_path: str) -> dict[str, str]:
    """Return parameter name -> QGIS parameter type directly from a .model3."""
    root = ET.parse(model_path).getroot()
    definitions = next(
        (item for item in root.iter("Option") if item.get("name") == "parameterDefinitions"),
        None,
    )
    if definitions is None:
        raise ValueError(f"No parameterDefinitions in QGIS model: {model_path}")
    result = {}
    for definition in definitions:
        values = {item.get("name"): item.get("value") for item in definition if item.tag == "Option"}
        name = values.get("name") or definition.get("name")
        if name:
            result[name] = values.get("parameter_type", "")
    return result


class ModelPipeline:
    def __init__(
        self,
        project_path: str,
        *,
        model_directory: Optional[str] = None,
        script_directory: Optional[str] = None,
        style_directory: Optional[str] = None,
        runner: Callable[..., ProcessingRunResult] = run_qgis_model,
        script_registrar: Callable[..., object] = register_processing_scripts,
        descriptions: Sequence[ModelDescription] = MODEL_DESCRIPTIONS,
    ):
        root_path = tool_root()
        self.project_path = os.path.abspath(project_path)
        self.model_directory = os.path.abspath(
            model_directory or os.path.join(root_path, QGIS_MODELS_SUBDIR)
        )
        self.script_directory = os.path.abspath(
            script_directory or os.path.join(root_path, QGIS_SCRIPTS_SUBDIR)
        )
        self.style_directory = os.path.abspath(
            style_directory or os.path.join(root_path, "core", "scripts", "qgis", "styles")
        )
        self.runner = runner
        self.script_registrar = script_registrar
        self.descriptions = tuple(descriptions)

    def run(
        self,
        initial_context: Mapping[str, object],
        *,
        phase_c_hook: Optional[Callable[[dict[str, object]], Optional[Mapping[str, object]]]] = None,
        stop_event: Optional[Event] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int], None]] = None,
        on_progress_text: Optional[Callable[[str], None]] = None,
        reusable_outputs: Optional[Mapping[str, Mapping[str, object]]] = None,
        on_model_started: Optional[Callable[[ModelDescription, int, int], None]] = None,
        on_model_reused: Optional[
            Callable[[ModelDescription, int, int, Mapping[str, object]], None]
        ] = None,
        on_model_finished: Optional[
            Callable[[ModelDescription, Mapping[str, object], Mapping[str, object]], None]
        ] = None,
        on_file_locked: Optional[Callable[[str], bool]] = None,
    ) -> ModelPipelineResult:
        context = dict(initial_context)
        model_results: dict[str, dict[str, object]] = {}
        scripts = {
            "script:central_place_categorisation": os.path.join(self.script_directory, "Model2_CityCategorisation.py"),
            "script:osmpoly_generator": os.path.join(self.script_directory, "Model4_Export_poly.py"),
            "script:raster_neighborhood_max": os.path.join(
                self.script_directory,
                "Model5_RasterNeighborhood.py",
            ),
        }
        registration = self.script_registrar(scripts, on_log=on_log)
        reusable_outputs = reusable_outputs or {}

        def _safe_remove(target: str) -> None:
            if not os.path.exists(target):
                return
            while os.path.exists(target):
                try:
                    if os.path.isdir(target):
                        shutil.rmtree(target)
                    else:
                        os.remove(target)
                    break
                except (PermissionError, OSError) as exc:
                    is_lock_err = (
                        isinstance(exc, PermissionError)
                        or getattr(exc, "winerror", None) in (32, 5)
                        or "used by another process" in str(exc).lower()
                        or "verwendet wird" in str(exc).lower()
                    )
                    if is_lock_err and on_file_locked:
                        if on_log:
                            on_log(f"Warning: Output file '{os.path.basename(target)}' is locked by another process. Prompting user...")
                        retry = on_file_locked(target)
                        if retry:
                            import time
                            time.sleep(0.5)
                            continue
                        else:
                            try:
                                from core.locales.localizer import localizer
                                err_msg = localizer.get_string("file_locked_interrupted_error", filename=os.path.basename(target))
                            except Exception:
                                err_msg = f"Processing aborted: File '{os.path.basename(target)}' is locked."
                            raise InterruptedError(err_msg)
                    elif is_lock_err:
                        import time
                        time.sleep(0.5)
                        try:
                            if os.path.isdir(target):
                                shutil.rmtree(target)
                            else:
                                os.remove(target)
                            break
                        except Exception:
                            try:
                                from core.locales.localizer import localizer
                                err_msg = localizer.get_string("file_locked_permission_error", filename=os.path.basename(target), filepath=target)
                            except Exception:
                                err_msg = f"File '{os.path.basename(target)}' is locked.\nPath: {target}"
                            raise PermissionError(err_msg) from exc
                    else:
                        if on_log:
                            on_log(f"Note: Could not remove previous output file {target}: {exc}")
                        break

        def _sanitize_geopackage_geometries(gpkg_path: str, expected_kind: str = "polygon") -> bool:
            if not gpkg_path or not os.path.isfile(gpkg_path):
                return False
            try:
                import geopandas as gpd
                from shapely.geometry import Polygon, MultiPolygon, Point, MultiPoint

                gdf = gpd.read_file(gpkg_path)
                if gdf.empty:
                    return True

                has_collections = any(
                    geom is not None and getattr(geom, "geom_type", "") == "GeometryCollection"
                    for geom in gdf.geometry
                )
                if not has_collections:
                    return True

                if on_log:
                    on_log(f"Bereinige GeometryCollection-Artefakte in {os.path.basename(gpkg_path)} ...")

                cleaned_geoms = []
                valid_mask = []
                for geom in gdf.geometry:
                    if geom is None or geom.is_empty:
                        valid_mask.append(False)
                        continue
                    if expected_kind == "polygon":
                        if geom.geom_type in ("Polygon", "MultiPolygon"):
                            cleaned_geoms.append(geom)
                            valid_mask.append(True)
                        elif geom.geom_type == "GeometryCollection":
                            polys = [p for g in geom.geoms for p in (g.geoms if isinstance(g, MultiPolygon) else [g]) if isinstance(p, Polygon)]
                            if polys:
                                cleaned_geoms.append(polys[0] if len(polys) == 1 else MultiPolygon(polys))
                                valid_mask.append(True)
                            else:
                                valid_mask.append(False)
                        else:
                            valid_mask.append(False)
                    elif expected_kind == "point":
                        if geom.geom_type in ("Point", "MultiPoint"):
                            cleaned_geoms.append(geom)
                            valid_mask.append(True)
                        elif geom.geom_type == "GeometryCollection":
                            pts = [p for g in geom.geoms for p in (g.geoms if isinstance(g, MultiPoint) else [g]) if isinstance(p, Point)]
                            if pts:
                                cleaned_geoms.append(pts[0] if len(pts) == 1 else MultiPoint(pts))
                                valid_mask.append(True)
                            else:
                                valid_mask.append(False)
                        else:
                            valid_mask.append(False)

                gdf_clean = gdf[valid_mask].copy()
                gdf_clean["geometry"] = cleaned_geoms
                gdf_clean.to_file(gpkg_path, driver="GPKG", layer_options={"SPATIAL_INDEX": "YES"})
                return True
            except Exception:
                return False

        try:
            for index, description in enumerate(self.descriptions, 1):
                if stop_event and stop_event.is_set():
                    raise InterruptedError("QGIS model pipeline stopped by user")
                if description.key == "model5":
                    if phase_c_hook:
                        updates = phase_c_hook(dict(context))
                        if updates:
                            context.update(updates)
                    for gpkg_key, expected_kind in (
                        ("poi_polygons", "polygon"),
                        ("osm_polygons", "polygon"),
                        ("study_area_polygons", "polygon"),
                        ("poi_points", "point"),
                        ("osm_points", "point"),
                        ("study_area_points", "point"),
                        ("grid_zone_pa_ia1_ia2", "polygon"),
                    ):
                        pv = str(context.get(gpkg_key) or "")
                        if pv and os.path.isfile(pv):
                            _sanitize_geopackage_geometries(pv, expected_kind=expected_kind)
                reused = dict(reusable_outputs.get(description.key) or {})
                if reused:
                    context.update(reused)
                    model_results[description.key] = reused
                    if on_model_reused:
                        on_model_reused(
                            description,
                            index,
                            len(self.descriptions),
                            dict(context),
                        )
                    continue
                if on_model_started:
                    on_model_started(description, index, len(self.descriptions))
                parameters, output_bindings = self._parameters_for(description, context)

                # Remove any existing output files from previous runs to prevent GDAL/OGR 'already exists' errors
                for _out, _act, fixed_path in output_bindings:
                    _safe_remove(fixed_path)
                    for aux_ext in ("-wal", "-shm", ".aux.xml", ".tmp"):
                        aux_path = fixed_path + aux_ext
                        if os.path.exists(aux_path):
                            _safe_remove(aux_path)

                runtime_model = self._runtime_model(description, on_log=on_log)
                try:
                    from core.locales.localizer import localizer
                    model_display = localizer.get_string(f"model_name_{description.key}", default=description.name)
                except Exception:
                    model_display = description.name

                if on_log:
                    on_log(f"Running {model_display}")
                result = self.runner(
                    QgisModelRunConfig(
                        model_path=runtime_model,
                        parameters=parameters,
                    ),
                    stop_event=stop_event,
                    on_log=on_log,
                    on_progress=on_progress,
                    on_progress_text=on_progress_text,
                )
                raw_outputs = dict(result.outputs or {})
                model_results[description.key] = raw_outputs
                for output, actual_name, fixed_path in output_bindings:
                    value = _lookup_output(raw_outputs, output.aliases + (actual_name,))
                    context[output.context_key] = _resolve_output_path(
                        value,
                        fixed_path,
                        output.context_key,
                    )
                if on_model_finished:
                    on_model_finished(description, raw_outputs, dict(context))
        finally:
            registration.unregister()
        return ModelPipelineResult(context, model_results)

    def _runtime_model(
        self,
        description: ModelDescription,
        *,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> str:
        project_model = os.path.join(self.project_path, "qgis_models", description.filename)
        source = project_model if os.path.isfile(project_model) else os.path.join(self.model_directory, description.filename)
        target = os.path.join(self.project_path, "temp", "qgis_models", description.filename)
        disabled_styles = materialize_runtime_model(
            source,
            target,
            style_directory=self.style_directory,
            normalise_zone_type=description.key in {"model3", "model3_4", "model4", "model5", "model6"},
        )
        if disabled_styles and on_log:
            on_log(
                f"{description.name}: {disabled_styles} optionale Style-Schritte deaktiviert "
                "(keine portablen QML-Dateien gefunden)"
            )
        return target

    def _parameters_for(
        self, description: ModelDescription, context: Mapping[str, object]
    ) -> tuple[dict[str, object], list[tuple[ModelOutput, str, str]]]:
        model_path = os.path.join(self.model_directory, description.filename)
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"QGIS model not found: {model_path}")
        available = inspect_model_parameters(model_path)
        parameters: dict[str, object] = {}
        missing = []
        for binding in description.inputs:
            actual = _resolve_alias(binding.aliases, available)
            if actual is None:
                raise ValueError(f"{description.filename}: no parameter matching {binding.aliases!r}")
            value = _context_value(context, description.key, binding)
            if value is None or value == "":
                if binding.required:
                    missing.append(binding.context_key)
                continue
            parameters[actual] = value
        if missing:
            raise ValueError(f"{description.name}: missing required inputs: {', '.join(missing)}")

        output_dir = os.path.join(self.project_path, "processed", "qgis_output", description.output_directory)
        os.makedirs(output_dir, exist_ok=True)
        output_bindings = []
        for output in description.outputs:
            actual = _resolve_alias(output.aliases, available)
            if actual is None:
                raise ValueError(f"{description.filename}: no output matching {output.aliases!r}")
            path = os.path.join(output_dir, output.filename)
            parameters[actual] = path
            output_bindings.append((output, actual, path))
        return parameters, output_bindings


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _resolve_alias(aliases: Sequence[str], available: Mapping[str, object]) -> Optional[str]:
    for alias in aliases:
        if alias in available:
            return alias
    normalised = {_normalise(name): name for name in available}
    for alias in aliases:
        match = normalised.get(_normalise(alias))
        if match:
            return match
    return None


def _lookup_output(outputs: Mapping[str, object], aliases: Sequence[str]) -> object:
    actual = _resolve_alias(aliases, outputs)
    return outputs.get(actual) if actual else None


def _context_value(
    context: Mapping[str, object],
    model_key: str,
    binding: ParameterBinding,
) -> object:
    aliases = (binding.context_key, *binding.aliases)
    actual = _resolve_alias(aliases, context)
    if actual is not None:
        return context[actual]
    for container_name in (
        f"{model_key}_parameters",
        f"{model_key}_params",
        f"{model_key.replace('_', '')}_parameters",
        f"{model_key.replace('_', '')}_params",
    ):
        container_key = _resolve_alias((container_name,), context)
        nested = context.get(container_key) if container_key else None
        if isinstance(nested, Mapping):
            actual = _resolve_alias(aliases, nested)
            if actual is not None:
                return nested[actual]
    return binding.default


def _path_from_result(value: object) -> Optional[str]:
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, str) and value:
        return value
    source = getattr(value, "source", None)
    if callable(source):
        path = source()
        return path if isinstance(path, str) and path else None
    return None


def _resolve_output_path(value: object, fixed_path: str, context_key: str) -> str:
    """Resolve file outputs returned as directories by legacy QGIS scripts, placing poly files directly in fixed_path."""

    path = _path_from_result(value) or fixed_path
    if not fixed_path.casefold().endswith(".poly"):
        return path

    if os.path.isfile(fixed_path):
        return fixed_path

    if os.path.isdir(path):
        candidates = sorted(
            entry.path
            for entry in os.scandir(path)
            if entry.is_file() and entry.name.casefold().endswith(".poly")
        )
        if not candidates:
            raise RuntimeError(f"Der POLY-Exportordner enthält keine .poly-Datei: {path}")
        chosen = candidates[0]
        temp_poly = fixed_path + ".tmp"
        shutil.copy2(chosen, temp_poly)
        shutil.rmtree(path, ignore_errors=True)
        if os.path.exists(fixed_path):
            if os.path.isdir(fixed_path):
                shutil.rmtree(fixed_path, ignore_errors=True)
            else:
                os.remove(fixed_path)
        os.rename(temp_poly, fixed_path)
        return fixed_path

    return path


def materialize_runtime_model(
    source_path: str,
    target_path: str,
    *,
    style_directory: Optional[str] = None,
    normalise_zone_type: bool = False,
) -> int:
    """Create a portable runtime copy while leaving the editable source intact.

    Model outputs remain fixed because every destination parameter is supplied by
    :class:`ModelPipeline`. This function additionally removes workstation-only
    style paths and migrates the legacy ``Zone Type`` field spelling.
    """
    tree = ET.parse(source_path)
    root = tree.getroot()
    if normalise_zone_type:
        for item in root.iter():
            value = item.get("value")
            if value and "Zone Type" in value:
                item.set("value", value.replace("Zone Type", "ZoneType"))

    disabled_styles = 0
    for child in root.iter("Option"):
        algorithm_id = next(
            (
                item.get("value")
                for item in child
                if item.tag == "Option" and item.get("name") == "alg_id"
            ),
            None,
        )
        if algorithm_id != "native:setlayerstyle":
            continue
        style_values = [
            item for item in child.iter("Option")
            if item.get("name") == "static_value" and (item.get("value") or "").lower().endswith(".qml")
        ]
        found_style = False
        for style_value in style_values:
            old_value = style_value.get("value") or ""
            if "point" in old_value.casefold():
                candidates = ["centrality_points.qml", "categorisation_point.qml", "Categorization 0 1 2 3 4 9 Point.qml"]
            else:
                candidates = ["centrality_polygons.qml", "categorisation_zone.qml", "Categorization 0 1 2 3 4 9 Zone.qml"]

            for c_name in candidates:
                candidate = os.path.join(style_directory or "", c_name)
                if style_directory and os.path.isfile(candidate):
                    style_value.set("value", os.path.abspath(candidate))
                    found_style = True
                    break
            if not found_style:
                style_value.set("value", "")
        if not found_style:
            active = next(
                (item for item in child if item.tag == "Option" and item.get("name") == "active"),
                None,
            )
            if active is not None:
                active.set("value", "false")
                disabled_styles += 1

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    tree.write(target_path, encoding="utf-8", xml_declaration=True)
    return disabled_styles
