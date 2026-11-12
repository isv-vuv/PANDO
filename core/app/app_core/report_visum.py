"""Native Visum report figures, including the model's own GPA legend."""
from __future__ import annotations

from pathlib import Path
import platform
import xml.etree.ElementTree as ET

LEGEND_SCALE = 0.7
LEGEND_SPACE = 0.34

FIGURES = (
    ('10_luftlinien_aussenraum.png', 'Luftlinienverbindungen im Außenraum (Stufen 0 und I)'),
    ('10a_luftlinien_einflussraum.png', 'Luftlinienverbindungen im Planungs- und Einflussraum (Stufen I, II und III)'),
    ('11_strassennetz_aussenraum.png', 'Kategorisiertes Straßennetz im Außenraum'),
    ('12_strassennetz_einflussraum.png', 'Kategorisiertes Straßennetz im Planungs- und Einflussraum'),
    ('13_strassennetz_planungsraum.png', 'Kategorisiertes Straßennetz im Planungsraum'),
    ('14_strassennetz_detail.png', 'Kategorisiertes Straßennetz im Detailausschnitt der drei intensitätsstärksten innerörtlichen Zentralitäten'),
)


FIGURE_VIEWS = (('outer', (0, 1)), ('study', (1, 2, 3)),
                ('outer', ()), ('study', ()), ('planning', ()), ('detail', ()))

ACCESSIBILITY_FIGURES = (
    ('15_erreichbarkeit_planungsraum.png', 'Erreichbarkeitsanalyse im Planungsraum'),
    ('16_erreichbarkeit_einflussraum.png', 'Erreichbarkeitsanalyse im Planungs- und Einflussraum'),
)


def resolve_accessibility_gpa(project_path, language='de'):
    from core.app.app_core.project import tool_root
    english = str(language).lower().startswith('en')
    name = 'Accessibility.gpax' if english else 'Erreichbarkeit.gpax'
    prefix = 'EN_' if english else 'DE_'
    directory = Path(project_path) / 'processed/visum/gpa'
    for candidate in (directory / name, directory / (prefix + name),
                      Path(tool_root()) / 'core/scripts/visum/helper_files/gpa' / (prefix + name)):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError('Grafikparameter für Erreichbarkeit fehlen.')

def find_final_version(project_path):
    """Intermediate exports do not prove that step 5 completed successfully."""
    directory = Path(project_path) / 'processed/visum/ver'
    versions = [p for p in directory.glob('07_*_Model.ver') if p.is_file()]
    return max(versions, key=lambda p: p.stat().st_mtime) if versions else None


def resolve_network_gpa(project_path, language='de'):
    from core.app.app_core.project import tool_root
    directory = Path(project_path) / 'processed/visum/gpa'
    english = str(language).lower().startswith('en')
    name = 'Central_Places_Direct_Lines_CFL.gpax' if english else 'Zentrale_Orte_Luftlinienverbindungen_VFS.gpax'
    prefix = 'EN_' if english else 'DE_'
    for candidate in (directory / name, directory / (prefix + name),
                      Path(tool_root()) / 'core/scripts/visum/helper_files/gpa' / (prefix + name)):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError('Grafikparameter für Zentrale Orte / Luftlinienverbindungen / VFS fehlen.')


def report_frame(xmin, ymin, xmax, ymax, legend_space=LEGEND_SPACE):
    """Fit the whole region into the left part of a 3:2 image."""
    center_x, center_y = (xmin + xmax) / 2, (ymin + ymax) / 2
    width = max((xmax - xmin) / (1 - legend_space), (ymax - ymin) * 1.5)
    height = width / 1.5
    # Extending the viewport eastward moves the map content left in the image.
    left = center_x - width * (1 - legend_space) / 2
    return left, center_y - height / 2, left + width, center_y + height / 2


