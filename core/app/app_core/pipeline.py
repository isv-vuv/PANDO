"""End-to-end orchestration for the OSM and QGIS processing stages."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable, Mapping, Optional

from core.app.app_core.geo import get_utm_epsg
from core.app.app_core.grid import restore_grid_geometries
from core.app.app_core.location import get_alpha2_code
from core.app.app_core.model_pipeline import (
    MODEL_DESCRIPTIONS,
    ModelPipeline,
    ModelPipelineResult,
    load_parameter_defaults,
)
from core.app.app_core.osm_pipeline import (
    OsmPhaseAConfig,
    OsmPhaseCConfig,
    OsmPipeline,
)
from core.app.app_core.osmium import resolve_osmium
from core.app.app_core.processing import register_processing_scripts
from core.app.app_core.project import (
    canonical_project_paths,
    load_pipeline_manifest,
    normalize_pbf_name,
    pbf_metadata,
    phase_can_be_reused,
    populate_visum_gpa_files,
    resolve_active_model_source,
    save_pipeline_manifest,
    tool_root,
    update_manifest_phase,
)


@dataclass(frozen=True)
class PipelineCallbacks:
    phase_started: Callable[[str, int, int], None] = lambda _name, _index, _total: None
    phase_progress: Callable[[int], None] = lambda _percent: None
    phase_detail: Callable[[str], None] = lambda _text: None
    log: Callable[[str], None] = lambda _text: None
    output: Callable[[str, str], None] = lambda _key, _path: None


_OSM_PROGRESS_LABELS = {
    "merge": "PBF-Dateien zusammenführen",
    "pa_extract": "PA-Gebiet ausschneiden",
    "cities": "Städte und Gemeinden extrahieren",
    "uninhabited": "Unbewohnte Flächen extrahieren",
    "network_prepare": "Straßennetz vorbereiten",
    "master_network": "Master-Netzklassen analysieren",
    "network_pa_extract": "Kernzone PA + IA1 ausschneiden",
    "network_pa_filter": "Straßen in PA + IA1 filtern",
    "network_ia2_extract": "Einflusszone IA2 ausschneiden",
    "network_ia2_filter": "Straßen in IA2 filtern",
    "network_oa_filter": "Straßen im Außenraum filtern",
    "network_ferry_filter": "Fährverbindungen extrahieren",
    "network_merge": "Netz-Schichten zusammenführen",
    "network_original": "Originalnetz schreiben",
    "network_classification": "Netztopologie analysieren und Attribute ergänzen",
    "network_modified": "Bearbeitetes Netz schreiben",
    "study_area_prepare": "POI-Extraktion vorbereiten",
    "study_area_extract": "Study Area ausschneiden",
    "study_area_points": "Punkt-POIs schreiben",
    "study_area_multipolygons": "Flächen-POIs schreiben",
    "complete": "Abgeschlossen",
}


def _forward_osm_progress(
    callbacks: PipelineCallbacks,
    name: str,
    index: int,
    total: int,
) -> None:
    callbacks.phase_detail(_OSM_PROGRESS_LABELS.get(name, name.replace("_", " ")))
    callbacks.phase_progress(int(index / max(1, total) * 100))


@dataclass(frozen=True)
class PreparedInputs:
    local_crs: str
    center_point: Path
    zone_type_selected: Path
    pa_poly: Path
    minimum_extent_radius_km: float
    grid_size_e0_m: float
    iso3_codes: str


def pipeline_readiness(
    project_path: str,
    step_data: Mapping[str, object],
    *,
    root: Optional[str] = None,
) -> list[str]:
    """Return human-readable blockers without changing project state."""
    base = Path(root or tool_root())
    missing: list[str] = []
    if not project_path:
        missing.append("Projektpfad fehlt")
    references = list(step_data.get("pbf_references") or [])
    pbfs = [Path(str(item.get("path"))) for item in references if isinstance(item, Mapping) and item.get("path")]
    if not pbfs and step_data.get("user_pbf_path"):
        pbfs = [Path(str(step_data["user_pbf_path"]))]
    if not pbfs:
        missing.append("Keine PBF-Datei ausgewählt")
    else:
        missing.extend(f"PBF fehlt: {path}" for path in pbfs if not path.is_file())
    selected = step_data.get("selected_cells") or {}
    if not isinstance(selected, Mapping) or not selected.get("PA"):
        missing.append("Mindestens eine PA-Zelle ist erforderlich")
    resources = {
        "GADM ADM0": base / "core/data/gadm/gadm_adm0.gpkg",
        "GADM ADM1": base / "core/data/gadm/gadm_adm1.gpkg",
        "GADM ADM2": base / "core/data/gadm/gadm_adm2.gpkg",
        "GADM ADM3": base / "core/data/gadm/gadm_adm3.gpkg",
        "GHS-POP": base / "core/data/ghs_pop/ghs_pop_global.tif",
        "Master-Linktypen": base / "core/scripts/visum/helper_files/master_linktypes.net",
    }
    missing.extend(f"{label} fehlt: {path}" for label, path in resources.items() if not path.is_file())
    return missing


def resolve_all_iso3_country_codes(
    location: Any = None,
    step_data: Optional[Mapping[str, Any]] = None,
    project_path: Optional[str] = None,
) -> str:
    """Resolve all involved ISO 3166-1 alpha-3 country codes as comma-separated string (e.g. 'DEU, DNK')."""
    import pycountry

    iso3_list: list[str] = []

    def _add_alpha2(a2: Any) -> None:
        if not a2 or not isinstance(a2, str):
            return
        a2 = a2.strip().upper()
        if len(a2) == 2:
            try:
                country = pycountry.countries.get(alpha_2=a2)
                if country and getattr(country, "alpha_3", None):
                    code3 = country.alpha_3.upper()
                    if code3 not in iso3_list:
                        iso3_list.append(code3)
            except Exception:
                pass
        elif len(a2) == 3:
            try:
                country = pycountry.countries.get(alpha_3=a2)
                code3 = country.alpha_3.upper() if country else a2.upper()
                if code3 not in iso3_list:
                    iso3_list.append(code3)
            except Exception:
                if a2 not in iso3_list:
                    iso3_list.append(a2)

    # 1. Primary location
    if location is not None:
        try:
            a2 = get_alpha2_code(location)
            _add_alpha2(a2)
        except Exception:
            pass

    # 2. Check explicit country_codes in step_data or project metadata
    data = dict(step_data or {})
    if project_path and not data.get("pbf_references"):
        for fname in ("project_metadata.json", "project.json", "config.json"):
            meta_file = os.path.join(project_path, fname)
            if os.path.isfile(meta_file):
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    if isinstance(meta, dict):
                        data.setdefault("pbf_references", meta.get("pbf_references"))
                        data.setdefault("download_jobs", meta.get("download_jobs"))
                        data.setdefault("selected_countries", meta.get("selected_countries"))
                        data.setdefault("country_codes", meta.get("country_codes"))
                except Exception:
                    pass

    for raw_c in (data.get("selected_countries") or data.get("country_codes") or []):
        _add_alpha2(str(raw_c))

    # 3. PBF references and download jobs
    pbf_items: list[dict] = []
    if isinstance(data.get("pbf_references"), list):
        pbf_items.extend(item for item in data["pbf_references"] if isinstance(item, dict))
    if isinstance(data.get("download_jobs"), list):
        pbf_items.extend(item for item in data["download_jobs"] if isinstance(item, dict))

    if project_path:
        osm_in = Path(project_path) / "processed" / "osm" / "01_input"
        if osm_in.is_dir():
            for pbf_file in osm_in.glob("*.osm.pbf"):
                if pbf_file.name != "merged.osm.pbf":
                    pbf_items.append({"filename": pbf_file.name})

    if pbf_items:
        geofabrik_index = None
        index_path = Path(tool_root()) / "core" / "data" / "osm" / "geofabrik-index.json"
        if index_path.is_file():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    geofabrik_index = json.load(f)
            except Exception:
                pass

        id_to_feature: dict[str, dict] = {}
        if geofabrik_index and isinstance(geofabrik_index, dict):
            for feat in geofabrik_index.get("features", []):
                fid = feat.get("properties", {}).get("id")
                if fid:
                    id_to_feature[fid] = feat

        for item in pbf_items:
            if item.get("country_code"):
                _add_alpha2(item["country_code"])
            if item.get("iso3"):
                _add_alpha2(item["iso3"])

            osm_id = str(item.get("osm_id") or "")
            filename = str(item.get("filename") or item.get("name") or "")
            norm_name = normalize_pbf_name(filename)

            feat = id_to_feature.get(osm_id) or id_to_feature.get(norm_name)
            if not feat:
                for f_item in id_to_feature.values():
                    pbf_u = f_item.get("properties", {}).get("urls", {}).get("pbf", "")
                    if pbf_u and os.path.basename(pbf_u) == filename:
                        feat = f_item
                        break

            if feat:
                cur = feat
                while cur:
                    props = cur.get("properties", {})
                    iso_a2 = props.get("iso3166-1:alpha2")
                    if iso_a2 and isinstance(iso_a2, list) and iso_a2[0]:
                        _add_alpha2(iso_a2[0])
                        break
                    parent_id = props.get("parent")
                    cur = id_to_feature.get(parent_id) if parent_id else None

            if not feat and norm_name:
                try:
                    c = pycountry.countries.lookup(norm_name)
                    if c and getattr(c, "alpha_3", None):
                        _add_alpha2(c.alpha_3)
                except Exception:
                    pass

    if not iso3_list:
        iso3_list = ["DEU"]

    return ", ".join(iso3_list)


def prepare_project_inputs(project_path: str, step_data: Mapping[str, object]) -> PreparedInputs:
    """Persist center/grid selections and scalar inputs using the canonical contract."""
    try:
        import geopandas as gpd
        import pycountry
        from shapely.geometry import Point
    except ImportError as exc:
        raise RuntimeError("GeoPandas, Shapely und pycountry werden für die Projekteingaben benötigt") from exc

    paths = canonical_project_paths(project_path)
    input_dir = Path(paths["input"])
    bounds_dir = Path(paths["filter_bounds"])
    input_dir.mkdir(parents=True, exist_ok=True)
    bounds_dir.mkdir(parents=True, exist_ok=True)

    location = step_data.get("selected_loc")
    latitude = getattr(location, "latitude", None)
    longitude = getattr(location, "longitude", None)
    if latitude is None or longitude is None:
        raise ValueError("Der Stadtmittelpunkt enthält keine Koordinaten")
    local_crs = get_utm_epsg(float(latitude), float(longitude))
    if not local_crs:
        raise ValueError("Für den Stadtmittelpunkt konnte kein lokales UTM-CRS bestimmt werden")

    center_path = input_dir / "center_point.gpkg"
    gpd.GeoDataFrame(
        [{"name": getattr(location, "address", "")}],
        geometry=[Point(float(longitude), float(latitude))],
        crs="EPSG:4326",
    ).to_file(center_path, layer="center_point", driver="GPKG")

    grid_map_data = [
        dict(cell)
        for cell in (step_data.get("grid_map_data") or [])
        if isinstance(cell, Mapping)
    ]
    restore_grid_geometries(grid_map_data)
    cell_by_id = {
        cell.get("id"): cell.get("shapely_poly_wgs84")
        for cell in grid_map_data
    }
    records = []
    for zone_type in ("PA", "IA1", "IA2"):
        for cell_id in (step_data.get("selected_cells") or {}).get(zone_type, ()):
            geometry = cell_by_id.get(cell_id)
            if geometry is not None and not geometry.is_empty:
                records.append({"CellId": cell_id, "ZoneType": zone_type, "geometry": geometry})
    if not records or not any(item["ZoneType"] == "PA" for item in records):
        raise ValueError("Die Auswahl muss mindestens eine gültige PA-Zelle enthalten")
    zone_path = input_dir / "zone_type_selected.gpkg"
    gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326").to_crs(local_crs).to_file(
        zone_path, layer="zone_type_selected", driver="GPKG"
    )

    (input_dir / "local_crs.txt").write_text(local_crs, encoding="utf-8")
    iso3_codes = resolve_all_iso3_country_codes(location, step_data, project_path)
    (input_dir / "country_code.txt").write_text(iso3_codes, encoding="utf-8")

    extent = float(step_data.get("radius_km") or 30)
    grid_size = float(step_data.get("cell_size_m") or 4500)
    (input_dir / "Model3_params.json").write_text(
        json.dumps(
            {"minimum_extent_radius_km": extent, "grid_size_e0_m": grid_size},
            indent=2,
        ),
        encoding="utf-8",
    )
    pa_poly = build_pa_poly(str(zone_path), str(bounds_dir))
    return PreparedInputs(local_crs, center_path, zone_path, pa_poly, extent, grid_size, iso3_codes)


def build_pa_poly(zone_layer_path: str, output_directory: str) -> Path:
    """Create the pre-model PA boundary through the bundled Processing script."""
    try:
        import processing
    except ImportError as exc:
        raise RuntimeError("PyQGIS Processing wird zum Erzeugen der PA-POLY-Datei benötigt") from exc
    script_path = Path(tool_root()) / "core/scripts/qgis/scripts/Model4_Export_poly.py"
    registration = register_processing_scripts(
        {"script:osmpoly_generator": str(script_path)}
    )
    try:
        extracted = processing.run(
            "native:extractbyexpression",
            {
                "INPUT": zone_layer_path,
                "EXPRESSION": '"ZoneType" IN (\'PA\', \'IA1\')',
                "OUTPUT": "TEMPORARY_OUTPUT",
            },
        )["OUTPUT"]
        dissolved = processing.run(
            "native:dissolve",
            {"INPUT": extracted, "FIELD": [], "SEPARATE_DISJOINT": False, "OUTPUT": "TEMPORARY_OUTPUT"},
        )["OUTPUT"]
        processing.run(
            "script:osmpoly_generator",
            {
                "INPUT": dissolved,
                "OUTPUT_DIR": output_directory,
                "USE_SELECTED": False,
                "NAMING_METHOD": 2,
                "FIELD_FOR_NAMES": None,
                "CUSTOM_NAME_TEMPLATE": "bound_pa_ia1",
            },
        )
    finally:
        registration.unregister()
    output = Path(output_directory) / "bound_pa_ia1.poly"
    if not output.is_file():
        legacy = Path(output_directory) / "bound_pa.poly"
        if legacy.is_file():
            shutil.move(legacy, output)
        else:
            raise RuntimeError(f"Der POLY-Algorithmus hat den erwarteten Output nicht erzeugt: {output}")
    return output


def create_dummy_population(pop_zero_path: str, output_path: str, local_crs: str) -> str:
    """Create the prescribed 1 x 1 cm POP=0 polygon at an uninhabited location."""
    import geopandas as gpd
    from shapely.geometry import box, Point

    if os.path.exists(pop_zero_path):
        source = gpd.read_file(pop_zero_path)
    else:
        source = gpd.GeoDataFrame()

    if not source.empty and not source.geometry.is_empty.all():
        projected = source.to_crs(local_crs)
        point = projected.geometry.iloc[0].representative_point()
    else:
        point = Point(0, 0)

    half = 0.005
    dummy = gpd.GeoDataFrame(
        [{"POP": 0}],
        geometry=[box(point.x - half, point.y - half, point.x + half, point.y + half)],
        crs=local_crs,
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    dummy.to_file(output_path, layer="dummy_pop_local", driver="GPKG")
    return output_path


def parse_poly_file_to_polygon(poly_path: str | Path):
    """Parse Osmosis .poly file format into a Shapely Polygon or MultiPolygon."""
    from shapely.geometry import Polygon, MultiPolygon

    poly_path = Path(poly_path)
    if not poly_path.is_file():
        return None

    rings = []
    current_ring = []
    with open(poly_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.lower().startswith("none") or line.upper() == "END":
                if current_ring:
                    if len(current_ring) >= 3:
                        rings.append(current_ring)
                    current_ring = []
                continue
            parts = line.split()
            if len(parts) == 2:
                try:
                    lon, lat = float(parts[0]), float(parts[1])
                    current_ring.append((lon, lat))
                except ValueError:
                    pass

    if not rings:
        return None

    polygons = [Polygon(r) for r in rings if len(r) >= 3]
    if not polygons:
        return None
    if len(polygons) == 1:
        return polygons[0]
    return MultiPolygon(polygons)


def get_osm_download_geometry(
    project_path: str | Path,
    step_data: Optional[dict] = None,
    log_fn=print,
) -> tuple[Optional[object], Optional[tuple[float, float, float, float]]]:
    """Retrieve union polygon geometry and bbox of selected Geofabrik OSM download regions."""
    import json
    import re
    from pathlib import Path
    from shapely.geometry import shape
    from shapely.ops import unary_union

    project_path = Path(project_path)
    index_path = Path(tool_root()) / "core" / "data" / "osm" / "geofabrik-index.json"
    if not index_path.is_file():
        return None, None

    pbf_targets = set()
    if step_data:
        refs = step_data.get("pbf_references") or step_data.get("download_jobs") or []
        for ref in refs:
            if isinstance(ref, dict):
                for k in ("id", "filename", "name", "url"):
                    val = ref.get(k)
                    if val and isinstance(val, str):
                        pbf_targets.add(val)
                urls = ref.get("urls")
                if isinstance(urls, dict):
                    pbf_url = urls.get("pbf")
                    if pbf_url and isinstance(pbf_url, str):
                        pbf_targets.add(pbf_url)

    input_dir = project_path / "input"
    if input_dir.is_dir():
        for pbf_file in input_dir.glob("*.osm.pbf"):
            pbf_targets.add(pbf_file.name)

    if not pbf_targets:
        return None, None

    def normalize(s: str) -> str:
        s = str(s).lower().replace("\\", "/").split("/")[-1]
        s = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", s)
        s = re.sub(r"-(latest|\d{6})\.osm\.pbf$", "", s)
        s = s.removesuffix(".osm.pbf").removesuffix(".pbf")
        s = s.replace("-", "").replace("_", "").replace(" ", "")
        return s

    norm_targets = {normalize(t) for t in pbf_targets if t}

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index_data = json.load(f)
        features = index_data.get("features", [])
    except Exception as exc:
        log_fn(f"Hinweis beim Laden des Geofabrik-Index: {exc}")
        return None, None

    matched_geoms = []
    matched_names = []

    for feat in features:
        props = feat.get("properties", {})
        fid = props.get("id", "")
        fname = props.get("name", "")
        pbf_url = props.get("urls", {}).get("pbf", "") if isinstance(props.get("urls"), dict) else ""

        norm_id = normalize(fid)
        norm_name = normalize(fname)
        norm_pbf = normalize(pbf_url)

        if (
            norm_id in norm_targets
            or norm_name in norm_targets
            or norm_pbf in norm_targets
            or any(t in norm_id or norm_id in t for t in norm_targets if len(t) > 3)
        ):
            geom_data = feat.get("geometry")
            if geom_data:
                try:
                    s_geom = shape(geom_data)
                    if s_geom and not s_geom.is_empty:
                        matched_geoms.append(s_geom)
                        matched_names.append(fname or fid)
                except Exception:
                    pass

    if not matched_geoms:
        return None, None

    union_geom = unary_union(matched_geoms)
    if union_geom is None or union_geom.is_empty:
        return None, None

    log_fn(f"OSM-Downloadregion(en) für ADM-Zuschnitt erkannt: {', '.join(set(matched_names))}")
    return union_geom, union_geom.bounds


def buffer_geometry_km(geom, buffer_km: float = 50.0):
    """Buffer a WGS84 (EPSG:4326) geometry by buffer_km kilometers."""
    if geom is None or geom.is_empty:
        return geom
    try:
        import geopandas as gpd

        gdf = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
        gdf_proj = gdf.to_crs(epsg=3857)
        buffered_proj = gdf_proj.buffer(buffer_km * 1000.0)
        buffered_wgs84 = buffered_proj.to_crs(epsg=4326).iloc[0]
        return buffered_wgs84
    except Exception:
        deg = buffer_km / 111.0
        return geom.buffer(deg)


def prepare_clipped_gadm_layers(
    project_path: str | Path,
    poly_path: Optional[str | Path] = None,
    step_data: Optional[dict] = None,
    log_fn=print,
) -> dict[str, str]:
    """Pre-crop global GADM ADM0..3 layers using the OSM download region spatial bounds (buffered by 50 km) before Model 1."""
    import json
    import pyogrio

    project_path = Path(project_path)
    gadm_dir = Path(tool_root()) / "core" / "data" / "gadm"
    temp_dir = project_path / "temp" / "gadm_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    meta_file = temp_dir / "adm_clip_meta.json"

    result_paths = {}
    bbox = None
    geom = None

    # First try OSM download region geometry
    geom, bbox = get_osm_download_geometry(project_path, step_data, log_fn=log_fn)

    # Fallback to poly_path or candidate poly files if download region bounds couldn't be resolved
    if bbox is None:
        poly_candidates = []
        if poly_path:
            poly_candidates.append(Path(poly_path))
        poly_candidates.extend([
            project_path / "processed" / "osm" / "02_filter_bounds" / "bound_pa_ia1.poly",
            project_path / "processed" / "osm" / "02_filter_bounds" / "bound_pa.poly",
            project_path / "processed" / "osm" / "02_filter_bounds" / "selected_pa_cells.gpkg",
            project_path / "processed" / "qgis_output" / "model4_TierAssign" / "bound_pa_ia1.poly",
        ])

        for candidate in poly_candidates:
            if candidate.is_file():
                try:
                    if candidate.suffix.lower() == ".poly":
                        geom = parse_poly_file_to_polygon(candidate)
                        if geom is not None and not geom.is_empty:
                            bbox = geom.bounds
                            break
                    elif candidate.suffix.lower() in (".gpkg", ".geojson", ".shp"):
                        import geopandas as gpd

                        gdf = gpd.read_file(candidate)
                        if not gdf.empty:
                            if gdf.crs and str(gdf.crs).upper() != "EPSG:4326":
                                gdf = gdf.to_crs("EPSG:4326")
                            geom = gdf.unary_union
                            if geom is not None and not geom.is_empty:
                                bbox = geom.bounds
                                break
                except Exception as exc:
                    log_fn(f"Hinweis beim Lesen von Poly-Kandidat {candidate.name}: {exc}")

    # Buffer geometry by 50 km for seamless coverage
    if geom is not None and not geom.is_empty:
        log_fn("Puffere OSM-Umrissgeometrie um 50 km für ADM-Zuschnitt ...")
        geom = buffer_geometry_km(geom, buffer_km=50.0)
        bbox = geom.bounds

    current_bbox_list = [round(c, 5) for c in bbox] if bbox else None
    cache_valid = False
    if meta_file.is_file() and current_bbox_list:
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                saved_meta = json.load(f)
            if (
                saved_meta.get("bbox") == current_bbox_list
                and saved_meta.get("hard_clip") is True
                and saved_meta.get("buffer_km") == 50.0
            ):
                cache_valid = True
        except Exception:
            cache_valid = False

    for level in range(4):
        global_file = gadm_dir / f"gadm_adm{level}.gpkg"
        clipped_file = temp_dir / f"gadm_adm{level}_clipped.gpkg"

        if not global_file.is_file():
            result_paths[f"gadm_adm{level}"] = str(global_file)
            continue

        if cache_valid and clipped_file.is_file() and clipped_file.stat().st_size > 0:
            result_paths[f"gadm_adm{level}"] = str(clipped_file)
            continue

        if bbox is None:
            result_paths[f"gadm_adm{level}"] = str(global_file)
            continue

        try:
            import geopandas as gpd

            log_fn(f"GADM ADM{level}: Schneide globale ADM-Datei hart auf 50 km gepufferte OSM-Umrisslinie zu ...")
            df = pyogrio.read_dataframe(global_file, bbox=bbox)
            if df.empty:
                result_paths[f"gadm_adm{level}"] = str(global_file)
                continue

            gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")
            if geom is not None and not geom.is_empty:
                gdf = gpd.clip(gdf, mask=geom)
                gdf = gdf[~gdf.geometry.is_empty]

            if gdf.empty:
                result_paths[f"gadm_adm{level}"] = str(global_file)
                continue

            pyogrio.write_dataframe(gdf, clipped_file, driver="GPKG")
            log_fn(f"GADM ADM{level} erfolgreich hart zugeschnitten ({len(gdf)} Regionen): {clipped_file.name}")
            result_paths[f"gadm_adm{level}"] = str(clipped_file)
        except Exception as exc:
            log_fn(f"Hinweis: Zuschneiden von ADM{level} übersprungen ({exc}), verwende Quelldatei.")
            result_paths[f"gadm_adm{level}"] = str(global_file)

    if current_bbox_list:
        try:
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump({"bbox": current_bbox_list, "hard_clip": True, "buffer_km": 50.0}, f, indent=2)
        except Exception:
            pass

    return result_paths


class UrbanActPipeline:
    """Coordinate phase A, models 1-4, phase C, and models 5-6."""

    def __init__(self, project_path: str, step_data: Mapping[str, object]):
        self.project_path = os.path.abspath(project_path)
        self.step_data = dict(step_data)

    def reusable_phase_a_outputs(self) -> Optional[dict[str, str]]:
        """Return existing Phase-A outputs when manifest and current inputs still match."""
        manifest = load_pipeline_manifest(self.project_path)
        if not phase_can_be_reused(manifest, "osm_phase_a"):
            return None
        pbfs = self._pbf_paths()
        if not self._manifest_pbfs_match(manifest, pbfs):
            return None
        phase = manifest.get("phases", {}).get("osm_phase_a", {})
        stored_signature = (phase.get("parameters") or {}).get("signature")
        if stored_signature and stored_signature != self._phase_a_signature(pbfs):
            return None
        outputs = phase.get("outputs") or {}
        required = ("merged_pbf", "bound_pa", "osm_cities", "osm_pop_0")
        if not all(outputs.get(key) and Path(str(outputs[key])).is_file() for key in required):
            return None
        if (
            not stored_signature
            and not self._legacy_pa_geometry_matches(str(outputs["bound_pa"]))
        ):
            return None
        return {key: str(outputs[key]) for key in required}

    def run_phase_a_only(
        self,
        *,
        explicit_osmium: Optional[str] = None,
        stop_event: Optional[Event] = None,
        callbacks: PipelineCallbacks = PipelineCallbacks(),
    ) -> dict[str, str]:
        stop_event = stop_event or Event()
        paths = canonical_project_paths(self.project_path)
        manifest = load_pipeline_manifest(self.project_path)
        reused = self.reusable_phase_a_outputs()
        if reused is not None:
            callbacks.phase_started("OSM Phase A (wiederverwendet)", 1, 9)
            callbacks.phase_progress(100)
            callbacks.log("Vorhandene OSM-Phase-A-Dateien werden wiederverwendet.")
            for key, value in reused.items():
                callbacks.output(key, value)
            return reused

        prepared = prepare_project_inputs(self.project_path, self.step_data)
        pbfs = self._pbf_paths()
        signature = self._phase_a_signature(pbfs)
        manifest["local_crs"] = prepared.local_crs
        manifest["input_pbfs"] = [pbf_metadata(path) for path in pbfs]
        runtime = resolve_osmium(explicit_osmium)
        manifest["runtime"]["osmium"] = {
            "path": str(runtime.executable),
            "version": runtime.version,
            "platform": runtime.platform,
            "architecture": runtime.architecture,
            "bundled": runtime.bundled,
        }
        log_lines: list[str] = []

        def log(message: str) -> None:
            text = str(message)
            log_lines.append(text)
            callbacks.log(text)
            Path(paths["osm_log"]).parent.mkdir(parents=True, exist_ok=True)
            with open(paths["osm_log"], "a", encoding="utf-8") as stream:
                stream.write(text + "\n")

        callbacks.phase_started("OSM Phase A", 1, 9)
        update_manifest_phase(
            manifest,
            "osm_phase_a",
            "running",
            parameters={"pa_poly": str(prepared.pa_poly), "signature": signature},
        )
        save_pipeline_manifest(self.project_path, manifest)
        osm = OsmPipeline(
            runtime,
            stop_event=stop_event,
            log=log,
            progress=lambda name, index, total: _forward_osm_progress(
                callbacks, name, index, total
            ),
        )
        try:
            outputs = dict(
                osm.run_phase_a(
                    OsmPhaseAConfig(
                        Path(self.project_path),
                        [Path(path) for path in pbfs],
                        prepared.pa_poly,
                    )
                )
            )
            update_manifest_phase(manifest, "osm_phase_a", "done", outputs=outputs, logs=log_lines)
            save_pipeline_manifest(self.project_path, manifest)
        except Exception as exc:
            update_manifest_phase(manifest, "osm_phase_a", "failed", logs=log_lines, error=str(exc))
            save_pipeline_manifest(self.project_path, manifest)
            raise
        result = {key: str(value) for key, value in outputs.items()}
        for key, value in result.items():
            callbacks.output(key, value)
        return result

    def _pbf_paths(self) -> list[str]:
        references = list(self.step_data.get("pbf_references") or [])
        paths = [
            str(item["path"])
            for item in references
            if isinstance(item, Mapping) and item.get("path")
        ]
        if not paths and self.step_data.get("user_pbf_path"):
            paths = [str(self.step_data["user_pbf_path"])]
        return paths

    @staticmethod
    def _manifest_pbfs_match(manifest: Mapping[str, object], pbfs: list[str]) -> bool:
        stored = {
            os.path.abspath(str(item["path"])): item
            for item in (manifest.get("input_pbfs") or [])
            if isinstance(item, Mapping) and item.get("path")
        }
        current_paths = {os.path.abspath(path) for path in pbfs}
        if not current_paths or set(stored) != current_paths:
            return False
        for path in current_paths:
            if not os.path.isfile(path):
                return False
            current = pbf_metadata(path)
            if (
                stored[path].get("size_bytes") != current.get("size_bytes")
                or stored[path].get("mtime") != current.get("mtime")
            ):
                return False
        return True

    def _phase_a_signature(self, pbfs: list[str]) -> str:
        selected_pa = sorted(
            (self.step_data.get("selected_cells") or {}).get("PA", ()),
            key=str,
        )
        cells = {
            cell.get("id"): cell
            for cell in (self.step_data.get("grid_map_data") or [])
            if isinstance(cell, Mapping)
        }
        payload = {
            "pbfs": [
                {
                    "path": os.path.abspath(path),
                    "size_bytes": os.stat(path).st_size,
                    "mtime_ns": os.stat(path).st_mtime_ns,
                }
                for path in sorted(pbfs)
            ],
            "pa_cells": [
                {
                    "id": cell_id,
                    "geometry": str(
                        (cells.get(cell_id) or {}).get("shapely_poly_wgs84")
                        or (cells.get(cell_id) or {}).get("wgs84_coords_map")
                        or ""
                    ),
                }
                for cell_id in selected_pa
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _legacy_pa_geometry_matches(self, poly_path: str) -> bool:
        """Validate unsigned legacy manifests against their existing PA-POLY geometry."""
        selected_ids = set(
            (self.step_data.get("selected_cells") or {}).get("PA", ())
        )
        selected_geometries = [
            cell.get("shapely_poly_wgs84")
            for cell in (self.step_data.get("grid_map_data") or [])
            if (
                isinstance(cell, Mapping)
                and cell.get("id") in selected_ids
                and getattr(cell.get("shapely_poly_wgs84"), "is_empty", True) is False
            )
        ]
        if not selected_geometries:
            return True
        try:
            from shapely.geometry import Polygon
            from shapely.ops import unary_union

            positive_rings: list[object] = []
            negative_rings: list[object] = []
            current: list[tuple[float, float]] = []
            negative = False
            with open(poly_path, encoding="utf-8") as stream:
                next(stream, None)
                for raw_line in stream:
                    parts = raw_line.split()
                    if not parts:
                        continue
                    if parts[0].upper() == "END":
                        if len(current) >= 3:
                            (negative_rings if negative else positive_rings).append(
                                Polygon(current)
                            )
                        current = []
                        negative = False
                        continue
                    if len(parts) == 1:
                        negative = parts[0].startswith("!")
                        continue
                    try:
                        current.append((float(parts[0]), float(parts[1])))
                    except (TypeError, ValueError):
                        return False
            if not positive_rings:
                return False
            saved_geometry = unary_union(positive_rings)
            if negative_rings:
                saved_geometry = saved_geometry.difference(unary_union(negative_rings))
            selected_geometry = unary_union(selected_geometries)
            tolerance = max(1e-7, selected_geometry.area * 1e-5)
            return selected_geometry.symmetric_difference(saved_geometry).area <= tolerance
        except (ImportError, OSError, ValueError):
            return False

    def run(
        self,
        model2_parameters: Optional[Mapping[str, object]] = None,
        *,
        model3_parameters: Optional[Mapping[str, object]] = None,
        model5_parameters: Optional[Mapping[str, object]] = None,
        pop_local: Optional[str] = None,
        pop_local_field: str = "POP",
        custom_census: Optional[str] = None,
        no_local_reference: bool = False,
        explicit_osmium: Optional[str] = None,
        stop_event: Optional[Event] = None,
        callbacks: PipelineCallbacks = PipelineCallbacks(),
        force_restart_models: bool = False,
    ) -> ModelPipelineResult:
        stop_event = stop_event or Event()
        paths = canonical_project_paths(self.project_path)
        manifest = load_pipeline_manifest(self.project_path)
        stored_runtime_models = dict(
            (manifest.get("runtime") or {}).get("models") or {}
        )
        logs: list[str] = []
        project_log_path = Path(self.project_path) / "pipeline.log"
        osm_log_path = Path(paths["osm_log"])

        def log(message: str) -> None:
            text = str(message)
            logs.append(text)
            callbacks.log(text)
            for log_file in (osm_log_path, project_log_path):
                try:
                    log_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(log_file, "a", encoding="utf-8") as stream:
                        stream.write(text + "\n")
                        stream.flush()
                except Exception:
                    pass

        if force_restart_models:
            reused_phase_a = None
            log("Neuberechnung angefordert: Alle bisherigen OSM- und Modell-Zwischenstände werden verworfen.")
            for phase_key in ("osm_phase_a", "osm_phase_c", "model1", "model2", "model3", "model3_4", "model4", "model5", "model6"):
                if "phases" in manifest and phase_key in manifest["phases"]:
                    del manifest["phases"][phase_key]
            save_pipeline_manifest(self.project_path, manifest)

            osm_dir = Path(paths["osm"])
            if osm_dir.exists():
                for item in osm_dir.iterdir():
                    try:
                        if item.is_file():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                    except Exception as exc:
                        log(f"Hinweis: Konnte {item.name} in processed/osm nicht löschen: {exc}")
        else:
            reused_phase_a = self.reusable_phase_a_outputs()

        prepared = prepare_project_inputs(self.project_path, self.step_data)
        project_input = Path(paths["input"])
        if pop_local:
            source = Path(pop_local)
            target = project_input / "pop_local.gpkg"
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            pop_local = str(target)
            (project_input / "pop_local_fieldname.txt").write_text(pop_local_field, encoding="utf-8")
        if custom_census:
            source = Path(custom_census)
            target = project_input / "custom_census.tif"
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            custom_census = str(target)
        (project_input / "Model2_params.json").write_text(
            json.dumps(dict(model2_parameters or {}), indent=2),
            encoding="utf-8",
        )
        (project_input / "Model3_params.json").write_text(
            json.dumps(dict(model3_parameters or {}), indent=2),
            encoding="utf-8",
        )
        (project_input / "Model5_params.json").write_text(
            json.dumps(dict(model5_parameters or {}), indent=2),
            encoding="utf-8",
        )
        pbfs = self._pbf_paths()
        phase_a_signature = self._phase_a_signature(pbfs)
        manifest["local_crs"] = prepared.local_crs
        manifest["input_pbfs"] = [pbf_metadata(path) for path in pbfs]
        manifest["active_model_source"] = resolve_active_model_source()
        manifest["runtime"]["models"] = {
            description.key: {
                "path": str(Path(manifest["active_model_source"]) / description.filename),
                "mtime": (Path(manifest["active_model_source"]) / description.filename).stat().st_mtime,
            }
            for description in MODEL_DESCRIPTIONS
        }

        runtime = resolve_osmium(explicit_osmium)
        manifest["runtime"]["osmium"] = {
            "path": str(runtime.executable),
            "version": runtime.version,
            "platform": runtime.platform,
            "architecture": runtime.architecture,
            "bundled": runtime.bundled,
        }
        osm = OsmPipeline(
            runtime,
            stop_event=stop_event,
            log=log,
            progress=lambda name, index, total: _forward_osm_progress(
                callbacks, name, index, total
            ),
        )
        if reused_phase_a is not None:
            phase_a = dict(reused_phase_a)
            callbacks.phase_started("OSM Phase A (wiederverwendet)", 1, 9)
            callbacks.phase_progress(100)
            log("Gültige OSM-Phase-A-Outputs werden wiederverwendet.")
        else:
            update_manifest_phase(
                manifest,
                "osm_phase_a",
                "running",
                parameters={
                    "pa_poly": str(prepared.pa_poly),
                    "signature": phase_a_signature,
                },
            )
            save_pipeline_manifest(self.project_path, manifest)
            callbacks.phase_started("OSM Phase A", 1, 9)
            try:
                phase_a = dict(
                    osm.run_phase_a(
                        OsmPhaseAConfig(
                            Path(self.project_path), [Path(p) for p in pbfs], prepared.pa_poly
                        )
                    )
                )
                update_manifest_phase(manifest, "osm_phase_a", "done", outputs=phase_a, logs=logs)
                save_pipeline_manifest(self.project_path, manifest)
            except Exception as exc:
                update_manifest_phase(manifest, "osm_phase_a", "failed", logs=logs, error=str(exc))
                save_pipeline_manifest(self.project_path, manifest)
                raise
        for key, value in phase_a.items():
            callbacks.output(key, str(value))

        root = Path(tool_root())
        effective_pop = custom_census or str(root / "core/data/ghs_pop/ghs_pop_global.tif")
        if no_local_reference:
            pop_local = create_dummy_population(
                str(phase_a["osm_pop_0"]),
                str(Path(paths["temp"]) / "dummy_pop_local.gpkg"),
                prepared.local_crs,
            )
            pop_local_field = "POP"
        effective_radius = prepared.minimum_extent_radius_km
        effective_cell_size = prepared.grid_size_e0_m
        if model3_parameters:
            if "minimum_extent_radius_km" in model3_parameters:
                effective_radius = int(model3_parameters["minimum_extent_radius_km"])
            if "grid_size_e0_m" in model3_parameters:
                effective_cell_size = int(model3_parameters["grid_size_e0_m"])

        m5_defaults = load_parameter_defaults("model5")
        if model5_parameters:
            m5_defaults.update(dict(model5_parameters))

        context: dict[str, object] = {
            "iso_country_codes": prepared.iso3_codes,
            "ghs_pop_raster": effective_pop,
            "pop_local": pop_local,
            "pop_local_field": pop_local_field,
            "pop_zero_osm": str(phase_a["osm_pop_0"]),
            "osm_cities": str(phase_a["osm_cities"]),
            "local_crs": prepared.local_crs,
            "center_point": str(prepared.center_point),
            "zone_type_selected": str(prepared.zone_type_selected),
            "minimum_extent_radius_km": effective_radius,
            "grid_size_e0_m": effective_cell_size,
            **dict(model2_parameters or {}),
            **m5_defaults,
        }
        for level in range(4):
            context[f"gadm_adm{level}"] = str(root / f"core/data/gadm/gadm_adm{level}.gpkg")

        # Pre-clip global GADM ADM layers to project spatial bounds before Model 1
        poly_file_for_adm = phase_a.get("bound_pa")
        clipped_adms = prepare_clipped_gadm_layers(
            self.project_path,
            poly_file_for_adm,
            step_data=self.step_data,
            log_fn=callbacks.log,
        )
        for level in range(4):
            key = f"gadm_adm{level}"
            if key in clipped_adms:
                context[key] = clipped_adms[key]

        current_model = {"key": None}
        model_parameters = {
            "model1": {
                "pop_local": os.path.abspath(pop_local),
                "pop_local_field": pop_local_field,
                "custom_census": os.path.abspath(custom_census) if custom_census else None,
                "no_local_reference": bool(no_local_reference),
            },
            "model2": dict(model2_parameters or {}),
            "model3": {
                "minimum_extent_radius_km": effective_radius,
                "grid_size_e0_m": effective_cell_size,
                "local_crs": prepared.local_crs,
            },
            "model3_4": {},
            "model4": {},
            "model5": dict(m5_defaults),
            "model6": {},
        }
        current_runtime_models = dict(manifest["runtime"]["models"])
        reusable_outputs: dict[str, dict[str, object]] = {}
        reuse_allowed = not force_restart_models
        phase_c_reusable = False
        for description in MODEL_DESCRIPTIONS:
            if description.key == "model5":
                phase_c_reusable = reuse_allowed and phase_can_be_reused(
                    manifest,
                    "osm_phase_c",
                )
                if not phase_c_reusable:
                    reuse_allowed = False
            stored_runtime = stored_runtime_models.get(description.key) or {}
            current_runtime = current_runtime_models.get(description.key) or {}
            source_unchanged = (
                os.path.abspath(str(stored_runtime.get("path") or ""))
                == os.path.abspath(str(current_runtime.get("path") or ""))
                and stored_runtime.get("mtime") == current_runtime.get("mtime")
            )
            phase = (manifest.get("phases") or {}).get(description.key) or {}
            stored_parameters = phase.get("parameters") or {}
            legacy_model1_parameters = description.key == "model1" and not stored_parameters
            parameters_unchanged = (
                stored_parameters == model_parameters[description.key]
                or legacy_model1_parameters
            )
            can_reuse = (
                reuse_allowed
                and source_unchanged
                and parameters_unchanged
                and phase_can_be_reused(manifest, description.key)
            )
            if can_reuse:
                reusable_outputs[description.key] = dict(phase.get("outputs") or {})
                if legacy_model1_parameters:
                    phase["parameters"] = model_parameters[description.key]
            else:
                reuse_allowed = False

        def model_started(description, index: int, _total: int) -> None:
            current_model["key"] = description.key
            callbacks.phase_started(description.name, index + 1 + (1 if index >= 6 else 0), 9)
            callbacks.phase_progress(0)
            log(f"▶ Starte QGIS-Schritt {index + 1}/6: {description.key} ({description.name}) ...")
            update_manifest_phase(
                manifest,
                description.key,
                "running",
                parameters=model_parameters[description.key],
            )
            save_pipeline_manifest(self.project_path, manifest)

        def model_reused(description, index: int, _total: int, model_context) -> None:
            callbacks.phase_started(
                f"{description.name} (wiederverwendet)",
                index + 1 + (1 if index >= 6 else 0),
                9,
            )
            callbacks.phase_progress(100)
            log(f"{description.name}: vorhandene, vollständige Outputs werden wiederverwendet.")
            for output in description.outputs:
                value = model_context.get(output.context_key)
                if value:
                    callbacks.output(output.context_key, str(value))

        def model_finished(description, _raw_outputs, model_context) -> None:
            outputs = {
                output.context_key: model_context.get(output.context_key)
                for output in description.outputs
            }
            update_manifest_phase(manifest, description.key, "done", outputs=outputs)
            save_pipeline_manifest(self.project_path, manifest)
            for key, value in outputs.items():
                if value:
                    callbacks.output(key, str(value))

            idx = next((i for i, d in enumerate(MODEL_DESCRIPTIONS) if d.key == description.key), -1)
            if idx >= 0 and idx + 1 < len(MODEL_DESCRIPTIONS):
                next_step_desc = f"{MODEL_DESCRIPTIONS[idx + 1].key} ({MODEL_DESCRIPTIONS[idx + 1].name})"
            elif description.key == "model5":
                next_step_desc = "OSM Phase C (Straßennetz & Study-Area Extrakte)"
            elif description.key == "model6":
                next_step_desc = "Pipeline vollständig abgeschlossen"
            else:
                next_step_desc = "Nächster Prozessschritt"

            log(f"✓ {description.key} ({description.name}) erfolgreich abgeschlossen. Starte nächsten Schritt: {next_step_desc} ...")

        def phase_c(model_context: dict[str, object]) -> Mapping[str, object]:
            current_model["key"] = "osm_phase_c"
            if phase_c_reusable:
                phase = (manifest.get("phases") or {}).get("osm_phase_c") or {}
                outputs = dict(phase.get("outputs") or {})
                callbacks.phase_started("OSM Phase C (wiederverwendet)", 7, 9)
                callbacks.phase_progress(100)
                log("OSM Phase C: vorhandene, vollständige Outputs werden wiederverwendet.")
                for key, value in outputs.items():
                    callbacks.output(key, str(value))
                return {
                    "poi_points": str(outputs["study_area_points"]),
                    "poi_polygons": str(outputs["study_area_polygons"]),
                    **outputs,
                }
            callbacks.phase_started("OSM Phase C", 7, 9)
            update_manifest_phase(manifest, "osm_phase_c", "running")
            save_pipeline_manifest(self.project_path, manifest)
            outputs = dict(
                osm.run_phase_c(
                    OsmPhaseCConfig(
                        Path(self.project_path),
                        Path(str(model_context["merged_pbf"])),
                        Path(str(model_context["poly_paia1"])),
                        Path(str(model_context["poly_study_area_paia1ia2"])),
                        root / "core/scripts/visum/helper_files/master_linktypes.net",
                    )
                )
            )
            update_manifest_phase(manifest, "osm_phase_c", "done", outputs=outputs)
            save_pipeline_manifest(self.project_path, manifest)
            return {
                "poi_points": str(outputs["study_area_points"]),
                "poi_polygons": str(outputs["study_area_polygons"]),
                **outputs,
            }

        context["merged_pbf"] = str(phase_a["merged_pbf"])
        pipeline = ModelPipeline(
            self.project_path,
            model_directory=manifest["active_model_source"],
        )
        try:
            result = pipeline.run(
                context,
                phase_c_hook=phase_c,
                stop_event=stop_event,
                on_log=log,
                on_progress=callbacks.phase_progress,
                on_progress_text=callbacks.phase_detail,
                reusable_outputs=reusable_outputs,
                on_model_started=model_started,
                on_model_reused=model_reused,
                on_model_finished=model_finished,
            )
        except Exception as exc:
            error_trace = traceback.format_exc()
            log(f"FEHLER: {exc}")
            log(error_trace)
            try:
                error_log_path = os.path.join(self.project_path, "pipeline_error.log")
                with open(error_log_path, "w", encoding="utf-8") as err_file:
                    err_file.write(f"Pipeline failure at {datetime.now().isoformat()}\n")
                    err_file.write(f"Error: {exc}\n\n")
                    err_file.write(error_trace)
                    err_file.flush()
            except Exception:
                pass
            if current_model["key"]:
                update_manifest_phase(manifest, str(current_model["key"]), "failed", error=f"{exc}\n{error_trace}")
                save_pipeline_manifest(self.project_path, manifest)
            raise
        post_process_exports_and_styles(self.project_path, log=log)
        save_pipeline_manifest(self.project_path, manifest)
        callbacks.phase_progress(100)
        return result


def reproject_vector_file(input_gpkg: str, target_crs: str) -> bool:
    """Reproject a GeoPackage file in-place to target_crs if needed."""
    if not os.path.isfile(input_gpkg) or not target_crs:
        return False

    # Strategy 1: GeoPandas
    try:
        import geopandas as gpd
        gdf = gpd.read_file(input_gpkg)
        if gdf.empty:
            return True
        if gdf.crs is None or str(gdf.crs).lower() != str(target_crs).lower():
            gdf = gdf.to_crs(target_crs)
            gdf.to_file(input_gpkg, driver="GPKG")
        return True
    except Exception:
        pass

    # Strategy 2: PyQGIS
    try:
        from qgis.core import QgsVectorLayer, QgsVectorFileWriter, QgsCoordinateTransformContext, QgsCoordinateReferenceSystem
        layer = QgsVectorLayer(input_gpkg, "layer", "ogr")
        if layer.isValid():
            dest_crs = QgsCoordinateReferenceSystem(target_crs)
            if layer.crs() != dest_crs:
                temp_output = input_gpkg + ".tmp.gpkg"
                options = QgsVectorFileWriter.SaveVectorOptions()
                options.driverName = "GPKG"
                options.overrideCrs = dest_crs
                writer_res = QgsVectorFileWriter.writeAsVectorFormatV2(
                    layer, temp_output, QgsCoordinateTransformContext(), options
                )
                err = writer_res[0] if isinstance(writer_res, tuple) else writer_res
                if err == QgsVectorFileWriter.NoError:
                    shutil.move(temp_output, input_gpkg)
                    return True
                if os.path.isfile(temp_output):
                    os.remove(temp_output)
            else:
                return True
    except Exception:
        pass

    # Strategy 3: OSGeo GDAL / OGR
    try:
        from osgeo import gdal
        temp_output = input_gpkg + ".tmp.gpkg"
        options = gdal.VectorTranslateOptions(format="GPKG", dstSRS=target_crs)
        ds = gdal.VectorTranslate(temp_output, input_gpkg, options=options)
        if ds is not None:
            ds = None
            shutil.move(temp_output, input_gpkg)
            return True
        if os.path.isfile(temp_output):
            os.remove(temp_output)
    except Exception:
        pass

    return False


def export_vector_file_to_shapefile(input_gpkg: str, output_shp: str, target_crs: Optional[str] = None) -> bool:
    """Export a GeoPackage vector file to ESRI Shapefile format, optionally reprojecting to target_crs."""
    if not os.path.isfile(input_gpkg):
        return False
    os.makedirs(os.path.dirname(output_shp), exist_ok=True)

    # Strategy 1: PyQGIS QgsVectorFileWriter
    try:
        from qgis.core import QgsVectorLayer, QgsVectorFileWriter, QgsCoordinateTransformContext, QgsCoordinateReferenceSystem
        layer = QgsVectorLayer(input_gpkg, "export_layer", "ogr")
        if layer.isValid():
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "ESRI Shapefile"
            options.fileEncoding = "UTF-8"
            if target_crs:
                options.overrideCrs = QgsCoordinateReferenceSystem(target_crs)
            writer_res = QgsVectorFileWriter.writeAsVectorFormatV2(
                layer, output_shp, QgsCoordinateTransformContext(), options
            )
            err = writer_res[0] if isinstance(writer_res, tuple) else writer_res
            if err == QgsVectorFileWriter.NoError:
                return True
    except Exception:
        pass

    # Strategy 2: OSGeo GDAL / OGR
    try:
        from osgeo import gdal
        if target_crs:
            options = gdal.VectorTranslateOptions(format="ESRI Shapefile", dstSRS=target_crs)
            ds = gdal.VectorTranslate(output_shp, input_gpkg, options=options)
        else:
            ds = gdal.VectorTranslate(output_shp, input_gpkg, format="ESRI Shapefile")
        if ds is not None:
            ds = None
            return True
    except Exception:
        pass

    # Strategy 3: GeoPandas
    try:
        import geopandas as gpd
        gdf = gpd.read_file(input_gpkg)
        if target_crs and gdf.crs is not None:
            gdf = gdf.to_crs(target_crs)
        gdf.to_file(output_shp, driver="ESRI Shapefile", encoding="utf-8")
        return True
    except Exception:
        pass

    return False


def extract_centrality_points_from_zone_gpkg(model5_dir: str, local_crs: Optional[str] = None, log=None) -> Optional[str]:
    """Extract discrete centrality points from zone_pa_ia1_ia2.gpkg using XCoord/YCoord in local CRS."""
    src_gpkg = os.path.join(model5_dir, "zone_pa_ia1_ia2.gpkg")
    out_gpkg = os.path.join(model5_dir, "zone_pa_ia1_ia2_points.gpkg")
    if not os.path.isfile(src_gpkg):
        return None

    try:
        import geopandas as gpd
        from shapely.geometry import Point

        gdf = gpd.read_file(src_gpkg)
        x_col = next((c for c in gdf.columns if c.lower() == "xcoord"), None)
        y_col = next((c for c in gdf.columns if c.lower() == "ycoord"), None)

        if x_col and y_col:
            points = []
            for _, row in gdf.iterrows():
                try:
                    val_x = row[x_col]
                    val_y = row[y_col]
                    if val_x is not None and val_y is not None:
                        points.append(Point(float(val_x), float(val_y)))
                    else:
                        points.append(None)
                except (ValueError, TypeError):
                    points.append(None)

            point_gdf = gdf.copy()
            point_gdf.set_geometry(points, inplace=True)
            point_gdf = point_gdf[point_gdf.geometry.notnull()]

            crs_to_use = local_crs if local_crs else gdf.crs
            if crs_to_use:
                point_gdf.set_crs(crs_to_use, allow_override=True, inplace=True)

            point_gdf.to_file(out_gpkg, driver="GPKG")
            if log:
                log(f"Zentralitäten-Punkte extrahiert ({len(point_gdf)} Punkte) in {crs_to_use}: {out_gpkg}")
            return out_gpkg
    except Exception as exc:
        if log:
            log(f"Hinweis beim Erstellen von zone_pa_ia1_ia2_points.gpkg: {exc}")
    return None


def post_process_exports_and_styles(project_path: str, *, log: Optional[Callable[[str], None]] = None) -> None:
    """Copy QML style files next to zones/zone_centroids, reproject zones to local_crs, and export Shapefiles for PTV Visum."""
    root = tool_root()
    styles_dir = os.path.join(root, "core", "scripts", "qgis", "styles")
    model6_dir = os.path.join(project_path, "processed", "qgis_output", "model6_ZoneAssembler")
    model5_dir = os.path.join(project_path, "processed", "qgis_output", "model5_UrbanCentrality")

    local_crs = None
    crs_file = os.path.join(project_path, "input", "local_crs.txt")
    if os.path.isfile(crs_file):
        try:
            with open(crs_file, encoding="utf-8") as f:
                local_crs = f.read().strip()
        except Exception:
            pass
    if not local_crs:
        try:
            manifest = load_pipeline_manifest(project_path)
            local_crs = manifest.get("local_crs")
        except Exception:
            pass

    # 1. Reproject zones, mainzones, and zone_centroids in model6_dir to local_crs
    if local_crs and os.path.isdir(model6_dir):
        for zone_name in ("zones", "mainzones", "zone_centroids"):
            gpkg_path = os.path.join(model6_dir, f"{zone_name}.gpkg")
            if os.path.isfile(gpkg_path):
                reprojected = reproject_vector_file(gpkg_path, local_crs)
                if reprojected and log:
                    log(f"Koordinatensystem für {zone_name}.gpkg auf {local_crs} gesetzt.")

    # 2. Copy QML styles for Model 2, Model 5, and Model 6
    point_style_src = os.path.join(styles_dir, "centrality_points.qml")
    zone_style_src = os.path.join(styles_dir, "centrality_polygons.qml")

    # 2.a Model 2 (ZoneClass) styles
    model2_dir = os.path.join(project_path, "processed", "qgis_output", "model2_ZoneClass")
    if os.path.isdir(model2_dir):
        if os.path.isfile(zone_style_src):
            for target_name in ("zone_adm2_typeno.qml",):
                target_path = os.path.join(model2_dir, target_name)
                try:
                    shutil.copy2(zone_style_src, target_path)
                    if log:
                        log(f"QML-Style kopiert: {target_path}")
                except Exception as e:
                    if log:
                        log(f"Warnung beim Kopieren von Model 2 Flächen-Style: {e}")

        if os.path.isfile(point_style_src):
            for target_name in ("central_place_points.qml",):
                target_path = os.path.join(model2_dir, target_name)
                try:
                    shutil.copy2(point_style_src, target_path)
                    if log:
                        log(f"QML-Style kopiert: {target_path}")
                except Exception as e:
                    if log:
                        log(f"Warnung beim Kopieren von Model 2 Punkt-Style: {e}")

    # 2.b Model 5 (UrbanCentrality) styles & extracted points
    if os.path.isdir(model5_dir):
        if os.path.isfile(zone_style_src):
            for target_name in ("zone_pa_ia1_ia2.qml",):
                target_path = os.path.join(model5_dir, target_name)
                try:
                    shutil.copy2(zone_style_src, target_path)
                    if log:
                        log(f"QML-Style kopiert: {target_path}")
                except Exception as e:
                    if log:
                        log(f"Warnung beim Kopieren von Model 5 Flächen-Style: {e}")

        # 2.b.1 POI Points style for sector_all_points.qml and all sector_*_points.qml
        poi_style_src = os.path.join(styles_dir, "poi_points.qml")
        if os.path.isfile(poi_style_src):
            for fname in os.listdir(model5_dir):
                if fname.startswith("sector_") and fname.endswith("_points.gpkg"):
                    target_name = fname.replace(".gpkg", ".qml")
                    target_path = os.path.join(model5_dir, target_name)
                    try:
                        shutil.copy2(poi_style_src, target_path)
                        if log:
                            log(f"POI-QML-Style kopiert: {target_path}")
                    except Exception as e:
                        if log:
                            log(f"Warnung beim Kopieren von POI-Point-Style: {e}")

        # 2.b.2 Categorization point style for extracted centralities points
        if os.path.isfile(point_style_src):
            for target_name in ("zone_pa_ia1_ia2_points.qml",):
                target_path = os.path.join(model5_dir, target_name)
                try:
                    shutil.copy2(point_style_src, target_path)
                    if log:
                        log(f"QML-Style kopiert: {target_path}")
                except Exception as e:
                    if log:
                        log(f"Warnung beim Kopieren von Model 5 Zentralitäten-Punkt-Style: {e}")

        # Extract discrete points for centralities from zone_pa_ia1_ia2.gpkg using XCoord/YCoord
        extract_centrality_points_from_zone_gpkg(model5_dir, local_crs=local_crs, log=log)

    # 2.c Model 6 (ZoneAssembler) styles
    if os.path.isfile(point_style_src) and os.path.isdir(model6_dir):
        for target_name in ("zone_centroids.qml", "zone_centroids_centroids.qml"):
            target_path = os.path.join(model6_dir, target_name)
            try:
                shutil.copy2(point_style_src, target_path)
                if log:
                    log(f"QML-Style kopiert: {target_path}")
            except Exception as e:
                if log:
                    log(f"Warnung beim Kopieren von Punkt-Style: {e}")

    if os.path.isfile(zone_style_src) and os.path.isdir(model6_dir):
        for target_name in ("zones.qml",):
            target_path = os.path.join(model6_dir, target_name)
            try:
                shutil.copy2(zone_style_src, target_path)
                if log:
                    log(f"QML-Style kopiert: {target_path}")
            except Exception as e:
                if log:
                    log(f"Warnung beim Kopieren von Flächen-Style: {e}")

    # 2.d Copy Intensity QML style for Model 5 raster intensity estimation outputs
    intensity_style_src = os.path.join(styles_dir, "intensity.qml")
    if os.path.isfile(intensity_style_src) and os.path.isdir(model5_dir):
        for filename in os.listdir(model5_dir):
            lower = filename.lower()
            if lower.endswith((".tif", ".sdat", ".asc")) and ("intensity" in lower or "heatmap" in lower or "sector" in lower or "raster" in lower):
                stem = os.path.splitext(filename)[0]
                target_path = os.path.join(model5_dir, f"{stem}.qml")
                try:
                    shutil.copy2(intensity_style_src, target_path)
                    if log:
                        log(f"Intensity QML-Style kopiert: {target_path}")
                except Exception as e:
                    if log:
                        log(f"Warnung beim Kopieren von Intensity-Style: {e}")

    # 2.e Copy Population Raster QML style for Model 1 pop_raster_corr
    pop_style_src = os.path.join(styles_dir, "population_raster.qml")
    if not os.path.isfile(pop_style_src):
        pop_style_src = os.path.join(styles_dir, "pop_raster_corr.qml")
    model1_dir = os.path.join(project_path, "processed", "qgis_output", "model1_DataPrep")
    if os.path.isfile(pop_style_src) and os.path.isdir(model1_dir):
        for target_name in ("pop_raster_corr.qml", "population_raster.qml"):
            target_path = os.path.join(model1_dir, target_name)
            try:
                shutil.copy2(pop_style_src, target_path)
                if log:
                    log(f"Population Raster QML-Style kopiert: {target_path}")
            except Exception as e:
                if log:
                    log(f"Warnung beim Kopieren von Pop-Raster-Style: {e}")

    # 2.f Copy ADM0-ADM3 boundary QML styles for Model 1
    if os.path.isdir(model1_dir):
        for i in range(4):
            adm_src = os.path.join(styles_dir, f"adm{i}.qml")
            if os.path.isfile(adm_src):
                adm_dst = os.path.join(model1_dir, f"adm{i}.qml")
                try:
                    shutil.copy2(adm_src, adm_dst)
                    if log:
                        log(f"ADM{i} QML-Style kopiert: {adm_dst}")
                except Exception as e:
                    if log:
                        log(f"Warnung beim Kopieren von ADM{i}-Style: {e}")

    # 3. Export Shapefiles for Zones & Mainzones in local CRS
    visum_zones_dir = os.path.join(project_path, "processed", "visum", "shapefile", "Zones")
    for zone_name in ("zones", "mainzones"):
        gpkg_path = os.path.join(model6_dir, f"{zone_name}.gpkg")
        shp_path = os.path.join(visum_zones_dir, f"{zone_name}.shp")
        if os.path.isfile(gpkg_path):
            ok = export_vector_file_to_shapefile(gpkg_path, shp_path, target_crs=local_crs)
            if ok and log:
                log(f"Visum-Shapefile exportiert: {shp_path}")

    # 4. Export Shapefiles for POI Sector Points from Model 5
    visum_poi_dir = os.path.join(project_path, "processed", "visum", "shapefile", "POI")
    if os.path.isdir(model5_dir):
        for filename in os.listdir(model5_dir):
            if filename.startswith("sector_") and filename.endswith("_points.gpkg"):
                gpkg_path = os.path.join(model5_dir, filename)
                shp_name = filename.replace(".gpkg", ".shp")
                shp_path = os.path.join(visum_poi_dir, shp_name)
                ok = export_vector_file_to_shapefile(gpkg_path, shp_path, target_crs=local_crs)
                if ok and log:
                    log(f"Visum-POI-Shapefile exportiert: {shp_path}")

    # 5. Populate Visum template GPA files
    populate_visum_gpa_files(project_path)

    # 6. Generate Master QGIS Project with local CRS and OpenStreetMap background
    try:
        from core.app.app_qt.steps.step6_results import create_qgis_project_for_layers
        qgis_out = Path(project_path) / "processed" / "qgis_output"
        all_layers = [
            qgis_out / "model6_ZoneAssembler" / "zone_centroids.gpkg",
            qgis_out / "model6_ZoneAssembler" / "zones.gpkg",
            qgis_out / "model5_UrbanCentrality" / "zone_pa_ia1_ia2_points.gpkg",
            qgis_out / "model5_UrbanCentrality" / "zone_pa_ia1_ia2.gpkg",
            qgis_out / "model2_ZoneClass" / "central_place_points.gpkg",
            qgis_out / "model2_ZoneClass" / "zone_adm2_typeno.gpkg",
            qgis_out / "model5_UrbanCentrality" / "sector_all_intensity.tif",
            qgis_out / "model5_UrbanCentrality" / "sector_all_points.gpkg",
            qgis_out / "model1_DataPrep" / "pop_raster_corr.tif",
        ]
        qgs_file = create_qgis_project_for_layers(project_path, all_layers, project_name="pando_master")
        if qgs_file and log:
            log(f"Master QGIS-Projekt erzeugt (lokales KOS, OpenStreetMap): {qgs_file}")
    except Exception as exc:
        if log:
            log(f"Hinweis beim Erzeugen des Master-QGIS-Projekts: {exc}")
