"""Core processing helpers for step 5."""

from __future__ import annotations

import contextlib
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from threading import Event
from typing import Callable, Mapping, Optional, Sequence

from core.app.app_core.location import get_alpha2_code
from core.app.app_core.project import PROJECT_INPUT_DIR, tool_root


class PbfPrepareState(str, Enum):
    NO_PROJECT = "no_project"
    NO_FILES = "no_files"
    READY_FINAL = "ready_final"
    READY_SINGLE = "ready_single"
    NEEDS_MERGE = "needs_merge"


@dataclass(frozen=True)
class PbfPreparationStatus:
    state: PbfPrepareState
    project_path: Optional[str]
    input_dir: Optional[str]
    input_files: list[str]
    raw_files: list[str]
    final_pbf_path: Optional[str]
    selected_pbf_path: Optional[str]
    final_filename: str

    @property
    def needs_merge(self) -> bool:
        return self.state == PbfPrepareState.NEEDS_MERGE

    @property
    def is_usable(self) -> bool:
        return self.selected_pbf_path is not None


@dataclass(frozen=True)
class ProcessingRunResult:
    output_path: Optional[str] = None
    message: str = ""
    count: Optional[int] = None
    return_code: int = 0
    outputs: Optional[dict[str, object]] = None


@dataclass
class RegisteredProcessingScripts:
    """Algorithms temporarily added to QGIS' Processing registry."""

    registrations: list[tuple[object, str]]

    def unregister(self) -> None:
        for owner, algorithm_id in reversed(self.registrations):
            try:
                owner.removeAlgorithm(algorithm_id)
            except (AttributeError, RuntimeError):
                pass
        self.registrations.clear()


@dataclass(frozen=True)
class QgisModelRunConfig:
    model_path: str
    parameters: dict[str, object]
    qgis_interpreter: Optional[str] = None


class _LogTextStream:
    """Line-buffered text stream forwarding Python console output to the UI."""

    encoding = "utf-8"

    def __init__(self, callback: Optional[Callable[[str], None]], fallback):
        self.callback = callback
        self.fallback = fallback
        self.buffer = ""

    def write(self, text) -> int:
        value = str(text)
        self.buffer += value
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if self.callback and line.rstrip():
                self.callback(line.rstrip())
        return len(value)

    def flush(self) -> None:
        if self.callback and self.buffer.rstrip():
            self.callback(self.buffer.rstrip())
        self.buffer = ""

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self.fallback.fileno()

    def __getattr__(self, name):
        return getattr(self.fallback, name)


def inspect_pbf_preparation(
    project_path: Optional[str],
    selected_location=None,
    *,
    data_path: Optional[str] = None,
    pbf_references: Optional[Sequence[dict]] = None,
    user_pbf_path: Optional[str] = None,
) -> PbfPreparationStatus:
    """Inspect Step-5 PBF readiness without mutating files."""
    country_code = get_alpha2_code(selected_location)
    final_filename = f"{country_code}.osm.pbf"
    input_dir = data_path or (os.path.join(project_path, PROJECT_INPUT_DIR) if project_path else None)
    if not input_dir:
        return PbfPreparationStatus(
            state=PbfPrepareState.NO_PROJECT,
            project_path=project_path,
            input_dir=None,
            input_files=[],
            raw_files=[],
            final_pbf_path=None,
            selected_pbf_path=None,
            final_filename=final_filename,
        )

    if not os.path.isdir(input_dir):
        return PbfPreparationStatus(
            state=PbfPrepareState.NO_FILES,
            project_path=project_path,
            input_dir=input_dir,
            input_files=[],
            raw_files=[],
            final_pbf_path=None,
            selected_pbf_path=None,
            final_filename=final_filename,
        )

    input_files = _referenced_pbf_files(input_dir, pbf_references)
    if not input_files:
        input_files = sorted(
            os.path.join(input_dir, name)
            for name in os.listdir(input_dir)
            if name.lower().endswith(".osm.pbf")
        )
    if user_pbf_path and os.path.isfile(user_pbf_path) and user_pbf_path not in input_files:
        input_files.append(user_pbf_path)
        input_files.sort()
    final_pbf_path = os.path.join(input_dir, final_filename)
    final_exists = os.path.exists(final_pbf_path)
    raw_files = [path for path in input_files if os.path.normpath(path) != os.path.normpath(final_pbf_path)]

    if final_exists:
        state = PbfPrepareState.READY_FINAL
        selected_pbf_path = final_pbf_path
    elif len(raw_files) == 1:
        state = PbfPrepareState.READY_SINGLE
        selected_pbf_path = raw_files[0]
    elif len(raw_files) > 1:
        state = PbfPrepareState.NEEDS_MERGE
        selected_pbf_path = None
    else:
        state = PbfPrepareState.NO_FILES
        selected_pbf_path = None

    return PbfPreparationStatus(
        state=state,
        project_path=project_path,
        input_dir=input_dir,
        input_files=input_files,
        raw_files=raw_files,
        final_pbf_path=final_pbf_path if final_exists else None,
        selected_pbf_path=selected_pbf_path,
        final_filename=final_filename,
    )


