"""Core logic for step 4 project folders and PBF handling."""

from __future__ import annotations

import os
import re
import json
import time
import hashlib
import logging
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

from core.app.app_core.location import get_clean_filename_city


USER_AGENT = "PANDO V1.0 (Urban-Act Tool)"
PROJECT_METADATA_FILENAME = "config.json"
PROJECT_METADATA_VERSION = 1
PROJECT_CONFIG_FILENAME = "config.json"
PIPELINE_MANIFEST_FILENAME = "pipeline_manifest.json"
PIPELINE_MANIFEST_VERSION = 1
PROJECTS_DIR = "projects"
WORKSPACE_DATA_DIR = os.path.join("core", "data", "osm")
PROJECT_INPUT_DIR = "input"
PROJECT_TEMP_DIR = "temp"
PROJECT_PROCESSED_DIR = "processed"
PROJECT_OSM_DIR = os.path.join(PROJECT_PROCESSED_DIR, "osm")
PROJECT_QGIS_OUTPUT_DIR = os.path.join(PROJECT_PROCESSED_DIR, "qgis_output")
PROJECT_VISUM_DIR = os.path.join(PROJECT_PROCESSED_DIR, "visum")
ACTIVE_QGIS_MODEL_SOURCE = os.path.join("core", "scripts", "qgis", "models")
PIPELINE_PHASE_STATUSES = frozenset({"pending", "running", "done", "failed", "stale"})
DEFAULT_PBF_MAX_AGE_DAYS = 30
DEFAULT_PIPELINE_PHASES = (
    "osm_phase_a",
    "model1",
    "model2",
    "model3",
    "model3_4",
    "model4",
    "osm_phase_c",
    "model5",
    "model6",
)

PROJECT_INPUT_SUBDIRS: tuple[str, ...] = ()
PROJECT_OSM_SUBDIRS = ("01_input", "02_filter_bounds", "03_features", "04_network")
PROJECT_QGIS_SUBDIRS = (
    "model1_DataPrep",
    "model2_ZoneClass",
    "model3_GridGen",
    "model3-4_GridAssign",
    "model4_TierAssign",
    "model5_UrbanCentrality",
    "model6_ZoneAssembler",
)
PROJECT_VISUM_SUBDIRS = (
    "att",
    "dir",
    "dmd",
    "fil",
    "gpa",
    "icon",
    "lay",
    "lla",
    "log-file",
    "mtx",
    "net",
    "par",
    "pro",
    "qla",
    "screenshot",
    "script",
    "shapefile",
    "tt-gpa",
    "ver",
)
PROJECT_VISUM_SHAPEFILE_SUBDIRS = ("POI", "Zones")


@dataclass(frozen=True)
class PbfVerification:
    all_found: bool
    found_files: list[str]
    found_names: list[str]
    missing_basenames: list[str]
    user_pbf_path: Optional[str]


def tool_root() -> str:
    """Return the repository/application root containing ``core/app``."""
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )


def projects_dir(base_dir: Optional[str] = None) -> str:
    return os.path.join(os.path.abspath(base_dir or tool_root()), PROJECTS_DIR)


def workspace_data_dir(workspace_path: str, iso3_code: Optional[str] = None) -> str:
    """Return the global OSM cache, optionally scoped to an ISO3 country code."""
    data_path = os.path.join(os.path.abspath(workspace_path), WORKSPACE_DATA_DIR)
    return os.path.join(data_path, iso3_code.upper()) if iso3_code else data_path


def ensure_workspace_structure(workspace_path: str) -> dict[str, str]:
    """Create the fixed application-level projects and OSM cache folders."""
    workspace_path = os.path.abspath(workspace_path)
    os.makedirs(workspace_path, exist_ok=True)
    projects_path = projects_dir(workspace_path)
    data_path = workspace_data_dir(workspace_path)
    os.makedirs(projects_path, exist_ok=True)
    os.makedirs(data_path, exist_ok=True)
    return {"workspace_path": workspace_path, "projects_path": projects_path, "data_path": data_path}


def build_project_folder_name(selected_location, created_at: Optional[datetime] = None, unknown_place: str = "unknown_place") -> str:
    timestamp = (created_at or datetime.now()).strftime("%Y%m%d")
    city_name = get_clean_filename_city(selected_location, unknown_place)
    slug = re.sub(r"[^a-z0-9]+", "_", city_name.lower()).strip("_") or unknown_place
    return f"{timestamp}_{slug}"


