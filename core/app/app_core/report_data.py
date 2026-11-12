"""Build reproducible report inputs from the results actually calculated."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import math
from core.app.app_core.project import load_project_metadata, load_pipeline_manifest
from core.app.app_core.result_maps import result_paths

PARAMETERS = {
    'model3': {'grid_size_e0_m': 'grid_size_e0_m'},
    'model2': {
        'min_population_MR': 'minimum_population_level_0',
        'min_population_OZ': 'minimum_population_level_1',
        'min_population_MZ': 'minimum_population_level_2',
        'deviation_min_population': 'population_tolerance',
        'deviation_min_distance': 'distance_tolerance',
        'search_radius_DZ': 'dual_centres_search_radius_km',
        'deviation_min_DZ': 'dual_centres_population_tolerance',
    },
    'model5': {
        'min_distance_3-4': 'minimum_distance_level_3_4_m',
        'min_distance_3': 'minimum_distance_level_3_m',
        'min_distance_4': 'minimum_distance_level_4_m',
        'min_intensity_3': 'minimum_intensity_level_3',
        'min_intensity_4': 'minimum_intensity_level_4',
    },
}
NOT_DOCUMENTED = 'nicht dokumentiert'


def report_blockers(project_path, manifest=None):
    root = Path(project_path)
    if not root.is_dir():
        return [f'Projektordner fehlt: {root}']
    try:
        manifest = manifest if manifest is not None else load_pipeline_manifest(str(root))
    except (OSError, ValueError) as exc:
        return [f'Projektmanifest nicht lesbar: {exc}']
    errors = []
    for model in ('model1', 'model2', 'model3', 'model4', 'model5', 'model6'):
        phase = manifest.get('phases', {}).get(model, {})
        if phase.get('status') != 'done':
            errors.append(f'{model}: Ergebnisse fehlen oder müssen neu berechnet werden.')
    for model, mapping in PARAMETERS.items():
        params = manifest.get('phases', {}).get(model, {}).get('parameters', {})
        for key in mapping.values():
            value = params.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (model == 'model3' and value <= 0):
                errors.append(f'{model}: Berechnungsparameter {key} fehlt oder ist ungültig.')
    paths = [root / 'input/zone_type_selected.gpkg']
    for group in ('population', 'central_places', 'urban', 'zones'):
        # POI and zone centroids are not part of the requested report maps.
        paths.extend(p for p in result_paths(root, group)
                     if p.stem not in ('sector_all_points', 'zone_centroids'))
    errors.extend(f'Ergebnisdatei fehlt: {path}' for path in paths if not path.is_file())
    from core.app.app_core.centrality_edits import EditSession
    if (root / 'centrality_edits.sqlite').is_file():
        if EditSession(str(root)).pending():
            errors.append('Zentralitätsänderungen müssen zuerst neu berechnet werden (Schritt 6).')
    elif manifest.get('centrality_edits', {}).get('pending'):
        errors.append('Zentralitätsänderungen müssen zuerst neu berechnet werden (Schritt 6).')
    return errors


def build_report_inputs(project_path, *, metadata=None, manifest=None, language="de"):
    english = language == "en"
    not_documented = "not documented" if english else NOT_DOCUMENTED
    root = Path(project_path)
    metadata = metadata if metadata is not None else load_project_metadata(str(root))
    manifest = manifest if manifest is not None else load_pipeline_manifest(str(root))
    errors = report_blockers(root, manifest)
    if errors:
        raise ValueError('\n'.join(errors))
    location = metadata.get('selected_location') or {}
    address = (location.get('raw') or {}).get('address') or {}
    municipality = next((address.get(k) for k in ('city', 'town', 'village', 'municipality', 'county') if address.get(k)), None)
    municipality = municipality or location.get('address') or metadata.get('search_city') or root.name
    refs = metadata.get('pbf_references') or []
    rows = []
    for pbf in manifest.get('input_pbfs') or []:
        region = pbf.get('region') or pbf.get('name') or pbf.get('filename') or Path(pbf.get('path') or '').name
        ref = next((r for r in refs if r.get('path') == pbf.get('path') or r.get('osm_id') == pbf.get('region')), {})
        name = ref.get('name') or region
        date = not_documented
        if pbf.get('downloaded_at'):
            try:
                date = datetime.fromisoformat(pbf['downloaded_at'].replace('Z', '+00:00')).strftime('%Y-%m-%d' if english else '%d.%m.%Y')
            except (TypeError, ValueError):
                pass
        rows.append([name or not_documented, date])
    phases = manifest['phases']
    m1 = phases.get('model1', {}).get('parameters', {})
    sources = ['GHS-POP: https://human-settlement.emergency.copernicus.eu/GHSLWeGenerateData.php']
    if m1.get('custom_census'):
        sources.append(('Additional census data: ' if english else 'Zusätzliche Zensusdaten: ') + str(m1['custom_census']))
    if m1.get('pop_local') and not m1.get('no_local_reference'):
        sources.append(('Local population reference: ' if english else 'Lokale Einwohnerreferenz: ') + str(m1['pop_local']) + ('; field: ' if english else '; Feld: ') + str(m1.get('pop_local_field') or not_documented))
    result = {
        'title': ('Automatically generated report for PANDO project: ' if english else 'Automatisch generierter Report zum PANDO-Projekt: ') + root.name,
        'munincipality': municipality,
        'used_osm_area_with_date': rows or [[not_documented, not_documented]],
        'gadm_source': 'https://gadm.org', 'pop_source': '; '.join(sources),
        'language': language,
        'generated_at': datetime.now().astimezone().isoformat(),
        'input_pbfs': manifest.get('input_pbfs') or [],
        'pbf_references': refs,
    }
    for model, mapping in PARAMETERS.items():
        for report_key, model_key in mapping.items():
            result[report_key] = phases[model]['parameters'][model_key]
    # Model 3 divides the chosen E0 edge by 3 (IA1) and by 9 (PA).
    # Use the executed manifest value, even if the current UI was edited later.
    result['cell_size_pa_m'] = result['grid_size_e0_m'] / 9
    result['cell_size_ia1_m'] = result['grid_size_e0_m'] / 3
    result['cell_size_ia2_m'] = result['grid_size_e0_m']
    return result