def merge_pbf_files(
    input_files: Sequence[str],
    output_path: str,
    *,
    stop_event: Optional[Event] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> ProcessingRunResult:
    """Merge PBF files with pyosmium while deduplicating OSM object ids."""
    if not input_files:
        raise ValueError("At least one PBF file is required for merge or copy")
    _ensure_parent_dir(output_path)

    # Optimization: if only 1 input file, do not rewrite via osmium; simply copy directly.
    if len(input_files) == 1:
        src = input_files[0]
        filename = os.path.basename(src)
        if on_log:
            on_log(f"Einzelne PBF-Datei wird kopiert (kein Merge notwendig): {filename} -> {os.path.basename(output_path)}")
        shutil.copyfile(src, output_path)
        return ProcessingRunResult(output_path=output_path, message="Einzelne PBF-Datei kopiert", count=1)

    osmium = _require_osmium()

    class MergeHandler(osmium.SimpleHandler):
        def __init__(self, writer, current_filename="", on_log=None):
            super().__init__()
            self.writer = writer
            self.current_filename = current_filename
            self.on_log = on_log
            self.written_nodes = set()
            self.written_ways = set()
            self.written_relations = set()
            self.total_count = 0
            self._last_log_count = 0

        def _check_progress(self):
            self.total_count += 1
            if self.on_log and (self.total_count - self._last_log_count >= 250000):
                self._last_log_count = self.total_count
                self.on_log(f"Zusammenführen von {self.current_filename}: {self.total_count:,} OSM-Objekte verarbeitet...")

        def node(self, node):
            if node.id not in self.written_nodes:
                self.writer.add_node(node)
                self.written_nodes.add(node.id)
            self._check_progress()

        def way(self, way):
            if way.id not in self.written_ways:
                self.writer.add_way(way)
                self.written_ways.add(way.id)
            self._check_progress()

        def relation(self, relation):
            if relation.id not in self.written_relations:
                self.writer.add_relation(relation)
                self.written_relations.add(relation.id)
            self._check_progress()

    writer = None
    try:
        writer = osmium.SimpleWriter(output_path)
        handler = MergeHandler(writer, on_log=on_log)
        for index, pbf_file in enumerate(input_files, start=1):
            if stop_event and stop_event.is_set():
                raise InterruptedError("PBF merge stopped by user")
            filename = os.path.basename(pbf_file)
            if on_log:
                on_log(f"Starte Zusammenführen von PBF {index}/{len(input_files)}: {filename}")
            handler.current_filename = filename
            handler.apply_file(pbf_file)
            if on_log:
                on_log(f"PBF {index}/{len(input_files)} ({filename}) verarbeitet ({handler.total_count:,} Objekte gesamt).")
        writer.close()
        writer = None
        return ProcessingRunResult(output_path=output_path, message="PBF merge complete", count=len(input_files))
    except Exception:
        if writer is not None:
            writer.close()
        if os.path.exists(output_path):
            os.remove(output_path)
        raise


def extract_cities_towns(
    pbf_path: str,
    output_path: str,
    *,
    stop_event: Optional[Event] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> ProcessingRunResult:
    osmium = _require_osmium()
    geopandas, Point, _unary_union = _require_geo_stack()

    class CityHandler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.features = []

        def node(self, node):
            if stop_event and stop_event.is_set():
                raise InterruptedError("City extraction stopped by user")
            if node.location.valid() and node.tags.get("place") in ("city", "town"):
                self.features.append(
                    {"geometry": Point(node.location.lon, node.location.lat), **{tag.k: tag.v for tag in node.tags}}
                )

    if on_log:
        on_log(f"Extracting cities/towns from {os.path.basename(pbf_path)}")
    handler = CityHandler()
    handler.apply_file(pbf_path, locations=True, idx="flex_mem")
    _write_geodataframe(geopandas, handler.features, output_path, "GeoJSON")
    return ProcessingRunResult(output_path=output_path, message="City/town extraction complete", count=len(handler.features))


def extract_non_residential_areas(
    pbf_path: str,
    output_path: str,
    filter_polygon,
    *,
    stop_event: Optional[Event] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> ProcessingRunResult:
    osmium = _require_osmium()
    geopandas, _Point, _unary_union = _require_geo_stack()
    tags_config = _non_residential_tags()

    class NonResidentialHandler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.features = []
            self.wkbfab = osmium.geom.WKBFactory()

        def area(self, area):
            if stop_event and stop_event.is_set():
                raise InterruptedError("Non-residential extraction stopped by user")
            if not _matches_tags(area.tags, tags_config):
                return
            try:
                geometry = geopandas.GeoSeries.from_wkb([self.wkbfab.create_multipolygon(area)], crs="EPSG:4326").iloc[0]
                if not geometry.is_valid or not filter_polygon.intersects(geometry):
                    return
                clipped = geometry.intersection(filter_polygon)
                for part in _polygon_parts(clipped):
                    self.features.append({"geometry": part, **{tag.k: tag.v for tag in area.tags}})
            except Exception:
                return

    if on_log:
        on_log(f"Extracting non-residential areas from {os.path.basename(pbf_path)}")
    handler = NonResidentialHandler()
    handler.apply_file(pbf_path, locations=True, idx="flex_mem")
    _write_geodataframe(geopandas, handler.features, output_path, "GeoJSON")
    return ProcessingRunResult(output_path=output_path, message="Non-residential extraction complete", count=len(handler.features))


def extract_street_network(
    pbf_path: str,
    output_path: str,
    area_definitions: dict[str, object],
    *,
    special_tags: str = "",
    stop_event: Optional[Event] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> ProcessingRunResult:
    osmium = _require_osmium()
    geopandas, _Point, _unary_union = _require_geo_stack()
    hw_filters = {
        "PA_IA1": {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
                   "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
                   "service", "road"},
        "IA2": {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
                "secondary_link", "tertiary", "tertiary_link", "unclassified", "road"},
        "OA": {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
               "secondary_link"},
    }
    parsed_special_tags = _parse_special_tags(special_tags)
    node_ids, ways_data = set(), {}

    class RoadNetHandler(osmium.SimpleHandler):
        def __init__(self, filter_polygon, highway_tags, special_kvs=None):
            super().__init__()
            self.filter_polygon = filter_polygon
            self.highway_tags = set(highway_tags or [])
            self.special_kvs = special_kvs or []
            self.wkbfab = osmium.geom.WKBFactory()

        def way(self, way):
            if stop_event and stop_event.is_set():
                raise InterruptedError("Street-network extraction stopped by user")
            tags = {tag.k: tag.v for tag in way.tags} if way.tags else {}
            relevant = ("highway" in tags and tags["highway"] in self.highway_tags) or any(
                key in tags and tags[key] == value for key, value in self.special_kvs
            )
            if not relevant:
                return
            if self.filter_polygon:
                try:
                    line = geopandas.GeoSeries.from_wkb([self.wkbfab.create_linestring(way)], crs="EPSG:4326").iloc[0]
                    if not self.filter_polygon.intersects(line):
                        return
                except Exception:
                    return
            refs = [node.ref for node in way.nodes]
            node_ids.update(refs)
            ways_data[way.id] = {"nodes": refs, "tags": tags}

    phases = [
        ("PA+IA1", area_definitions.get("PA_IA1_poly"), hw_filters["PA_IA1"], None),
        ("IA2", area_definitions.get("PA_IA1_IA2_poly"), hw_filters["IA2"], None),
        ("OA", None, hw_filters["OA"], None),
        ("Special tags", None, None, parsed_special_tags),
    ]
    for label, filter_polygon, highway_tags, special_kvs in phases:
        if label == "Special tags" and not special_kvs:
            continue
        if label in {"PA+IA1", "IA2"} and not filter_polygon:
            continue
        if on_log:
            on_log(f"Extracting street-network phase: {label}")
        RoadNetHandler(filter_polygon, highway_tags, special_kvs).apply_file(pbf_path, locations=True, idx="flex_mem")

    class FinalNetWriter(osmium.SimpleHandler):
        def __init__(self, writer):
            super().__init__()
            self.writer = writer
            self.nodes_out = 0
            self.ways_out = 0

        def node(self, node):
            if stop_event and stop_event.is_set():
                raise InterruptedError("Street-network extraction stopped by user")
            if node.id in node_ids:
                self.writer.add_node(node)
                self.nodes_out += 1

        def way(self, way):
            if stop_event and stop_event.is_set():
                raise InterruptedError("Street-network extraction stopped by user")
            way_info = ways_data.get(way.id)
            if way_info and way_info.get("nodes"):
                self.writer.add_way(
                    osmium.osm.mutable.Way(
                        id=way.id,
                        nodes=way_info["nodes"],
                        tags={key: str(value) for key, value in way_info.get("tags", {}).items()},
                    )
                )
                self.ways_out += 1

    _ensure_parent_dir(output_path)
    with osmium.SimpleWriter(output_path) as writer:
        final_writer = FinalNetWriter(writer)
        final_writer.apply_file(pbf_path, locations=True, idx="flex_mem")
    return ProcessingRunResult(
        output_path=output_path,
        message=f"Street-network extraction complete ({final_writer.ways_out} ways, {final_writer.nodes_out} nodes)",
        count=final_writer.ways_out,
    )


def build_area_definitions(grid_map_data: Sequence[dict], selected_cells: dict[str, Sequence[int] | set[int]]) -> dict[str, object]:
    _geopandas, _Point, unary_union = _require_geo_stack()
    cell_map = {cell["id"]: cell.get("shapely_poly_wgs84") for cell in grid_map_data if "id" in cell}
    pa_ia1 = _union_selected(cell_map, selected_cells, ("PA", "IA1"), unary_union)
    pa_ia1_ia2 = _union_selected(cell_map, selected_cells, ("PA", "IA1", "IA2"), unary_union)
    return {"PA_IA1_poly": pa_ia1, "PA_IA1_IA2_poly": pa_ia1_ia2}


def build_selected_area_polygon(grid_map_data: Sequence[dict], selected_cells: dict[str, Sequence[int] | set[int]], mode: str = "PA"):
    _geopandas, _Point, unary_union = _require_geo_stack()
    cell_map = {cell["id"]: cell.get("shapely_poly_wgs84") for cell in grid_map_data if "id" in cell}
    polygon = _union_selected(cell_map, selected_cells, (mode,), unary_union)
    if polygon is None:
        raise ValueError(f"No valid {mode} polygons are selected")
    return polygon


def build_qgis_model_command(config: QgisModelRunConfig) -> list[str]:
    if not config.model_path:
        raise ValueError("QGIS model path is required")
    executable = config.qgis_interpreter or "python3"
    command = [executable, config.model_path]
    for key in sorted(config.parameters):
        value = config.parameters[key]
        if value is None or value == "":
            continue
        command.append(f"{key}={value}")
    return command


def run_qgis_model_subprocess(
    config: QgisModelRunConfig,
    *,
    stop_event: Optional[Event] = None,
    on_log: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    on_progress_text: Optional[Callable[[str], None]] = None,
) -> ProcessingRunResult:
    command = build_qgis_model_command(config)
    if on_log:
        on_log("Running QGIS model command: " + " ".join(command))
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert process.stdout is not None
    for line in process.stdout:
        if stop_event and stop_event.is_set():
            process.terminate()
            raise InterruptedError("QGIS model run stopped by user")
        if on_log:
            on_log(line.rstrip())
        if on_progress:
            match = re.search(r"(?<!\d)(100|\d{1,2})(?:\.\d+)?\s*%", line)
            if match:
                on_progress(int(float(match.group(1))))
                if on_progress_text:
                    on_progress_text(line.strip())
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"QGIS model failed with exit code {return_code}")
    return ProcessingRunResult(message="QGIS model run complete", return_code=return_code)


def run_qgis_model(
    config: QgisModelRunConfig,
    *,
    stop_event: Optional[Event] = None,
    on_log: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    on_progress_text: Optional[Callable[[str], None]] = None,
) -> ProcessingRunResult:
    """Run a QGIS model through the appropriate centralized adapter."""
    if config.model_path.lower().endswith(".model3"):
        return run_qgis_model_processing(
            config,
            stop_event=stop_event,
            on_log=on_log,
            on_progress=on_progress,
            on_progress_text=on_progress_text,
        )
    return run_qgis_model_subprocess(
        config,
        stop_event=stop_event,
        on_log=on_log,
        on_progress=on_progress,
        on_progress_text=on_progress_text,
    )


def _get_dynamic_gdal_cache_mb() -> int:
    """Dynamically determine optimal GDAL cache in MB based on available system memory."""
    avail_mb = 0
    try:
        import psutil
        avail_mb = int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:
        if os.name == "nt":
            try:
                import ctypes
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]
                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
                avail_mb = int(stat.ullAvailPhys / (1024 * 1024))
            except Exception:
                pass

    if avail_mb <= 0:
        return 2048
    # Use 50% of currently available physical RAM for GDAL raster cache, min 2048 MB
    return max(2048, int(avail_mb * 0.50))


def _ensure_gdal_environment() -> None:
    """Ensure GDAL and OSGeo4W binaries and data directories are present in os.environ."""
    # Use thread-safe single-thread execution for in-process GDAL/OpenMP raster pipelines to prevent C++ heap crashes
    os.environ["GDAL_NUM_THREADS"] = "1"
    os.environ["GDAL_CONCURRENCY"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    cache_mb = _get_dynamic_gdal_cache_mb()
    os.environ["GDAL_CACHEMAX"] = str(cache_mb)

    local_appdata = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_appdata, "Programs", "OSGeo4W", "bin"),
        r"C:\OSGeo4W\bin",
        r"C:\OSGeo4W64\bin",
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            path_env = os.environ.get("PATH", "")
            if candidate not in path_env:
                os.environ["PATH"] = candidate + os.pathsep + path_env
            gdal_data = os.path.join(os.path.dirname(candidate), "share", "gdal")
            if os.path.isdir(gdal_data) and "GDAL_DATA" not in os.environ:
                os.environ["GDAL_DATA"] = gdal_data
            proj_data = os.path.join(os.path.dirname(candidate), "share", "proj")
            if os.path.isdir(proj_data) and "PROJ_LIB" not in os.environ:
                os.environ["PROJ_LIB"] = proj_data
                os.environ["PROJ_DATA"] = proj_data
            break


def _create_fallback_geotiff(file_path: str) -> bool:
    """Create a minimal valid NoData GeoTIFF at file_path when an algorithm skips writing an empty raster."""
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        try:
            from osgeo import gdal
            driver = gdal.GetDriverByName("GTiff")
            if driver:
                ds = driver.Create(file_path, 10, 10, 1, gdal.GDT_Float32)
                ds.SetGeoTransform([0, 1, 0, 0, 0, -1])
                band = ds.GetRasterBand(1)
                band.SetNoDataValue(-9999)
                band.Fill(-9999)
                band.FlushCache()
                ds = None
                return True
        except Exception:
            pass

        # Fallback if osgeo.gdal is unavailable: write minimal TIFF header
        with open(file_path, "wb") as f:
            f.write(b"II*\x00\x08\x00\x00\x00")
        return True
    except Exception:
        return False


def _patch_gdal_utils_for_live_progress() -> None:
    """Patch QGIS GdalUtils.runGdal to emit realtime progress chunks instead of buffering the whole line."""
    try:
        from processing.algs.gdal.GdalUtils import GdalUtils
        if getattr(GdalUtils, "_pando_live_progress_patched", False):
            return

        import re
        from qgis.core import (
            Qgis,
            QgsApplication,
            QgsBlockingProcess,
            QgsMessageLog,
            QgsProcessingException,
            QgsProcessingFeedback,
            QgsRunProcess,
            QgsSettings,
        )
        from qgis.PyQt.QtCore import QProcess

        def patched_run_gdal(commands, feedback=None):
            if feedback is None:
                feedback = QgsProcessingFeedback()
            envval = os.getenv("PATH", "")
            isDarwin = False
            try:
                isDarwin = platform.system() == "Darwin"
            except OSError:
                pass
            if isDarwin and os.path.isfile(
                os.path.join(QgsApplication.prefixPath(), "Contents", "MacOS", "gdalinfo")
            ):
                os.environ["PATH"] = "{}{}{}".format(
                    os.path.join(QgsApplication.prefixPath(), "Contents", "MacOS"),
                    os.pathsep,
                    envval,
                )
            else:
                settings = QgsSettings()
                path = settings.value("/GdalTools/gdalPath", "")
                if path and path.lower() not in envval.lower().split(os.pathsep):
                    envval += f"{os.pathsep}{path}"
                    os.putenv("PATH", envval)

            fused_command = " ".join([str(c) for c in commands])
            QgsMessageLog.logMessage(fused_command, "Processing", Qgis.MessageLevel.Info)
            feedback.pushInfo(GdalUtils.tr("GDAL command:"))
            feedback.pushCommandInfo(fused_command)
            feedback.pushInfo(GdalUtils.tr("GDAL command output:"))

            loglines = [GdalUtils.tr("GDAL execution console output")]
            progress_string_list = [str(a) for a in range(0, 100)]

            def on_stdout(ba):
                val = ba.data().decode("UTF-8", errors="replace")
                # Catch progress reports
                if val == "100 - done.":
                    on_stdout.progress = 100
                    feedback.setProgress(on_stdout.progress)
                else:
                    match = re.match(r".*?(\d+)\.+\s*$", val)
                    found_number = False
                    if match:
                        int_val = match.group(1)
                        if int_val in progress_string_list:
                            on_stdout.progress = int(int_val)
                            feedback.setProgress(on_stdout.progress)
                            found_number = True

                    if not found_number and val == ".":
                        on_stdout.progress += 2.5
                        feedback.setProgress(on_stdout.progress)

                on_stdout.buffer += val
                clean_buf = on_stdout.buffer.replace("\r", "").replace("\n", "").strip()
                if clean_buf and feedback:
                    feedback.pushConsoleInfo(f"PROGRESS_INLINE:{clean_buf}")

                if on_stdout.buffer.endswith("\n") or on_stdout.buffer.endswith("\r"):
                    loglines.append(on_stdout.buffer.rstrip())
                    on_stdout.buffer = ""

            on_stdout.progress = 0
            on_stdout.buffer = ""

            def on_stderr(ba):
                val = ba.data().decode("UTF-8", errors="replace")
                on_stderr.buffer += val

                if on_stderr.buffer.endswith("\n") or on_stderr.buffer.endswith("\r"):
                    feedback.reportError(on_stderr.buffer.rstrip())
                    loglines.append(on_stderr.buffer.rstrip())
                    on_stderr.buffer = ""

            on_stderr.buffer = ""

            command, *arguments = QgsRunProcess.splitCommand(fused_command)
            proc = QgsBlockingProcess(command, arguments)
            proc.setStdOutHandler(on_stdout)
            proc.setStdErrHandler(on_stderr)

            res = proc.run(feedback)

            if on_stdout.buffer:
                clean_buf = on_stdout.buffer.replace("\r", "").replace("\n", "").strip()
                if clean_buf and feedback:
                    feedback.pushConsoleInfo(f"PROGRESS_INLINE:{clean_buf}")
                loglines.append(on_stdout.buffer.rstrip())
            if on_stderr.buffer:
                loglines.append(on_stderr.buffer.rstrip())

            if feedback.isCanceled() and res != 0:
                feedback.pushInfo(GdalUtils.tr("Process was canceled and did not complete"))
            elif (
                not feedback.isCanceled()
                and proc.exitStatus() == QProcess.ExitStatus.CrashExit
            ):
                raise QgsProcessingException(
                    GdalUtils.tr("Process was unexpectedly terminated")
                )
            elif res == 0:
                feedback.pushInfo(GdalUtils.tr("Process completed successfully"))
            elif proc.processError() == QProcess.ProcessError.FailedToStart:
                raise QgsProcessingException(
                    GdalUtils.tr(
                        "Process {} failed to start. Either {} is missing, or you may have insufficient permissions to run the program."
                    ).format(command, command)
                )
            else:
                feedback.reportError(
                    GdalUtils.tr("Process returned error code {}").format(res)
                )

            return loglines

        GdalUtils.runGdal = staticmethod(patched_run_gdal)
        GdalUtils._pando_live_progress_patched = True
    except Exception:
        pass


def run_qgis_model_processing(
    config: QgisModelRunConfig,
    *,
    stop_event: Optional[Event] = None,
    on_log: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    on_progress_text: Optional[Callable[[str], None]] = None,
) -> ProcessingRunResult:
    """Run a QGIS `.model3` file via the QGIS 4 Processing adapter."""
    _ensure_gdal_environment()

    try:
        import processing
        from qgis.core import QgsProcessingFeedback, QgsProcessingModelAlgorithm
    except ImportError as exc:
        raise RuntimeError("PyQGIS Processing is required to run .model3 files") from exc

    _patch_gdal_utils_for_live_progress()

    stage_names = _model_stage_names(config.model_path)

    class LogFeedback(QgsProcessingFeedback):
        def __init__(self):
            super().__init__()
            self._completed_stages: set[str] = set()
            self._last_progress = -1
            self._last_detail = ""

        def _emit_progress(self, progress) -> None:
            value = max(0, min(100, int(round(float(progress)))))
            if value != self._last_progress:
                self._last_progress = value
                if on_progress:
                    on_progress(value)

        def _record(self, message) -> None:
            text = str(message).strip()
            if text and on_log:
                on_log(text)
            folded = text.casefold()
            matched_detail = ""
            for stage in stage_names:
                if stage not in self._completed_stages and stage.casefold() in folded:
                    self._completed_stages.add(stage)
                    matched_detail = stage
                    self._emit_progress(
                        len(self._completed_stages) / max(1, len(stage_names)) * 100
                    )
                elif stage.casefold() in folded:
                    matched_detail = stage
            if (
                not matched_detail
                and any(
                    marker in folded
                    for marker in (
                        "preparing algorithm",
                        "running algorithm",
                        "executing algorithm",
                        "algorithmus wird",
                    )
                )
            ):
                matched_detail = text
            if (
                on_progress_text
                and matched_detail
                and matched_detail != self._last_detail
            ):
                self._last_detail = matched_detail
                on_progress_text(matched_detail)

        def pushInfo(self, info):
            self._record(info)
            return super().pushInfo(info)

        def reportError(self, error, fatalError=False):
            self._record(error)
            return super().reportError(error, fatalError)

        def setProgressText(self, text):
            self._record(text)
            return super().setProgressText(text)

        def setProgress(self, progress):
            self._emit_progress(progress)
            return super().setProgress(progress)

        def pushCommandInfo(self, info):
            self._record(info)
            parent = getattr(super(), "pushCommandInfo", None)
            return parent(info) if parent else None

        def pushConsoleInfo(self, info):
            self._record(info)
            parent = getattr(super(), "pushConsoleInfo", None)
            return parent(info) if parent else None

        def pushDebugInfo(self, info):
            self._record(info)
            parent = getattr(super(), "pushDebugInfo", None)
            return parent(info) if parent else None

        def pushWarning(self, warning):
            self._record(warning)
            parent = getattr(super(), "pushWarning", None)
            return parent(warning) if parent else None

        def isCanceled(self):
            return bool(stop_event and stop_event.is_set()) or super().isCanceled()

    algorithm = QgsProcessingModelAlgorithm()
    loaded = algorithm.fromFile(config.model_path)
    if loaded is False:
        raise RuntimeError(f"Could not load QGIS model: {config.model_path}")

    algorithm_id = algorithm.id()
    if on_log:
        on_log(f"Running QGIS Processing model: {algorithm_id}")
    feedback = LogFeedback()
    progress_signal = getattr(feedback, "progressChanged", None)
    if on_progress and progress_signal is not None:
        progress_signal.connect(feedback._emit_progress)
    max_retries = 3
    result = None
    msg_handler = None
    msg_log = None

    import gc
    gc.collect()

    # Safely attach PyQGIS Message Log hook for this run
    try:
        from qgis.core import QgsApplication
        qgis_app = QgsApplication.instance()
        if qgis_app:
            msg_log = qgis_app.messageLog()
            if msg_log and hasattr(msg_log, "messageReceived"):
                def msg_handler(msg, tag, level):
                    try:
                        if on_log and str(msg).strip():
                            on_log(f"[QGIS-{tag}] {str(msg).strip()}")
                    except Exception:
                        pass
                msg_log.messageReceived.connect(msg_handler)
    except Exception:
        pass

    # Ensure QGIS Processing temporary folder exists before running algorithm
    try:
        from qgis.core import QgsProcessingUtils
        temp_proc_dir = QgsProcessingUtils.tempFolder()
        if temp_proc_dir and not os.path.exists(temp_proc_dir):
            os.makedirs(temp_proc_dir, exist_ok=True)
    except Exception:
        pass

    # Build spatial indexes for any input GPKG vector layer that lacks one to prevent memory allocation crashes
    try:
        from qgis.core import QgsVectorLayer
        for param_name, param_val in config.parameters.items():
            if isinstance(param_val, str) and param_val.lower().endswith(".gpkg") and os.path.isfile(param_val):
                vlayer = QgsVectorLayer(param_val, "tmp_spatial_idx_check", "ogr")
                if vlayer.isValid() and vlayer.dataProvider():
                    if not vlayer.hasSpatialIndex():
                        if on_log:
                            on_log(f"Erzeuge räumlichen Index für Layer '{os.path.basename(param_val)}'...")
                        vlayer.dataProvider().createSpatialIndex()
                del vlayer
    except Exception as exc:
        if on_log:
            on_log(f"Hinweis beim Erzeugen des räumlichen Index: {exc}")

    try:
        for attempt in range(max_retries):
            try:
                if on_log:
                    stdout_stream = _LogTextStream(on_log, sys.__stdout__)
                    stderr_stream = _LogTextStream(on_log, sys.__stderr__)
                    try:
                        with contextlib.redirect_stdout(stdout_stream), contextlib.redirect_stderr(stderr_stream):
                            result = processing.run(algorithm, dict(config.parameters), feedback=feedback)
                    finally:
                        stdout_stream.flush()
                        stderr_stream.flush()
                else:
                    result = processing.run(algorithm, dict(config.parameters), feedback=feedback)
                break
            except Exception as exc:
                import traceback
                err_str = str(exc)
                tb_str = traceback.format_exc()
                if on_log:
                    on_log(f"QGIS Modellfehler aufgetreten (Versuch {attempt+1}/{max_retries}): {exc}\n{tb_str}")

                match = re.search(r"Could not load source layer for [^:]+:\s*(.+?\.tif)\s+not found", err_str, re.IGNORECASE)
                if match and attempt < max_retries - 1:
                    missing_tif = match.group(1).strip()
                    if on_log:
                        on_log(f"Hinweis: Temporäre Raster-Datei fehlte ({missing_tif}). Erzeuge Fallback-Raster und wiederhole Modellschritt...")
                    _create_fallback_geotiff(missing_tif)
                    continue
                raise
    finally:
        if msg_log and msg_handler:
            try:
                msg_log.messageReceived.disconnect(msg_handler)
            except Exception:
                pass
        gc.collect()

    feedback._emit_progress(100)
    if stop_event and stop_event.is_set():
        raise InterruptedError("QGIS model run stopped by user")
    return ProcessingRunResult(
        message=f"QGIS model run complete: {result}",
        outputs=dict(result or {}),
    )


def _model_stage_names(model_path: str) -> list[str]:
    """Return active child descriptions used as a progress-text fallback."""

    try:
        root = ET.parse(model_path).getroot()
    except (OSError, ET.ParseError):
        return []
    stages = []
    for item in root.iter("Option"):
        direct = {child.get("name"): child.get("value") for child in item}
        if not direct.get("alg_id") or direct.get("active") == "false":
            continue
        description = direct.get("component_description") or direct["alg_id"]
        if description and description not in stages:
            stages.append(description)
    return stages


def _copy_if_newer(src: str, dest: str) -> bool:
    if not os.path.exists(dest):
        shutil.copy2(src, dest)
        return True
    try:
        src_stat = os.stat(src)
        dest_stat = os.stat(dest)
        if src_stat.st_mtime > dest_stat.st_mtime or src_stat.st_size != dest_stat.st_size:
            shutil.copy2(src, dest)
            return True
    except Exception:
        pass
    return False


def ensure_qgis_scripts_installed(
    root_dir: Optional[str] = None,
    *,
    on_log: Optional[Callable[[str], None]] = None,
) -> list[str]:
    """Copy bundled QGIS scripts (*.py) and models (*.model3) to QGIS profile directories.

    Target directories:
    - Active PyQGIS profile directory (via QgsApplication.qgisSettingsDirPath() if available)
    - %APPDATA%/QGIS/QGIS4/profiles/default/processing/scripts (and /models)
    - %APPDATA%/QGIS/QGIS3/profiles/default/processing/scripts (and /models)
    """
    root = root_dir or tool_root()
    scripts_src = os.path.join(root, "core", "scripts", "qgis", "scripts")
    models_src = os.path.join(root, "core", "scripts", "qgis", "models")

    target_script_dirs: list[str] = []
    target_model_dirs: list[str] = []

    # 1. Check APPDATA paths for QGIS 4 and QGIS 3
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    if appdata:
        for version in ("QGIS4", "QGIS3"):
            prof_dir = os.path.join(appdata, "QGIS", version, "profiles")
            if os.path.isdir(prof_dir):
                for p_name in os.listdir(prof_dir):
                    p_path = os.path.join(prof_dir, p_name)
                    if os.path.isdir(p_path):
                        target_script_dirs.append(os.path.join(p_path, "processing", "scripts"))
                        target_model_dirs.append(os.path.join(p_path, "processing", "models"))

        # Fallbacks for standard default profiles if profile dirs don't exist yet
        target_script_dirs.append(os.path.join(appdata, "QGIS", "QGIS4", "profiles", "default", "processing", "scripts"))
        target_model_dirs.append(os.path.join(appdata, "QGIS", "QGIS4", "profiles", "default", "processing", "models"))
        target_script_dirs.append(os.path.join(appdata, "QGIS", "QGIS3", "profiles", "default", "processing", "scripts"))
        target_model_dirs.append(os.path.join(appdata, "QGIS", "QGIS3", "profiles", "default", "processing", "models"))

    # 2. Check active PyQGIS profile
    try:
        from qgis.core import QgsApplication
        settings_dir = QgsApplication.qgisSettingsDirPath() if hasattr(QgsApplication, "qgisSettingsDirPath") else ""
        if settings_dir and os.path.isdir(settings_dir):
            target_script_dirs.append(os.path.join(settings_dir, "processing", "scripts"))
            target_model_dirs.append(os.path.join(settings_dir, "processing", "models"))
    except Exception:
        pass

    unique_script_dirs = list(dict.fromkeys(target_script_dirs))
    unique_model_dirs = list(dict.fromkeys(target_model_dirs))

    copied_files: list[str] = []

    # Copy scripts
    if os.path.isdir(scripts_src):
        script_files = [f for f in os.listdir(scripts_src) if f.endswith(".py")]
        for script_file in script_files:
            src_file = os.path.join(scripts_src, script_file)
            for dest_dir in unique_script_dirs:
                try:
                    os.makedirs(dest_dir, exist_ok=True)
                    dest_file = os.path.join(dest_dir, script_file)
                    if _copy_if_newer(src_file, dest_file):
                        copied_files.append(dest_file)
                        if on_log:
                            on_log(f"Skript kopiert nach QGIS-Profil: {dest_file}")
                except Exception as exc:
                    if on_log:
                        on_log(f"Warnung: Skript konnte nicht nach {dest_dir} kopiert werden: {exc}")

    # Copy models
    if os.path.isdir(models_src):
        model_files = [f for f in os.listdir(models_src) if f.endswith(".model3")]
        for model_file in model_files:
            src_file = os.path.join(models_src, model_file)
            for dest_dir in unique_model_dirs:
                try:
                    os.makedirs(dest_dir, exist_ok=True)
                    dest_file = os.path.join(dest_dir, model_file)
                    if _copy_if_newer(src_file, dest_file):
                        copied_files.append(dest_file)
                        if on_log:
                            on_log(f"Modell kopiert nach QGIS-Profil: {dest_file}")
                except Exception as exc:
                    if on_log:
                        on_log(f"Warnung: Modell konnte nicht nach {dest_dir} kopiert werden: {exc}")

    return copied_files


def register_processing_scripts(
    scripts: Mapping[str, str],
    *,
    on_log: Optional[Callable[[str], None]] = None,
) -> RegisteredProcessingScripts:
    """Load bundled Processing algorithms without requiring user installation."""
    try:
        ensure_qgis_scripts_installed(on_log=on_log)
    except Exception:
        pass
    try:
        from qgis.core import QgsApplication, QgsProcessingAlgorithm
    except ImportError as exc:
        raise RuntimeError("PyQGIS Processing is required to register processing scripts") from exc

    registry = QgsApplication.processingRegistry()
    registered: list[tuple[object, str]] = []
    for expected_id, script_path in scripts.items():
        if registry.algorithmById(expected_id) is not None:
            continue
        if not os.path.isfile(script_path):
            raise FileNotFoundError(f"Processing script not found: {script_path}")

        module_name = "_urban_act_processing_" + re.sub(r"\W+", "_", expected_id)
        module_spec = importlib.util.spec_from_file_location(module_name, script_path)
        if module_spec is None or module_spec.loader is None:
            raise RuntimeError(f"Could not load Processing script: {script_path}")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)

        algorithms = []
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and value is not QgsProcessingAlgorithm
                and issubclass(value, QgsProcessingAlgorithm)
                and value.__module__ == module.__name__
            ):
                algorithms.append(value())
        if not algorithms:
            raise RuntimeError(f"No QgsProcessingAlgorithm found in script: {script_path}")

        owner = registry.providerById("script") if hasattr(registry, "providerById") else None
        owner = owner or registry
        for algorithm in algorithms:
            algorithm_name = algorithm.name()
            algorithm_id = expected_id if expected_id.endswith(f":{algorithm_name}") else algorithm_name
            if not owner.addAlgorithm(algorithm):
                raise RuntimeError(f"Could not register Processing algorithm: {expected_id}")
            if registry.algorithmById(expected_id) is None and registry.algorithmById(algorithm_name) is None:
                owner.removeAlgorithm(algorithm_name)
                raise RuntimeError(f"Registered script is not available as {expected_id}")
            registered.append((owner, algorithm_name))
            if on_log:
                on_log(f"Registered Processing algorithm: {expected_id}")
    return RegisteredProcessingScripts(registered)


