"""Fixed boarding delay, in seconds, on land-to-ferry turns only."""
import json
import math
from pathlib import Path


def load_wait_minutes(root):
    path = Path(root) / 'ferry_parameters.json'
    value = json.loads(path.read_text(encoding='utf-8'))['wait_minutes'] if path.exists() else 20
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('Ferry waiting time must be finite and non-negative')
    return value


def apply_ferry_wait(visum, minutes):
    """Replace our previous delay, preserving other turn penalties and restrictions.

    Link type names survive the conversion from importer type 94 to master 939.
    Ferry-to-ferry turns do not board again, even across split OSM ways.
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
        delay = seconds if outgoing == 'Ferry' and incoming != 'Ferry' else 0
        times.append((index, float(current) - float(previous or 0) + delay))
        waits.append((index, delay))
    if times:
        turns.SetMultiAttValues('T0PRT', times)
        turns.SetMultiAttValues(uda, waits)
        turns.SetMultiAttValues('ADDVAL2', [(index, delay / 60) for index, delay in waits])
    return sum(delay > 0 for _, delay in waits)
