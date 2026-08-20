from datetime import date
from types import SimpleNamespace
import unittest

from core.app.app_core.project import PbfVerification
from core.app.app_qt.steps.step2_data import (
    PbfSearchWorker,
    Step2DataWidget,
    availability_for_job,
    geojson_bounds,
)


class _ButtonStub:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, enabled):
        self.enabled = enabled


class Step2DataLogicTests(unittest.TestCase):
    def _widget_without_qt_init(self):
        widget = Step2DataWidget.__new__(Step2DataWidget)
        widget.project_path = "/tmp/project"
        widget.workspace_path = "/tmp"
        widget.data_path = "/tmp/data"
        widget.download_jobs = [
            {
                "osm_id": "germany/baden-wuerttemberg",
                "filename": "baden-wuerttemberg-latest.osm.pbf",
            }
        ]
        widget.user_pbf_path = None
        widget.pbf_references = []
        widget.next_button = _ButtonStub()
        widget.localizer = SimpleNamespace(get_string=lambda key, **kw: key)
        return widget

    def test_forced_check_does_not_reuse_cached_missing_result(self):
        widget = self._widget_without_qt_init()
        widget._last_verification = SimpleNamespace(all_found=False)
        widget._last_verified_job_key = widget._job_key()
        starts = []
        widget._start_verify_worker = lambda **kwargs: starts.append(kwargs)

        result = widget.check_pbf_files(show_feedback=True, force=True)

        self.assertFalse(result)
        self.assertIsNone(widget._last_verification)
        self.assertIsNone(widget._last_verified_job_key)
        self.assertEqual(starts, [{"show_feedback": True, "confirm_after": False}])

    def test_cached_result_is_still_reused_for_unchanged_automatic_check(self):
        widget = self._widget_without_qt_init()
        cached = SimpleNamespace(all_found=True)
        widget._last_verification = cached
        widget._last_verified_job_key = widget._job_key()
        starts = []
        widget._start_verify_worker = lambda **kwargs: starts.append(kwargs)

        result = widget.check_pbf_files(show_feedback=False)

        self.assertTrue(result)
        self.assertIs(widget._last_verification, cached)
        self.assertEqual(starts, [])

    def test_cached_successful_verification_emits_confirmed_payload_when_confirm_after_is_true(self):
        widget = self._widget_without_qt_init()
        cached = SimpleNamespace(all_found=True, user_pbf_path="/tmp/test.osm.pbf", found_files=["/tmp/test.osm.pbf"])
        widget._last_verification = cached
        widget._last_verified_job_key = widget._job_key()
        widget._update_selected_availability = lambda: None
        emitted = []
        widget._emit_confirmed_payload = lambda: emitted.append(True)

        result = widget.check_pbf_files(show_feedback=True, confirm_after=True)

        self.assertTrue(result)
        self.assertEqual(emitted, [True])

    def test_verification_result_keeps_the_selection_snapshot_it_checked(self):
        widget = self._widget_without_qt_init()
        checked_key = widget._job_key()
        widget._verification_job_key = checked_key
        widget.download_jobs.append(
            {
                "osm_id": "germany/bayern",
                "filename": "bayern-latest.osm.pbf",
            }
        )
        widget._verify_after_finish = True
        widget._update_selected_availability = lambda: None
        emitted_payloads = []
        widget._emit_confirmed_payload = lambda: emitted_payloads.append(True)
        verification = PbfVerification(
            all_found=True,
            found_files=["/tmp/2026-07-30_baden-wuerttemberg.osm.pbf"],
            found_names=["2026-07-30_baden-wuerttemberg.osm.pbf"],
            missing_basenames=[],
            user_pbf_path="/tmp/2026-07-30_baden-wuerttemberg.osm.pbf",
        )

        widget._handle_verify_results(verification, show_feedback=False)

        self.assertEqual(widget._last_verified_job_key, checked_key)
        self.assertNotEqual(widget._last_verified_job_key, widget._job_key())
        self.assertEqual(emitted_payloads, [])

    def test_changed_selection_restarts_verification_after_worker_cleanup(self):
        widget = self._widget_without_qt_init()
        widget._verify_thread = object()
        widget._verify_worker = object()
        widget._verification_job_key = (("old", "old.osm.pbf"),)
        widget._verify_restart_requested = True
        widget._verify_after_finish = False
        widget._set_busy = lambda _busy: None
        starts = []
        widget._start_verify_worker = lambda **kwargs: starts.append(kwargs)

        widget._clear_verify_worker()

        self.assertEqual(starts, [{"show_feedback": False, "confirm_after": False}])

    def test_region_search_worker_can_be_cancelled_before_navigation(self):
        worker = PbfSearchWorker(location=object(), radius_km=20, geofabrik_index={})

        worker.cancel()

        self.assertTrue(worker._cancel_event.is_set())

    def test_availability_is_reported_per_selected_file(self):
        verification = PbfVerification(
            all_found=False,
            found_files=["/tmp/2026-07-30_bayern.osm.pbf"],
            found_names=["2026-07-30_bayern.osm.pbf"],
            missing_basenames=["hessen"],
            user_pbf_path=None,
        )

        self.assertEqual(
            availability_for_job({"filename": "bayern-latest.osm.pbf"}, verification),
            "offline",
        )
        self.assertEqual(
            availability_for_job({"filename": "hessen-latest.osm.pbf"}, verification),
            "download",
        )
        self.assertEqual(
            availability_for_job({"filename": "hessen-latest.osm.pbf"}, None),
            "checking",
        )

    def test_old_offline_file_is_available_but_update_is_recommended(self):
        verification = PbfVerification(
            all_found=True,
            found_files=["/tmp/2026-06-01_bayern.osm.pbf"],
            found_names=["2026-06-01_bayern.osm.pbf"],
            missing_basenames=[],
            user_pbf_path="/tmp/2026-06-01_bayern.osm.pbf",
        )

        self.assertEqual(
            availability_for_job(
                {"filename": "bayern-latest.osm.pbf"},
                verification,
                now=date(2026, 7, 30),
            ),
            "stale",
        )

    def test_file_up_to_thirty_days_old_remains_current(self):
        verification = PbfVerification(
            all_found=True,
            found_files=["/tmp/2026-06-30_bayern.osm.pbf"],
            found_names=["2026-06-30_bayern.osm.pbf"],
            missing_basenames=[],
            user_pbf_path="/tmp/2026-06-30_bayern.osm.pbf",
        )

        self.assertEqual(
            availability_for_job(
                {"filename": "bayern-latest.osm.pbf"},
                verification,
                now=date(2026, 7, 30),
            ),
            "offline",
        )

    def test_geojson_bounds_support_polygon_and_multipolygon(self):
        polygon = {
            "type": "Polygon",
            "coordinates": [[[7.0, 48.0], [9.0, 48.5], [8.0, 50.0], [7.0, 48.0]]],
        }
        multipolygon = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[5.0, 47.0], [6.0, 47.0], [6.0, 48.0], [5.0, 47.0]]],
                [[[10.0, 51.0], [11.0, 51.0], [11.0, 52.0], [10.0, 51.0]]],
            ],
        }

        self.assertEqual(geojson_bounds(polygon), (7.0, 48.0, 9.0, 50.0))
        self.assertEqual(geojson_bounds(multipolygon), (5.0, 47.0, 11.0, 52.0))

    def test_start_download_uses_pending_pbf_download_jobs_correctly(self):
        widget = self._widget_without_qt_init()
        widget._last_verification = SimpleNamespace(all_found=True, found_names=["baden-wuerttemberg-latest.osm.pbf"])
        checked = []
        widget.check_pbf_files = lambda **kwargs: checked.append(kwargs)

        widget.start_download()

        self.assertEqual(checked, [{"show_feedback": True, "force": True}])

    def test_download_finished_formats_all_downloads_complete_without_placeholder_error(self):
        from core.locales.localizer import _Localizer
        loc = _Localizer()
        msg_de = loc.get_string("step4_status_all_downloads_complete", total_files=3)
        self.assertNotIn("PLACEHOLDER_ERROR", msg_de)
        self.assertEqual(msg_de, "Alle 3 Downloads abgeschlossen.")

        loc.set_language("en")
        msg_en = loc.get_string("step4_status_all_downloads_complete", total_files=3)
        self.assertNotIn("PLACEHOLDER_ERROR", msg_en)
        self.assertEqual(msg_en, "All 3 downloads completed.")
        loc.set_language("de")

        # Test safe fallback with default if placeholder is missing
        msg_fallback = loc.get_string("step4_status_all_downloads_complete", default="Download abgeschlossen.")
        self.assertEqual(msg_fallback, "Download abgeschlossen.")

    def test_pbf_details_caching_and_retrieval(self):
        from core.app.app_core.geofabrik import get_cached_pbf_details, clear_pbf_details_cache, find_pbf_details

        clear_pbf_details_cache()
        loc = SimpleNamespace(latitude=48.137, longitude=11.576)
        self.assertIsNone(get_cached_pbf_details(loc, 100))

        # Perform search with minimal dummy index
        dummy_index = {"features": []}
        result = find_pbf_details(loc, 100, dummy_index, "TestAgent")
        self.assertEqual(result, {"pbfs": []})

        # Cache should now contain the result
        cached = get_cached_pbf_details(loc, 100)
        self.assertEqual(cached, {"pbfs": []})

        # Clear cache and verify
        clear_pbf_details_cache()
        self.assertIsNone(get_cached_pbf_details(loc, 100))


if __name__ == "__main__":
    unittest.main()