def report_graphic_parameters(source, destination, levels=(0, 1)):
    """Scale a report-only copy of the native legend; keep source GPAs intact."""
    tree = ET.parse(source)
    legend = tree.getroot().find('legendParameters')
    if legend is None:
        raise ValueError('Die Visum-Grafikparameter enthalten keine Legende.')
    # Point symbols retain their GPA size in native legends. Keep their row
    # height so smaller text does not make adjacent central-place symbols overlap.
    sizes = {'textSize', 'symbolWidth', 'maximumWidth',
             'gap_Classifications', 'gap_Columns', 'gap_Elements',
             'gap_FooterTexts_GraphicScale', 'gap_LegendElements_Footer',
             'gap_SubElements', 'gap_Symbol_Label',
             'gap_Title_LegendElements', 'margin_Bottom', 'margin_Left',
             'margin_Right', 'margin_Top', 'imageHeight', 'minLineHeight'}
    for element in legend.iter():
        for key in sizes.intersection(element.attrib):
            value = element.get(key)
            if value:
                element.set(key, format(float(value) * LEGEND_SCALE, '.8g'))
        if element.tag == 'stroke' and element.get('width'):
            element.set('width', format(float(element.get('width')) * LEGEND_SCALE, '.8g'))
    general = legend.find('legendGeneralParameters')
    if general is not None:
        general.set('legendAlignment', 'TOPRIGHTALIGNMENT')
        general.set('gap_Symbols', format(max(2.0, float(general.get('gap_Symbols') or 0)), '.8g'))
    # Matrix 1 encodes level 0, matrix 2 level I, and so on.
    enabled_matrices = {f'MATVALUE({level + 1})' for level in levels}
    for item in tree.getroot().findall('.//desireLinesZones/items/item'):
        bar = item.find('standardBar')
        if bar is not None:
            item.set('draw', str(bar.get('scaleAttrID') in enabled_matrices).lower())
    destination = Path(destination)
    tree.write(destination, encoding='utf-8', xml_declaration=True)
    return destination


def report_extents(root, data, projection):
    """Transform the actual report polygons into the loaded Visum model's CRS."""
    from qgis.core import QgsProject, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry
    from core.app.app_core.report_maps import _union, _outer_geometry, detail_geometry
    from core.app.app_core.result_maps import load_result_layer
    root = Path(root)
    project = QgsProject()
    target = QgsCoordinateReferenceSystem()
    if not target.createFromWkt(str(projection)):
        raise ValueError('Das Koordinatensystem des fertigen Visum-Modells ist ungültig.')
    source = QgsCoordinateReferenceSystem((root / 'input/local_crs.txt').read_text(encoding='utf-8').strip())
    project.setCrs(source)
    selection = load_result_layer(root / 'input/zone_type_selected.gpkg')
    field = next((f.name() for f in selection.fields() if f.name().lower().replace('_', '').replace(' ', '') == 'zonetype'), None)
    if field is None:
        raise ValueError('ZoneType fehlt in den ausgewählten Planungs- und Einflussräumen.')
    polygons = {
        'outer': _outer_geometry(root, data, project, source),
        'study': _union(selection, project, source, lambda f: f[field] in ('PA', 'IA1', 'IA2')),
        'planning': _union(selection, project, source, lambda f: f[field] == 'PA'),
    }
    polygons['detail'] = detail_geometry(root, project, source, polygons['planning'])
    transform = QgsCoordinateTransform(source, target, project)
    extents = {}
    for name, original in polygons.items():
        geometry = QgsGeometry(original)
        geometry.transform(transform)
        bounds = geometry.boundingBox()
        if bounds.isEmpty() or bounds.width() <= 0 or bounds.height() <= 0:
            raise ValueError(f'Kein gültiger Visum-Kartenausschnitt: {name}')
        if name != 'detail':
            bounds.scale(1.08)
        extents[name] = report_frame(bounds.xMinimum(), bounds.yMinimum(),
                                     bounds.xMaximum(), bounds.yMaximum(),
                                     legend_space=LEGEND_SPACE if name == 'outer' else 0.38)
    return extents


def configure_network_view(visum, *, desire_lines, central_places=True):
    graphic = visum.Net.GraphicParameters
    graphic.Links.SetAttValue('DRAW', not desire_lines)
    graphic.DesireLinesZones.SetAttValue('DRAW', desire_lines)
    graphic.DesireLinesMainZones.SetAttValue('DRAW', False)
    graphic.Zones.SetAttValue('DRAW', central_places)
    legend = visum.Net.LegendParameters
    legend.LegendGeneralParameters.SetAttValue('DRAWLEGEND', True)
    # Keep the model's own labels, symbols and layout, but show the legend
    # entries belonging to the visible objects only.
    elements = legend.LegendElementsParameters
    elements.ItemByKey('LINKS').Enabled = not desire_lines
    elements.ItemByKey('ZONES').Enabled = central_places
    elements.ItemByKey('DESIRELINESZONES').Enabled = desire_lines


def own_visum_lifetime(visum):
    """Close only this dedicated COM server even when the report worker is killed."""
    import win32api
    import win32job
    import uuid
    job = win32job.CreateJobObject(None, "PANDO_Report_" + uuid.uuid4().hex)
    try:
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        pid = int(visum.ProcessId)
        process = win32api.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        try:
            win32job.AssignProcessToJobObject(job, process)
        finally:
            process.Close()
        return job
    except Exception:
        job.Close()
        raise


