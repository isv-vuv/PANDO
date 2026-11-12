"""QGIS print layouts for report figures (runs in the report process)."""
from __future__ import annotations
from pathlib import Path
import json
import re
from core.app.app_core.result_maps import AREA_COLORS, result_paths, load_result_layer, layer_draw_order_key, create_osm_background

MAP_NAMES = (
    '01_planungsraeume', '02_aussenraum',
    '03_verkehrszellen_planung_einfluss', '04_verkehrszellen_aussenraum',
    '05_einwohner_planung', '06_einwohner_planung_einfluss',
    '07_zentrale_orte', '08_intensitaet_planung', '09_intensitaet_planung_einfluss',
    '05a_einwohner_detail', '07a_intensitaet_detail',
)


def _union(layer, project, crs, predicate=lambda f: True):
    from qgis.core import QgsGeometry, QgsCoordinateTransform
    transform = QgsCoordinateTransform(layer.crs(), crs, project)
    geometries = []
    for f in layer.getFeatures():
        if predicate(f) and f.hasGeometry():
            geometry = QgsGeometry(f.geometry())
            geometry.transform(transform)
            if not geometry.isEmpty():
                geometries.append(geometry)
    if not geometries:
        raise ValueError('Keine Geometrien für den angeforderten Berichtsraum vorhanden.')
    result = QgsGeometry.unaryUnion(geometries)
    if result.isEmpty():
        raise ValueError('Berichtsraum konnte nicht vereinigt werden.')
    return result.makeValid()


def _polygon(geometry, name, crs, fill='transparent', outline='#111111'):
    from qgis.core import QgsVectorLayer, QgsFeature, QgsFillSymbol
    layer = QgsVectorLayer('MultiPolygon?crs=' + crs.authid(), name, 'memory')
    feature = QgsFeature(layer.fields())
    feature.setGeometry(geometry)
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    layer.renderer().setSymbol(QgsFillSymbol.createSimple({
        'color': fill, 'outline_color': outline, 'outline_width': '0.3',
        'joinstyle': 'round',
    }))
    return layer


