"""QGIS geometry adapter for the durable centrality editing service."""
from pathlib import Path

from core.app.app_core.centrality_edits import Table, Change, Command, point_blob


# Explicit roles, rather than accepting every point layer with a TypeNo column.
ROLES = {
    'central_place_points': ('model2', 'model2_ZoneClass/zone_adm2_typeno.gpkg', True),
    'zone_pa_ia1_ia2_points': ('model5', 'model5_UrbanCentrality/zone_pa_ia1_ia2.gpkg', True),
    'zone_centroids': ('model6', 'model6_ZoneAssembler/zones.gpkg', True),
    'inner_urban_central_points_nationwide': ('nationwide_inner', 'model1_DataPrep/adm2.gpkg', False),
    'nationwide_centrality_points': ('nationwide_inner', 'model1_DataPrep/adm2.gpkg', False),
    'central_place_points_nationwide': ('nationwide_places', 'model2_ZoneClass/zone_adm2_typeno.gpkg', False),
}


def geometry(blob):
    from qgis.core import QgsGeometry
    geom = QgsGeometry()
    if blob:
        envelope_bytes = (0, 32, 48, 48, 64)[(blob[3] >> 1) & 7]
        geom.fromWkb(bytes(blob[8 + envelope_bytes:]))
    return geom


class CentralityEditor:
    def __init__(self, root, layer):
        from qgis.core import QgsVectorLayer, QgsWkbTypes
        self.root = Path(root).resolve()
        self.layer = layer
        path = layer.source().split('|')[0]
        stem = Path(path).stem.lower()
        self.role, relative, self.sync_zone = ROLES[stem]
        directory = {'model2': 'model2_ZoneClass', 'model5': 'model5_UrbanCentrality', 'model6': 'model6_ZoneAssembler',
                     'nationwide_inner': 'model5_NationwideIntensity', 'nationwide_places': 'model5_NationwideIntensity'}[self.role]
        if Path(path).resolve().parent != self.root / 'processed/qgis_output' / directory:
            raise ValueError('This is not a supported result-layer location')
        self.points = Table.open(path)
        if not Path(path).resolve().is_relative_to(self.root):
            raise ValueError('Layer is outside the current project')
        if layer.geometryType() != QgsWkbTypes.PointGeometry or not self.points.field('TypeNo'):
            raise ValueError('A centrality point layer with TypeNo is required')
        zone_path = self.root / 'processed' / 'qgis_output' / relative
        if not zone_path.is_file():
            raise ValueError(f'Missing zone layer: {zone_path}')
        self.zones = Table.open(zone_path)
        self.zone_layer = QgsVectorLayer(str(zone_path), self.zones.name, 'ogr')
        if not self.zone_layer.isValid():
            raise ValueError(f'Invalid zone layer: {zone_path}')
        self.link = next(((self.points.field(k), self.zones.field(k)) for k in ('ZONENO', 'NO')
                          if self.points.field(k) and self.zones.field(k)), None)
        if self.sync_zone and (not self.link or not all(self.zones.field(k) for k in ('TypeNo', 'XCoord', 'YCoord'))):
            raise ValueError('Zone keys, centrality and centre coordinates are required')

    def transform(self, point, source_crs, target_crs):
        from qgis.core import QgsCoordinateTransform, QgsProject
        return QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance()).transform(point)

    def zones_at(self, point, canvas_crs):
        from qgis.core import QgsGeometry
        local = self.transform(point, canvas_crs, self.zone_layer.crs())
        geom = QgsGeometry.fromPointXY(local)
        return [r for r in self.zones.rows() if geometry(r[self.zones.geometry]).intersects(geom)]

    def label(self, row, table=None):
        table = table or self.points
        name = table.field('name')
        return f"{row.get(name) or '–'} · ID {row[table.key]}"

    def _zone_of(self, row, zones):
        if self.link:
            return next((z for z in zones if z[self.link[1]] == row[self.link[0]]), None)
        if not row.get(self.points.geometry):
            return None
        point = geometry(row[self.points.geometry]).asPoint()
        hits = self.zones_at(point, self.layer.crs())
        return hits[0] if len(hits) == 1 else None

    def occupied(self, zone, excluding=None):
        zones = self.zones.rows()
        result = []
        for row in self.points.rows():
            if row[self.points.key] == excluding:
                continue
            parent = self._zone_of(row, zones)
            if parent and parent[self.zones.key] == zone[self.zones.key]:
                result.append(row)
        return result

    def command(self, mode, row=None, point=None, canvas_crs=None, level=None, zone=None, replaced=()):
        rows = self.points.rows()
        zones = self.zones.rows()
        changes = []
        original_zone = self._zone_of(row, zones) if row else None
        if self.sync_zone and row and original_zone is None:
            raise ValueError('The original zone cannot be identified unambiguously')
        if mode == 'delete':
            changes.append(Change(self.points, row, None))
        else:
            after = dict(row) if row else {col: None for col in self.points.columns}
            if row is None:
                after[self.points.key] = max((r[self.points.key] for r in rows), default=0) + 1
                for col in self.points.columns:
                    match = self.zones.field(col)
                    if match and col not in (self.points.key, self.points.geometry):
                        after[col] = zone[match]
                # These are computed values, not properties of a manually placed point.
                for col in self.points.columns:
                    if 'intensity' in col.lower() or col.lower().startswith('p_'):
                        after[col] = None
            if point is not None:
                local = self.transform(point, canvas_crs, self.layer.crs())
                after[self.points.geometry] = point_blob(local.x(), local.y(), self.points.srs_id)
                for name, value in (('XCoord', local.x()), ('YCoord', local.y())):
                    if self.points.field(name):
                        after[self.points.field(name)] = value
                if self.link:
                    after[self.link[0]] = zone[self.link[1]]
                for name in ('name', 'MAINZONENO', 'NO_E0', 'NO_E1', 'NO_E2'):
                    if self.points.field(name) and self.zones.field(name):
                        after[self.points.field(name)] = zone[self.zones.field(name)]
            if level is not None:
                after[self.points.field('TypeNo')] = level
            changes.extend(Change(self.points, old, None) for old in replaced)
            changes.append(Change(self.points, row, after))

        if self.sync_zone:
            updated = {}
            for old_zone in (original_zone,):
                if old_zone:
                    updated[old_zone[self.zones.key]] = dict(old_zone)
                    for name, value in (('TypeNo', 9), ('XCoord', None), ('YCoord', None)):
                        updated[old_zone[self.zones.key]][self.zones.field(name)] = value
            if mode != 'delete':
                target = zone or original_zone
                new_zone = dict(target)
                new_zone[self.zones.field('TypeNo')] = after[self.points.field('TypeNo')]
                pt = geometry(after[self.points.geometry]).asPoint()
                pt = self.transform(pt, self.layer.crs(), self.zone_layer.crs())
                new_zone[self.zones.field('XCoord')] = pt.x()
                new_zone[self.zones.field('YCoord')] = pt.y()
                updated[target[self.zones.key]] = new_zone
            for before in zones:
                if before[self.zones.key] in updated:
                    changes.append(Change(self.zones, before, updated[before[self.zones.key]]))
        kind = 'level' if mode == 'level' else ('position' if mode == 'move' else 'membership')
        return Command(mode, self.role, kind, changes)