def create_project(base_dir: str, selected_location, unknown_place: str = "unknown_place", created_at: Optional[datetime] = None) -> str:
    paths = ensure_workspace_structure(base_dir)
    project_path = unique_project_path(
        paths["projects_path"],
        build_project_folder_name(selected_location, created_at, unknown_place),
    )
    create_project_structure(project_path)
    created = created_at or datetime.now()
    save_project_metadata(
        project_path,
        build_project_metadata(
            workspace_path=base_dir,
            project_path=project_path,
            selected_location=selected_location,
            last_step=1,
            created_at=created,
        ),
    )
    save_pipeline_manifest(
        project_path,
        build_pipeline_manifest(
            project_path,
            created_at=created,
            active_model_source=resolve_active_model_source(base_dir),
        ),
    )
    return project_path


def unique_project_path(base_dir: str, folder_name: str) -> str:
    project_path = os.path.join(base_dir, folder_name)
    if not os.path.exists(project_path):
        return project_path
    counter = 2
    while True:
        candidate = os.path.join(base_dir, f"{folder_name}_{counter}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def populate_visum_gpa_files(project_path: str, language: Optional[str] = None) -> list[str]:
    """Copy template .gpa/.gpax files matching application language into processed/visum/gpa."""
    visum_gpa_dir = os.path.join(project_path, "processed", "visum", "gpa")
    os.makedirs(visum_gpa_dir, exist_ok=True)

    if not language:
        for fname in ["config.json", "project.json", "project_metadata.json"]:
            meta_file = os.path.join(project_path, fname)
            if os.path.isfile(meta_file):
                try:
                    import json
                    with open(meta_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    l_val = data.get("language") or data.get("app_language")
                    if l_val:
                        language = str(l_val).strip().lower()
                        break
                except Exception:
                    pass
    lang_str = (language or "de").lower()

    root = tool_root() if "tool_root" in globals() else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    helper_dir = os.path.join(root, "core", "scripts", "visum", "helper_files")

    copied = []
    if os.path.isdir(helper_dir):
        for filename in os.listdir(helper_dir):
            if filename.lower().endswith(".gpa") or filename.lower().endswith(".gpax"):
                name_upper = filename.upper()
                if lang_str.startswith("en"):
                    if name_upper.startswith("DE_") or name_upper.startswith("DE"):
                        continue
                else:
                    if name_upper.startswith("EN_") or name_upper.startswith("EN"):
                        continue
                stem, ext = os.path.splitext(filename)
                upper_stem = stem.upper()
                if upper_stem.startswith("DE_") or upper_stem.startswith("EN_"):
                    clean_filename = f"{stem[3:]}{ext}"
                elif upper_stem.startswith("DE") or upper_stem.startswith("EN"):
                    clean_filename = f"{stem[2:].lstrip('_')}{ext}"
                elif upper_stem.startswith("GPA_"):
                    clean_filename = f"{stem[4:]}{ext}"
                else:
                    clean_filename = filename

                src = os.path.join(helper_dir, filename)
                dst = os.path.join(visum_gpa_dir, clean_filename)
                shutil.copy2(src, dst)
                copied.append(dst)
    return copied


def create_project_structure(project_path: str) -> None:
    paths = canonical_project_paths(project_path)
    for key in ("root", "input", "temp", "processed", "osm", "qgis_output", "visum"):
        os.makedirs(paths[key], exist_ok=True)
    for subfolder in PROJECT_INPUT_SUBDIRS:
        os.makedirs(os.path.join(paths["input"], subfolder), exist_ok=True)
    for subfolder in PROJECT_OSM_SUBDIRS:
        os.makedirs(os.path.join(paths["osm"], subfolder), exist_ok=True)
    for subfolder in PROJECT_QGIS_SUBDIRS:
        os.makedirs(os.path.join(paths["qgis_output"], subfolder), exist_ok=True)
    for subfolder in PROJECT_VISUM_SUBDIRS:
        os.makedirs(os.path.join(paths["visum"], subfolder), exist_ok=True)
    for subfolder in PROJECT_VISUM_SHAPEFILE_SUBDIRS:
        os.makedirs(os.path.join(paths["visum"], "shapefile", subfolder), exist_ok=True)
    populate_visum_gpa_files(project_path)


def populate_project_qgis_files(project_path: str, root_dir: Optional[str] = None) -> list[str]:
    """Deprecated helper; QGIS scripts and models are managed globally in the QGIS profile."""
    return []


def project_input_dir(project_path: str) -> str:
    return os.path.join(project_path, PROJECT_INPUT_DIR)


def canonical_project_paths(project_path: str) -> dict[str, str]:
    """Return the single canonical path contract used by all pipeline stages."""
    root = os.path.abspath(project_path)
    osm = os.path.join(root, PROJECT_OSM_DIR)
    qgis = os.path.join(root, PROJECT_QGIS_OUTPUT_DIR)
    return {
        "root": root,
        "config": os.path.join(root, PROJECT_CONFIG_FILENAME),
        "legacy_metadata": project_metadata_path(root),
        "manifest": os.path.join(root, PIPELINE_MANIFEST_FILENAME),
        "input": os.path.join(root, PROJECT_INPUT_DIR),
        "temp": os.path.join(root, PROJECT_TEMP_DIR),
        "processed": os.path.join(root, PROJECT_PROCESSED_DIR),
        "osm": osm,
        "osm_log": os.path.join(osm, "pipeline.log"),
        "merged_pbf": os.path.join(osm, "01_input", "merged.osm.pbf"),
        "filter_bounds": os.path.join(osm, "02_filter_bounds"),
        "osm_features": os.path.join(osm, "03_features"),
        "osm_network": os.path.join(osm, "04_network"),
        "qgis_output": qgis,
        "visum": os.path.join(root, PROJECT_VISUM_DIR),
    }


def project_metadata_path(project_path: str) -> str:
    return os.path.join(project_path, PROJECT_METADATA_FILENAME)


def project_config_path(project_path: str) -> str:
    return os.path.join(project_path, PROJECT_CONFIG_FILENAME)


def project_manifest_path(project_path: str) -> str:
    return os.path.join(project_path, PIPELINE_MANIFEST_FILENAME)


def is_project_folder(path: str) -> bool:
    return os.path.isfile(project_config_path(path)) or os.path.isfile(project_metadata_path(path))


def list_projects(workspace_path: str) -> list[dict[str, Any]]:
    search_path = os.path.abspath(workspace_path)
    nested_projects = projects_dir(search_path)
    if os.path.isdir(nested_projects):
        search_path = nested_projects
    if not os.path.isdir(search_path):
        return []
    projects = []
    for entry in sorted(os.listdir(search_path)):
        project_path = os.path.join(search_path, entry)
        if not os.path.isdir(project_path) or not is_project_folder(project_path):
            continue
        try:
            projects.append(load_project_metadata(project_path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return projects


def build_project_metadata(
    *,
    workspace_path: str,
    project_path: str,
    data_path: Optional[str] = None,
    selected_location: Any = None,
    last_step: int = 1,
    step3_data: Optional[dict[str, Any]] = None,
    step4_data: Optional[dict[str, Any]] = None,
    pbf_references: Optional[list[dict[str, Any]]] = None,
    language: str = "de",
    created_at: Optional[datetime] = None,
    updated_at: Optional[datetime] = None,
) -> dict[str, Any]:
    timestamp = (updated_at or datetime.now()).isoformat(timespec="seconds")
    metadata = {
        "schema_version": PROJECT_METADATA_VERSION,
        "workspace_path": workspace_path,
        "project_path": project_path,
        "data_path": data_path or workspace_data_dir(workspace_path),
        "language": language,
        "last_step": int(last_step),
        "pbf_references": list(pbf_references or []),
        "updated_at": timestamp,
    }
    if created_at is not None:
        metadata["created_at"] = created_at.isoformat(timespec="seconds")
    if selected_location is not None:
        metadata["selected_location"] = location_to_metadata(selected_location)
    if step3_data is not None:
        metadata["step3_data"] = _jsonable(step3_data)
    if step4_data is not None:
        metadata["step4_data"] = _jsonable(step4_data)
    return metadata


def validate_project_metadata(metadata: dict[str, Any]) -> None:
    required_keys = {"schema_version", "workspace_path", "project_path", "data_path", "last_step", "pbf_references"}
    missing = sorted(required_keys.difference(metadata))
    if missing:
        raise ValueError(f"Project metadata is missing required keys: {', '.join(missing)}")
    if metadata["schema_version"] != PROJECT_METADATA_VERSION:
        raise ValueError(f"Unsupported project metadata schema_version: {metadata['schema_version']}")


def save_project_metadata(project_path: str, metadata: dict[str, Any]) -> str:
    os.makedirs(project_path, exist_ok=True)
    metadata = dict(metadata)
    metadata.setdefault("project_path", project_path)
    metadata.setdefault("workspace_path", os.path.dirname(project_path))
    metadata.setdefault("data_path", workspace_data_dir(metadata["workspace_path"]))
    metadata.setdefault("schema_version", PROJECT_METADATA_VERSION)
    metadata.setdefault("language", "de")
    metadata.setdefault("last_step", 1)
    metadata.setdefault("pbf_references", [])
    metadata["updated_at"] = metadata.get("updated_at") or datetime.now().isoformat(timespec="seconds")
    validate_project_metadata(metadata)
    path = project_metadata_path(project_path)
    with open(path, "w", encoding="utf-8") as metadata_file:
        json.dump(_jsonable(metadata), metadata_file, ensure_ascii=False, indent=2, sort_keys=True)
        metadata_file.write("\n")
    _write_json(project_config_path(project_path), metadata)
    return path


def load_project_metadata(project_path_or_file: str) -> dict[str, Any]:
    basename = os.path.basename(project_path_or_file)
    if basename in {PROJECT_METADATA_FILENAME, PROJECT_CONFIG_FILENAME}:
        metadata_file_path = project_path_or_file
    else:
        config_path = project_config_path(project_path_or_file)
        metadata_file_path = config_path if os.path.isfile(config_path) else project_metadata_path(project_path_or_file)
    with open(metadata_file_path, "r", encoding="utf-8") as metadata_file:
        metadata = json.load(metadata_file)
    validate_project_metadata(metadata)
    return metadata


def resolve_active_model_source(base_dir: Optional[str] = None, configured_path: Optional[str] = None) -> str:
    """Resolve exactly one QGIS model source.

    A configured relative path is interpreted relative to the tool root.
    """
    root = os.path.abspath(base_dir or tool_root())
    source = configured_path or ACTIVE_QGIS_MODEL_SOURCE
    return os.path.abspath(source if os.path.isabs(source) else os.path.join(root, source))


def build_pipeline_manifest(
    project_path: str,
    *,
    created_at: Optional[datetime] = None,
    local_crs: Optional[str] = None,
    active_model_source: Optional[str] = None,
    input_pbfs: Optional[Iterable[Mapping[str, Any] | str]] = None,
    phases: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    timestamp = (created_at or datetime.now()).isoformat(timespec="seconds")
    root = os.path.abspath(project_path)
    pbf_entries = [
        pbf_metadata(item) if isinstance(item, (str, os.PathLike)) else dict(item)
        for item in (input_pbfs or [])
    ]
    return {
        "schema_version": PIPELINE_MANIFEST_VERSION,
        "project_name": os.path.basename(root),
        "project_path": root,
        "created_at": timestamp,
        "updated_at": timestamp,
        "local_crs": local_crs,
        "active_model_source": os.path.abspath(
            active_model_source or resolve_active_model_source()
        ),
        "input_pbfs": pbf_entries,
        "phases": {
            phase: {
                "status": "pending",
                "parameters": {},
                "outputs": {},
                "logs": [],
            }
            for phase in (DEFAULT_PIPELINE_PHASES if phases is None else phases)
        },
        "runtime": {},
    }


def validate_pipeline_manifest(manifest: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "project_name",
        "project_path",
        "created_at",
        "active_model_source",
        "input_pbfs",
        "phases",
    }
    missing = sorted(required.difference(manifest))
    if missing:
        raise ValueError(f"Pipeline manifest is missing required keys: {', '.join(missing)}")
    if manifest["schema_version"] != PIPELINE_MANIFEST_VERSION:
        raise ValueError(f"Unsupported pipeline manifest schema_version: {manifest['schema_version']}")
    if not isinstance(manifest["phases"], Mapping):
        raise ValueError("Pipeline manifest 'phases' must be an object")
    invalid = sorted({
        str(phase.get("status")) if isinstance(phase, Mapping) else type(phase).__name__
        for phase in manifest["phases"].values()
        if not isinstance(phase, Mapping) or phase.get("status") not in PIPELINE_PHASE_STATUSES
    })
    if invalid:
        raise ValueError(f"Invalid pipeline phase status: {', '.join(invalid)}")


def save_pipeline_manifest(project_path: str, manifest: Mapping[str, Any]) -> str:
    payload = dict(manifest)
    payload.setdefault("schema_version", PIPELINE_MANIFEST_VERSION)
    payload.setdefault("project_name", os.path.basename(os.path.abspath(project_path)))
    payload.setdefault("project_path", os.path.abspath(project_path))
    payload.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    payload.setdefault("local_crs", None)
    payload.setdefault("active_model_source", resolve_active_model_source())
    payload.setdefault("input_pbfs", [])
    payload.setdefault("phases", {})
    payload.setdefault("runtime", {})
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    validate_pipeline_manifest(payload)
    path = project_manifest_path(project_path)
    _write_json(path, payload)
    return path


def load_pipeline_manifest(project_path_or_file: str) -> dict[str, Any]:
    path = (
        project_path_or_file
        if os.path.basename(project_path_or_file) == PIPELINE_MANIFEST_FILENAME
        else project_manifest_path(project_path_or_file)
    )
    with open(path, "r", encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)
    validate_pipeline_manifest(manifest)
    return manifest


def update_manifest_phase(
    manifest: dict[str, Any],
    phase_name: str,
    status: str,
    *,
    parameters: Optional[Mapping[str, Any]] = None,
    outputs: Optional[Mapping[str, Any]] = None,
    logs: Optional[Sequence[str] | str] = None,
    error: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Update one phase in-place and return the manifest for easy chaining."""
    if status not in PIPELINE_PHASE_STATUSES:
        raise ValueError(f"Unsupported phase status: {status}")
    timestamp = (now or datetime.now()).isoformat(timespec="seconds")
    phase = manifest.setdefault("phases", {}).setdefault(phase_name, {})
    phase.setdefault("parameters", {})
    phase.setdefault("outputs", {})
    phase.setdefault("logs", [])
    phase["status"] = status
    if parameters is not None:
        phase["parameters"] = _jsonable(dict(parameters))
    if outputs is not None:
        phase["outputs"] = _jsonable(dict(outputs))
    if logs is not None:
        phase["logs"] = [logs] if isinstance(logs, str) else list(logs)
    if error is not None:
        phase["error"] = str(error)
    elif status != "failed":
        phase.pop("error", None)
    if status == "running":
        phase["started_at"] = timestamp
        phase.pop("finished_at", None)
    elif status in {"done", "failed"}:
        phase["finished_at"] = timestamp
    phase["updated_at"] = timestamp
    manifest["updated_at"] = timestamp
    return manifest


def mark_phases_stale(
    manifest: dict[str, Any],
    phase_names: Iterable[str],
    *,
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    for phase_name in phase_names:
        update_manifest_phase(manifest, phase_name, "stale", now=now)
        if reason:
            manifest["phases"][phase_name]["stale_reason"] = reason
    return manifest


def mark_downstream_phases_stale(
    manifest: dict[str, Any],
    changed_phase: str,
    *,
    phase_order: Sequence[str] = DEFAULT_PIPELINE_PHASES,
    reason: Optional[str] = None,
    include_changed: bool = True,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Mark a changed phase and every later pipeline consumer as stale."""
    if changed_phase not in phase_order:
        raise ValueError(f"Unknown pipeline phase: {changed_phase}")
    start = phase_order.index(changed_phase) + (0 if include_changed else 1)
    affected = [name for name in phase_order[start:] if name in manifest.get("phases", {})]
    return mark_phases_stale(manifest, affected, reason=reason, now=now)


def manifest_outputs_valid(phase: Mapping[str, Any]) -> bool:
    """Check that every declared file output still exists."""
    outputs = phase.get("outputs", {})
    paths = list(_iter_output_paths(outputs))
    return bool(paths) and all(os.path.exists(path) for path in paths)


def phase_can_be_reused(
    manifest: Mapping[str, Any],
    phase_name: str,
    *,
    max_pbf_age_days: int = DEFAULT_PBF_MAX_AGE_DAYS,
    now: Optional[datetime | date] = None,
) -> bool:
    phase = manifest.get("phases", {}).get(phase_name, {})
    if phase.get("status") != "done" or not manifest_outputs_valid(phase):
        return False
    input_pbfs = manifest.get("input_pbfs", [])
    return bool(input_pbfs) and all(
        not pbf_is_stale(item, max_age_days=max_pbf_age_days, now=now)
        for item in input_pbfs
    )


def location_to_metadata(location: Any) -> dict[str, Any]:
    if isinstance(location, dict):
        return {
            "address": location.get("address") or location.get("display_name") or location.get("name"),
            "latitude": location.get("latitude") or location.get("lat"),
            "longitude": location.get("longitude") or location.get("lon"),
            "raw": _jsonable(location.get("raw")),
        }
    return {
        "address": getattr(location, "address", None),
        "latitude": getattr(location, "latitude", None),
        "longitude": getattr(location, "longitude", None),
        "raw": _jsonable(getattr(location, "raw", None)),
    }


def build_download_job(region: dict) -> Optional[dict]:
    pbf_url = region.get("pbf_url")
    if not pbf_url:
        return None
    osm_id = region.get("osm_id") or region.get("id")
    size_val = region.get("size_bytes") or region.get("bytes", 0)
    if not size_val and pbf_url:
        try:
            from core.app.app_core.geofabrik import _URL_SIZE_CACHE
            size_val = _URL_SIZE_CACHE.get(pbf_url, 0)
        except Exception:
            pass
    return {
        "name": region.get("name") or osm_id,
        "url": pbf_url,
        "osm_id": osm_id,
        "bytes": size_val,
        "filename": os.path.basename(pbf_url),
    }


def add_download_job(download_jobs: list[dict], region: dict) -> bool:
    job = build_download_job(region)
    if not job:
        return False
    if any(existing.get("osm_id") == job.get("osm_id") for existing in download_jobs):
        return False
    download_jobs.append(job)
    return True


def remove_download_job(download_jobs: Iterable[dict], osm_id: str) -> list[dict]:
    return [job for job in download_jobs if job.get("osm_id") != osm_id]


def total_download_size(download_jobs: Iterable[dict]) -> int:
    return sum(int(job.get("size_bytes") or job.get("bytes") or 0) for job in download_jobs)


def normalize_pbf_name(filename: str) -> str:
    stem = filename.lower().removesuffix(".osm.pbf")
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", stem)
    while re.search(r"-(latest|\d{6}|\d{8})$", stem):
        stem = re.sub(r"-(latest|\d{6}|\d{8})$", "", stem)
    return stem


def dated_pbf_filename(filename: str, downloaded_at: Optional[datetime] = None) -> str:
    """Return a normalized PBF filename annotated with its download date."""
    date_prefix = (downloaded_at or datetime.now()).strftime("%Y-%m-%d")
    return f"{date_prefix}_{normalize_pbf_name(os.path.basename(filename))}.osm.pbf"


def pbf_downloaded_at(pbf: Mapping[str, Any] | str) -> Optional[datetime]:
    """Read a PBF download timestamp from manifest data, filename, or mtime."""
    entry = {"path": os.fspath(pbf)} if isinstance(pbf, (str, os.PathLike)) else pbf
    for key in ("downloaded_at", "timestamp", "mtime"):
        value = entry.get(key)
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    path = os.fspath(entry.get("path") or "")
    name = os.path.basename(path or str(entry.get("filename") or ""))
    match = re.match(r"^(\d{4}-\d{2}-\d{2})_", name)
    if match:
        return datetime.strptime(match.group(1), "%Y-%m-%d")
    legacy_match = re.search(r"-(\d{8})\.osm\.pbf$", name, flags=re.IGNORECASE)
    if legacy_match:
        return datetime.strptime(legacy_match.group(1), "%Y%m%d")
    if path and os.path.isfile(path):
        return datetime.fromtimestamp(os.path.getmtime(path))
    return None


def pbf_age_days(pbf: Mapping[str, Any] | str, *, now: Optional[datetime | date] = None) -> Optional[int]:
    downloaded = pbf_downloaded_at(pbf)
    if downloaded is None:
        return None
    current = now or datetime.now()
    current_date = current.date() if isinstance(current, datetime) else current
    return max(0, (current_date - downloaded.date()).days)


def pbf_is_stale(
    pbf: Mapping[str, Any] | str,
    *,
    max_age_days: int = DEFAULT_PBF_MAX_AGE_DAYS,
    now: Optional[datetime | date] = None,
) -> bool:
    age = pbf_age_days(pbf, now=now)
    return age is None or age > max_age_days


def pbf_metadata(
    path: str | os.PathLike[str],
    *,
    region: Optional[str] = None,
    downloaded_at: Optional[datetime] = None,
    checksum: bool | str = False,
) -> dict[str, Any]:
    """Build the reproducibility metadata stored for one input PBF."""
    absolute_path = os.path.abspath(os.fspath(path))
    stat = os.stat(absolute_path)
    timestamp = downloaded_at or pbf_downloaded_at(absolute_path) or datetime.fromtimestamp(stat.st_mtime)
    payload: dict[str, Any] = {
        "path": absolute_path,
        "filename": os.path.basename(absolute_path),
        "region": region or normalize_pbf_name(os.path.basename(absolute_path)),
        "size_bytes": stat.st_size,
        "downloaded_at": timestamp.isoformat(timespec="seconds"),
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }
    if checksum:
        algorithm = checksum if isinstance(checksum, str) else "sha256"
        digest = hashlib.new(algorithm)
        with open(absolute_path, "rb") as pbf_file:
            for chunk in iter(lambda: pbf_file.read(1024 * 1024), b""):
                digest.update(chunk)
        payload["checksum"] = {"algorithm": algorithm, "value": digest.hexdigest()}
    return payload


def verify_pbf_files(project_path: str, download_jobs: Iterable[dict], data_path: Optional[str] = None) -> PbfVerification:
    pbf_dir = data_path or project_input_dir(project_path)
    os.makedirs(pbf_dir, exist_ok=True)

    job_map = {normalize_pbf_name(job["filename"]): job for job in download_jobs}
    expected_basenames = set(job_map.keys())
    actual_files = [name for name in os.listdir(pbf_dir) if name.lower().endswith(".osm.pbf")]
    actual_basenames_map = {}
    for name in actual_files:
        filepath = os.path.join(pbf_dir, name)
        norm_name = normalize_pbf_name(name)
        file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        expected_size = job_map.get(norm_name, {}).get("size_bytes") or job_map.get(norm_name, {}).get("bytes", 0)
        if expected_size > 1024 and file_size < int(expected_size * 0.5):
            logger.warning(f"File {name} is incomplete or corrupted ({file_size} bytes, expected {expected_size} bytes). Ignoring.")
            continue
        actual_basenames_map[norm_name] = name

    found_basenames = sorted(expected_basenames.intersection(actual_basenames_map.keys()))
    missing_basenames = sorted(expected_basenames.difference(actual_basenames_map.keys()))
    found_names = [actual_basenames_map[name] for name in found_basenames]
    found_files = [os.path.join(pbf_dir, name) for name in found_names]
    all_found = not missing_basenames
    user_pbf_path = found_files[0] if all_found and len(found_files) == 1 else None
    return PbfVerification(
        all_found=all_found,
        found_files=found_files,
        found_names=found_names,
        missing_basenames=missing_basenames,
        user_pbf_path=user_pbf_path,
    )


def pending_pbf_download_jobs(
    download_jobs: Iterable[dict],
    verification: Optional[PbfVerification],
    *,
    now: Optional[date] = None,
) -> list[dict]:
    """Return only jobs that are missing locally or have become stale."""
    jobs = list(download_jobs)
    if verification is None:
        return jobs

    found_names = {
        normalize_pbf_name(name): name
        for name in verification.found_names
    }
    pending = []
    for job in jobs:
        found_name = found_names.get(normalize_pbf_name(job.get("filename", "")))
        if found_name is None:
            pending.append(job)
            continue
        age_days = pbf_age_days(found_name, now=now)
        if age_days is not None and age_days > DEFAULT_PBF_MAX_AGE_DAYS:
            pending.append(job)
    return pending


def download_jobs_from_pbf_references(pbf_references: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    jobs = []
    for reference in pbf_references:
        filename = reference.get("filename") or os.path.basename(reference.get("path") or "")
        if not filename:
            continue
        sb = reference.get("size_bytes") or reference.get("bytes", 0)
        jobs.append(
            {
                "name": reference.get("name") or normalize_pbf_name(filename),
                "url": reference.get("url"),
                "osm_id": reference.get("osm_id") or normalize_pbf_name(filename),
                "bytes": sb,
                "filename": filename,
            }
        )
    return jobs


def download_pbf_jobs(
    jobs: Iterable[dict],
    project_path: str,
    *,
    data_path: Optional[str] = None,
    user_agent: str = USER_AGENT,
    is_stopped: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable] = None,
    on_file_ready: Optional[Callable[[str, int, int], None]] = None,
) -> bool:
    """Download PBF files for the selected regions.

    *on_progress* is called with:
        (filename, file_downloaded, file_total, file_index, total_files,
         cumulative_downloaded, cumulative_total)

    Note: ``requests`` transparently decompresses gzip/deflate responses, so
    ``iter_content`` yields *decompressed* bytes while the ``Content-Length``
    header reflects the *compressed* size.  We therefore use the job's
    ``size_bytes`` (from the Geofabrik index – the true uncompressed size) for
    all progress tracking and only fall back to ``Content-Length`` when no
    ``size_bytes`` is available.
    """
    import requests

    job_list = list(jobs)
    total_files = len(job_list)
    # Pre-compute per-file size from job metadata (uncompressed / Geofabrik index size)
    job_sizes = [
        (job.get("size_bytes") or job.get("bytes") or 0)
        for job in job_list
    ]
    cumulative_total = sum(job_sizes)
    cumulative_downloaded = 0
    pbf_dir = data_path or project_input_dir(project_path)
    os.makedirs(pbf_dir, exist_ok=True)

    for file_index, job in enumerate(job_list, start=1):
        if is_stopped and is_stopped():
            return False
        target_filename = dated_pbf_filename(job["filename"])
        target_path = os.path.join(pbf_dir, target_filename)
        file_size = job_sizes[file_index - 1]
        try:
            response = requests.get(job["url"], stream=True, headers={"User-Agent": user_agent}, timeout=60)
            response.raise_for_status()
            content_length = int(response.headers.get("Content-Length", 0))

            # If no size_bytes in job data, use actual written bytes to estimate
            # (we cannot rely on Content-Length because it may be compressed size)
            file_downloaded = 0
            with open(target_path, "wb") as target_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if is_stopped and is_stopped():
                        target_file.close()
                        _remove_partial_file(target_path)
                        return False
                    if not chunk:
                        continue
                    target_file.write(chunk)
                    file_downloaded += len(chunk)

                    # Update file_size from actual written bytes if we had no estimate
                    if file_size == 0 and content_length > 0:
                        # Estimate true size from ratio of decompressed/compressed
                        file_size = content_length
                        old = job_sizes[file_index - 1]
                        job_sizes[file_index - 1] = file_size
                        cumulative_total = cumulative_total - old + file_size

                    if on_progress:
                        on_progress(
                            target_filename,
                            file_downloaded,
                            file_size,
                            file_index,
                            total_files,
                            cumulative_downloaded + file_downloaded,
                            cumulative_total,
                        )

            actual_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0

            # Update tracking with actual written size for accurate cumulative progress
            if actual_size > 0 and actual_size != file_size:
                old = job_sizes[file_index - 1]
                job_sizes[file_index - 1] = actual_size
                cumulative_total = cumulative_total - old + actual_size

            # Integrity check uses Content-Length (compressed wire size) only when
            # the server did *not* use transfer-encoding (i.e. raw download).
            encoding = response.headers.get("Content-Encoding", "")
            if not encoding and content_length > 0 and actual_size < content_length:
                _remove_partial_file(target_path)
                raise IOError(f"Incomplete download for {target_filename}: received {actual_size} bytes, expected {content_length} bytes.")
            cumulative_downloaded += actual_size or file_downloaded
        except Exception:
            _remove_partial_file(target_path)
            raise
        if on_file_ready:
            on_file_ready(target_filename, file_index, len(job_list))
    return True


def build_pbf_references(download_jobs: Iterable[dict], pbf_paths: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
    paths_by_name = {os.path.basename(path): path for path in pbf_paths or []}
    paths_by_normalized_name = {normalize_pbf_name(os.path.basename(path)): path for path in pbf_paths or []}
    references = []
    for job in download_jobs:
        filename = job.get("filename")
        references.append(
            {
                "osm_id": job.get("osm_id"),
                "name": job.get("name"),
                "filename": filename,
                "path": paths_by_name.get(filename) or paths_by_normalized_name.get(normalize_pbf_name(filename or "")),
                "url": job.get("url"),
            }
        )
    if not references:
        for path in pbf_paths or []:
            references.append({"filename": os.path.basename(path), "path": path})
    return references


def build_step4_payload(
    step3_data: dict,
    project_path: str,
    download_jobs: list[dict],
    user_pbf_path: Optional[str],
    *,
    workspace_path: Optional[str] = None,
    data_path: Optional[str] = None,
    pbf_references: Optional[list[dict[str, Any]]] = None,
) -> dict:
    workspace_path = workspace_path or step3_data.get("workspace_path") or os.path.dirname(project_path)
    data_path = data_path or step3_data.get("data_path") or workspace_data_dir(workspace_path)
    pbf_paths = [user_pbf_path] if user_pbf_path else []
    data = dict(step3_data)
    data.update(
        {
            "workspace_path": workspace_path,
            "project_path": project_path,
            "data_path": data_path,
            "download_jobs": download_jobs,
            "user_pbf_path": user_pbf_path,
            "pbf_references": pbf_references if pbf_references is not None else build_pbf_references(download_jobs, pbf_paths),
        }
    )
    return data


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(_jsonable(item) for item in value)
    if hasattr(value, "latitude") and hasattr(value, "longitude"):
        return location_to_metadata(value)
    return str(value)


def _iter_output_paths(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_output_paths(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_output_paths(item)
    elif isinstance(value, (str, os.PathLike)):
        yield os.fspath(value)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value)
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary_path = f"{path}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as target:
        json.dump(_jsonable(payload), target, ensure_ascii=False, indent=2, sort_keys=True)
        target.write("\n")

    for attempt in range(5):
        try:
            os.replace(temporary_path, path)
            return
        except PermissionError:
            if attempt < 4:
                time.sleep(0.05 * (2 ** attempt))
            else:
                try:
                    with open(path, "w", encoding="utf-8") as dest:
                        json.dump(_jsonable(payload), dest, ensure_ascii=False, indent=2, sort_keys=True)
                        dest.write("\n")
                    _remove_partial_file(temporary_path)
                    return
                except Exception:
                    os.replace(temporary_path, path)


def _remove_partial_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
