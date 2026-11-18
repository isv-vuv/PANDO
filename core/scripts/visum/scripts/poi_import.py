"""Import project POI points and administrative surfaces into existing categories."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import math
from pathlib import Path
import re
import tempfile
import unicodedata
from unittest.mock import NonCallableMock


SECTOR_CATEGORIES = {'healthcare': 21, 'leisure': 22, 'retail': 23,
                     'education': 24, 'government': 25}

SHAPEFILE_OBJ_TYPE_POIS_POLYGON = 9
SHAPEFILE_OBJ_TYPE_POI_CENTROIDS = 59

VISUM_VALUETYPE_INT = 1
VISUM_VALUETYPE_REAL = 2
VISUM_VALUETYPE_STRING = 5
VISUM_VALUETYPE_BOOL = 9

_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647

STANDARD_POI_ATTRS = frozenset({
    'NO', 'CODE', 'NAME', 'COMMENT', 'XCOORD', 'YCOORD',
    'WKTSURFACE', 'WKTLOC', 'WKTLOCWGS84', 'WKTSURFACEWGS84',
    'CATNO', 'CATNAME', 'AREAKM2', 'AREAMI2', 'SURFACEID',
    'IMAGEANGLE', 'IMAGEFILENAME', 'IMAGEHEIGHT', 'ISINSELECTION',
    'LABELPOSRELX', 'LABELPOSRELY', 'USEIMAGEFILE', 'USEIMAGEHEIGHT',
})

INTEGER_POI_ATTRS = frozenset({'FID', 'ZONENO', 'MAINZONENO', 'POP'})
BOOLEAN_POI_ATTRS = frozenset({'IS_POLYGON'})
IGNORED_POI_ATTRS = frozenset({'PATH'})
SHP_TRUNCATED_ATTR_MAP = {
    'CLASSIFICA': 'CLASSIFICATION',
    'DESCRIPTIO': 'DESCRIPTION',
    'PRIMARY_TA': 'PRIMARY_TAG',
    'SECONDARY_': 'SECONDARY_TAG',
}
OBSOLETE_POI_UDAS = frozenset({'PATH', *SHP_TRUNCATED_ATTR_MAP.keys()})

GEODATA_SUFFIXES = ('.geojson', '.json', '.shp')


def category_for_file(path, administrative=False):
    name = Path(path).stem.lower()
    if administrative:
        match = re.search(r'(?:^|_)adm([0-3])(?:_|$)', name)
        return 10 + int(match.group(1)) if match else None
    if 'all' in name:
        return None
    tokens = name.split('_')
    return next((number for sector, number in SECTOR_CATEGORIES.items()
                 if sector in tokens), None)


def ensure_import_categories(visum):
    """Prepare import categories independently of the procedure sequence."""
    existing = {int(cat.AttValue('No')) for cat in visum.Net.POICategories.GetAll}
    categories = [(1, 'ADM', None), (2, 'POI', None)]
    categories += [(10 + level, f'ADM{level}', 1) for level in range(4)]
    categories += [(20, 'All', 2)]
    categories += [(number, sector.title(), 2) for sector, number in SECTOR_CATEGORIES.items()]
    for number, code, parent in categories:
        if number in existing:
            continue
        category = visum.Net.AddPOICategory()
        category.SetAttValue('No', number)
        category.SetAttValue('Code', code)
        category.SetAttValue('Name', code)
        if parent is not None:
            category.SetAttValue('PARENTCATNO', parent)


class POIValidationError(ValueError):
    pass


@dataclass
class PreparedPOI:
    source: str
    record: str
    category: int
    x: float | None
    y: float | None
    name: str | None
    surface: str | None
    _geometry: object = field(default=None, repr=False)
    _administrative: bool = field(default=False, repr=False)

    def resolve_coords_and_surface(self) -> tuple[float, float, str | None]:
        """Materialize centroid coordinates and WKT surface only when needed for record fallback."""
        if (self.x is None or self.y is None) and self._geometry is not None:
            location = self._geometry.representative_point() if self._administrative else self._geometry
            self.x = float(location.x)
            self.y = float(location.y)
        if self.surface is None and self._administrative and self._geometry is not None:
            self.surface = self._geometry.wkt
        return float(self.x), float(self.y), self.surface


@dataclass
class PreparedPOISource:
    path: Path
    category: int
    administrative: bool
    frame: object
    modified: bool
    name_field: str | None
    count: int


class PreparedPOICollection(list):
    """List of PreparedPOI records carrying validated source GeoDataFrames for bulk import."""

    def __init__(self, records=(), sources: list[PreparedPOISource] | None = None):
        super().__init__(records)
        self.sources: list[PreparedPOISource] = list(sources or [])


def _repair_mojibake(value):
    """Repair only reversible mojibake; retain legitimate Unicode characters."""
    text = str(value)
    for _ in range(2):
        score = sum(text.count(marker) for marker in ('Ã', 'Â', 'â€', 'ðŸ'))
        if not score:
            break
        for encoding in ('cp1252', 'latin1'):
            try:
                candidate = text.encode(encoding).decode('utf-8')
            except (UnicodeError, LookupError):
                continue
            if sum(candidate.count(marker) for marker in ('Ã', 'Â', 'â€', 'ðŸ')) < score:
                text = candidate
                break
        else:
            break
    return text


def clean_poi_name(value):
    # Repair individual words too: a label can mix correct Unicode and mojibake.
    text = _repair_mojibake(value)
    text = ''.join(_repair_mojibake(part) for part in re.split(r'(\s+)', text))
    text = ''.join(' ' if unicodedata.category(char) == 'Cc' else char for char in text)
    return ' '.join(text.replace('\ufffd', '').replace(';', ',').split())


@dataclass(frozen=True)
class VisumAttrSpec:
    attr_id: str
    value_type: int
    dec_places: int


def iter_poi_geodata_files(visum_directory: Path | str) -> list[tuple[Path, int, bool]]:
    """Collect POI and administrative boundary files (.geojson preferred over .json and .shp per stem)."""
    discovered: list[tuple[Path, int, bool]] = []
    suffix_rank = {ext: idx for idx, ext in enumerate(GEODATA_SUFFIXES)}
    for folder, administrative in (('POI', False), ('Zones', True)):
        folder_path = Path(visum_directory) / 'shapefile' / folder
        if not folder_path.exists():
            continue
        by_stem: dict[str, Path] = {}
        for path in sorted(folder_path.iterdir()):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix not in suffix_rank:
                continue
            stem_key = path.stem.lower()
            existing = by_stem.get(stem_key)
            if existing is None or suffix_rank[suffix] < suffix_rank[existing.suffix.lower()]:
                by_stem[stem_key] = path
        for stem_key in sorted(by_stem):
            path = by_stem[stem_key]
            category_no = category_for_file(path, administrative)
            if category_no is not None:
                discovered.append((path, category_no, administrative))
    return discovered


def _infer_visum_attr_spec(series, attr_id: str | None = None) -> VisumAttrSpec:
    """Infer a Visum attribute specification from a column series and optional target attribute ID."""
    import pandas as pd

    target_id = str(attr_id or '').upper()
    if target_id in INTEGER_POI_ATTRS:
        return VisumAttrSpec(attr_id=target_id, value_type=VISUM_VALUETYPE_INT, dec_places=0)
    if target_id in BOOLEAN_POI_ATTRS:
        return VisumAttrSpec(attr_id=target_id, value_type=VISUM_VALUETYPE_BOOL, dec_places=0)

    dtype = getattr(series, 'dtype', series)
    if pd.api.types.is_bool_dtype(dtype):
        return VisumAttrSpec(attr_id=target_id, value_type=VISUM_VALUETYPE_BOOL, dec_places=0)
    if pd.api.types.is_integer_dtype(dtype):
        try:
            non_null = series.dropna()
            if len(non_null):
                min_v, max_v = int(non_null.min()), int(non_null.max())
                if min_v < _INT32_MIN or max_v > _INT32_MAX:
                    return VisumAttrSpec(attr_id=target_id, value_type=VISUM_VALUETYPE_REAL, dec_places=0)
        except Exception:
            pass
        return VisumAttrSpec(attr_id=target_id, value_type=VISUM_VALUETYPE_INT, dec_places=0)
    if pd.api.types.is_float_dtype(dtype):
        return VisumAttrSpec(attr_id=target_id, value_type=VISUM_VALUETYPE_REAL, dec_places=4)
    if dtype == object and hasattr(series, 'dropna'):
        non_null = series.dropna()
        if len(non_null) and all(isinstance(v, bool) for v in non_null.iloc[:50]):
            return VisumAttrSpec(attr_id=target_id, value_type=VISUM_VALUETYPE_BOOL, dec_places=0)
    return VisumAttrSpec(attr_id=target_id, value_type=VISUM_VALUETYPE_STRING, dec_places=0)


def map_geodata_dtype_to_visum(series, attr_id: str | None = None) -> tuple[int, int]:
    """Map a GeoDataFrame column series/dtype to a Visum (ValueType, dec_places) pair."""
    spec = _infer_visum_attr_spec(series, attr_id=attr_id)
    return spec.value_type, spec.dec_places


def _normalize_geodata_attributes(
    frame,
    *,
    drop_attrs=IGNORED_POI_ATTRS,
    int_attrs=INTEGER_POI_ATTRS,
    bool_attrs=BOOLEAN_POI_ATTRS,
):
    """Drop ignored POI columns, cast integer attrs to Int64, and cast bool attrs to bool."""
    import pandas as pd

    drop_set = {str(a).upper() for a in (drop_attrs or ())}
    int_set = {str(a).upper() for a in (int_attrs or ())}
    bool_set = {str(a).upper() for a in (bool_attrs or ())}

    drop_cols = [
        col for col in frame.columns
        if col != 'geometry' and str(col).upper() in drop_set
    ]
    cast_cols = [
        col for col in frame.columns
        if col != 'geometry' and col not in drop_cols
        and str(col).upper() in int_set
        and not pd.api.types.is_integer_dtype(frame[col].dtype)
    ]
    cast_bool_cols = [
        col for col in frame.columns
        if col != 'geometry' and col not in drop_cols and col not in cast_cols
        and str(col).upper() in bool_set
        and not pd.api.types.is_bool_dtype(frame[col].dtype)
    ]

    if not drop_cols and not cast_cols and not cast_bool_cols:
        return frame, False

    clean = frame.drop(columns=drop_cols).copy() if drop_cols else frame.copy()
    for col in cast_cols:
        clean[col] = pd.to_numeric(clean[col], errors='coerce').round().astype('Int64')
    for col in cast_bool_cols:
        series = clean[col]
        if pd.api.types.is_numeric_dtype(series.dtype):
            clean[col] = series.fillna(0).ne(0).astype(bool)
        else:
            clean[col] = series.map(
                lambda v: False if (pd.isna(v) or str(v).strip().lower() in ('', '0', '0.0', 'false', 'no', 'n')) else bool(v)
            ).astype(bool)
    return clean, True


def _write_rfc7946_geojson(frame, target_path: Path) -> None:
    """Write a GeoDataFrame as RFC 7946 GeoJSON (WGS84, no top-level 'crs' member)."""
    export_frame = frame
    crs = getattr(export_frame, 'crs', None)
    if crs is not None:
        try:
            if crs.to_epsg() != 4326:
                export_frame = export_frame.to_crs('EPSG:4326')
        except Exception:
            export_frame = export_frame.to_crs('EPSG:4326')
    try:
        export_frame.to_file(target_path, driver='GeoJSON', RFC7946='YES')
    except TypeError:
        export_frame.to_file(target_path, driver='GeoJSON')


def _geojson_has_stale_schema(path: Path) -> bool:
    """Return True if an existing GeoJSON has 'crs', ignored attrs ('path'), or float booleans."""
    try:
        head = path.read_text(encoding='utf-8', errors='ignore')[:4096]
    except OSError:
        return True
    if '"crs"' in head:
        return True
    head_lower = head.lower()
    for ignored in IGNORED_POI_ATTRS:
        if f'"{ignored.lower()}"' in head_lower:
            return True
    for bool_attr in BOOLEAN_POI_ATTRS:
        attr_lower = bool_attr.lower()
        if f'"{attr_lower}": 0.0' in head_lower or f'"{attr_lower}": 1.0' in head_lower:
            return True
    return False


def ensure_poi_geojson_files(visum_directory: Path | str, log=logging.info) -> int:
    """Ensure untruncated RFC 7946 GeoJSON exports exist for Model 1 ADM and Model 5 POI layers."""
    import geopandas as gpd

    visum_dir = Path(visum_directory).resolve()
    if visum_dir.name.lower() != 'visum' or visum_dir.parent.name.lower() != 'processed':
        return 0
    project_root = visum_dir.parent.parent
    qgis_out = project_root / 'processed' / 'qgis_output'
    if not qgis_out.is_dir():
        return 0

    candidates: list[tuple[Path, Path]] = []
    model1_dir = qgis_out / 'model1_DataPrep'
    zones_dir = visum_dir / 'shapefile' / 'Zones'
    if model1_dir.is_dir():
        for level in range(4):
            src = model1_dir / f'adm{level}.gpkg'
            if src.is_file():
                candidates.append((src, zones_dir / f'adm{level}.geojson'))

    model5_dir = qgis_out / 'model5_UrbanCentrality'
    poi_dir = visum_dir / 'shapefile' / 'POI'
    if model5_dir.is_dir():
        for src in sorted(model5_dir.glob('sector_*_points.gpkg')):
            if category_for_file(src, administrative=False) is None:
                continue
            candidates.append((src, poi_dir / f'{src.stem}.geojson'))

    written = 0
    for src, dst in candidates:
        needs_write = (
            not dst.is_file()
            or src.stat().st_mtime > dst.stat().st_mtime
            or _geojson_has_stale_schema(dst)
        )
        if not needs_write:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        frame = gpd.read_file(src)
        frame, _ = _normalize_geodata_attributes(frame)
        tmp_dst = dst.with_name(f'{dst.stem}.tmp.geojson')
        try:
            _write_rfc7946_geojson(frame, tmp_dst)
            tmp_dst.replace(dst)
        except Exception:
            if tmp_dst.exists():
                tmp_dst.unlink(missing_ok=True)
            raise
        written += 1
        log(f'GeoJSON für Visum-POI-Import aktualisiert: {dst.name}')
    return written


def _sanitize_visum_attr_id(column_name: str, used_ids: set[str]) -> str:
    """Return a unique valid Visum attribute ID for a shapefile/GeoJSON column."""
    raw = str(column_name).upper()
    cleaned = re.sub(r'[^A-Z0-9_]', '_', raw)
    if not cleaned or not re.search(r'[A-Z0-9]', cleaned):
        cleaned = 'ATTR'
    elif cleaned[0].isdigit():
        cleaned = f'A_{cleaned}'
    candidate = cleaned
    suffix = 1
    while candidate in used_ids:
        candidate = f'{cleaned}_{suffix}'
        suffix += 1
    return candidate


def build_attribute_mapping(frame) -> tuple[dict[str, str], list[tuple[str, str, int, int]]]:
    """Build source->Visum attribute mapping and list of (attr_id, source_col, value_type, dec_places)."""
    attr_map: dict[str, str] = {}
    uda_defs: list[tuple[str, str, int, int]] = []
    used_ids: set[str] = set()
    for col in frame.columns:
        if col == 'geometry':
            continue
        col_str = str(col)
        upper = col_str.upper()
        normalized_upper = SHP_TRUNCATED_ATTR_MAP.get(upper, upper)
        if upper in IGNORED_POI_ATTRS or normalized_upper in IGNORED_POI_ATTRS:
            continue
        if normalized_upper in STANDARD_POI_ATTRS and normalized_upper not in used_ids:
            attr_id = normalized_upper
            used_ids.add(attr_id)
            attr_map[col_str] = attr_id
            continue
        attr_id = _sanitize_visum_attr_id(normalized_upper, used_ids | set(STANDARD_POI_ATTRS))
        used_ids.add(attr_id)
        attr_map[col_str] = attr_id
        uda_label = normalized_upper.lower() if normalized_upper != upper else col_str
        vt, dec_places = map_geodata_dtype_to_visum(frame[col], attr_id=attr_id)
        uda_defs.append((attr_id, uda_label, vt, dec_places))
    return attr_map, uda_defs


def _ensure_poi_udas(pois, uda_defs: list[tuple[str, str, int, int]]) -> None:
    """Create missing UDAs, recreate UDAs with mismatched ValueTypes, and drop obsolete UDAs."""
    for obsolete_id in sorted(OBSOLETE_POI_UDAS):
        try:
            if pois.AttrExists(obsolete_id) is True:
                pois.DeleteUserDefinedAttribute(obsolete_id)
        except Exception:
            pass

    for attr_id, col_name, vt, dec_places in uda_defs:
        exists = pois.AttrExists(attr_id)
        if exists is True:
            try:
                attr_obj = pois.Attributes.ItemByKey(attr_id)
                existing_vt = getattr(attr_obj, 'ValueType', None)
                if isinstance(existing_vt, int) and existing_vt != vt:
                    pois.DeleteUserDefinedAttribute(attr_id)
                    exists = False
            except Exception:
                pass
        if not exists or exists is False:
            pois.AddUserDefinedAttribute(attr_id, col_name, col_name, vt, dec_places)


def ensure_poi_attributes_for_frame(category, frame) -> dict[str, str]:
    """Create missing User-Defined Attributes on a Visum POI category matching geodata column types."""
    attr_map, uda_defs = build_attribute_mapping(frame)
    _ensure_poi_udas(category.POIs, uda_defs)
    return attr_map


def _to_2d_geometry(geometry):
    """Strip Z coordinates using Shapely 2's force_2d when available."""
    try:
        from shapely import force_2d

        return force_2d(geometry)
    except ImportError:
        from shapely.ops import transform

        return transform(lambda x, y, z=None: (x, y), geometry)


