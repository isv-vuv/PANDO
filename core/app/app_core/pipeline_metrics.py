"""Per-run, append-only benchmark tables (durations in seconds, sizes in bytes)."""
from __future__ import annotations

import csv
import json
import os
import platform
import time
from dataclasses import replace
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from threading import Event, RLock, Thread
from uuid import uuid4
from contextvars import ContextVar

_current = ContextVar("pipeline_metrics", default=None)


def benchmark_step(name):
    metrics = _current.get()
    if metrics is not None:
        metrics.transition(name)


class RunMetrics:
    def __init__(self, project):
        self.project = Path(project)
        self.directory = self.project / "pipeline_metrics" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8])
        self.directory.mkdir(parents=True)
        self.lock = RLock()
        self.stop = Event()
        self.rows = []
        self.initial = self.size()
        self.peak = self.initial
        self.start = time.perf_counter_ns()
        self.active = None
        self.thread = Thread(target=self._sample, daemon=True)
        self.thread.start()
        self.transition("preparation")

    def size(self):
        total = 0
        for root, dirs, files in os.walk(self.project):
            if Path(root) == self.project:
                dirs[:] = [d for d in dirs if d != "pipeline_metrics"]
            for name in files:
                try:
                    total += os.stat(os.path.join(root, name), follow_symlinks=False).st_size
                except FileNotFoundError:
                    pass  # A processing worker may remove a temporary file during a scan.
        return total

    def sample(self):
        with self.lock:
            size = self.size()
            self.peak = max(self.peak, size)
            if self.active:
                self.active["peak_bytes"] = max(self.active["peak_bytes"], size)
            return size

    def _sample(self):
        while not self.stop.wait(1):
            try:
                self.sample()
            except OSError:
                pass

    def transition(self, name, status="completed"):
        with self.lock:
            size = self.sample()
            now = time.perf_counter_ns()
            if self.active:
                row = self.active
                row.update(duration_seconds=(now - row.pop("clock")) / 1e9,
                           end_bytes=size, delta_bytes=size - row["start_bytes"],
                           cumulative_peak_bytes=self.peak, status=status)
                row["additional_peak_bytes"] = row["peak_bytes"] - row["start_bytes"]
                self.rows.append(row)
                with (self.directory / "steps.csv").open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
                    writer.writeheader()
                    writer.writerows(self.rows)
            self.active = None if name is None else dict(step=name, reused="wiederverwendet" in name,
                started_utc=datetime.now(timezone.utc).isoformat(), clock=time.perf_counter_ns(),
                start_bytes=size, peak_bytes=size)

    def finish(self, status, metadata):
        self.stop.set()
        self.thread.join()
        self.transition(None, status)
        metadata.update(schema_version=1, status=status, duration_seconds=(time.perf_counter_ns() - self.start) / 1e9,
                        initial_project_bytes=self.initial, peak_project_bytes=self.peak,
                        additional_peak_bytes=self.peak - self.initial,
                        storage_scope="project files excluding pipeline_metrics; logical bytes; external inputs and system/QGIS temp excluded",
                        sample_interval_seconds=1, hostname=platform.node(), platform=platform.platform(),
                        processor=platform.processor(), cpu_count=os.cpu_count(), python=platform.python_version())
        (self.directory / "run.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_regions(directory, context):
    """Aggregate the final Model 4 grid: each grid cell contributes exactly once."""
    from qgis.core import QgsVectorLayer, QgsDistanceArea
    from qgis.core import QgsProject

    layer = QgsVectorLayer(str(context["grid_zone_pa_ia1_ia2"]), "metrics", "ogr")
    if not layer.isValid():
        raise ValueError("Model-4 grid is not readable")
    names = layer.fields().names()
    zone_field = next(n for n in ("ZoneType", "Zone Type", "zone_type") if n in names)
    if "POP" not in names:
        raise ValueError("Model-4 grid has no POP field")
    measure = QgsDistanceArea()
    measure.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
    measure.setEllipsoid("WGS84")
    rows = {z: dict(region=z, population=0.0, area_km2=0.0, cells=0) for z in ("PA", "IA1", "IA2")}
    for feature in layer.getFeatures():
        row = rows.get(str(feature[zone_field]))
        if row is not None:
            row["population"] += float(feature["POP"])
            row["area_km2"] += measure.measureArea(feature.geometry()) / 1e6
            row["cells"] += 1
    with (directory / "regions.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["region", "population", "area_km2", "cells"])
        writer.writeheader()
        writer.writerows(rows.values())


def benchmark_run(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        metrics = RunMetrics(self.project_path)
        token = _current.set(metrics)
        # Import lazily to avoid a circular dependency with pipeline.py.
        from core.app.app_core.pipeline import PipelineCallbacks
        callbacks = kwargs.get("callbacks", PipelineCallbacks())
        def started(name, index, total):
            metrics.transition(name)
            callbacks.phase_started(name, index, total)
        kwargs["callbacks"] = replace(callbacks, phase_started=started)
        status = "failed"
        metadata = dict(project=str(self.project_path), entrypoint=method.__name__,
                        positional_parameters=args,
                        parameters={k: v for k, v in kwargs.items() if k not in ("callbacks", "stop_event")},
                        selected_cells=self.step_data.get("selected_cells"),
                        input_pbfs=[dict(path=p, size_bytes=Path(p).stat().st_size) for p in self._pbf_paths() if Path(p).is_file()])
        try:
            result = method(self, *args, **kwargs)
            status = "completed"
            if hasattr(result, "context"):
                metrics.transition("benchmark_region_statistics")
                try:
                    write_regions(metrics.directory, result.context)
                except Exception as exc:
                    metadata["region_statistics_error"] = str(exc)
                    callbacks.log(f"Benchmark-Gebietsstatistik konnte nicht erstellt werden: {exc}")
            return result
        except BaseException as exc:
            metadata["error"] = str(exc)
            raise
        finally:
            _current.reset(token)
            metrics.finish(status, metadata)
    return wrapped
