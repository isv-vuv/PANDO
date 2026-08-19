"""Qt page for Step 6: Model Results & Evaluation Overview."""

from __future__ import annotations

import glob
import logging
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from core.locales import localizer
from core.app.app_qt.qt_base import (
    Dialogs,
    Qt,
    QtCore,
    QtGui,
    QtWidgets,
    app_font,
    create_step_header,
    pyqtSignal,
    qfont_bold,
    qt_enum,
)

_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object
logger = logging.getLogger(__name__)
_BACKGROUND_PROCESSES: list[subprocess.Popen] = []
_ACTIVE_VISUM_INSTANCES: list[Any] = []


def open_path_in_explorer(target_path: str | Path) -> bool:
    """Open a folder or select a file in system file explorer."""
    path_str = str(target_path)
    if not os.path.exists(path_str):
        parent = os.path.dirname(path_str)
        if os.path.exists(parent):
            path_str = parent
        else:
            return False

    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(path_str)
            return True
        elif system == "Darwin":
            proc = subprocess.Popen(["open", path_str])
            _BACKGROUND_PROCESSES.append(proc)
            return True
        else:
            proc = subprocess.Popen(["xdg-open", path_str])
            _BACKGROUND_PROCESSES.append(proc)
            return True
    except Exception as exc:
        logger.warning("Could not open file explorer for %s: %s", path_str, exc)
        return False


