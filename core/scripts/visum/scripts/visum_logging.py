"""Low-cost diagnostics shared by package and standalone Visum scripts."""
import logging
from contextlib import contextmanager
from time import perf_counter
from functools import wraps


def measured(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with timed_step(function.__qualname__):
            return function(*args, **kwargs)
    return wrapped


@contextmanager
def timed_step(label):
    started = perf_counter()
    logging.info("[Messung] Start: %s", label)
    try:
        yield
    except BaseException:
        logging.error("[Messung] Abgebrochen: %s | Dauer %.2f s", label, perf_counter() - started)
        raise
    else:
        logging.info("[Messung] Ende: %s | Dauer %.2f s", label, perf_counter() - started)


def log_network_counts(visum, label):
    """Read collection counts only; never change filters or transfer full tables."""
    counts = []
    for name in ("Nodes", "Links", "Turns", "Zones", "Connectors", "Matrices"):
        try:
            counts.append(f"{name}={getattr(visum.Net, name).Count}")
        except Exception as exc:
            logging.info("[Diagnose] %s: Anzahl %s nicht verfügbar: %s", label, name, exc)
    logging.info("[Diagnose] Netzbestand %s: %s", label, "; ".join(counts))