def _validate_single_geometry(geometry, administrative: bool, context: str, corrections: list[str]):
    """Validate and repair a single geometry; returns cleaned 2D geometry or None to skip."""
    if geometry is None or (isinstance(geometry, float) and math.isnan(geometry)) or geometry.is_empty:
        corrections.append(f'{context}: Ohne Geometrie übersprungen.')
        return None, True

    changed = False
    if administrative:
        if geometry.geom_type not in ('Polygon', 'MultiPolygon'):
            raise ValueError('Polygon oder MultiPolygon erwartet.')
        if not geometry.is_valid:
            from shapely.validation import make_valid

            geometry = make_valid(geometry)
            if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in ('Polygon', 'MultiPolygon'):
                raise ValueError('Fläche konnte nicht sicher repariert werden.')
            corrections.append(f'{context}: Ungültige Fläche repariert.')
            changed = True
    elif geometry.geom_type != 'Point':
        raise ValueError('Punktgeometrie erwartet.')

    if geometry.has_z:
        geometry = _to_2d_geometry(geometry)
        corrections.append(f'{context}: Höhenkoordinate entfernt (2D-Import).')
        changed = True

    if administrative:
        bounds = geometry.bounds
        if not bounds or len(bounds) != 4 or not all(math.isfinite(val) for val in bounds):
            raise ValueError('Koordinaten sind nicht endlich.')
    else:
        x, y = float(geometry.x), float(geometry.y)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError('Koordinaten sind nicht endlich.')

    if not geometry.is_valid:
        raise ValueError('Ungültige Geometrie.')

    return geometry, changed


