"""UI-free orchestration of OSM preprocessing phases A and C."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Callable, Mapping, Sequence

from core.app.app_core.osmium import OsmiumRuntime, run_osmium


LogCallback = Callable[[str], None]
ProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class OsmPhaseAConfig:
    project_path: Path
    input_pbfs: Sequence[Path]
    pa_poly: Path | None = None
    zone_type_selected: Path | None = None
    center_coordinate: tuple[float, float] | None = None
    radius_km: float | None = None
    utm_crs: str | None = None


@dataclass(frozen=True)
class OsmPhaseCConfig:
    project_path: Path
    merged_pbf: Path
    pa_ia1_poly: Path
    study_area_poly: Path
    master_network: Path


def _paths(project_path: Path) -> dict[str, Path]:
    osm = project_path / "processed" / "osm"
    return {
        "osm": osm,
        "merged_pbf": osm / "01_input" / "merged.osm.pbf",
        "selected_pa_cells": osm / "02_filter_bounds" / "selected_pa_cells.gpkg",
        "bound_pa": osm / "02_filter_bounds" / "bound_pa_ia1.poly",
        "pa_clip": project_path / "temp" / "pa_ia1_clip.osm.pbf",
        "osm_cities": osm / "03_features" / "osm_cities.gpkg",
        "osm_pop_0": osm / "03_features" / "pop_zero_osm.gpkg",
        "study_area_points": osm / "03_features" / "poi_points.gpkg",
        "study_area_polygons": osm / "03_features" / "poi_polygons.gpkg",
        "network_original": osm / "04_network" / "road_network_hierarchical_original.osm",
        "network_modified": osm / "04_network" / "road_network_hierarchical_modified.osm",
    }


def canonical_osm_outputs(project_path: str | Path) -> Mapping[str, Path]:
    """Return the stable OSM output contract for a project."""

    return _paths(Path(project_path))


class OsmPipeline:
    """Orchestrates legacy domain functions while owning paths and subprocesses."""

    def __init__(
        self,
        runtime: OsmiumRuntime,
        *,
        stop_event=None,
        log: LogCallback | None = None,
        progress: ProgressCallback | None = None,
    ):
        self.runtime = runtime
        self.stop_event = stop_event
        self.log = log or (lambda _message: None)
        self.progress = progress or (lambda _name, _index, _total: None)

    def _check_cancelled(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            from core.app.app_core.osmium import OsmiumCancelledError

            raise OsmiumCancelledError("OSM-Pipeline wurde abgebrochen.")

    def _run(self, arguments, *, cwd=None):
        self.log("Osmium: " + " ".join(str(argument) for argument in arguments))
        return run_osmium(
            self.runtime,
            arguments,
            cwd=cwd,
            stop_event=self.stop_event,
            log=self.log,
        )

    @staticmethod
    def _prepare_directories(paths: Mapping[str, Path]) -> None:
        for path in paths.values():
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)

    def run_phase_a(
        self,
        config: OsmPhaseAConfig,
        *,
        build_pa_polygon_fn=None,
        export_cities_fn=None,
        export_uninhabited_fn=None,
    ) -> Mapping[str, Path]:
        paths = _paths(config.project_path)
        self._prepare_directories(paths)
        inputs = [Path(path) for path in config.input_pbfs]
        if not inputs or any(not path.is_file() for path in inputs):
            raise FileNotFoundError("Mindestens eine vorhandene lokale PBF-Datei ist erforderlich.")
        provided_pa_poly = Path(config.pa_poly) if config.pa_poly else None
        pa_poly = paths["bound_pa"]
        if provided_pa_poly and provided_pa_poly.is_file():
            if provided_pa_poly.resolve() != pa_poly.resolve():
                shutil.copyfile(provided_pa_poly, pa_poly)
        if not pa_poly.is_file():
            model4_poly = config.project_path / "processed" / "qgis_output" / "model4_TierAssign" / "bound_pa_ia1.poly"
            if model4_poly.is_file():
                shutil.copyfile(model4_poly, pa_poly)
        if not pa_poly.is_file():
            if build_pa_polygon_fn is None or config.zone_type_selected is None:
                raise FileNotFoundError(
                    "PA-POLY-Datei fehlt; für ihre Erzeugung werden "
                    "`zone_type_selected` und `build_pa_polygon_fn` benötigt."
                )
            zone_layer = Path(config.zone_type_selected)
            if not zone_layer.is_file():
                raise FileNotFoundError(f"Layer mit ausgewählten Zellen fehlt: {zone_layer}")
            build_pa_polygon_fn(
                zone_type_selected=str(zone_layer),
                selected_pa_cells=str(paths["selected_pa_cells"]),
                output_poly=str(pa_poly),
                target_crs="EPSG:4326",
                log=self.log,
            )
            if not pa_poly.is_file():
                raise RuntimeError(f"PA-POLY-Erzeugung lieferte keinen Output: {pa_poly}")

        total = 4
        self.progress("merge", 0, total)
        self.log("▶ [OSM Phase A 1/4] Starte PBF-Dateien zusammenführen ...")
        self._check_cancelled()
        if len(inputs) == 1:
            self.log(f"Einzelne PBF-Datei wird kopiert (kein Osmium-Cat erforderlich): {inputs[0].name}")
            shutil.copyfile(inputs[0], paths["merged_pbf"])
        else:
            # Adjacent regional extracts overlap at their borders. ``osmium
            # merge`` deliberately retains every encountered object version,
            # but downstream snapshot commands such as ``extract`` reject
            # duplicate IDs. Build a history-like intermediate stream and
            # reduce it to the newest version of every object.
            merged_versions = config.project_path / "temp" / "merged_versions.osm.pbf"
            try:
                self._run(["merge", *inputs, "-o", merged_versions, "--overwrite"])
                self._check_cancelled()
                self.log("OSM Phase A: überlappende OSM-Objekte konsolidieren ...")
                self._run(
                    [
                        "time-filter",
                        merged_versions,
                        "-o",
                        paths["merged_pbf"],
                        "--overwrite",
                    ]
                )
            finally:
                merged_versions.unlink(missing_ok=True)
        self.log("✓ [OSM Phase A 1/4] PBF-Zusammenführung abgeschlossen. Nächster Schritt: Untersuchungsgebiet PA/IA1 ausschneiden (Schritt 2/4) ...")

        self.progress("pa_extract", 1, total)
        self.log("▶ [OSM Phase A 2/4] Starte Untersuchungsgebiet (PA & IA1) ausschneiden ...")
        self._check_cancelled()
        self._run(
            [
                "extract",
                "-p",
                pa_poly,
                paths["merged_pbf"],
                "-o",
                paths["pa_clip"],
                "--overwrite",
            ]
        )
        self.log("✓ [OSM Phase A 2/4] Untersuchungsgebiet-Ausschnitt erstellt. Nächster Schritt: Städte und Gemeinden extrahieren (Schritt 3/4) ...")

        if export_cities_fn is None:
            from core.scripts.osm.export_cities import export_cities as export_cities_fn
        if export_uninhabited_fn is None:
            from core.scripts.osm.export_uninhabited import export_uninhabited as export_uninhabited_fn

        self.progress("cities", 2, total)
        self.log("▶ [OSM Phase A 3/4] Starte Städte und Gemeinden extrahieren ...")
        self._check_cancelled()
        export_cities_fn(
            str(paths["merged_pbf"]),
            str(paths["osm"].parent),
            str(self.runtime.executable),
            str(self.runtime.executable.parent),
            output_path=str(paths["osm_cities"]),
            run_command=self._run,
            log=self.log,
        )
        self.log("✓ [OSM Phase A 3/4] Städte und Gemeinden extrahiert. Nächster Schritt: Unbewohnte Flächen filtern (Schritt 4/4) ...")

        self.progress("uninhabited", 3, total)
        self.log("▶ [OSM Phase A 4/4] Starte nachweislich unbewohnte Flächen extrahieren ...")
        self._check_cancelled()
        export_uninhabited_fn(
            str(paths["pa_clip"]),
            str(paths["osm"].parent),
            str(self.runtime.executable),
            str(self.runtime.executable.parent),
            output_path=str(paths["osm_pop_0"]),
            run_command=self._run,
            log=self.log,
        )
        for required in ("merged_pbf", "osm_cities", "osm_pop_0"):
            if not paths[required].is_file():
                raise RuntimeError(f"OSM-Phase A lieferte den erwarteten Output nicht: {paths[required]}")
        self.progress("complete", total, total)
        self.log("✓ [OSM Phase A] Alle 4 Teilschritte der OSM-Vorverarbeitung erfolgreich abgeschlossen! Nächster Schritt: GADM-Zuschnitt & QGIS Modell 1 (Datenaufbereitung) ...")
        return {key: paths[key] for key in ("merged_pbf", "bound_pa", "osm_cities", "osm_pop_0")}

    def run_phase_c(
        self,
        config: OsmPhaseCConfig,
        *,
        network_fn=None,
        study_area_fn=None,
    ) -> Mapping[str, Path]:
        paths = _paths(config.project_path)
        self._prepare_directories(paths)
        for source in (config.merged_pbf, config.pa_ia1_poly, config.study_area_poly, config.master_network):
            if not Path(source).is_file():
                raise FileNotFoundError(f"OSM-Phase-C-Input fehlt: {source}")
        if network_fn is None:
            from core.scripts.osm.export_network import build_hierarchical_network as network_fn
        if study_area_fn is None:
            from core.scripts.osm.export_study_area import export_study_area as study_area_fn

        phase_c_steps = 14
        self.progress("network_prepare", 0, phase_c_steps)
        self.log("▶ [OSM Phase C 1/2] Starte hierarchisches Straßennetz generieren (export_network) ...")
        self._check_cancelled()

        def network_progress(name: str, index: int, _total: int) -> None:
            self.progress(name, index, phase_c_steps)

        network_result = network_fn(
            str(config.merged_pbf),
            str(paths["network_modified"].parent),
            str(self.runtime.executable),
            str(self.runtime.executable.parent),
            str(config.pa_ia1_poly),
            str(config.study_area_poly),
            str(config.master_network),
            output_original=str(paths["network_original"]),
            output_modified=str(paths["network_modified"]),
            run_command=self._run,
            log=self.log,
            progress=network_progress,
        )
        if not network_result:
            raise RuntimeError("Erzeugung des hierarchischen Straßennetzes fehlgeschlagen.")
        self.log("✓ [OSM Phase C 1/2] Hierarchisches Straßennetz generiert. Nächster Schritt: POIs & Flächen der Study-Area extrahieren (Schritt 2/2) ...")

        self.progress("study_area_prepare", 11, phase_c_steps)
        self.log("▶ [OSM Phase C 2/2] Starte POIs & Flächen der Study-Area extrahieren (export_study_area) ...")
        self._check_cancelled()

        def study_progress(name: str, index: int, _total: int) -> None:
            self.progress(name, 11 + index, phase_c_steps)

        study_result = study_area_fn(
            str(config.merged_pbf),
            str(paths["osm"].parent),
            str(self.runtime.executable),
            str(self.runtime.executable.parent),
            str(config.study_area_poly),
            output_points=str(paths["study_area_points"]),
            output_polygons=str(paths["study_area_polygons"]),
            run_command=self._run,
            log=self.log,
            progress=study_progress,
        )
        if not study_result:
            raise RuntimeError("Erzeugung der Study-Area-POIs fehlgeschlagen.")
        self.progress("complete", phase_c_steps, phase_c_steps)
        self.log("✓ [OSM Phase C] Phase C vollständig abgeschlossen! Nächster Schritt: QGIS Modell 6 (Visum-Importdateien erzeugen) ...")
        required = ("network_original", "network_modified", "study_area_points", "study_area_polygons")
        for key in required:
            if not paths[key].is_file():
                raise RuntimeError(f"OSM-Phase C lieferte den erwarteten Output nicht: {paths[key]}")
        return {key: paths[key] for key in required}
