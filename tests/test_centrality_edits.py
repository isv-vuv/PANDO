"""File-backed transactional history and selective dependency coverage."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core.app.app_core.centrality_edits import EditSession, Table, Command, Change, point_blob, connect, consumers


class CentralityEditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.session = EditSession(self.root)
        self.points = self.make_table('points')
        self.zones = self.make_table('zones')

    def tearDown(self):
        self.directory.cleanup()

    def make_table(self, name):
        path = self.root / (name + '.gpkg')
        with connect(path) as db:
            db.executescript('''
                CREATE TABLE gpkg_geometry_columns(table_name TEXT,column_name TEXT,srs_id INTEGER);
                CREATE TABLE gpkg_contents(table_name TEXT,last_change TEXT);
                CREATE TABLE features(fid INTEGER PRIMARY KEY, ZONENO INTEGER, TypeNo INTEGER, XCoord REAL, YCoord REAL, geom BLOB);
                INSERT INTO gpkg_geometry_columns VALUES ('features','geom',3857);
                INSERT INTO gpkg_contents VALUES ('features',NULL);
            ''')
            db.execute('INSERT INTO features VALUES (1,10,2,100,200,?)', (point_blob(100, 200, 3857),))
        return Table.open(path)

    def command(self, level=1):
        changes = []
        for table in (self.points, self.zones):
            before = table.rows()[0]
            changes.append(Change(table, before, {**before, 'TypeNo': level}))
        return Command('level', 'model2', 'level', changes)

    def test_durable_edit_undo_redo_and_restart(self):
        self.session.execute(self.command())
        self.assertEqual(self.points.rows()[0]['TypeNo'], 1)
        self.assertIn('assembly', self.session.pending())
        self.assertIn('connectors', self.session.pending())
        restarted = EditSession(self.root)
        self.assertEqual(restarted.pending(), self.session.pending())
        self.assertEqual(restarted.commands, [])
        self.session.undo()
        self.assertEqual(self.zones.rows()[0]['TypeNo'], 2)
        self.assertEqual(self.session.pending(), [])
        self.session.redo()
        self.assertEqual(self.points.rows()[0]['TypeNo'], 1)
        manifest = json.loads((self.root / 'pipeline_manifest.json').read_text())
        self.assertIn('assembly', manifest['centrality_edits']['pending'])

    def test_atomic_rollback_on_second_file_failure(self):
        command = self.command()
        with connect(self.zones.path) as db:
            db.execute("CREATE TRIGGER fail BEFORE UPDATE ON features BEGIN SELECT RAISE(ABORT,'locked zone'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.session.execute(command)
        self.assertEqual(self.points.rows()[0]['TypeNo'], 2)
        self.assertEqual(self.session.position, 0)
        self.assertEqual(self.session.pending(), [])

    def test_external_edit_blocks_undo_without_losing_history(self):
        self.session.execute(self.command())
        with connect(self.points.path) as db:
            db.execute('UPDATE features SET TypeNo=0')
        with self.assertRaisesRegex(RuntimeError, 'Externally'):
            self.session.undo()
        self.assertEqual(self.session.position, 1)
        self.assertEqual(self.zones.rows()[0]['TypeNo'], 1)

    def test_delete_restore_stable_identity_and_geometry(self):
        row = self.points.rows()[0]
        self.session.execute(Command('delete', 'model6', 'membership', [Change(self.points, row, None)]))
        self.assertEqual(self.points.rows(), [])
        self.session.undo()
        self.assertEqual(self.points.rows(), [row])
        self.session.redo()
        self.assertEqual(self.points.rows(), [])

    def test_new_edit_discards_redo(self):
        self.session.execute(self.command())
        self.session.undo()
        self.session.execute(self.command(level=3))
        self.assertEqual(len(self.session.commands), 1)
        self.assertEqual(self.session.position, 1)

    def test_successful_refresh_then_undo_is_outdated_again(self):
        self.session.execute(self.command())
        for product in self.session.pending():
            self.session.complete(product, self.session.revision(product))
        self.assertEqual(self.session.pending(), [])
        self.session.undo()
        self.assertIn('assembly', self.session.pending())
        self.session.redo()
        self.assertEqual(self.session.pending(), [])

    def test_only_dependent_handlers_run_and_failure_can_resume(self):
        from core.app.app_core.centrality_update import run_update
        self.session.execute(self.command())
        products = self.session.pending()
        ran = []
        def fail():
            raise RuntimeError('export failed')
        handlers = {p: (lambda name=p: ran.append(name)) for p in products}
        handlers['exports'] = fail
        with patch('core.app.app_core.centrality_update.preflight', side_effect=lambda s: s.pending()):
            with self.assertRaisesRegex(RuntimeError, 'export failed'):
                run_update(self.session, handlers=handlers, log=lambda _: None)
            self.assertEqual(ran, ['copies', 'assembly'])
            self.assertFalse(self.session.busy)
            self.assertNotIn('assembly', self.session.pending())
            handlers['exports'] = lambda: ran.append('exports')
            run_update(self.session, handlers=handlers, log=lambda _: None)
        self.assertEqual(ran, products)
        self.assertEqual(self.session.pending(), [])

    def test_no_centrality_generators_or_unrelated_layers_are_consumers(self):
        for source in ('model2', 'model5', 'model6'):
            for kind in ('level', 'position', 'membership'):
                self.assertFalse(set(consumers(source, kind)) & {'model2', 'model5', 'model1', 'heatmap', 'osm'})
        self.assertNotIn('assembly', consumers('model6', 'position'))
        self.assertEqual(consumers('nationwide_inner', 'position'), ())

    def test_preflight_does_not_fall_back_to_full_pipeline(self):
        from core.app.app_core.centrality_update import preflight
        self.session.execute(self.command())
        with self.assertRaisesRegex(ValueError, 'no complete pipeline'):
            preflight(self.session)

    def test_derived_changes_do_not_pollute_manual_revision(self):
        self.session.execute(self.command())
        before = self.points.rows()[0]
        revision = self.session.revision('exports')
        self.session.write_product_changes([Change(self.points, before, {**before, 'XCoord': 999.})])
        self.session.undo()
        self.assertEqual(self.session.pending(), [])
        self.session.redo()
        self.assertEqual(self.session.revision('exports'), revision)

    def test_new_source_undone_does_not_invalidate_successful_results(self):
        self.session.execute(self.command())
        for product in self.session.pending():
            self.session.complete(product, self.session.revision(product))
        row = self.points.rows()[0]
        self.session.execute(Command('level', 'model6', 'level', [Change(self.points, row, {**row, 'TypeNo': 0})]))
        self.session.undo()
        self.assertEqual(self.session.pending(), [])

    def test_visum_refresh_uses_prepared_network_and_selected_scripts(self):
        from unittest.mock import MagicMock
        from core.app.app_core.centrality_update import VisumRefresh
        refresh = VisumRefresh(self.session)
        visum = MagicMock()
        refresh.visum = visum
        scripts = {}
        for product, module, method in (
            ('connectors', '04_generate_connectors', 'run_generate_connectors'),
            ('matrices', '05_generate_direct_line_matrices', 'run_direct_line_matrix_generation'),
            ('analysis', '06_run_procedure_sequence', 'run_procedure_sequence'),
            ('presentation', '07_apply_gpa_parameters', 'apply_gpa_parameters_to_visum'),
        ):
            script = MagicMock()
            getattr(script, method).return_value = visum
            scripts[module] = script
        with patch.object(refresh, 'module', side_effect=lambda name: scripts[name]):
            for product in ('connectors', 'matrices', 'analysis', 'presentation'):
                refresh.run(product, lambda _: None)
        self.assertEqual(visum.SaveVersion.call_count, 4)
        visum.LoadVersion.assert_not_called()
        self.assertFalse(any('01_import' in name or '02_derive' in name for name in scripts))


if __name__ == '__main__':
    unittest.main()
