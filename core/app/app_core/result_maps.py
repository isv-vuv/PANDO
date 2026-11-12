"""Shared result map construction for interactive previews and report exports."""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional, Sequence
logger = logging.getLogger(__name__)

AREA_COLORS = {
    "PA": "#008080", "IA1": "#FF8C00", "IA2": "#8A2BE2",
    "DEFAULT_FILL": "", "DEFAULT_OUTLINE": "gray",
}

RESULT_GROUPS = {
    "population": ("model1_DataPrep/pop_raster_corr.tif",),
    "central_places": ("model2_ZoneClass/zone_adm2_typeno.gpkg", "model2_ZoneClass/central_place_points.gpkg"),
    "urban": ("model5_UrbanCentrality/sector_all_intensity.tif", "model5_UrbanCentrality/sector_all_points.gpkg", "model5_UrbanCentrality/zone_pa_ia1_ia2.gpkg", "model5_UrbanCentrality/zone_pa_ia1_ia2_points.gpkg"),
    "zones": ("model6_ZoneAssembler/zones.gpkg", "model6_ZoneAssembler/zone_centroids.gpkg"),
}

CENTRALITY_POINT_LAYERS = {
    "central_place_points", "zone_pa_ia1_ia2_points", "zone_centroids",
    "central_place_points_nationwide", "inner_urban_central_points_nationwide",
    "nationwide_centrality_points",
}

def result_paths(root, group):
    return [Path(root) / "processed/qgis_output" / name for name in RESULT_GROUPS[group]]

def load_result_layer(path, name=None):
    from qgis.core import QgsCategorizedSymbolRenderer, QgsMarkerSymbol, QgsRasterLayer, QgsVectorLayer
    path = Path(path)
    layer = (QgsRasterLayer(str(path), name or path.stem, "gdal")
             if path.suffix.lower() in (".tif", ".tiff", ".asc", ".sdat")
             else QgsVectorLayer(str(path), name or path.stem, "ogr"))
    if not layer.isValid():
        raise ValueError(f"Ungültiger Kartenlayer: {path}")
    sidecar = path.with_suffix(".qml")
    # Existing projects may still have a copied rainbow style beside the raster.
    # Always use the current application palette for intensity estimates.
    if path.stem in ("sector_all_intensity", "pop_raster_corr") or not sidecar.is_file():
        from core.app.app_core.project import tool_root
        style = {
            "pop_raster_corr": "population_raster", "sector_all_intensity": "intensity",
            "sector_all_points": "poi_points", "zone_adm2_typeno": "centrality_polygons",
            "zone_pa_ia1_ia2": "centrality_polygons", "zones": "centrality_polygons",
            **dict.fromkeys(CENTRALITY_POINT_LAYERS, "centrality_points"),
        }.get(path.stem, path.stem)
        sidecar = Path(tool_root()) / "core/scripts/qgis/styles" / (style + ".qml")
    if sidecar.is_file():
        layer.loadNamedStyle(str(sidecar))
    if path.stem in CENTRALITY_POINT_LAYERS:
        renderer = layer.renderer()
        if isinstance(renderer, QgsCategorizedSymbolRenderer) and renderer.classAttribute() == "TypeNo":
            for index, category in enumerate(renderer.categories()):
                if category.renderState() and isinstance(category.symbol(), QgsMarkerSymbol):
                    symbol = category.symbol().clone()
                    symbol.setSize(symbol.size() * 1.3)
                    renderer.updateCategorySymbol(index, symbol)
    if path.stem == "zone_pa_ia1_ia2" and hasattr(layer.renderer(), "categories"):
        renderer = layer.renderer()
        for index, category in enumerate(renderer.categories()):
            if str(category.value()) == "9":
                symbol = category.symbol().clone()
                symbol.setOpacity(0)
                renderer.updateCategorySymbol(index, symbol)
    # GHS-POP stores counts per raster cell; legacy QML labels incorrectly said /ha.
    if path.stem == "pop_raster_corr" and hasattr(layer.renderer(), "shader"):
        shader = layer.renderer().shader().rasterShaderFunction()
        if hasattr(shader, "colorRampItemList"):
            items = shader.colorRampItemList()
            for item in items:
                item.label = item.label.replace("Einw./ha", "Einw./Rasterzelle")
            shader.setColorRampItemList(items)
    return layer


def create_osm_background():
    from qgis.core import QgsRasterLayer, QgsHueSaturationFilter
    url = "type=xyz&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png&zmax=19&zmin=0"
    layer = QgsRasterLayer(url, "OpenStreetMap (OSM) Grey", "wms")
    if layer.isValid():
        layer.setOpacity(0.5)
        hs = layer.pipe().hueSaturationFilter()
        if hs:
            hs.setGrayscaleMode(QgsHueSaturationFilter.GrayscaleMode.GrayscaleLightness)
            hs.setSaturation(-100)
    return layer

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

    # 2. Model 5 (Planning & Influence Areas PA / IA1 / IA2 & Nationwide Centralities)
    if ("inner_urban" in name or "pa_ia" in name or "urban" in name) and ("point" in name or "pt" in name):
        return 20
    if "central_place" in name and "nationwide" in name:
        return 23
    if "nationwide" in name and ("point" in name or "pt" in name):
        return 25
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
            QgsCoordinateTransform,
            QgsProject,
            QgsRasterLayer,
            QgsVectorLayer,
        )

        project = QgsProject()
        project.clear()
        project.setCrs(QgsCoordinateReferenceSystem(local_crs))

        # 1. Background OpenStreetMap Layer (50% opacity, grayscale, distinct name)
        osm_layer = create_osm_background()
        if osm_layer.isValid():
            project.addMapLayer(osm_layer, False)

        # 2. Result Layers
        added_layers = []
        for lp in sorted_existing_layers:
            try:
                layer = load_result_layer(lp)
            except ValueError as exc:
                logger.warning("Skipping invalid result layer: %s", exc)
                continue
            if layer.isValid():
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