def _outer_geometry(root, data, project, crs):
    """Match selected regions exactly; parent regions must never match by substring."""
    from core.app.app_core.project import tool_root
    from qgis.core import QgsGeometry, QgsCoordinateReferenceSystem, QgsCoordinateTransform
    def normalized(value):
        value = str(value).replace('\\', '/').rstrip('/').split('/')[-1]
        value = re.sub(r'^\d{4}-\d{2}-\d{2}_', '', value.lower())
        value = re.sub(r'-(latest|\d{6})\.osm\.pbf$', '', value)
        return re.sub(r'[^a-z0-9]', '', value.removesuffix('.osm.pbf').removesuffix('.pbf'))
    index = Path(tool_root()) / 'core/data/osm/geofabrik-index.json'
    features = json.loads(index.read_text(encoding='utf-8'))['features']
    selected = data['input_pbfs']
    geometries = []
    missing = []
    for pbf in selected:
        targets = {normalized(pbf.get(k, '')) for k in ('region', 'filename', 'path', 'url', 'name')} - {''}
        ref = next((r for r in data['pbf_references'] if r.get('path') == pbf.get('path') or r.get('osm_id') == pbf.get('region')), {})
        targets.update(normalized(ref[k]) for k in ('osm_id', 'url', 'filename') if ref.get(k))
        match = next((f for f in features if targets.intersection({
            normalized(f['properties'].get('id', '')),
            normalized(f['properties'].get('name', '')),
            normalized((f['properties'].get('urls') or {}).get('pbf', '')),
        } - {''})), None)
        if match is None:
            missing.append(pbf.get('filename') or str(pbf))
        else:
            from shapely.geometry import shape
            geometries.append(QgsGeometry.fromWkt(shape(match['geometry']).wkt))
    if missing or not geometries:
        raise ValueError('Außenraum nicht eindeutig aus den gewählten OSM-Gebieten bestimmbar: ' + ', '.join(missing))
    result = QgsGeometry.unaryUnion(geometries).makeValid()
    result.transform(QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'), crs, project))
    return result


def detail_geometry(root, project, crs, planning):
    """Shared 3:2 viewport around the three strongest urban centres in PA."""
    import math
    from qgis.core import QgsGeometry, QgsCoordinateTransform, QgsRectangle
    layer = load_result_layer(result_paths(root, 'urban')[-1], 'Zentralitäten')
    transform = QgsCoordinateTransform(layer.crs(), crs, project)
    candidates = []
    for feature in layer.getFeatures():
        if not feature.hasGeometry() or int(feature['TypeNo']) not in (3, 4):
            continue
        geometry = QgsGeometry(feature.geometry())
        geometry.transform(transform)
        if not planning.intersects(geometry):
            continue
        try:
            intensity = float(feature['Total_Intensity'])
        except (TypeError, ValueError):
            continue
        if math.isfinite(intensity):
            candidates.append((intensity, feature.id(), geometry))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    if not candidates:
        raise ValueError('Keine innerörtlichen Zentralitäten mit Intensität im Planungsraum vorhanden.')
    bounds = QgsGeometry.unaryUnion([item[2] for item in candidates[:3]]).boundingBox()
    center = bounds.center()
    # At least 1 km across, even if only one centrality is available.
    width = max(bounds.width() * 1.2, bounds.height() * 1.2 * 1.5, 1000)
    height = width / 1.5
    return QgsGeometry.fromRect(QgsRectangle(center.x() - width / 2, center.y() - height / 2,
                                            center.x() + width / 2, center.y() + height / 2))


def population_report_range(layer, geometry, crs, project):
    """Use all inhabited pixels visible in the largest population report map."""
    import math
    import numpy as np
    from osgeo import gdal, ogr, osr
    from qgis.core import QgsGeometry, QgsCoordinateTransform, QgsColorRampShader
    from qgis.PyQt.QtGui import QColor
    region = QgsGeometry(geometry)
    region.transform(QgsCoordinateTransform(crs, layer.crs(), project))
    dataset = gdal.Open(layer.source())
    transform = dataset.GetGeoTransform()
    inverse = gdal.InvGeoTransform(transform)
    bounds = region.boundingBox()
    corners = [gdal.ApplyGeoTransform(inverse, x, y)
               for x in (bounds.xMinimum(), bounds.xMaximum())
               for y in (bounds.yMinimum(), bounds.yMaximum())]
    x0 = max(0, math.floor(min(x for x,y in corners)))
    y0 = max(0, math.floor(min(y for x,y in corners)))
    x1 = min(dataset.RasterXSize, math.ceil(max(x for x,y in corners)))
    y1 = min(dataset.RasterYSize, math.ceil(max(y for x,y in corners)))
    if x1 <= x0 or y1 <= y0:
        raise ValueError('Keine Einwohnerdaten im größten Berichtsausschnitt vorhanden.')
    band = dataset.GetRasterBand(1)
    values = band.ReadAsArray(x0, y0, x1-x0, y1-y0)
    mask = gdal.GetDriverByName('MEM').Create('', x1-x0, y1-y0, 1, gdal.GDT_Byte)
    origin = gdal.ApplyGeoTransform(transform, x0, y0)
    mask.SetGeoTransform((origin[0], transform[1], transform[2], origin[1], transform[4], transform[5]))
    mask.SetProjection(dataset.GetProjection())
    shapes = (ogr.GetDriverByName('MEM') or ogr.GetDriverByName('Memory')).CreateDataSource('')
    raster_crs = osr.SpatialReference()
    raster_crs.ImportFromWkt(dataset.GetProjection())
    polygons = shapes.CreateLayer('region', srs=raster_crs, geom_type=ogr.wkbUnknown)
    feature = ogr.Feature(polygons.GetLayerDefn())
    feature.SetGeometry(ogr.CreateGeometryFromWkt(region.asWkt()))
    polygons.CreateFeature(feature)
    gdal.RasterizeLayer(mask, [1], polygons, burn_values=[1])
    valid = (mask.ReadAsArray() != 0) & np.isfinite(values) & (values > 0)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        valid &= values != nodata
    valid &= band.GetMaskBand().ReadAsArray(x0, y0, x1-x0, y1-y0) != 0
    inhabited = values[valid]
    # An entirely uninhabited region still needs a valid, shared legend.
    minimum, maximum = (float(inhabited.min()), float(inhabited.max())) if inhabited.size else (0.1, 1.0)
    if minimum == maximum:
        minimum = maximum / 2
    renderer = layer.renderer()
    shader = renderer.shader().rasterShaderFunction()
    colors = [item.color for item in shader.colorRampItemList() if item.value > 0]
    items = [QgsColorRampShader.ColorRampItem(0, QColor(0,0,0,0), '0 (unbewohnt)')]
    for index, color in enumerate(colors):
        value = minimum + (maximum - minimum) * index / (len(colors)-1)
        items.append(QgsColorRampShader.ColorRampItem(value, color, f'{value:.2f} Einw./Rasterzelle'))
    shader.setMinimumValue(minimum)
    shader.setMaximumValue(maximum)
    shader.setColorRampItemList(items)
    renderer.setClassificationMin(minimum)
    renderer.setClassificationMax(maximum)
    layer.triggerRepaint()
    return minimum, maximum


MAX_FIGURE_HEIGHT_MM = 297 * 0.8


def report_map_dimensions(width, height, available_height, legend_width=None):
    """Preserve the geographic aspect ratio without horizontal padding."""
    map_width = 180.0 if legend_width is None else 180.0 - legend_width - 5.0
    if map_width <= 0:
        raise ValueError('Die Legende ist breiter als die verfügbare Grafik.')
    map_height = map_width * height / width
    if map_height > available_height:
        map_height = available_height
        map_width = map_height * width / height
    return map_width, map_height


def measure_side_legend(legend, exporter):
    """Paint inside the page so QGIS resolves the legend's natural size."""
    from qgis.core import QgsLayoutPoint, QgsLayoutSize
    from qgis.PyQt.QtCore import QSize
    legend.setColumnCount(1)
    legend.setResizeToContents(True)
    legend.attemptMove(QgsLayoutPoint(5, 2))
    legend.attemptResize(QgsLayoutSize(1, 1))
    legend.adjustBoxSize()
    exporter.renderPageToImage(0, QSize(380, 475))
    legend.adjustBoxSize()
    return legend.sizeWithUnits().width(), legend.sizeWithUnits().height()


def export_report_maps(root, output, data, progress):
    from qgis.core import (QgsProject, QgsCoordinateReferenceSystem, QgsFillSymbol,
        QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsLayoutItemMap,
        QgsPrintLayout, QgsLayoutItemLegend, QgsLayoutItemLabel, QgsLayoutItemScaleBar,
        QgsLayoutPoint, QgsLayoutSize, QgsLayoutExporter, QgsMapClippingRegion,
        QgsLegendStyle, Qgis, QgsSingleSymbolRenderer)
    from qgis.PyQt.QtGui import QFont, QColor, QFontDatabase
    from core.app.app_core.project import tool_root
    for font in (Path(tool_root()) / f"core/report_template_{data.get('language', 'de')}/fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(font))
    from core.app.app_core.report_language import report_text
    tr = lambda text: report_text(text, data.get('language', 'de'))
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    project = QgsProject()
    crs_path = root / 'input/local_crs.txt'
    crs = QgsCoordinateReferenceSystem(crs_path.read_text().strip() if crs_path.is_file() else 'EPSG:3857')
    if not crs.isValid():
        raise ValueError('Ungültiges Projekt-Koordinatensystem.')
    project.setCrs(crs)
    selection = load_result_layer(root / 'input/zone_type_selected.gpkg', 'Planungs- und Einflussräume')
    field = next((f.name() for f in selection.fields() if f.name().lower().replace('_', '').replace(' ', '') == 'zonetype'), None)
    if not field:
        raise ValueError('ZoneType fehlt in zone_type_selected.gpkg.')
    pa = _union(selection, project, crs, lambda f: f[field] == 'PA')
    study = _union(selection, project, crs, lambda f: f[field] in ('PA', 'IA1', 'IA2'))
    outer = _outer_geometry(root, data, project, crs)
    categories = []
    for key, title in [('PA','Planungsraum'), ('IA1','Einflussraum 1'), ('IA2','Einflussraum 2')]:
        color = QColor(AREA_COLORS[key]); color.setAlpha(90)
        symbol = QgsFillSymbol.createSimple({'color': AREA_COLORS[key], 'outline_color': AREA_COLORS[key], 'outline_width': '0.3'})
        symbol.setColor(color)
        categories.append(QgsRendererCategory(key, symbol, title))
    selection.setRenderer(QgsCategorizedSymbolRenderer(field, categories))
    project.addMapLayer(selection, False)
    zones = load_result_layer(result_paths(root, 'zones')[0], 'Verkehrszellen')
    zones.setRenderer(QgsSingleSymbolRenderer(
        QgsFillSymbol.createSimple({'color': 'transparent', 'outline_color': '#000000', 'outline_width': '0.16'})))
    population = load_result_layer(result_paths(root, 'population')[0], 'Einwohner je Rasterzelle')
    # All three figures reuse this one renderer and its study-area range.
    population_report_range(population, study, crs, project)
    central = [load_result_layer(p, {'zone_adm2_typeno':'Flächenklassen', 'central_place_points':'Zentrale Orte'}.get(p.stem)) for p in result_paths(root, 'central_places')]
    urban = [load_result_layer(p, {'sector_all_intensity':'Intensität (Modell 5)', 'zone_pa_ia1_ia2':'Flächenklassen', 'zone_pa_ia1_ia2_points':'Zentralitäten'}.get(p.stem)) for p in result_paths(root, 'urban') if p.stem != 'sector_all_points']
    for layer in [zones, population, *central, *urban]:
        project.addMapLayer(layer, False)
    # Reuse exactly the background style used by the interactive result projects.
    basemap = None
    background_note = 'Hintergrundkarte nicht verfügbar; neutraler Hintergrund.'
    try:
        import requests
        response = requests.get('https://tile.openstreetmap.org/0/0/0.png', timeout=4, headers={'User-Agent':'PANDO report'})
        if response.status_code == 200 and response.content.startswith(b'\x89PNG'):
            basemap = create_osm_background()
            if basemap.isValid():
                project.addMapLayer(basemap, False)
                background_note = 'Hintergrund: © OpenStreetMap-Mitwirkende (openstreetmap.org/copyright)'
            else:
                basemap = None
    except Exception:
        basemap = None
    detail = detail_geometry(root, project, crs, pa)
    specs = [
        (study, [selection], 'Planungsraum und Einflussräume'),
        (outer, [selection], 'Außenraum mit Planungs- und Einflussräumen'),
        (study, [zones, selection], 'Verkehrszellen im Planungs- und Einflussraum'),
        (outer, [zones], 'Verkehrszellen im Außenraum'),
        (pa, [zones, population], 'Einwohnerverteilung im Planungsraum'),
        (study, [zones, population], 'Einwohnerverteilung im Planungs- und Einflussraum'),
        (outer, sorted(central, key=lambda l: layer_draw_order_key(l.source())), 'Zentrale Orte im Außenraum'),
        (pa, sorted(urban, key=lambda l: layer_draw_order_key(l.source())), 'Intensität und Zentralitäten im Planungsraum'),
        (study, sorted(urban, key=lambda l: layer_draw_order_key(l.source())), 'Intensität und Zentralitäten im Planungs- und Einflussraum'),
    ]
    specs.extend([
        (detail, [zones, population], 'Einwohnerverteilung im Detailausschnitt'),
        (detail, sorted(urban, key=lambda l: layer_draw_order_key(l.source())), 'Innerörtliche Intensitäten im Detailausschnitt'),
    ])
    for index, (geometry, layers, title) in enumerate(specs):
        progress(8 + index * 6, f"report_map_{index + 1}")
        boundary = _polygon(geometry, 'Außenraum' if index in (1,3,6) else 'Gebietsgrenze', crs)
        project.addMapLayer(boundary, False)
        layers = [boundary, *layers]
        for layer in layers:
            layer.setName(tr(layer.name()))
            renderer = layer.renderer()
            if hasattr(renderer, 'categories'):
                for category_index, category in enumerate(renderer.categories()):
                    renderer.updateCategoryLabel(category_index, tr(category.label()))
            if hasattr(renderer, 'shader'):
                shader = renderer.shader().rasterShaderFunction()
                items = shader.colorRampItemList()
                for item in items:
                    item.label = tr(item.label)
                shader.setColorRampItemList(items)
        layout = QgsPrintLayout(project); layout.initializeDefaults()
        layout.renderContext().setDpi(300)
        page = layout.pageCollection().pages()[0]
        page.setPageSize(QgsLayoutSize(190, 225))
        map_item = QgsLayoutItemMap(layout); layout.addLayoutItem(map_item)
        map_item.attemptMove(QgsLayoutPoint(5, 2))
        map_item.setCrs(crs); map_item.setLayers(layers + ([basemap] if basemap else []))
        bounds = geometry.boundingBox()
        if index < 9:
            bounds.scale(1.08)
        map_width, map_height = report_map_dimensions(bounds.width(), bounds.height(), MAX_FIGURE_HEIGHT_MM)
        map_item.attemptResize(QgsLayoutSize(map_width, map_height))
        map_item.zoomToExtent(bounds)
        # Atlas coverage clips both raster and vector data to the actual polygon,
        # without changing source files or their renderers.
        atlas = layout.atlas(); atlas.setCoverageLayer(boundary); atlas.setEnabled(True)
        atlas.beginRender(); atlas.seekTo(0)
        clip = map_item.atlasClippingSettings(); clip.setEnabled(True)
        clip.setRestrictToLayers(True); clip.setLayersToClip(layers)
        clip.setFeatureClippingType(QgsMapClippingRegion.FeatureClippingType.ClipToIntersection)
        clip.setForceLabelsInsideFeature(True)
        legend = QgsLayoutItemLegend(layout); layout.addLayoutItem(legend)
        legend.setTitle(tr('Legende')); legend.setLinkedMap(map_item); legend.setAutoUpdateModel(False)
        legend.model().rootGroup().clear()
        for layer in layers:
            legend.model().rootGroup().addLayer(layer)
        legend.setColumnCount(3 if index >= 6 else 2); legend.setSplitLayer(False)
        legend.setLegendFilterByMapEnabled(True)
        for style in (QgsLegendStyle.Style.Title, QgsLegendStyle.Style.Group, QgsLegendStyle.Style.Subgroup, QgsLegendStyle.Style.SymbolLabel):
            legend.setStyleFont(style, QFont('Inter', 9))
        legend.setResizeToContents(True)
        legend.attemptMove(QgsLayoutPoint(5, map_height + 16)); legend.attemptResize(QgsLayoutSize(1, 1))
        legend.adjustBoxSize()
        scale = QgsLayoutItemScaleBar(layout); layout.addLayoutItem(scale)
        scale.setStyle('Single Box'); scale.setLinkedMap(map_item)
        scale.setUnits(Qgis.DistanceUnit.Kilometers); scale.setUnitLabel('km')
        import math
        desired = map_item.extent().width() / 1000 / 6
        base = 10 ** math.floor(math.log10(max(desired, 0.001)))
        segment = next((n * base for n in (1, 2, 5, 10) if n * base >= desired), base * 10)
        scale.setUnitsPerSegment(segment); scale.setNumberOfSegments(2); scale.setNumberOfSegmentsLeft(0)
        scale.setFont(QFont('Inter', 8))
        scale.attemptMove(QgsLayoutPoint(5, map_height + 4))
        def label(text, x, y, width, height, size):
            item = QgsLayoutItemLabel(layout); item.setText(text); item.setFont(QFont('Inter', size))
            layout.addLayoutItem(item); item.attemptMove(QgsLayoutPoint(x,y)); item.attemptResize(QgsLayoutSize(width,height))
            return item
        # A first render resolves raster legend node sizes and column layout.
        # Measure afterwards so source notes cannot overlap a dynamic legend.
        exporter = QgsLayoutExporter(layout)
        from qgis.PyQt.QtCore import QSize
        exporter.renderPageToImage(0, QSize(380, 450))
        legend.adjustBoxSize()
        note = background_note + '\nFachdaten: PANDO; GADM; GHS-POP; OpenStreetMap.'
        if index in (4, 5, 9):
            note += '\nEinwohnerwerte: Einwohner je Rasterzelle.'
        source = label(tr(note), 5, 0, 180, 1, 8)
        source.adjustSizeToText()
        source_height = source.sizeWithUnits().height()
        bottom = map_height + 16 + legend.sizeWithUnits().height() + 3
        if bottom + source_height + 1 > MAX_FIGURE_HEIGHT_MM:
            # Measure a compact single-column legend before assigning the
            # remaining width to the map. Legend text is never shortened.
            legend_width, _ = measure_side_legend(legend, exporter)
            map_width, map_height = report_map_dimensions(
                bounds.width(), bounds.height(), MAX_FIGURE_HEIGHT_MM - source_height - 20,
                legend_width=legend_width)
            map_item.attemptResize(QgsLayoutSize(map_width, map_height))
            map_item.zoomToExtent(bounds)
            # Keep the legend's right edge at the page margin, including when
            # the height limit constrains the map width.
            legend.attemptMove(QgsLayoutPoint(185 - legend_width, 2))
            scale.attemptMove(QgsLayoutPoint(5, map_height + 4))
            exporter.renderPageToImage(0, QSize(380, 475))
            legend.adjustBoxSize()
            bottom = max(map_height + 16, 2 + legend.sizeWithUnits().height()) + 3
        source.attemptMove(QgsLayoutPoint(5, bottom))
        figure_height = bottom + source_height + 1
        if figure_height > MAX_FIGURE_HEIGHT_MM + 0.1:
            raise RuntimeError('Kartenlegende überschreitet die maximale Berichtsgrafikhöhe: ' + title)
        page.setPageSize(QgsLayoutSize(190, figure_height))
        exporter = QgsLayoutExporter(layout)
        settings = QgsLayoutExporter.ImageExportSettings(); settings.dpi = 300
        settings.exportMetadata = False
        settings.generateWorldFile = False
        result = exporter.exportToImage(str(output / (MAP_NAMES[index] + '.png')), settings)
        atlas.endRender()
        if result != QgsLayoutExporter.ExportResult.Success:
            raise RuntimeError(f'QGIS-Kartenexport fehlgeschlagen: {title} ({result})')
        project.removeMapLayer(boundary.id())
        del exporter, layout
    project.clear()