def find_qgis_executable() -> Optional[str]:
    """Discover QGIS 4 / QGIS desktop executable candidate."""
    candidates = []

    env_bat = os.environ.get("QGIS_BAT")
    if env_bat and os.path.isfile(env_bat):
        bin_dir = os.path.dirname(env_bat)
        for name in ("qgis-bin.exe", "qgis.bat", "qgis.exe"):
            p = os.path.join(bin_dir, name)
            if os.path.isfile(p):
                candidates.append(p)

    candidates.extend(glob.glob(r"C:\OSGeo4W*\bin\qgis-bin.exe"))
    candidates.extend(glob.glob(r"C:\OSGeo4W*\bin\qgis.bat"))
    candidates.extend(glob.glob(r"C:\Program Files\QGIS*\bin\qgis-bin.exe"))
    candidates.extend(glob.glob(r"C:\Program Files\QGIS*\bin\qgis.bat"))
    candidates.extend(glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Programs\OSGeo4W\bin\qgis-bin.exe")))
    candidates.extend(glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Programs\OSGeo4W\bin\qgis.bat")))

    for cand in candidates:
        if os.path.isfile(cand):
            return cand

import xml.etree.ElementTree as ET


def _inject_qgs_canvas_extent(qgs_file: Path, xmin: float, ymin: float, xmax: float, ymax: float) -> None:
    """Inject/update the mapcanvas and ProjectViewSettings extent in a .qgs XML file."""
    try:
        if not qgs_file.is_file():
            return
        tree = ET.parse(str(qgs_file))
        root = tree.getroot()

        # 1. ProjectViewSettings
        pvs = root.find("ProjectViewSettings")
        if pvs is None:
            pvs = ET.SubElement(root, "ProjectViewSettings")
        pvs.set("UseProjectScales", "0")
        pvs.set("rotation", "0")

        dve = pvs.find("DefaultViewExtent")
        if dve is None:
            dve = ET.SubElement(pvs, "DefaultViewExtent")
        dve.set("xmin", f"{xmin:.6f}")
        dve.set("ymin", f"{ymin:.6f}")
        dve.set("xmax", f"{xmax:.6f}")
        dve.set("ymax", f"{ymax:.6f}")

        # 2. theMapCanvas
        mc = None
        for child in root.findall("mapcanvas"):
            if child.get("name") in ("theMapCanvas", None, ""):
                mc = child
                break
        if mc is None:
            mc = ET.SubElement(root, "mapcanvas", {"name": "theMapCanvas"})

        ext = mc.find("extent")
        if ext is None:
            ext = ET.SubElement(mc, "extent")
        for c in list(ext):
            ext.remove(c)

        xmin_el = ET.SubElement(ext, "xmin")
        xmin_el.text = f"{xmin:.6f}"
        ymin_el = ET.SubElement(ext, "ymin")
        ymin_el.text = f"{ymin:.6f}"
        xmax_el = ET.SubElement(ext, "xmax")
        xmax_el.text = f"{xmax:.6f}"
        ymax_el = ET.SubElement(ext, "ymax")
        ymax_el.text = f"{ymax:.6f}"

        tree.write(str(qgs_file), encoding="utf-8", xml_declaration=True)
    except Exception as exc:
        logger.warning("Could not inject canvas extent into %s: %s", qgs_file, exc)


def layer_draw_order_key(layer_path: str | Path) -> int:
    """
    Returns layer ordering priority for QGIS layer tree.
    Lower number = placed higher in the layer tree / rendered on top.
    
    Paired Priority Hierarchy:
    1. Model 6: zone_centroids (10) > zones (11)
    2. Model 5: zone_pa_ia1_ia2_points (20) > zone_pa_ia1_ia2 (21)
    3. Model 2: central_place_points (30) > zone_adm2_typeno (31)
    4. Heatmap & POI: sector_all_points (40) > sector_all_intensity (41)
    5. Raster: pop_raster_corr (50)
    6. Generic: other points (60) > other polygons (70) > other rasters (80)
    7. OpenStreetMap background (99, bottom)
    """
    name = Path(layer_path).stem.lower()
    suffix = Path(layer_path).suffix.lower()

    # 1. Model 6 (Assembled Final Zones)
    if "centroid" in name:
        return 10
    if name == "zones" or name.startswith("zone_assembled") or name.startswith("06_"):
        return 11

    # 2. Model 5 (Planning & Influence Areas PA / IA1 / IA2)
    if ("pa_ia" in name or "urban" in name) and ("point" in name or "pt" in name):
        return 20
    if "pa_ia" in name or "urban" in name:
        return 21

    # 3. Model 2 (Central Places & Administrative ADM2 Zones)
    if ("central_place" in name or "adm2" in name) and ("point" in name or "pt" in name):
        return 30
    if "adm2" in name or "zone_adm" in name or "type" in name or "model2" in name:
        return 31

    # 4. Model 5 Heatmap & POI Sector Points (Intensity above Sector Points)
    if "intensity" in name:
        return 40
    if "sector" in name or "poi" in name:
        if suffix in (".tif", ".tiff", ".asc", ".sdat"):
            return 40
        return 41

    # 5. Model 1 (ADM Boundaries: ADM3 on top down to ADM0, and Population Raster)
    if name == "adm3":
        return 45
    if name == "adm2":
        return 46
    if name == "adm1":
        return 47
    if name == "adm0":
        return 48
    if "pop" in name or "census" in name or "zensus" in name:
        return 50

    # Generic fallbacks
    if "point" in name:
        return 60
    if suffix in (".tif", ".tiff", ".asc", ".sdat"):
        return 80
    return 70


def create_qgis_project_for_layers(
    project_path: str | Path,
    layer_paths: Sequence[str | Path],
    project_name: str = "pando_results",
) -> Optional[Path]:
    """Create a .qgs project file configured with the project's local CRS and an OpenStreetMap background layer."""
    project_path = Path(project_path)
    if not project_path.exists():
        return None

    # Determine local CRS
    local_crs = "EPSG:3857"
    crs_file = project_path / "input" / "local_crs.txt"
    if crs_file.is_file():
        try:
            local_crs = crs_file.read_text(encoding="utf-8").strip() or local_crs
        except Exception:
            pass
    else:
        try:
            from core.app.app_core.project import load_pipeline_manifest
            manifest = load_pipeline_manifest(str(project_path))
            local_crs = manifest.get("local_crs") or local_crs
        except Exception:
            pass

    existing_layers = [Path(p) for p in layer_paths if Path(p).exists()]
    if not existing_layers:
        return None

    # Sort existing layers by priority hierarchy (Points > Zones > Heatmap > Base)
    sorted_existing_layers = sorted(existing_layers, key=layer_draw_order_key)

    qgs_dir = project_path / "processed" / "qgis_output"
    qgs_dir.mkdir(parents=True, exist_ok=True)
    qgs_file = qgs_dir / f"{project_name}.qgs"

    try:
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsProject,
            QgsRasterLayer,
            QgsVectorLayer,
        )

        project = QgsProject()
        project.clear()
        project.setCrs(QgsCoordinateReferenceSystem(local_crs))

        # 1. Background OpenStreetMap Layer (50% opacity, grayscale, distinct name)
        osm_url = "type=xyz&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png&zmax=19&zmin=0"
        osm_layer = QgsRasterLayer(osm_url, "OpenStreetMap (OSM) Grey", "wms")
        if osm_layer.isValid():
            osm_layer.setOpacity(0.5)
            pipe = osm_layer.pipe()
            if pipe:
                hs = pipe.hueSaturationFilter()
                if hs:
                    try:
                        from qgis.core import QgsHueSaturationFilter
                        hs.setGrayscaleMode(QgsHueSaturationFilter.GrayscaleLightness)
                    except Exception:
                        pass
                    hs.setSaturation(-100)
            project.addMapLayer(osm_layer, False)

        # 2. Result Layers
        added_layers = []
        for lp in sorted_existing_layers:
            suffix = lp.suffix.lower()
            if suffix in (".tif", ".tiff", ".asc", ".sdat"):
                layer = QgsRasterLayer(str(lp), lp.stem, "gdal")
            else:
                layer = QgsVectorLayer(str(lp), lp.stem, "ogr")

            if layer.isValid():
                qml_sidecar = lp.with_suffix(".qml")
                if qml_sidecar.is_file():
                    layer.loadNamedStyle(str(qml_sidecar))
                project.addMapLayer(layer, False)
                added_layers.append(layer)

        # 3. Calculate Bounding Box of result data layers (excluding background OSM) in project CRS
        combined_extent = None
        proj_crs = project.crs()

        for layer in added_layers:
            if not layer.isValid():
                continue
            if not isinstance(layer, QgsRasterLayer):
                try:
                    layer.updateExtents()
                except Exception:
                    pass

            ext = layer.extent()
            if ext.isEmpty() or ext.isNull():
                continue

            layer_crs = layer.crs()
            if not layer_crs.isValid():
                layer_crs = QgsCoordinateReferenceSystem("EPSG:4326")

            if layer_crs != proj_crs:
                transformed_ext = None
                for ctx in (project.transformContext(), QgsProject.instance().transformContext(), project):
                    try:
                        ct = QgsCoordinateTransform(layer_crs, proj_crs, ctx)
                        if ct.isValid():
                            t_box = ct.transformBoundingBox(ext)
                            if not t_box.isEmpty() and not t_box.isNull():
                                transformed_ext = t_box
                                break
                    except Exception:
                        pass

                if transformed_ext is None:
                    # Fallback transformation via pyproj if QgsCoordinateTransform context was unavailable
                    try:
                        import pyproj
                        transformer = pyproj.Transformer.from_crs(layer_crs.authid(), proj_crs.authid(), always_xy=True)
                        x1, y1 = transformer.transform(ext.xMinimum(), ext.yMinimum())
                        x2, y2 = transformer.transform(ext.xMaximum(), ext.yMaximum())
                        from qgis.core import QgsRectangle
                        transformed_ext = QgsRectangle(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
                    except Exception:
                        transformed_ext = ext
                ext = transformed_ext

            if combined_extent is None or combined_extent.isEmpty():
                from qgis.core import QgsRectangle
                combined_extent = QgsRectangle(ext)
            else:
                combined_extent.combineExtentWith(ext)

        if combined_extent is not None and not combined_extent.isEmpty() and not combined_extent.isNull():
            combined_extent.scale(1.08)  # 8% margin around the data
            try:
                project.viewSettings().setDefaultViewExtent(combined_extent)
            except Exception:
                pass

        # Build Layer Tree: Result layers on TOP (Points -> Zones -> Heatmap), OSM layer at the BOTTOM
        root = project.layerTreeRoot()
        for layer in added_layers:
            root.addLayer(layer)
        if osm_layer.isValid():
            root.addLayer(osm_layer)

        project.write(str(qgs_file))

        # Ensure XML canvas extent is written into the project file
        if combined_extent is not None and not combined_extent.isEmpty():
            _inject_qgs_canvas_extent(
                qgs_file,
                xmin=combined_extent.xMinimum(),
                ymin=combined_extent.yMinimum(),
                xmax=combined_extent.xMaximum(),
                ymax=combined_extent.yMaximum(),
            )

        logger.info("Created QGIS project with local CRS %s, OpenStreetMap (OSM) Grey, and extent: %s", local_crs, qgs_file)
        return qgs_file
    except Exception as exc:
        logger.warning("Could not create QGIS project via PyQGIS: %s", exc)
        return existing_layers[0] if existing_layers else None


def open_in_qgis(layer_paths: Sequence[str | Path], project_path: str | Path = "", title: str = "pando_view") -> bool:
    """Launch QGIS 4 with a configured project containing local CRS, 50% grayscale OSM, and selected layers."""
    qgs_proj = None
    if project_path:
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", title).strip("_").lower()
        if not slug:
            slug = "view"
        qgs_proj = create_qgis_project_for_layers(project_path, layer_paths, project_name=f"pando_{slug}")

    target_to_open = str(qgs_proj) if qgs_proj and os.path.exists(str(qgs_proj)) else None
    if not target_to_open:
        existing = [str(p) for p in layer_paths if os.path.exists(str(p))]
        if existing:
            target_to_open = existing[0]

    if not target_to_open:
        return False

    qgis_bin = find_qgis_executable()
    try:
        if qgis_bin:
            kwargs: dict[str, Any] = {}
            if platform.system() == "Windows":
                detached = getattr(subprocess, "DETACHED_PROCESS", 0)
                new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                kwargs["creationflags"] = detached | new_group
            proc = subprocess.Popen([qgis_bin, target_to_open], **kwargs)
            _BACKGROUND_PROCESSES.append(proc)
            return True
        else:
            return open_path_in_explorer(target_to_open)
    except Exception as exc:
        logger.warning("Could not launch QGIS with %s: %s", target_to_open, exc)
        return open_path_in_explorer(target_to_open)


def _set_visum_project_directories(visum, language: str = "de") -> None:
    """Set project directory paths in Visum using the script from procedure sequence XML (DE_Verfahrensablauf.xml / EN_ProcedureSequence.xml)."""
    is_en = str(language).lower().startswith("en")
    target_xml_name = "EN_ProcedureSequence.xml" if is_en else "DE_Verfahrensablauf.xml"

    script_executed = False
    try:
        from core.app.app_core.model_pipeline import tool_root
        root = Path(tool_root())
        candidate_paths = [
            root / "core" / "scripts" / "visum" / "helper_files" / "pro" / target_xml_name,
            root / "core" / "scripts" / "visum" / "helper_files" / target_xml_name,
        ]

        for cand in candidate_paths:
            if cand.exists():
                import xml.etree.ElementTree as ET
                tree = ET.parse(str(cand))
                for op in tree.getroot().findall(".//OPERATION"):
                    comment = op.get("COMMENT", "")
                    if "Project Directories" in comment or "Verzeichnis" in comment:
                        script_elem = op.find(".//INTERNALSCRIPTCODE")
                        if script_elem is not None and script_elem.text:
                            globs = {"Visum": visum, "visum": visum, "print": logger.info}
                            exec(script_elem.text, globs)
                            script_executed = True
                            logger.info("Project directories successfully set in Visum from %s", cand.name)
                            break
            if script_executed:
                break
    except Exception as exc:
        logger.warning("Could not execute script from %s: %s", target_xml_name, exc)

    if not script_executed:
        try:
            doc_name = str(visum.UserPreferences.DocumentName or "")
            if "\\" in doc_name:
                p = doc_name.rsplit("\\", 2)[0]
            else:
                p = str(visum.GetPath(2) or "").rstrip("\\")
            paths = {
                37: "\\script",
                21: "\\fil",
                15: "\\att",
                41: "\\screenshot",
                43: "\\tt-gpa",
                92: "\\lay",
                8:  "\\gpa",
                47: "\\gpa",
                20: "\\lla",
                57: "\\log-file",
                69: "\\mtx",
                5:  "\\dmd",
                1:  "\\net",
                2:  "\\ver",
                25: "\\shapefile",
                81: "\\qla",
                33: "\\icon",
            }
            for k, v in paths.items():
                visum.SetPath(k, p + v)
            logger.info("Projektpfade gesetzt (Fallback): %s", p)
        except Exception as exc:
            logger.warning("Fallback setting Visum project paths failed: %s", exc)


def find_visum_target_version(visum_dir: Path) -> Optional[Path]:
    """Find the best candidate .ver (or .net) file in visum directory or its subdirectories."""
    ver_files = list(visum_dir.rglob("*.ver"))
    if not ver_files:
        net_files = list(visum_dir.rglob("*.net"))
        return net_files[0] if net_files else None

    # Priority 1: 07_*_Model.ver or *Model*.ver (excluding Debug)
    model_vers = [f for f in ver_files if "model" in f.stem.lower() and "debug" not in f.stem.lower()]
    if model_vers:
        return sorted(model_vers, key=lambda f: f.name, reverse=True)[0]

    # Priority 2: Non-debug .ver files with highest step number
    non_debug = [f for f in ver_files if "debug" not in f.stem.lower()]
    if non_debug:
        return sorted(non_debug, key=lambda f: f.name, reverse=True)[0]

    return sorted(ver_files, key=lambda f: f.name, reverse=True)[0]


def resolve_visum_gpa(visum_dir: Path, gpa_filename: str) -> Optional[Path]:
    """Resolve a graphic parameter file (.gpax / .gpa) from project or helper templates."""
    if not gpa_filename:
        return None
    visum_dir = Path(visum_dir)
    gpa_dir = visum_dir / "gpa"

    # 1. Exact match in project gpa dir
    cand = gpa_dir / gpa_filename
    if cand.is_file():
        return cand

    # 2. Match without DE_/EN_ prefix in project gpa dir
    clean_name = gpa_filename.replace("DE_", "").replace("EN_", "")
    cand = gpa_dir / clean_name
    if cand.is_file():
        return cand

    # 3. Match stem with .gpax or .gpa in project gpa dir
    stem = Path(clean_name).stem
    for ext in (".gpax", ".gpa"):
        cand = gpa_dir / f"{stem}{ext}"
        if cand.is_file():
            return cand

    # 4. Search in helper_files template folder
    try:
        from core.app.app_core.model_pipeline import tool_root
        helper_dir = Path(tool_root()) / "core" / "scripts" / "visum" / "helper_files" / "gpa"
        for name in (gpa_filename, clean_name, f"{stem}.gpax", f"{stem}.gpa"):
            cand = helper_dir / name
            if cand.is_file():
                return cand
    except Exception:
        pass

    return None


def ensure_visum_language(visum, language: str = "de") -> None:
    """Ensure Visum application language matches PANDO's current UI language ('ENG' or 'DEU')."""
    target_code = "ENG" if str(language).lower().startswith("en") else "DEU"
    try:
        curr_lang = str(visum.GetCurrentLanguage()).strip().upper()
        if target_code in curr_lang or (target_code == "DEU" and ("DE" in curr_lang or "GER" in curr_lang)):
            return
        try:
            visum.SetLanguage(target_code)
        except Exception:
            try:
                visum.SetCurrentLanguage(target_code)
            except Exception:
                pass
        logger.info("Visum-Sprache auf '%s' umgestellt.", target_code)
    except Exception as exc:
        logger.debug("Visum language switch: %s", exc)


def deactivate_visum_links_and_odpairs(visum) -> None:
    """Hide links and desire lines / OD matrix layers in Visum graphic parameters for a clean zone view."""
    try:
        gp = visum.Net.GraphicParameters

        # 1. Strecken-Layer ausblenden (DRAW = 0)
        try:
            gp.Links.SetAttValue("DRAW", 0)
            logger.info("Strecken-Layer in Visum ausgeblendet (DRAW = 0).")
        except Exception as exc:
            logger.debug("Fehler beim Ausblenden von Links: %s", exc)

        # 2. Wunschlinien / OD-Matrix-Layer (Verkehrszellen & Hauptverkehrszellen) ausblenden
        for od_attr in ["DesireLinesZones", "DesireLinesMainZones"]:
            if hasattr(gp, od_attr):
                try:
                    getattr(gp, od_attr).SetAttValue("DRAW", 0)
                    logger.info("%s-Layer in Visum ausgeblendet (DRAW = 0).", od_attr)
                except Exception as exc:
                    logger.debug("Fehler beim Ausblenden von %s: %s", od_attr, exc)

        logger.info("Strecken- und Wunschlinien-Layer in Visum erfolgreich ausgeblendet.")
    except Exception as exc:
        logger.warning("Hinweis beim Ausblenden von Strecken/Wunschlinien in Visum: %s", exc)


def open_in_visum(
    project_path: str | Path,
    gpa_filename: Optional[str] = None,
    zoom_study_area: bool = False,
    deactivate_links_and_odpairs: bool = False,
    language: str = "de",
) -> bool:
    """Launch PTV Visum with the project .ver file, optionally apply GPA file, run step 2, and keep Visum open."""
    project_path = Path(project_path)
    visum_dir = project_path / "processed" / "visum"
    if not visum_dir.exists():
        return False

    target_ver = find_visum_target_version(visum_dir)
    if not target_ver or not target_ver.exists():
        return open_path_in_explorer(visum_dir)

    is_en = str(language).lower().startswith("en")
    gpa_path = resolve_visum_gpa(visum_dir, gpa_filename) if gpa_filename else None

    study_zoom_gpa = None
    if zoom_study_area:
        zoom_name = "_Extent_StudyArea.gpa" if is_en else "_Ausschnitt_Untersuchungsraum.gpa"
        study_zoom_gpa = resolve_visum_gpa(visum_dir, zoom_name)

    try:
        import win32com.client as com
        visum = None
        for prog_id in ("Visum.Visum.250", "Visum.Visum.25", "Visum.Visum.240", "Visum.Visum.24", "Visum.Visum"):
            try:
                visum = com.Dispatch(prog_id)
                break
            except Exception:
                continue

        if visum is None:
            raise RuntimeError("Visum COM automation object could not be dispatched.")

        visum.Graphic.ShowMaximized()
        visum.LoadVersion(str(target_ver.resolve()))
        logger.info("Visum version loaded via COM: %s", target_ver)

        # 1. Sprache synchronisieren
        ensure_visum_language(visum, language=language)

        # 2. Set Project Directories aus dem Skript der XML-Datei
        _set_visum_project_directories(visum, language=language)

        # 3. Apply Study Area Zoom GPA if requested
        if study_zoom_gpa and study_zoom_gpa.exists():
            try:
                visum.Net.GraphicParameters.Open(str(study_zoom_gpa.resolve()))
                logger.info("Zoom GPA applied: %s", study_zoom_gpa.name)
            except Exception as exc:
                logger.warning("Could not open zoom GPA: %s", exc)

        # 4. Apply Graphic Parameters GPA
        if gpa_path and gpa_path.exists():
            try:
                if gpa_path.suffix.lower() == ".gpax":
                    try:
                        visum.Net.GraphicParameters.OpenXml(str(gpa_path.resolve()))
                    except Exception:
                        visum.Net.GraphicParameters.Open(str(gpa_path.resolve()))
                else:
                    visum.Net.GraphicParameters.Open(str(gpa_path.resolve()))
                logger.info("GPA applied: %s", gpa_path.name)
            except Exception as exc:
                logger.warning("Could not open GPA %s: %s", gpa_path, exc)

        # 5. Strecken und OD-Pairs nach dem GPA-Laden deaktivieren (damit GPA den Filter nicht überschreibt)
        if deactivate_links_and_odpairs:
            deactivate_visum_links_and_odpairs(visum)

        visum.Graphic.Redraw()
        _ACTIVE_VISUM_INSTANCES.append(visum)
        return True

    except Exception as exc:
        logger.warning("Visum COM automation failed (%s). Opening .ver file directly.", exc)
        return open_path_in_explorer(target_ver)


class ResultCardWidget(_Q_WIDGET_BASE):
    """Card representing a result dataset with explanation and action buttons."""

    def __init__(
        self,
        title: str,
        description: str,
        layer_paths: list[str],
        folder_path: str,
        open_qgis_callback=None,
        open_visum_callback=None,
        show_folder: bool = True,
        parent: Optional[object] = None,
    ):
        super().__init__(parent)
        self.title = title
        self.description = description
        self.layer_paths = layer_paths
        self.folder_path = folder_path
        self.open_qgis_callback = open_qgis_callback
        self.open_visum_callback = open_visum_callback
        self.show_folder = show_folder

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.group_box = QtWidgets.QGroupBox(self.title, self)
        card_layout = QtWidgets.QVBoxLayout(self.group_box)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(6)

        # Description text
        desc_label = QtWidgets.QLabel(self.description, self.group_box)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #333333; font-size: 11px;")
        card_layout.addWidget(desc_label)

        # Action Buttons row
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch()

        # Check availability
        exists = any(os.path.exists(p) for p in self.layer_paths) if self.layer_paths else os.path.exists(self.folder_path)

        # Folder button (if enabled)
        if self.show_folder and self.folder_path:
            self.btn_folder = QtWidgets.QPushButton(localizer.get_string("step7_btn_open_folder", default="Ordner öffnen"), self.group_box)
            self.btn_folder.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
            self.btn_folder.clicked.connect(self._on_open_folder)
            btn_row.addWidget(self.btn_folder)

        # QGIS button
        if self.open_qgis_callback:
            self.btn_qgis = QtWidgets.QPushButton(localizer.get_string("step7_btn_open_qgis", default="In QGIS öffnen"), self.group_box)
            self.btn_qgis.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
            self.btn_qgis.setEnabled(exists)
            self.btn_qgis.clicked.connect(self._on_open_qgis)
            btn_row.addWidget(self.btn_qgis)

        # Visum button
        if self.open_visum_callback:
            self.btn_visum = QtWidgets.QPushButton(localizer.get_string("step7_btn_open_visum", default="In Visum öffnen"), self.group_box)
            self.btn_visum.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
            self.btn_visum.setEnabled(exists)
            self.btn_visum.clicked.connect(self._on_open_visum)
            btn_row.addWidget(self.btn_visum)

        card_layout.addLayout(btn_row)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.group_box)

    def _on_open_folder(self) -> None:
        open_path_in_explorer(self.folder_path)

    def _on_open_qgis(self) -> None:
        if self.open_qgis_callback:
            self.open_qgis_callback(self.layer_paths)

    def _on_open_visum(self) -> None:
        if self.open_visum_callback:
            self.open_visum_callback(self.folder_path)


class Step6ResultsWidget(_Q_WIDGET_BASE):
    """Step 6 Widget providing an overview of all generated results and opening tools."""

    def __init__(self, localizer_obj, parent: Optional[object] = None, project_path: str = ""):
        super().__init__(parent)
        self.localizer = localizer_obj
        self.project_path = project_path

        self._setup_ui()

    def set_project_path(self, path: str) -> None:
        self.project_path = path
        self.refresh_results()

    def _setup_ui(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(16, 10, 16, 10)
        main_layout.setSpacing(8)

        # Header
        self.header, _title_label, _step_label = create_step_header(
            self.localizer.get_string("step7_title", default="Schritt 6: Ergebnisse und Auswertung"),
            current_step=6,
            total_steps=6,
            localizer=self.localizer,
            parent=self,
        )
        main_layout.addWidget(self.header)

        # Top Controls Bar: Description + Global Open Action Buttons
        top_bar = QtWidgets.QHBoxLayout()
        self.intro_label = QtWidgets.QLabel(
            self.localizer.get_string(
                "step7_desc",
                default="Übersicht AUSGEWÄHLTER Ergebnisdaten sowie kurze Erklärungen zu den Daten. Weitere Dokumentation und Erklärungen folgt in einer neuen Version des Tools."
            ),
            self,
        )
        self.intro_label.setWordWrap(True)
        self.intro_label.setStyleSheet("color: #444444; font-size: 11px;")
        top_bar.addWidget(self.intro_label, 1)

        self.btn_open_all_qgis = QtWidgets.QPushButton(self.localizer.get_string("step7_btn_open_all_qgis", default="🗺️ Alle Layer in QGIS öffnen"), self)
        self.btn_open_all_qgis.setFont(app_font(10, qfont_bold()))
        self.btn_open_all_qgis.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
        self.btn_open_all_qgis.clicked.connect(self._on_open_all_in_qgis)
        top_bar.addWidget(self.btn_open_all_qgis)

        self.btn_open_visum_dir = QtWidgets.QPushButton(self.localizer.get_string("step7_btn_open_visum_dir", default="📁 Visum-Projektordner öffnen"), self)
        self.btn_open_visum_dir.setFont(app_font(10, qfont_bold()))
        self.btn_open_visum_dir.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
        self.btn_open_visum_dir.clicked.connect(self._on_open_visum_dir)
        top_bar.addWidget(self.btn_open_visum_dir)

        main_layout.addLayout(top_bar)

        # Scrollable area for result cards
        self.scroll_area = QtWidgets.QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))

        self.scroll_widget = QtWidgets.QWidget()
        self.scroll_layout = QtWidgets.QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(0, 0, 8, 0)
        self.scroll_layout.setSpacing(10)

        self._build_cards()

        self.scroll_area.setWidget(self.scroll_widget)
        main_layout.addWidget(self.scroll_area, 1)

    def _open_layer_group_in_qgis(self, layer_paths: list[str], title: str) -> None:
        open_in_qgis(layer_paths, project_path=self.project_path, title=title)

    def _on_open_visum_dir(self) -> None:
        base = Path(self.project_path) if self.project_path else Path("")
        visum_dir = base / "processed" / "visum"
        open_path_in_explorer(str(visum_dir) if visum_dir.exists() else str(base))

    def _on_open_all_in_qgis(self) -> None:
        base = Path(self.project_path) if self.project_path else Path("")
        qgis_out = base / "processed" / "qgis_output"
        all_layers = [
            qgis_out / "model6_ZoneAssembler" / "zone_centroids.gpkg",
            qgis_out / "model6_ZoneAssembler" / "zones.gpkg",
            qgis_out / "model5_UrbanCentrality" / "zone_pa_ia1_ia2_points.gpkg",
            qgis_out / "model5_UrbanCentrality" / "zone_pa_ia1_ia2.gpkg",
            qgis_out / "model2_ZoneClass" / "central_place_points.gpkg",
            qgis_out / "model2_ZoneClass" / "zone_adm2_typeno.gpkg",
            qgis_out / "model5_UrbanCentrality" / "sector_all_intensity.tif",
            qgis_out / "model5_UrbanCentrality" / "sector_all_points.gpkg",
            qgis_out / "model1_DataPrep" / "adm3.gpkg",
            qgis_out / "model1_DataPrep" / "adm2.gpkg",
            qgis_out / "model1_DataPrep" / "adm1.gpkg",
            qgis_out / "model1_DataPrep" / "adm0.gpkg",
            qgis_out / "model1_DataPrep" / "pop_raster_corr.tif",
        ]
        open_in_qgis(all_layers, project_path=self.project_path, title="all_results")

    def _build_cards(self) -> None:
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        base = Path(self.project_path) if self.project_path else Path("")
        qgis_out = base / "processed" / "qgis_output"
        visum_out = base / "processed" / "visum"
        lang = getattr(self.localizer, "current_language", "de")
        is_en = str(lang).lower().startswith("en")

        # =========================================================================
        # 1. Grundlagendaten
        # =========================================================================
        sec1_header = QtWidgets.QLabel(
            f"<b>{self.localizer.get_string('step7_cat_base_data', default='1. Grundlagendaten')}</b>",
            self.scroll_widget,
        )
        sec1_header.setStyleSheet("font-size: 12px; color: #1a252f; margin-top: 4px;")
        self.scroll_layout.addWidget(sec1_header)

        # 1.1 Zensus-Einwohnerdaten
        m1_dir = qgis_out / "model1_DataPrep"
        pop_tif = m1_dir / "pop_raster_corr.tif"
        pop_gpkg = m1_dir / "pop_raster_corr.gpkg"
        pop_layers = [str(pop_tif) if pop_tif.exists() else str(pop_gpkg)]
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step7_layer_census_title", default="Zensus-Einwohnerdaten (Modell 1)"),
                description=self.localizer.get_string("step7_layer_census_desc", default="100 m × 100 m Zensusraster für den gesamten Modellraum, korrigiert durch OSM-Siedlungsflächen und optionale Referenzdaten."),
                layer_paths=pop_layers,
                folder_path=str(m1_dir),
                open_qgis_callback=lambda layers: self._open_layer_group_in_qgis(layers, "zensusraster"),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # 1.2 Administrative Grenzen (ADM0-ADM3)
        adm_layers = [str(m1_dir / f"adm{i}.gpkg") for i in (3, 2, 1, 0) if (m1_dir / f"adm{i}.gpkg").exists()]
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step7_layer_adm_title", default="Administrative Grenzen (ADM0–ADM3)"),
                description=self.localizer.get_string("step7_layer_adm_desc", default="Verwaltungsgrenzen der Ebenen ADM0 bis ADM3 inklusive aggregierter Einwohnerzahlen und hierarchischer Nummerierungsstruktur."),
                layer_paths=adm_layers,
                folder_path=str(m1_dir),
                open_qgis_callback=lambda layers: self._open_layer_group_in_qgis(layers, "administrative_grenzen"),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # =========================================================================
        # 2. Zentrale Orte und innergemeindliche Zentralitäten
        # =========================================================================
        sec2_header = QtWidgets.QLabel(
            f"<b>{self.localizer.get_string('step7_cat_centrality', default='2. Zentrale Orte und innergemeindliche Zentralitäten')}</b>",
            self.scroll_widget,
        )
        sec2_header.setStyleSheet("font-size: 12px; color: #1a252f; margin-top: 10px;")
        self.scroll_layout.addWidget(sec2_header)

        # 2.1 Zentrale Orte (Modell 2 / ADM2)
        m2_dir = qgis_out / "model2_ZoneClass"
        m2_zone = m2_dir / "zone_adm2_typeno.gpkg"
        m2_points = m2_dir / "central_place_points.gpkg"
        m2_layers = [str(m2_zone), str(m2_points)]
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step7_layer_central_places_title", default="Zentrale Orte (Modell 2 / ADM2)"),
                description=self.localizer.get_string("step7_layer_central_places_desc", default="Zentrale Orte des Modellraums, gespeichert als Gemeindeflächen und Punktobjekte basierend auf den Bevölkerungsschwellenwerten."),
                layer_paths=m2_layers,
                folder_path=str(m2_dir),
                open_qgis_callback=lambda layers: self._open_layer_group_in_qgis(layers, "zentrale_orte"),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # 2.2 Innergemeindliche Zentralitäten und Intensitätsschätzung (Modell 5)
        m5_dir = qgis_out / "model5_UrbanCentrality"
        m5_intensity = m5_dir / "sector_all_intensity.tif"
        m5_poi = m5_dir / "sector_all_points.gpkg"
        m5_zone = m5_dir / "zone_pa_ia1_ia2.gpkg"
        m5_points = m5_dir / "zone_pa_ia1_ia2_points.gpkg"
        m5_layers = [str(m5_intensity), str(m5_poi), str(m5_zone), str(m5_points)]
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step7_layer_urban_centrality_title", default="Innergemeindliche Zentralitäten und Intensitätsschätzung (Modell 5)"),
                description=self.localizer.get_string("step7_layer_urban_centrality_desc", default="Flächenhafte Intensitätsschätzung nach Funktionsbereichen (Heatmap), POI-Standorte sowie Planungs- und Einflussräume (PA, IA1, IA2) als Verkehrszellen und diskrete Zentralitätspunkte."),
                layer_paths=m5_layers,
                folder_path=str(m5_dir),
                open_qgis_callback=lambda layers: self._open_layer_group_in_qgis(layers, "urban_centrality"),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # 2.3 Finale Verkehrszellen und zentrale Orte (Modell 6)
        m6_dir = qgis_out / "model6_ZoneAssembler"
        m6_zones = m6_dir / "zones.gpkg"
        m6_centroids = m6_dir / "zone_centroids.gpkg"
        m6_layers = [str(m6_zones), str(m6_centroids)]
        cfl_gpa_name = "EN_Central_Places_Direct_Lines_CFL.gpax" if is_en else "DE_Zentrale_Orte_Luftlinienverbindungen_VFS.gpax"
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step7_layer_zones_title", default="Finale Verkehrszellen und zentrale Orte (Modell 6)"),
                description=self.localizer.get_string("step7_layer_zones_desc", default="Zusammengeführte finale Verkehrszellenstruktur inklusive zugewiesener zentraler Orte, Zensus-Einwohnerzahlen, Strukturdaten und Schwerpunkte."),
                layer_paths=m6_layers,
                folder_path=str(m6_dir),
                open_qgis_callback=lambda layers: self._open_layer_group_in_qgis(layers, "verkehrszellen"),
                open_visum_callback=lambda _: open_in_visum(self.project_path, gpa_filename=cfl_gpa_name, deactivate_links_and_odpairs=True, language=lang),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # =========================================================================
        # 3. Netzkategorisierung für den Kfz-Verkehr
        # =========================================================================
        sec3_header = QtWidgets.QLabel(
            f"<b>{self.localizer.get_string('step7_cat_network', default='3. Netzkategorisierung für den Kfz-Verkehr')}</b>",
            self.scroll_widget,
        )
        sec3_header.setStyleSheet("font-size: 12px; color: #1a252f; margin-top: 10px;")
        self.scroll_layout.addWidget(sec3_header)

        # 3.1 Aktuelles Straßennetz
        links_gpa_name = "EN_Links.gpax" if is_en else "DE_Strecken.gpax"
        visum_layers = [str(p) for p in (list(visum_out.rglob("*.ver")) + list(visum_out.rglob("*.net")))]
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step7_layer_links_osm_title", default="Aktuelles Straßennetz"),
                description=self.localizer.get_string("step7_layer_links_osm_desc", default="Kategorisierte Darstellung des Straßennetzes nach OSM-Streckentypen im aktuellen Zustand in PTV Visum."),
                layer_paths=visum_layers,
                folder_path=str(visum_out),
                open_visum_callback=lambda _: open_in_visum(self.project_path, gpa_filename=links_gpa_name, language=lang),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # 3.2 RIN-Netzkategorisierung und Luftlinienverbindungen
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step7_layer_network_rin_title", default="RIN-Netzkategorisierung und Luftlinienverbindungen"),
                description=self.localizer.get_string("step7_layer_network_rin_desc", default="Darstellung der Luftlinienverbindungen und der automatisierten Netzkategorisierung nach RIN in PTV Visum."),
                layer_paths=visum_layers,
                folder_path=str(visum_out),
                open_visum_callback=lambda _: open_in_visum(self.project_path, gpa_filename=cfl_gpa_name, language=lang),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # =========================================================================
        # 4. Erreichbarkeitsanalysen
        # =========================================================================
        sec4_header = QtWidgets.QLabel(
            f"<b>{self.localizer.get_string('step7_cat_accessibility', default='4. Erreichbarkeitsanalysen')}</b>",
            self.scroll_widget,
        )
        sec4_header.setStyleSheet("font-size: 12px; color: #1a252f; margin-top: 10px;")
        self.scroll_layout.addWidget(sec4_header)

        # 4.1 Erreichbarkeitsanalyse
        acc_gpa_name = "EN_Accessibility.gpax" if is_en else "DE_Erreichbarkeit.gpax"
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step7_layer_accessibility_title", default="Erreichbarkeitsanalyse"),
                description=self.localizer.get_string("step7_layer_accessibility_desc", default="Darstellung der Reisezeit zum nächsten untersuchten Ziel auf Basis der Verkehrszellen für Pkw, ÖV, Fuß und Rad in PTV Visum."),
                layer_paths=visum_layers,
                folder_path=str(visum_out),
                open_visum_callback=lambda _: open_in_visum(self.project_path, gpa_filename=acc_gpa_name, zoom_study_area=True, language=lang),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        self.scroll_layout.addStretch()

    def refresh_results(self) -> None:
        """Re-render cards with updated paths."""
        self._build_cards()
