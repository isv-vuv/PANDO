"""QtCore-only workers used by the QML presentation layer."""

from __future__ import annotations

from threading import Event

from core.app.app_core.geofabrik import find_pbf_details
from core.app.app_core.pipeline import PipelineCallbacks, UrbanActPipeline
from core.app.app_core.project import USER_AGENT, download_pbf_jobs, verify_pbf_files

from qgis.PyQt.QtCore import QObject, pyqtSignal


class GeocodeWorker(QObject):
    resultsReady = pyqtSignal(object)
    errorReady = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, city_name: str):
        super().__init__()
        self.city_name = city_name

    def run(self) -> None:
        try:
            from geopy.geocoders import Nominatim

            locations = Nominatim(user_agent="OSM_Tool_QML").geocode(
                self.city_name,
                exactly_one=False,
                limit=10,
                addressdetails=True,
                language="de",
                timeout=20,
            )
            self.resultsReady.emit(locations or [])
        except Exception as exc:
            self.errorReady.emit(str(exc))
        finally:
            self.finished.emit()


class RegionSearchWorker(QObject):
    resultsReady = pyqtSignal(object)
    errorReady = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, location, radius_km: int, geofabrik_index: dict):
        super().__init__()
        self.location = location
        self.radius_km = radius_km
        self.geofabrik_index = geofabrik_index
        self._cancel = Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            details = find_pbf_details(
                self.location,
                self.radius_km,
                self.geofabrik_index,
                USER_AGENT,
                is_cancelled=self._cancel.is_set,
            )
            if not self._cancel.is_set():
                if details and "error" in details:
                    self.errorReady.emit(str(details["error"]))
                else:
                    self.resultsReady.emit(details or {"pbfs": []})
        except Exception as exc:
            self.errorReady.emit(str(exc))
        finally:
            self.finished.emit()


class PbfDownloadWorker(QObject):
    progressReady = pyqtSignal(object, object, int, int)
    fileReady = pyqtSignal(str, int, int)
    errorReady = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, jobs: list[dict], project_path: str, data_path: str):
        super().__init__()
        self.jobs = list(jobs)
        self.project_path = project_path
        self.data_path = data_path
        self._stop = Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            success = download_pbf_jobs(
                self.jobs,
                self.project_path,
                data_path=self.data_path,
                is_stopped=self._stop.is_set,
                on_progress=self.progressReady.emit,
                on_file_ready=self.fileReady.emit,
            )
            self.finished.emit(success)
        except Exception as exc:
            self.errorReady.emit(str(exc))
            self.finished.emit(False)


class PbfVerifyWorker(QObject):
    resultsReady = pyqtSignal(object)
    errorReady = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, project_path: str, jobs: list[dict], data_path: str):
        super().__init__()
        self.project_path = project_path
        self.jobs = list(jobs)
        self.data_path = data_path

    def run(self) -> None:
        try:
            result = verify_pbf_files(self.project_path, self.jobs, data_path=self.data_path)
            self.resultsReady.emit(result)
        except Exception as exc:
            self.errorReady.emit(str(exc))
        finally:
            self.finished.emit()


class PipelineWorker(QObject):
    phaseStarted = pyqtSignal(str, int, int)
    phaseProgress = pyqtSignal(int)
    phaseDetail = pyqtSignal(str)
    logReady = pyqtSignal(str)
    outputReady = pyqtSignal(str, str)
    finished = pyqtSignal(object)
    errorReady = pyqtSignal(str, str)

    def __init__(self, pipeline: UrbanActPipeline, options: dict, *, phase_a_only: bool = False):
        super().__init__()
        self.pipeline = pipeline
        self.options = dict(options)
        self.phase_a_only = phase_a_only
        self.stop_event = Event()
        self._phase = "Vorbereitung"

    def stop(self) -> None:
        self.stop_event.set()
        self.logReady.emit("Abbruch angefordert …")

    def run(self) -> None:
        callbacks = PipelineCallbacks(
            phase_started=self._phase_started,
            phase_progress=self.phaseProgress.emit,
            phase_detail=self.phaseDetail.emit,
            log=self.logReady.emit,
            output=self.outputReady.emit,
        )
        try:
            if self.phase_a_only:
                result = self.pipeline.run_phase_a_only(
                    stop_event=self.stop_event,
                    callbacks=callbacks,
                    **self.options,
                )
            else:
                result = self.pipeline.run(
                    stop_event=self.stop_event,
                    callbacks=callbacks,
                    **self.options,
                ).context
            self.finished.emit(result)
        except Exception as exc:
            self.errorReady.emit(self._phase, str(exc))

    def _phase_started(self, name: str, index: int, total: int) -> None:
        self._phase = name
        self.phaseStarted.emit(name, index, total)