def _validate_geodataframe_fast(path: Path, category_no: int, administrative: bool,
                                frame, target_crs, corrections: list[str], errors: list[str], log):
    """Validate and normalize a real GeoDataFrame using vectorized checks where possible."""
    import pandas as pd

    orig_crs = frame.crs
    is_geojson = path.suffix.lower() in ('.geojson', '.json')
    try:
        is_wgs84 = orig_crs is not None and orig_crs.to_epsg() == 4326
    except Exception:
        is_wgs84 = False

    if is_geojson and is_wgs84:
        reprojected = frame
        crs_changed = False
    else:
        reprojected = frame.to_crs(target_crs)
        crs_changed = orig_crs != target_crs

    fields = {str(key).lower(): key for key in reprojected.columns}
    name_field = next((fields[key] for key in (f'name_{category_no - 10}', 'name') if key in fields), None)

    geoms = reprojected.geometry
    total = len(reprojected)

    # Vectorized geometry pre-checks so valid 40k+ polygon layers avoid slow row-by-row overhead.
    null_mask = geoms.isna().to_numpy()
    empty_mask = geoms.is_empty.to_numpy()
    if administrative:
        type_bad_mask = (~geoms.geom_type.isin(('Polygon', 'MultiPolygon'))).to_numpy()
    else:
        type_bad_mask = (geoms.geom_type != 'Point').to_numpy()
    valid_mask = geoms.is_valid.to_numpy()
    has_z_mask = geoms.has_z.to_numpy()

    import numpy as np

    bounds_arr = geoms.bounds.to_numpy(dtype='float64', na_value=float('nan'))
    nonfinite_mask = ~np.isfinite(bounds_arr).all(axis=1)

    needs_geom_work = bool((null_mask | empty_mask | type_bad_mask | (~valid_mask) | has_z_mask | nonfinite_mask).any())

    clean_frame = reprojected.copy() if (crs_changed or needs_geom_work) else None
    keep_mask = np.ones(total, dtype=bool)
    cleaned_geoms = list(geoms) if needs_geom_work else None
    geom_modified = crs_changed

    if needs_geom_work:
        indices = list(reprojected.index)
        for pos in range(total):
            is_problem = bool(
                null_mask[pos] or empty_mask[pos] or type_bad_mask[pos]
                or (not valid_mask[pos]) or has_z_mask[pos] or nonfinite_mask[pos]
            )
            if not is_problem:
                continue
            idx = indices[pos]
            context = f'{path.name}, Datensatz {pos + 1} (Index {idx}), Kategorie {category_no}'
            try:
                fixed_geom, changed = _validate_single_geometry(cleaned_geoms[pos], administrative, context, corrections)
                if fixed_geom is None:
                    keep_mask[pos] = False
                    geom_modified = True
                elif changed:
                    cleaned_geoms[pos] = fixed_geom
                    geom_modified = True
            except Exception as exc:
                errors.append(f'{context}: {exc}')
                keep_mask[pos] = False

    # Validate / clean names (copied on mutation so original caller DataFrames remain untouched).
    name_values = None
    name_modified = False
    if name_field is not None:
        raw_names = reprojected[name_field].tolist()
        name_values = list(raw_names)
        indices = list(reprojected.index)
        for pos, (idx, raw_val) in enumerate(zip(indices, raw_names)):
            if not keep_mask[pos] or not pd.notna(raw_val):
                continue
            original = str(raw_val)
            cleaned = clean_poi_name(original)
            if cleaned != original:
                context = f'{path.name}, Datensatz {pos + 1} (Index {idx}), Kategorie {category_no}'
                corrections.append(f'{context}: Name {original!r} -> {cleaned!r}')
                name_values[pos] = cleaned
                name_modified = True

    any_modified = geom_modified or name_modified
    if clean_frame is None:
        clean_frame = reprojected.copy() if any_modified else reprojected

    if geom_modified and cleaned_geoms is not None:
        clean_frame = clean_frame.set_geometry(cleaned_geoms, crs=reprojected.crs)
        if not keep_mask.all():
            clean_frame = clean_frame.loc[keep_mask].copy()
            if name_values is not None:
                name_values = [val for val, keep in zip(name_values, keep_mask) if keep]
    elif name_modified and not keep_mask.all():
        clean_frame = clean_frame.loc[keep_mask].copy()
        if name_values is not None:
            name_values = [val for val, keep in zip(name_values, keep_mask) if keep]

    if name_modified and name_field is not None and name_values is not None:
        clean_frame[name_field] = name_values

    clean_frame, attrs_modified = _normalize_geodata_attributes(clean_frame)
    any_modified = any_modified or attrs_modified

    # Build PreparedPOI records without eagerly computing .representative_point()/.wkt for valid polygons.
    final_geoms = clean_frame.geometry.tolist()
    final_names = (clean_frame[name_field].tolist() if name_field is not None else [None] * len(clean_frame))
    kept_positions = [i + 1 for i, keep in enumerate(keep_mask) if keep]
    kept_indices = [idx for idx, keep in zip(reprojected.index, keep_mask) if keep]

    records: list[PreparedPOI] = []
    for pos, idx, geom, raw_name in zip(kept_positions, kept_indices, final_geoms, final_names):
        context = f'{path.name}, Datensatz {pos} (Index {idx}), Kategorie {category_no}'
        poi_name = str(raw_name) if (name_field is not None and pd.notna(raw_name)) else None
        if administrative:
            records.append(PreparedPOI(path.name, context, category_no, None, None, poi_name, None, geom, True))
        else:
            records.append(PreparedPOI(path.name, context, category_no, float(geom.x), float(geom.y), poi_name, None))

    source_entry = PreparedPOISource(
        path=path,
        category=category_no,
        administrative=administrative,
        frame=clean_frame,
        modified=any_modified,
        name_field=name_field,
        count=len(clean_frame),
    )
    return records, source_entry