def export_visum_maps(project_path, output, data, progress):
    from core.app.app_core.report_language import report_text
    tr = lambda text: report_text(text, data.get('language', 'de'))
    final = find_final_version(project_path)
    if final is None:
        return {'status': 'skipped', 'message': tr('Der Visum-Import in Schritt 5 wurde nicht durchgeführt oder nicht vollständig abgeschlossen. Die Visum-Grafiken zur Netzkategorisierung werden deshalb übersprungen.'), 'figures': []}
    if platform.system() != 'Windows':
        return {'status': 'unavailable', 'message': tr('Ein fertiges Visum-Modell ist vorhanden. Der Export der Visum-Grafiken benötigt Windows mit einer installierten und lizenzierten PTV-Visum-Version.'), 'figures': []}
    gpa = resolve_network_gpa(project_path, data.get('language', 'de'))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report_gpa = report_graphic_parameters(gpa, output / '_report_graphic_parameters.gpax')
    study_gpa = report_graphic_parameters(gpa, output / '_report_graphic_parameters_study.gpax', levels=(1, 2, 3))
    import pythoncom
    import win32com.client as com
    from core.scripts.visum.scripts.visum_com import get_visum_progid
    pythoncom.CoInitialize()
    visum = None
    job = None
    try:
        # A dedicated instance keeps the user's currently open model unchanged.
        progress(83, "report_visum_start")
        visum = com.DispatchEx(get_visum_progid())
        job = own_visum_lifetime(visum)
        visum.LoadVersion(str(final.resolve()))
        visum.Graphic.ShowMaximized()
        visum.Graphic.MaximizeNetWindow()
        extents = report_extents(project_path, data, visum.Net.AttValue('PROJECTIONDEFINITION'))
        figures = []
        for index, ((filename, caption), (extent, levels)) in enumerate(zip(FIGURES, FIGURE_VIEWS)):
            progress(84 + index, f'report_visum_{index + 1}')
            parameters = study_gpa if levels == (1, 2, 3) else report_gpa
            visum.Net.GraphicParameters.OpenXml(str(parameters.resolve()))
            configure_network_view(visum, desire_lines=bool(levels), central_places=extent != 'outer')
            bounds = extents[extent]
            visum.Graphic.SetWindow(*bounds)
            if extent == 'detail':
                from importlib import import_module
                save = import_module('core.scripts.visum.scripts.07_apply_gpa_parameters').save_graphic_parameters
                detail_gpa = Path(project_path) / 'processed/visum/gpa/_Ausschnitt_Inneroertliche_Intensitaeten.gpa'
                detail_gpa.parent.mkdir(parents=True, exist_ok=True)
                save(visum, str(detail_gpa.resolve()))
            destination = output / filename
            # This native export includes Visum's GPA-based legend and scale.
            visum.Graphic.ExportNetworkImageFile(str(destination.resolve()), *bounds, 2400, 300, 100)
            if not destination.is_file() or destination.stat().st_size == 0:
                raise RuntimeError('Visum hat keine Grafik erzeugt: ' + filename)
            figures.append({'path': 'pictures/maps/' + filename, 'caption': tr(caption)})
        accessibility_gpa = report_graphic_parameters(
            resolve_accessibility_gpa(project_path, data.get('language', 'de')),
            output / '_report_accessibility.gpax', levels=())
        accessibility_figures = []
        for (filename, caption), extent in zip(ACCESSIBILITY_FIGURES, ('planning', 'study')):
            visum.Net.GraphicParameters.OpenXml(str(accessibility_gpa.resolve()))
            bounds = extents[extent]
            visum.Graphic.SetWindow(*bounds)
            destination = output / filename
            visum.Graphic.ExportNetworkImageFile(str(destination.resolve()), *bounds, 2400, 300, 100)
            if not destination.is_file() or destination.stat().st_size == 0:
                raise RuntimeError('Visum hat keine Grafik erzeugt: ' + filename)
            accessibility_figures.append({'path': 'pictures/maps/' + filename, 'caption': tr(caption)})
        return {'status': 'done', 'message': '', 'version': str(final), 'graphic_parameters': str(gpa), 'legend_scale': LEGEND_SCALE, 'legend_space': LEGEND_SPACE, 'figures': figures, 'accessibility_figures': accessibility_figures, 'detail_gpa': str(detail_gpa)}
    except Exception as exc:
        raise RuntimeError(f'Visum-Kartenexport fehlgeschlagen: {exc}') from exc
    finally:
        # Releasing all COM references closes a Visum COM instance normally.
        # The job also covers cancellation during an in-flight COM call.
        visum = None
        if job is not None:
            job.Close()
        pythoncom.CoUninitialize()
