"""Durable, atomic GeoPackage edits and session-scoped undo/redo.

The coordinator is an on-disk SQLite database. Its rollback journal coordinates
attached GeoPackages, so the edit and its dependency revisions commit together.
No QGIS objects are retained by the history.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import struct


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def geometry_bounds(blob):
    if not blob:
        return None
    flags = blob[3]
    if flags & 16:
        return None
    endian = '<' if flags & 1 else '>'
    envelope = (flags >> 1) & 7
    if envelope:
        return struct.unpack(endian + 'dddd', blob[8:40])
    # A Point can legally omit the envelope.
    order = '<' if blob[8] else '>'
    kind = struct.unpack(order + 'I', blob[9:13])[0]
    if kind % 1000 == 1:
        x, y = struct.unpack(order + 'dd', blob[13:29])
        return x, x, y, y
    # Some writers omit envelopes for every geometry.
    from qgis.core import QgsGeometry
    geom = QgsGeometry()
    geom.fromWkb(bytes(blob[8:]))
    if geom.isEmpty():
        return None
    box = geom.boundingBox()
    return box.xMinimum(), box.xMaximum(), box.yMinimum(), box.yMaximum()


def point_blob(x, y, srs_id):
    return b'GP\x00\x01' + struct.pack('<iBIdd', srs_id, 1, 1, x, y)


def centrality_level(value):
    """Model 6 stores TypeNo as text; other models use integer fields."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 9


class _Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path):
    connection = sqlite3.connect(str(path), timeout=2, factory=_Connection)
    connection.row_factory = sqlite3.Row
    connection.create_function('ST_IsEmpty', 1, lambda b: int(geometry_bounds(b) is None))
    for name, index in (('ST_MinX', 0), ('ST_MaxX', 1), ('ST_MinY', 2), ('ST_MaxY', 3)):
        connection.create_function(name, 1, lambda b, i=index: geometry_bounds(b)[i] if geometry_bounds(b) else None)
    return connection


@dataclass(frozen=True)
class Table:
    path: str
    name: str
    key: str
    geometry: str
    srs_id: int
    columns: tuple[str, ...]

    @classmethod
    def open(cls, path, table=None):
        path = str(Path(path).resolve())
        if not Path(path).is_file():
            raise ValueError(f'Missing GeoPackage: {path}')
        with connect(path) as db:
            rows = db.execute('SELECT table_name,column_name,srs_id FROM gpkg_geometry_columns').fetchall()
            rows = [r for r in rows if table is None or r[0] == table]
            if len(rows) != 1:
                raise ValueError(f'Ambiguous GeoPackage layer: {path}')
            name, geom, srs = rows[0]
            info = db.execute(f'PRAGMA table_info({quote(name)})').fetchall()
            keys = [r['name'] for r in info if r['pk']]
            if len(keys) != 1:
                raise ValueError(f'A stable primary key is required: {path}')
            return cls(path, name, keys[0], geom, srs, tuple(r['name'] for r in info))

    def rows(self):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute(f'SELECT * FROM {quote(self.name)} ORDER BY {quote(self.key)}')]

    def field(self, name):
        return next((c for c in self.columns if c.lower() == name.lower()), None)


@dataclass
class Change:
    table: Table
    before: dict | None
    after: dict | None


@dataclass
class Command:
    label: str
    source: str
    kind: str
    changes: list[Change]


# Dependencies deliberately exclude centrality generation and heatmap estimation.
PRODUCTS = {
    'copies': (),
    'assembly': (),
    'exports': ('assembly',),
    'visum_zones': ('exports',),
    'connectors': ('visum_zones',),
    'matrices': ('visum_zones',),
    'analysis': ('matrices', 'connectors'),
    'presentation': ('analysis',),
}


def consumers(source, kind):
    if source == 'nationwide_inner':
        return ()  # These points are currently presentation-only, not Model 6 input.
    if source == 'nationwide_places':
        return ()  # A separate, explicitly editable copy of Model 2 seeds.
    result = ['copies'] if source == 'model2' else []
    if source in ('model2', 'model5'):
        result.append('assembly')
    result.extend(('exports', 'visum_zones'))
    # Connector rules also read TYPENO (number/range/type of connectors).
    result.append('connectors')
    result.extend(('matrices', 'analysis', 'presentation'))
    return tuple(result)


