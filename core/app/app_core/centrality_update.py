"""Dependency-only refresh. Centrality generators are never invoked here."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

from core.app.app_core.centrality_edits import Change, Table, centrality_level


def assembly_context(root):
    """Only existing zone products are needed; generators are not prerequisites."""
    directory = root / 'processed/qgis_output/model6_ZoneAssembler'
    for name in ('zones', 'zone_centroids'):
        path = directory / (name + '.gpkg')
        if not path.is_file():
            raise ValueError(f'Missing zone assembly prerequisite: {path}')
    return directory


def preflight(session):
    pending = session.pending()
    root = session.root
    missing = []
    if 'assembly' in pending:
        try:
            assembly_context(root)
        except (ValueError, OSError) as exc:
            missing.append(str(exc))
    if 'visum_zones' in pending:
        checkpoint = root / 'processed/visum/ver/04_LinkTypes_Restructured.ver'
        if not checkpoint.is_file():
            missing.append(str(checkpoint))
    if 'exports' in pending:
        for name in ('zones', 'mainzones', 'zone_centroids'):
            path = root / 'processed/qgis_output/model6_ZoneAssembler' / (name + '.gpkg')
            if not path.is_file():
                missing.append(str(path))
    for product in ('connectors', 'matrices', 'analysis', 'presentation'):
        if product in pending and 'visum_zones' not in pending:
            predecessor = {'connectors': 'visum_zones', 'matrices': 'connectors', 'analysis': 'matrices', 'presentation': 'analysis'}[product]
            if predecessor not in pending:
                checkpoint = root / f'processed/visum/ver/centrality_{predecessor}.ver'
                if product == 'analysis' and not checkpoint.is_file():
                    checkpoint = root / 'processed/visum/ver/06_DirectLineMatrices_Added.ver'
                if not checkpoint.is_file():
                    missing.append(str(checkpoint))
            break
    if missing:
        raise ValueError('Missing prerequisites; no complete pipeline will be started:\n' + '\n'.join(missing))
    return pending


def _generated_changes(session, target, incoming):
    """Merge by business identity, preserve FIDs and explicit manual overrides."""
    old = target.rows()
    key = target.field('ZONENO') or target.field('NO')
    if not key or not incoming.field(key):
        raise ValueError(f'Missing stable zone identity: {target.path}')
    records = incoming.rows()
    def indexed(rows, column):
        result = {}
        for row in rows:
            if row[column] in result:
                raise ValueError(f'Ambiguous zone identity: {target.path}, {row[column]}')
            result[row[column]] = row
        return result
    old_by_key = indexed(old, key)
    new_by_key = indexed(records, incoming.field(key))
    overrides = session.state().get('overrides', {})
    for identity, values in overrides.items():
        relative, name, business, identity_value = json.loads(identity)
        if session.root / relative != Path(target.path) or name != target.name:
            continue
        match = next((k for k, row in new_by_key.items() if row.get(business) == identity_value), None)
        if values.get('__deleted__'):
            if match is not None:
                del new_by_key[match]
            continue
        decoded = {k: bytes.fromhex(v['blob']) if isinstance(v, dict) and 'blob' in v else v
                   for k, v in values.items() if k != '__deleted__'}
        if match is None:
            # A manually added point is not necessarily produced by assembly.
            existing = next((r for r in old if r.get(business) == identity_value), None)
            if existing is not None:
                decoded = {**existing, **decoded}
            if decoded.get(key) is None:
                raise ValueError(f'Cannot restore manual centre: {identity}')
            new_by_key[decoded[key]] = decoded
        else:
            new_by_key[match].update(decoded)
    changes = []
    maximum = max((r[target.key] for r in old), default=0)
    for value, row in new_by_key.items():
        before = old_by_key.get(value)
        after = dict(before) if before else {c: None for c in target.columns}
        for col in target.columns:
            source_col = incoming.geometry if col == target.geometry else incoming.field(col)
            if source_col in row and col != target.key:
                after[col] = row[source_col]
        if before is None:
            maximum += 1
            after[target.key] = maximum
        if after != before:
            changes.append(Change(target, before, after))
    changes.extend(Change(target, row, None) for value, row in old_by_key.items() if value not in new_by_key)
    return changes


def refresh_copies(session):
    root = session.root / 'processed/qgis_output'
    source = root / 'model2_ZoneClass/central_place_points.gpkg'
    target = root / 'model5_NationwideIntensity/central_place_points_nationwide.gpkg'
    if target.is_file():
        session.write_product_changes(_generated_changes(session, Table.open(target), Table.open(source)))


def refresh_assembly(session, log, stop_event):
    """Refresh the centre-dependent joins of Model 6, preserving its zoning.

    Full Model 6 also generates population heatmaps and reclassifies centres.
    Neither operation belongs in a manual-centre refresh. Existing zone geometry,
    population and structure data remain valid; only edited centre attributes and
    the derived point representation are joined again by the original ZONENO.
    """
    from qgis.core import QgsVectorLayer, QgsCoordinateTransform
    from core.app.app_core.centrality_edits import point_blob
    directory = assembly_context(session.root)
    state = session.state()
    source_values = {}
    source_tables = {}
    for identity in state.get('overrides', {}):
        relative, name, business, identity_value = json.loads(identity)
        path = session.root / relative
        if path.stem not in ('zone_adm2_typeno', 'zone_pa_ia1_ia2'):
            continue
        if (str(path), name) not in source_tables:
            table = Table.open(path, name)
            source_tables[(str(path), name)] = (table, table.rows())
        table, source_rows = source_tables[(str(path), name)]
        match = next((r for r in source_rows if r.get(business) == identity_value), None)
        if match is None:
            raise ValueError(f'Missing edited zone: {path}, {identity_value}')
        if identity_value in source_values:
            raise ValueError(f'Ambiguous source zone: {identity_value}')
        source_values[identity_value] = (table, match)
    targets = [Table.open(directory / (name + '.gpkg')) for name in ('zones', 'zone_centroids')]
    changes = []
    target_zone_rows = {r[(targets[0].field('ZONENO') or targets[0].field('NO'))]: r for r in targets[0].rows()}
    for target in targets:
        layer = QgsVectorLayer(target.path, target.name, 'ogr')
        key = target.field('ZONENO') or target.field('NO')
        rows = target.rows()
        by_key = {r[key]: r for r in rows}
        is_point = Path(target.path).stem == 'zone_centroids'
        next_id = max((r[target.key] for r in rows), default=0)
        for zone_id, (source, row) in source_values.items():
            if stop_event.is_set():
                raise InterruptedError('Update stopped')
            before = by_key.get(zone_id)
            if before is None and (not is_point or zone_id not in target_zone_rows):
                continue  # The source ADM zone was legitimately cropped from Model 6.
            seed = before or target_zone_rows[zone_id]
            after = {c: seed.get(c) for c in target.columns}
            if before is None:
                next_id += 1
                after[target.key] = next_id
            for name in ('TypeNo', 'XCoord', 'YCoord', 'name'):
                if target.field(name) and source.field(name):
                    after[target.field(name)] = row[source.field(name)]
            source_layer = QgsVectorLayer(source.path, source.name, 'ogr')
            x = row[source.field('XCoord')]
            y = row[source.field('YCoord')]
            if x is not None and y is not None:
                from qgis.core import QgsPointXY, QgsProject
                point = QgsCoordinateTransform(source_layer.crs(), layer.crs(), QgsProject.instance()).transform(QgsPointXY(float(x), float(y)))
                after[target.field('XCoord')] = point.x()
                after[target.field('YCoord')] = point.y()
                if is_point:
                    after[target.geometry] = point_blob(point.x(), point.y(), target.srs_id)
            if is_point and (centrality_level(row[source.field('TypeNo')]) not in (0, 1, 2, 3, 4) or x is None or y is None):
                after = None
            # Explicit edits made directly in Model 6 take precedence over copies.
            for identity, values in state.get('overrides', {}).items():
                relative, name, business, value = json.loads(identity)
                if session.root / relative == Path(target.path) and seed.get(business) == value:
                    if values.get('__deleted__'):
                        after = None
                    elif after is not None:
                        for col, v in values.items():
                            if col in target.columns and col != target.key:
                                after[col] = bytes.fromhex(v['blob']) if isinstance(v, dict) and 'blob' in v else v
            if before != after:
                changes.append(Change(target, before, after))
        del layer
    log('Model 6: refresh centre joins; preserve centre selection, heatmaps and zone geometry')
    session.write_product_changes(changes)


def refresh_exports(session):
    from core.app.app_core.pipeline import export_vector_file_to_shapefile
    source = session.root / 'processed/qgis_output/model6_ZoneAssembler'
    target = session.root / 'processed/visum/shapefile/Zones'
    target.mkdir(parents=True, exist_ok=True)
    for name in ('zones', 'mainzones', 'zone_centroids'):
        if not export_vector_file_to_shapefile(str(source / (name + '.gpkg')), str(target / (name + '.shp'))):
            raise RuntimeError(f'Could not export {name}')


class VisumRefresh:
    """Reuse the prepared network; update zones without repeating OSM import."""
    def __init__(self, session):
        self.session = session
        self.visum = None
        self._com_initialized = False
        self._chain = None

    def module(self, name):
        # Existing scripts import their siblings by their original module names.
        import sys
        directory = Path(__file__).resolve().parents[2] / 'scripts/visum/scripts'
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
        return importlib.import_module(name)

    def run(self, product, log):
        directory = self.session.root / 'processed/visum/ver'
        if self.visum is None:
            import pythoncom
            pythoncom.CoInitialize()
            self._com_initialized = True
            import win32com.client
            self.visum = win32com.client.Dispatch('Visum.Visum.250')
            predecessor = {'visum_zones': None, 'connectors': 'visum_zones', 'matrices': 'connectors',
                           'analysis': 'matrices', 'presentation': 'analysis'}[product]
            name = '04_LinkTypes_Restructured.ver' if predecessor is None else f'centrality_{predecessor}.ver'
            if product == 'analysis' and not (directory / name).is_file():
                name = '06_DirectLineMatrices_Added.ver'
            self.visum.LoadVersion(str(directory / name))
            chain = self.module('00_run_all_visum_scripts')
            chain._ACTIVE_VISUM_INSTANCE = self.visum
            self._chain = chain
        if product == 'visum_zones':
            import geopandas as gpd
            import pandas as pd
            step = self.module('01_import_links_and_zones')
            source = self.session.root / 'processed/qgis_output/model6_ZoneAssembler/zones.gpkg'
            zones = gpd.read_file(source)
            frame = step.prepare_geopackage_df(zones, is_mainzone=False)
            # Non-central traffic zones still need a valid Visum location.
            # Cleared centres fall back to the existing zone's geometric centroid.
            for column, values in (('XCOORD', zones.geometry.centroid.x), ('YCOORD', zones.geometry.centroid.y)):
                frame[column] = frame[column].fillna(values)
            existing = {int(no): index for index, no in self.visum.Net.Zones.GetMultiAttValues('NO', OnlyActive=False)}
            new_numbers = {int(row['NO']) for _, row in frame.iterrows()}
            # Editing a centre never removes its administrative/traffic zone.
            # Unexpected topology changes require a separate full import.
            if set(existing) != new_numbers:
                raise ValueError('Zone identities changed; update prerequisites before refreshing Visum')
            for col in frame.columns:
                if col in ('NO', 'WKTLOC'):
                    continue
                if not self.visum.Net.Zones.AttrExists(col):
                    continue
                values = []
                for _, row in frame.iterrows():
                    value = row[col]
                    if hasattr(value, 'item'):
                        value = value.item()
                    # Cleared central points retain the non-central zone's
                    # geometric location in Visum; TypeNo 9 disables centrality.
                    if col in ('XCOORD', 'YCOORD') and (value is None or pd.isna(value)):
                        continue
                    values.append((existing[int(row['NO'])], value))
                if values:
                    self.visum.Net.Zones.SetMultiAttValues(col, values)
        elif product == 'connectors':
            self.visum = self.module('04_generate_connectors').run_generate_connectors(target_project_dir=str(self.session.root), visum=self.visum)
        elif product == 'matrices':
            self.visum = self.module('05_generate_direct_line_matrices').run_direct_line_matrix_generation(target_project_dir=str(self.session.root), visum=self.visum)
        elif product == 'analysis':
            self.visum = self.module('06_run_procedure_sequence').run_procedure_sequence(target_project_dir=str(self.session.root), visum=self.visum, log=log)
        elif product == 'presentation':
            self.visum = self.module('07_apply_gpa_parameters').apply_gpa_parameters_to_visum(target_project_dir=str(self.session.root), visum=self.visum, log=log)
        if self.visum is None:
            raise RuntimeError(f'Visum refresh failed: {product}')
        self.visum.SaveVersion(str(directory / f'centrality_{product}.ver'))

    def close(self):
        # COM apartments must be released on the worker that initialized them.
        if self._chain is not None:
            self._chain._ACTIVE_VISUM_INSTANCE = None
        self.visum = None
        if self._com_initialized:
            import gc
            import pythoncom
            gc.collect()
            pythoncom.CoUninitialize()
            self._com_initialized = False


def run_update(session, *, log=print, stop_event=None, handlers=None):
    """Execute only stale products; acknowledge each successful input revision."""
    from threading import Event
    stop_event = stop_event or Event()
    pending = preflight(session)
    visum = VisumRefresh(session)
    default = {
        'copies': lambda: refresh_copies(session),
        'assembly': lambda: refresh_assembly(session, log, stop_event),
        'exports': lambda: refresh_exports(session),
    }
    if handlers:
        default.update(handlers)
    session.busy = True
    try:
        for product in pending:
            if stop_event.is_set():
                raise InterruptedError('Update stopped')
            revision = session.revision(product)
            log(f'{product}: updating dependent result')
            if product in default:
                default[product]()
            else:
                visum.run(product, log)
            if stop_event.is_set():
                raise InterruptedError('Update stopped')
            session.complete(product, revision)
    finally:
        visum.close()
        session.busy = False
