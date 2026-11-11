"""Derived distance grid, with explicit separation of interpolated and edited values."""
import hashlib
import json
import math

DISTANCES = [i / 10 for i in range(20, 9, -2)] + [i / 10 for i in range(9, -11, -1)]


def build_grid(points, channel, edits=None, basis=None):
    pairs = [(p['distance'], p['mean']) for p in points]
    current = hashlib.sha256(json.dumps([channel, pairs], allow_nan=False).encode()).hexdigest()
    edits = edits if basis == current and isinstance(edits, dict) else {}
    rows, accepted = [], {}
    for x in DISTANCES:
        candidates = [y for d, y in pairs if d is not None and y is not None and abs(d-x) < 1e-9]
        if not candidates:
            for (a, u), (b, v) in zip(pairs, pairs[1:]):
                if None in (a, u, b, v) or a == b:
                    continue
                if min(a, b) < x < max(a, b):
                    candidates.append(u + (v-u)*(x-a)/(b-a))
        value = candidates[0] if candidates and max(candidates)-min(candidates) < 1e-9 else None
        status = 'interpolated' if value is not None else ('ambiguous' if candidates else 'uncovered')
        key = f'{x:.1f}'
        output = value
        if key in edits and value is not None:
            try:
                output = float(edits[key])
            except (TypeError, ValueError):
                raise ValueError(f'{key} mm 电压必须为有限数字。') from None
            if not math.isfinite(output):
                raise ValueError(f'{key} mm 电压必须为有限数字。')
            if output != value:
                accepted[key] = output
                status = 'edited'
        rows.append(dict(distance_mm=x, interpolated_voltage_V=value, voltage_V=output,
                         edited=status == 'edited', status=status))
    return rows, current, accepted