def formatted_pbf_names(paths: list[str]) -> list[str]:
    """Return compact Geofabrik names for display."""
    names = []
    for path in paths:
        filename = os.path.basename(path)
        names.append(re.sub(r"-(latest|\d{6})", "", filename).replace(".osm.pbf", ""))
    return names


def _referenced_pbf_files(input_dir: str, pbf_references: Optional[Sequence[dict]]) -> list[str]:
    files = []
    for reference in pbf_references or []:
        path = reference.get("path")
        if not path and reference.get("filename"):
            path = os.path.join(input_dir, reference["filename"])
        if path and os.path.isfile(path) and path.lower().endswith(".osm.pbf"):
            files.append(path)
    return sorted(dict.fromkeys(files))


def _require_osmium():
    try:
        import osmium
    except ImportError as exc:
        raise RuntimeError("pyosmium/osmium is required for this PBF operation") from exc
    return osmium


def _require_geo_stack():
    try:
        import geopandas
        from shapely.geometry import Point
        from shapely.ops import unary_union
    except ImportError as exc:
        raise RuntimeError("geopandas and shapely are required for this PBF extraction") from exc
    return geopandas, Point, unary_union


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _write_geodataframe(geopandas, features: list[dict], output_path: str, driver: str) -> None:
    _ensure_parent_dir(output_path)
    if features:
        gdf = geopandas.GeoDataFrame(features, geometry="geometry", crs="EPSG:4326")
    else:
        gdf = geopandas.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    gdf.to_file(output_path, driver=driver)


