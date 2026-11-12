"""Import project POI points and administrative surfaces into existing categories."""
from pathlib import Path
import logging
import re
import json
import math
import unicodedata
from dataclasses import dataclass


SECTOR_CATEGORIES = {'healthcare': 21, 'leisure': 22, 'retail': 23,
                     'education': 24, 'government': 25}


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
    x: float
    y: float
    name: str | None
    surface: str | None


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


def prepare_project_pois(visum, visum_directory, log=logging.info):
    """Validate every source before any Visum object is created; keep sources intact."""
    import geopandas as gpd
    import pandas as pd
    from pyproj import CRS
    log('Prüfe alle POIs und Verwaltungsflächen vor dem Import...')
    target_crs = CRS.from_wkt(str(visum.Net.AttValue('PROJECTIONDEFINITION')))
    prepared, errors, corrections = [], [], []
    sources = 0
    for folder, administrative in (('POI', False), ('Zones', True)):
        for path in sorted((Path(visum_directory) / 'shapefile' / folder).glob('*.shp')):
            category_no = category_for_file(path, administrative)
            if category_no is None:
                continue
            sources += 1
            log(f'{path.name}: Lade und prüfe Kategorie {category_no}...')
            try:
                frame = gpd.read_file(path)
                if frame.crs is None:
                    raise ValueError('Koordinatensystem fehlt; eine sichere automatische Zuordnung ist nicht möglich.')
                frame = frame.to_crs(target_crs)
            except Exception as exc:
                errors.append(f'{path.name}, Kategorie {category_no}: {exc}')
                continue
            fields = {str(key).lower(): key for key in frame.columns}
            name_field = next((fields[key] for key in
                               (f'name_{category_no - 10}', 'name') if key in fields), None)
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
                        from shapely.ops import transform
                        geometry = transform(lambda x, y, z=None: (x, y), geometry)
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
                except Exception as exc:
                    errors.append(f'{context}: {exc}')
                if position % 1000 == 0:
                    log(f'{path.name}: {position} von {len(frame)} Datensätzen geprüft...')
    if not sources:
        errors.append('Keine passenden POI- oder Verwaltungsflächen-Shapefiles gefunden.')
    elif not prepared:
        errors.append('Keine importierbaren POIs oder Verwaltungsflächen vorhanden.')
    report = Path(visum_directory) / 'poi_validation_report.json'
    report.write_text(json.dumps({'errors': errors, 'corrections': corrections,
                                 'ready_to_import': len(prepared)}, ensure_ascii=False, indent=2), encoding='utf-8')
    log(f'POI-Vorprüfung: {len(prepared)} importierbar, {len(corrections)} Korrekturen, {len(errors)} Fehler. Bericht: {report}')
    if errors:
        raise POIValidationError('POI-Vorprüfung fehlgeschlagen. Es wurden keine POIs importiert.\n'
                                 + '\n'.join(errors[:20]) + f'\nVollständiger Prüfbericht: {report}')
    return prepared


def import_project_pois(visum, visum_directory, log=logging.info):
    prepared = prepare_project_pois(visum, visum_directory, log)
    log('Starte POI-Import und Import der Verwaltungsflächen...')
    ensure_import_categories(visum)
    numbers, categories = {}, {}
    count = 0
    for record in prepared:
        if record.category not in categories:
            category = visum.Net.POICategories.ItemByKey(record.category)
            categories[record.category] = category
            existing = category.POIs.GetMultiAttValues('NO', OnlyActive=False)
            numbers[record.category] = max((int(row[1]) for row in existing), default=0) + 1
        number = numbers[record.category]
        try:
            poi = visum.Net.AddPOI(number, categories[record.category], record.x, record.y)
            if record.name is not None:
                poi.SetAttValue('NAME', record.name)
            if record.surface is not None:
                poi.SetAttValue('WKTSURFACE', record.surface)
        except Exception as exc:
            raise RuntimeError(f'{record.record}, POI-Nr. {number}: Visum-Import fehlgeschlagen: {exc}') from exc
        numbers[record.category] += 1
        count += 1
        if count % 1000 == 0 or count == len(prepared):
            log(f'POI-Import: {count} von {len(prepared)} Datensätzen importiert ({record.source}).')
    log(f'POI-Import abgeschlossen: {count} POIs und Verwaltungsflächen importiert.')
    return count
