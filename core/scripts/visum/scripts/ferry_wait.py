"""Fixed boarding delay, in seconds, upon entering the ferry system."""
import json
import math
from pathlib import Path

FERRY_LINK_TYPES = frozenset({'Ferry', 'Ferry_Access'})


def load_wait_minutes(root):
    path = Path(root) / 'ferry_parameters.json'
    value = json.loads(path.read_text(encoding='utf-8'))['wait_minutes'] if path.exists() else 20
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('Ferry waiting time must be finite and non-negative')
    return value


def save_wait_minutes(root, minutes):
    value = float(minutes)
    if not math.isfinite(value) or value < 0:
        raise ValueError('Ferry waiting time must be finite and non-negative')
    path = Path(root) / 'ferry_parameters.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'wait_minutes': value}, indent=2), encoding='utf-8')
    temporary.replace(path)
    return path


def apply_ferry_wait(visum, minutes):
    """Replace our previous delay, preserving other turn penalties and restrictions.

    Both ``Ferry`` (importer type 94 / master 939) and ``Ferry_Access``
    (importer type 95 / master 940) are treated as part of the ferry system.
    The waiting-time penalty is applied exactly once per ferry boarding:
    - Road -> Ferry (direct ferry connection)
    - Road -> Ferry_Access (isolated terminal connected via Ferry_Access)
    Turns inside the ferry system (Ferry_Access -> Ferry, Ferry -> Ferry,
    Ferry -> Ferry_Access) receive zero additional boarding penalty.
    """
    seconds = float(minutes) * 60
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Invalid ferry waiting time')
    turns = visum.Net.Turns
    uda = 'PANDO_FERRY_WAIT'
    if not turns.AttrExists(uda):
        turns.AddUserDefinedAttribute(uda, uda, uda, 2, DefVal=0)
    rows = turns.GetMultipleAttributes([
        r'FROMLINK\LINKTYPE\NAME', r'TOLINK\LINKTYPE\NAME', 'T0PRT', uda,
    ])
    # The bundled procedure reserves AddVal2 for an unscaled waiting-time skim.
    # Refuse to silently include a custom model's unrelated additional values.
    for objects in (visum.Net.Links, visum.Net.Connectors, visum.Net.MainTurns):
        if any(float(value or 0) != 0 for _, value in objects.GetMultiAttValues('ADDVAL2')):
            raise ValueError('AddVal2 is reserved for the ferry waiting-time skim')
    additional = turns.GetMultiAttValues('ADDVAL2')
    if any(abs(float(value or 0) - float(row[3] or 0) / 60) > 1e-6
           for (_, value), row in zip(additional, rows)):
        raise ValueError('AddVal2 is reserved for the ferry waiting-time skim')
    times, waits = [], []
    for index, (incoming, outgoing, current, previous) in enumerate(rows, 1):
        delay = seconds if (outgoing in FERRY_LINK_TYPES and incoming not in FERRY_LINK_TYPES) else 0
        times.append((index, float(current) - float(previous or 0) + delay))
        waits.append((index, delay))
    if times:
        turns.SetMultiAttValues('T0PRT', times)
        turns.SetMultiAttValues(uda, waits)
        turns.SetMultiAttValues('ADDVAL2', [(index, delay / 60) for index, delay in waits])
    return sum(delay > 0 for _, delay in waits)