def _non_residential_tags() -> dict[str, object]:
    return {
        "shop": "mall",
        "building": ("commercial", "retail", "industrial", "warehouse", "office", "kiosk", "school", "university",
                     "hospital", "public", "construction", "service"),
        "amenity": ("hospital", "clinic", "doctors", "dentist", "pharmacy", "school", "kindergarten", "university",
                    "college", "library", "bus_station", "parking", "marketplace", "police", "fire_station",
                    "post_office", "townhall", "courthouse", "community_centre", "place_of_worship"),
        "landuse": ("commercial", "retail", "industrial", "institutional", "military", "railway", "quarry",
                    "landfill", "brownfield", "greenfield", "construction", "cemetery", "religious"),
        "leisure": ("golf_course", "stadium", "sports_centre", "track", "pitch", "playground", "park", "garden",
                    "nature_reserve", "marina"),
        "boundary": "protected_area",
        "aeroway": ("aerodrome", "terminal", "hangar", "helipad"),
        "power": ("plant", "substation", "generator"),
        "man_made": ("works", "wastewater_plant", "storage_tank"),
        "natural": ("wood", "scrub", "grassland", "wetland", "water", "beach", "bare_rock", "scree"),
    }


def _matches_tags(tags, tags_config: dict[str, object]) -> bool:
    for key, values in tags_config.items():
        if key not in tags:
            continue
        tag_value = tags[key]
        if isinstance(values, tuple) and tag_value in values:
            return True
        if tag_value == values:
            return True
    return False


def _polygon_parts(geometry) -> list[object]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "GeometryCollection":
        return [part for part in geometry.geoms if part.geom_type in ("Polygon", "MultiPolygon") and part.is_valid and not part.is_empty]
    if geometry.geom_type in ("Polygon", "MultiPolygon") and geometry.is_valid:
        return [geometry]
    return []


def _union_selected(cell_map: dict[int, object], selected_cells: dict[str, Sequence[int] | set[int]], modes: Sequence[str], unary_union):
    polygons = []
    for mode in modes:
        for cell_id in selected_cells.get(mode, set()):
            polygon = cell_map.get(cell_id)
            if polygon is not None and getattr(polygon, "is_valid", False):
                polygons.append(polygon)
    if not polygons:
        return None
    geometry = unary_union(polygons)
    if not geometry.is_valid:
        geometry = geometry.buffer(0)
    return geometry if geometry.is_valid and not geometry.is_empty else None


def _parse_special_tags(special_tags: str) -> list[tuple[str, str]]:
    return [
        (key.strip(), value.strip())
        for tag_pair in special_tags.split(",")
        if "=" in tag_pair
        for key, value in [tag_pair.split("=", 1)]
        if key.strip() and value.strip()
    ]