def prepare_project_pois(visum, visum_directory, log=logging.info):
    """Validate every source before any Visum object is created; keep sources intact."""
    import geopandas as gpd
    import pandas as pd
    from pyproj import CRS

    log('Prüfe alle POIs und Verwaltungsflächen vor dem Import...')
    ensure_poi_geojson_files(visum_directory, log=log)
    target_crs = CRS.from_wkt(str(visum.Net.AttValue('PROJECTIONDEFINITION')))
    prepared: list[PreparedPOI] = []
    source_entries: list[PreparedPOISource] = []
    errors: list[str] = []
    corrections: list[str] = []
    sources = 0

    for path, category_no, administrative in iter_poi_geodata_files(visum_directory):
        sources += 1
        log(f'{path.name}: Lade und prüfe Kategorie {category_no}...')
        try:
            frame = gpd.read_file(path)
            if frame.crs is None:
                raise ValueError('Koordinatensystem fehlt; eine sichere automatische Zuordnung ist nicht möglich.')
            gpd_cls = getattr(gpd, 'GeoDataFrame', None)
            if gpd_cls is not None and isinstance(frame, gpd_cls):
                file_records, source_entry = _validate_geodataframe_fast(
                    path, category_no, administrative, frame, target_crs, corrections, errors, log
                )
                prepared.extend(file_records)
                source_entries.append(source_entry)
                log(f'{path.name}: {len(frame)} Datensätze geprüft.')
                continue
            frame = frame.to_crs(target_crs)
        except Exception as exc:
            errors.append(f'{path.name}, Kategorie {category_no}: {exc}')
            continue

        # Mocked/non-GeoDataFrame fallback used in lightweight unit tests.
        fields = {str(key).lower(): key for key in frame.columns}
        name_field = next((fields[key] for key in (f'name_{category_no - 10}', 'name') if key in fields), None)
        file_count = 0
        for position, (index, row) in enumerate(frame.iterrows(), 1):
            context = f'{path.name}, Datensatz {position} (Index {index}), Kategorie {category_no}'
            try:
                geometry = row.geometry
                if geometry is None or (isinstance(geometry, float) and math.isnan(geometry)) or geometry.is_empty:
                    corrections.append(f'{context}: Ohne Geometrie übersprungen.')
                    continue
                if administrative:
                    if geometry.geom_type not in ('Polygon', 'MultiPolygon'):
                        raise ValueError('Polygon oder MultiPolygon erwartet.')
                    if not geometry.is_valid:
                        from shapely.validation import make_valid

                        geometry = make_valid(geometry)
                        if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in ('Polygon', 'MultiPolygon'):
                            raise ValueError('Fläche konnte nicht sicher repariert werden.')
                        corrections.append(f'{context}: Ungültige Fläche repariert.')
                elif geometry.geom_type != 'Point':
                    raise ValueError('Punktgeometrie erwartet.')
                if geometry.has_z:
                    geometry = _to_2d_geometry(geometry)
                    corrections.append(f'{context}: Höhenkoordinate entfernt (2D-Import).')
                location = geometry.representative_point() if administrative else geometry
                x, y = float(location.x), float(location.y)
                if not math.isfinite(x) or not math.isfinite(y):
                    raise ValueError('Koordinaten sind nicht endlich.')
                if not geometry.is_valid:
                    raise ValueError('Ungültige Geometrie.')
                name = None
                if name_field is not None and pd.notna(row[name_field]):
                    original = str(row[name_field])
                    name = clean_poi_name(original)
                    if name != original:
                        corrections.append(f'{context}: Name {original!r} -> {name!r}')
                prepared.append(PreparedPOI(path.name, context, category_no, x, y, name,
                                           geometry.wkt if administrative else None))
                file_count += 1
            except Exception as exc:
                errors.append(f'{context}: {exc}')
            if position % 1000 == 0:
                log(f'{path.name}: {position} von {len(frame)} Datensätzen geprüft...')
        source_entries.append(
            PreparedPOISource(path=path, category=category_no, administrative=administrative,
                              frame=frame, modified=False, name_field=name_field, count=file_count)
        )

    if not sources:
        errors.append('Keine passenden POI- oder Verwaltungsflächen-Shapefiles gefunden.')
    elif not prepared:
        errors.append('Keine importierbaren POIs oder Verwaltungsflächen vorhanden.')

    report = Path(visum_directory) / 'poi_validation_report.json'
    report.write_text(
        json.dumps({'errors': errors, 'corrections': corrections,
                    'ready_to_import': len(prepared)}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    log(f'POI-Vorprüfung: {len(prepared)} importierbar, {len(corrections)} Korrekturen, {len(errors)} Fehler. Bericht: {report}')
    if errors:
        raise POIValidationError(
            'POI-Vorprüfung fehlgeschlagen. Es wurden keine POIs importiert.\n'
            + '\n'.join(errors[:20]) + f'\nVollständiger Prüfbericht: {report}'
        )
    return PreparedPOICollection(prepared, sources=source_entries)


def _should_use_native_import(visum, prepared) -> bool:
    """Use native Visum bulk geodata import on COM objects (and opted-in mocks)."""
    sources = getattr(prepared, 'sources', None)
    if not sources:
        return False
    if isinstance(visum, NonCallableMock):
        return visum.__dict__.get('_use_native_shapefile_import') is True
    return hasattr(visum, 'IO') and hasattr(visum.IO, 'ImportShapefile')


def _geojson_needs_rfc7946_rewrite(path: Path, frame) -> bool:
    """Visum requires RFC 7946 GeoJSON (WGS 84 EPSG:4326 without a top-level 'crs' member)."""
    try:
        if frame.crs is not None and frame.crs.to_epsg() != 4326:
            return True
    except Exception:
        return True
    try:
        head = path.read_text(encoding='utf-8', errors='ignore')[:1024]
        if '"crs"' in head:
            return True
    except OSError:
        return True
    return False


def _configure_geodata_import_para(para, source: PreparedPOISource, offset: int,
                                   attr_map: dict[str, str], *, set_read_additive: bool = False) -> None:
    """Apply Visum shapefile/GeoJSON import parameters and explicit column-to-attribute mappings."""
    para.ObjectType = SHAPEFILE_OBJ_TYPE_POIS_POLYGON if source.administrative else SHAPEFILE_OBJ_TYPE_POI_CENTROIDS
    para.SetAttValue('POIKey', source.category)
    if set_read_additive:
        para.SetAttValue('ReadAdditive', True)
    if offset > 0:
        para.SetAttValue('Offset', offset)
    para.CreateUserDefinedAttributes = True
    para.SetAttributeAllocationsByIDs = True
    for col_name, attr_id in attr_map.items():
        para.AddAttributeAllocation(col_name, attr_id)


def _import_source_native(visum, category, source: PreparedPOISource, temp_dir: Path, log) -> int:
    """Import an entire shapefile or GeoJSON file natively via Visum COM with UDA type creation."""
    attr_map = ensure_poi_attributes_for_frame(category, source.frame)

    existing = category.POIs.GetMultiAttValues('NO', OnlyActive=False)
    offset = max((int(row[1]) for row in existing), default=0)

    suffix = source.path.suffix.lower()
    is_geojson = suffix in ('.geojson', '.json')

    if is_geojson:
        need_rewrite = source.modified or _geojson_needs_rfc7946_rewrite(source.path, source.frame)
        if need_rewrite:
            import_path = temp_dir / f'{source.path.stem}_clean.geojson'
            _write_rfc7946_geojson(source.frame, import_path)
        else:
            import_path = source.path

        para = visum.IO.CreateImportGeoJSONPara()
        _configure_geodata_import_para(para, source, offset, attr_map, set_read_additive=False)
        visum.IO.ImportGeoJSON(str(import_path), para)
    else:
        if source.modified or not source.path.exists():
            import_path = temp_dir / f'{source.path.stem}_clean.shp'
            source.frame.to_file(import_path, encoding='utf-8')
        else:
            import_path = source.path

        para = visum.IO.CreateImportShapeFilePara()
        _configure_geodata_import_para(para, source, offset, attr_map, set_read_additive=True)
        visum.IO.ImportShapefile(str(import_path), para)

    # Mirror NAME_0..NAME_3 into Visum's standard NAME attribute when no separate NAME column existed.
    if source.name_field:
        mapped_name_attr = attr_map.get(source.name_field, source.name_field.upper())
        if mapped_name_attr != 'NAME' and 'NAME' not in attr_map.values():
            values = category.POIs.GetMultiAttValues(mapped_name_attr, OnlyActive=False)
            if offset > 0:
                values = [(no, val) for no, val in values if int(no) > offset]
            if values:
                category.POIs.SetMultiAttValues('NAME', values, False)

    log(f'POI-Import: {source.count} Datensätze aus {source.path.name} in Kategorie {source.category} importiert.')
    return source.count


def _import_records_rowwise(visum, prepared, log) -> int:
    """Fallback record-by-record import used when native COM geodata import is unavailable (e.g. mocks)."""
    numbers, categories = {}, {}
    count = 0
    for record in prepared:
        if record.category not in categories:
            category = visum.Net.POICategories.ItemByKey(record.category)
            categories[record.category] = category
            existing = category.POIs.GetMultiAttValues('NO', OnlyActive=False)
            numbers[record.category] = max((int(row[1]) for row in existing), default=0) + 1
        number = numbers[record.category]
        x, y, surface = record.resolve_coords_and_surface()
        try:
            poi = visum.Net.AddPOI(number, categories[record.category], x, y)
            if record.name is not None:
                poi.SetAttValue('NAME', record.name)
            if surface is not None:
                poi.SetAttValue('WKTSURFACE', surface)
        except Exception as exc:
            raise RuntimeError(f'{record.record}, POI-Nr. {number}: Visum-Import fehlgeschlagen: {exc}') from exc
        numbers[record.category] += 1
        count += 1
        if count % 1000 == 0 or count == len(prepared):
            log(f'POI-Import: {count} von {len(prepared)} Datensätzen importiert ({record.source}).')
    return count


def import_project_pois(visum, visum_directory, log=logging.info):
    prepared = prepare_project_pois(visum, visum_directory, log)
    log('Starte POI-Import und Import der Verwaltungsflächen...')
    ensure_import_categories(visum)

    if _should_use_native_import(visum, prepared):
        count = 0
        with tempfile.TemporaryDirectory(prefix='pando_poi_import_') as tmp_dir_str:
            temp_dir = Path(tmp_dir_str)
            categories = {}
            for source in prepared.sources:
                if source.count == 0:
                    continue
                if source.category not in categories:
                    categories[source.category] = visum.Net.POICategories.ItemByKey(source.category)
                try:
                    count += _import_source_native(visum, categories[source.category], source, temp_dir, log)
                except Exception as exc:
                    raise RuntimeError(
                        f'{source.path.name}, Kategorie {source.category}: Visum-Geodaten-Import fehlgeschlagen: {exc}'
                    ) from exc
    else:
        count = _import_records_rowwise(visum, prepared, log)

    log(f'POI-Import abgeschlossen: {count} POIs und Verwaltungsflächen importiert.')
    return count