def digest_rows(rows):
    def value(v):
        return {'bytes': v.hex()} if isinstance(v, bytes) else v
    return hashlib.sha256(json.dumps(rows, default=value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class EditSession:
    def __init__(self, project_path):
        self.root = Path(project_path).resolve()
        self.path = self.root / 'centrality_edits.sqlite'
        self.commands = []
        self.position = 0
        self.busy = False
        self.last_preview = None

    def _db(self):
        db = connect(self.path)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.commit()
        return db

    def state(self):
        if not self.path.exists():
            return {'sources': {}, 'products': {}}
        with self._db() as db:
            row = db.execute("SELECT value FROM state WHERE key='revisions'").fetchone()
        return json.loads(row[0]) if row else {'sources': {}, 'products': {}}

    def _write_state(self, db, state):
        db.execute("INSERT OR REPLACE INTO state VALUES ('revisions',?)", (json.dumps(state),))

    def _apply(self, command, reverse=False, record_revision=True):
        if self.busy and record_revision:
            raise RuntimeError('An update is running')
        changes = [Change(c.table, c.after, c.before) for c in reversed(command.changes)] if reverse else command.changes
        paths = list(dict.fromkeys(c.table.path for c in changes))
        if any(not Path(p).is_relative_to(self.root) or not Path(p).is_file() for p in paths):
            raise ValueError('Edit source is missing or outside the project')
        db = self._db()
        try:
            for i, path in enumerate(paths):
                db.execute(f'ATTACH DATABASE ? AS d{i}', (path,))
                mode = db.execute(f'PRAGMA d{i}.journal_mode').fetchone()[0]
                if mode.lower() != 'delete':
                    raise RuntimeError(f'Close external editors before editing {path} (journal mode {mode})')
                db.execute(f'PRAGMA d{i}.synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            saved = db.execute("SELECT value FROM state WHERE key='revisions'").fetchone()
            state = json.loads(saved[0]) if saved else {'sources': {}, 'products': {}}
            source = state['sources'].get(command.source)
            baseline = digest_rows([])
            for c in changes:
                schema = f'd{paths.index(c.table.path)}'
                target = f'{schema}.{quote(c.table.name)}'
                key = (c.before if c.before is not None else c.after)[c.table.key]
                row = db.execute(f'SELECT * FROM {target} WHERE {quote(c.table.key)}=?', (key,)).fetchone()
                actual = dict(row) if row else None
                compared = [col for col in c.before if c.before[col] != c.after[col]] if c.before is not None and c.after is not None else None
                conflict = (actual is None or any(actual[col] != c.before[col] for col in compared)) if compared is not None else actual != c.before
                if conflict:
                    raise RuntimeError(f'Externally changed record: {c.table.path}, {key}')
                if c.after is None:
                    db.execute(f'DELETE FROM {target} WHERE {quote(c.table.key)}=?', (key,))
                elif c.before is None:
                    cols = list(c.after)
                    db.execute(f'INSERT INTO {target} ({",".join(map(quote, cols))}) VALUES ({",".join("?" for _ in cols)})', tuple(c.after.values()))
                else:
                    cols = [col for col in c.after if c.after[col] != c.before[col]]
                    if cols:
                        db.execute(f'UPDATE {target} SET {",".join(quote(col)+"=?" for col in cols)} WHERE {quote(c.table.key)}=?', (*[c.after[col] for col in cols], key))
                db.execute(f"UPDATE {schema}.gpkg_contents SET last_change=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE table_name=?", (c.table.name,))
            for path, name, geometry in {(c.table.path, c.table.name, c.table.geometry) for c in changes}:
                schema = f'd{paths.index(path)}'
                columns = {r['name'] for r in db.execute(f'PRAGMA {schema}.table_info(gpkg_contents)')}
                if 'min_x' in columns:
                    geom = quote(geometry)
                    bounds = db.execute(f'SELECT min(ST_MinX({geom})),min(ST_MinY({geom})),max(ST_MaxX({geom})),max(ST_MaxY({geom})) FROM {schema}.{quote(name)}').fetchone()
                    db.execute(f'UPDATE {schema}.gpkg_contents SET min_x=?,min_y=?,max_x=?,max_y=? WHERE table_name=?', (*bounds, name))
            for c in changes:
                if c.after is not None:
                    schema = f'd{paths.index(c.table.path)}'
                    saved_row = db.execute(f'SELECT * FROM {schema}.{quote(c.table.name)} WHERE {quote(c.table.key)}=?', (c.after[c.table.key],)).fetchone()
                    # Preserve SQLite's actual affinity conversions for undo checks.
                    columns = list(c.after) if c.before is None else [col for col in c.after if c.after[col] != c.before[col]]
                    c.after.update({col: saved_row[col] for col in columns})
            if record_revision:
                signature, tracked = self._source_revision(db, changes, paths, source)
                tables = sorted({(c.table.path, c.table.name) for c in changes})
                state['sources'][command.source] = {
                    'baseline': baseline, 'current': signature,
                    'tables': tables, 'kind': command.kind,
                    'tracked': tracked,
                    'consumers': sorted(set((source or {}).get('consumers', ())) | set(consumers(command.source, command.kind))),
                }
                overrides = state.setdefault('overrides', {})
                for c in changes:
                    row = c.after if c.after is not None else c.before
                    business = c.table.field('ZONENO') or c.table.field('NO') or c.table.key
                    identity = json.dumps((str(Path(c.table.path).relative_to(self.root)), c.table.name, business, row[business]))
                    if c.before and c.after and c.before[business] != c.after[business]:
                        previous = json.dumps((str(Path(c.table.path).relative_to(self.root)), c.table.name, business, c.before[business]))
                        overrides[previous] = {'__deleted__': True}
                    changed = list(row) if c.before is None or c.after is None else [k for k in c.after if c.after[k] != c.before[k]]
                    prior = overrides.get(identity, {})
                    prior.update({k: ({'blob': row[k].hex()} if isinstance(row[k], bytes) else row[k]) for k in changed})
                    prior['__deleted__'] = c.after is None
                    overrides[identity] = prior
                self._write_state(db, state)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        self.mirror_manifest()

    def write_product_changes(self, changes):
        if changes:
            self._apply(Command('derived', '__derived__', 'level', changes), record_revision=False)

    def _source_revision(self, db, changes, paths, source):
        """Hash manual deltas, excluding attributes changed by derived refreshes."""
        tracked = dict((source or {}).get('tracked', {}))
        encode = lambda v: {'blob': v.hex()} if isinstance(v, bytes) else v
        for change in changes:
            row = change.after if change.after is not None else change.before
            table = change.table
            identity = json.dumps((table.path, table.name, table.key, row[table.key]))
            if identity not in tracked:
                tracked[identity] = {'before': None if change.before is None else {}}
            baseline = tracked[identity]['before']
            if baseline is not None and change.before is not None:
                columns = list(change.before) if change.after is None else [col for col in change.before if change.before[col] != change.after[col]]
                for col in columns:
                    baseline.setdefault(col, encode(change.before[col]))
        deltas = []
        for identity, entry in sorted(tracked.items()):
            path, name, key, value = json.loads(identity)
            schema = f'd{paths.index(path)}'
            row = db.execute(f'SELECT * FROM {schema}.{quote(name)} WHERE {quote(key)}=?', (value,)).fetchone()
            original = entry['before']
            if row is None:
                current = None
            elif original is None:
                current = {col: encode(row[col]) for col in row.keys()}
            else:
                current = {col: encode(row[col]) for col in original}
            if current != original:
                deltas.append((identity, current))
        return digest_rows(deltas), tracked

    def execute(self, command):
        if not command.changes:
            return
        self._apply(command)
        self.commands[self.position:] = [command]
        self.position += 1

    def undo(self):
        if self.position:
            self._apply(self.commands[self.position - 1], reverse=True)
            self.position -= 1

    def redo(self):
        if self.position < len(self.commands):
            self._apply(self.commands[self.position])
            self.position += 1

    def revision(self, product, state=None):
        state = state or self.state()
        return {key: s['current'] for key, s in state['sources'].items() if product in s['consumers']}

    def pending(self):
        state = self.state()
        result = []
        for product in PRODUCTS:
            current = self.revision(product, state)
            baseline = {k: state['sources'][k]['baseline'] for k in current}
            used = {**baseline, **state['products'].get(product, {}).get('inputs', {})}
            if current != used:
                result.append(product)
        return result

    def set_ferry_wait(self, minutes):
        with self._db() as db:
            saved = db.execute("SELECT value FROM state WHERE key='revisions'").fetchone()
            state = json.loads(saved[0]) if saved else {'sources': {}, 'products': {}}
            source = state['sources'].setdefault('ferry_wait', {
                'baseline': None, 'current': 20.0, 'consumers': ['analysis', 'presentation']})
            source['current'] = float(minutes)
            self._write_state(db, state)
        self.mirror_manifest()

    def complete(self, product, revision):
        with self._db() as db:
            saved = db.execute("SELECT value FROM state WHERE key='revisions'").fetchone()
            state = json.loads(saved[0]) if saved else {'sources': {}, 'products': {}}
            state['products'][product] = {'inputs': revision}
            self._write_state(db, state)
        self.mirror_manifest()

    def mirror_manifest(self):
        # The SQLite record is authoritative; a failed JSON mirror cannot undo a
        # committed edit. Readers always consult this service, including restart.
        from core.app.app_core.project import load_pipeline_manifest, save_pipeline_manifest
        import logging
        try:
            try:
                manifest = load_pipeline_manifest(str(self.root))
            except FileNotFoundError:
                manifest = {'phases': {}}
            manifest['centrality_edits'] = {**self.state(), 'pending': self.pending()}
            save_pipeline_manifest(str(self.root), manifest)
        except Exception:
            logging.getLogger(__name__).exception('Could not mirror edit revisions to pipeline manifest')


def project_session(owner, project_path):
    """Keep history on the window, never on a disposable step widget."""
    session = getattr(owner, '_centrality_edit_session', None)
    if session is None or session.root != Path(project_path).resolve():
        session = EditSession(project_path)
        if owner is not None:
            owner._centrality_edit_session = session
    return session
