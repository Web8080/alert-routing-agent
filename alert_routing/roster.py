# author: Victor Ibhafidon
# date: 2026-08-15
"""On-call roster — a simple calendar of who is on call each day.

A roster is a JSON file of `shifts`. Each shift is a date range with a primary
and backups:

    {"id": "sh-1", "start": "2026-08-10", "end": "2026-08-16",
     "primary": "STK-001", "backups": ["STK-006"]}

Effective on-call for a given day is the union of primaries + backups across
every shift covering that day. When NO shift covers the day, the registry's
static `on_call` flags win (backward compatible with the plain registry).
"""

from __future__ import annotations

import json
from datetime import date as _date
from pathlib import Path
from typing import Optional

# generated ids: keep a counter inside the file so ids survive reloads
def _next_shift_id(shifts: list[dict]) -> str:
    used = {s.get("id", "") for s in shifts}
    n = 1
    while f"sh-{n}" in used:
        n += 1
    return f"sh-{n}"


class RosterValidationError(ValueError):
    pass


def _is_iso_day(v: str) -> bool:
    try:
        _date.fromisoformat(v)
    except (TypeError, ValueError):
        return False
    return True


def load_shifts(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    data = json.loads(p.read_text())
    shifts = data.get("shifts", [])
    for s in shifts:
        validate_shift(s, known_sids=None, require_id=False)
    return shifts


def save_shifts(path: str | Path, shifts: list[dict]) -> None:
    Path(path).write_text(json.dumps({"shifts": shifts}, indent=2) + "\n")


def validate_shift(shift: dict, known_sids=None, require_id: bool = True) -> dict:
    """Validate a shift dict; returns a normalized copy. Raises RosterValidationError."""
    start, end = shift.get("start"), shift.get("end")
    if not _is_iso_day(start) or not _is_iso_day(end):
        raise RosterValidationError("shift 'start'/'end' must be YYYY-MM-DD")
    if start > end:
        raise RosterValidationError(f"shift start {start} is after end {end}")
    primary = shift.get("primary")
    if not primary:
        raise RosterValidationError("shift must have a 'primary' stakeholder")
    backups = list(shift.get("backups") or [])
    if primary in backups:
        raise RosterValidationError("primary must not also be listed as a backup")
    if known_sids is not None:
        for sid in [primary] + backups:
            if sid not in known_sids:
                raise RosterValidationError(f"shift references unknown stakeholder {sid!r}")
    out = {"id": shift.get("id") or "", "start": start, "end": end,
           "primary": primary, "backups": backups}
    if require_id and not out["id"]:
        raise RosterValidationError("shift missing 'id'")
    return out


def covering_shifts(shifts: list[dict], day: str) -> list[dict]:
    return [s for s in shifts if s["start"] <= day <= s["end"]]


def effective_on_call(
    registry: dict, shifts: list[dict], day: Optional[str] = None,
) -> dict[str, bool]:
    """stakeholder_id -> on_call for `day` (real today when not given).

    Roster shift covers the day → only scheduled primaries/backups are on call.
    No shift covers the day → static registry flags apply unchanged.
    """
    day = day or _date.today().isoformat()
    covering = covering_shifts(shifts, day)
    if not covering:
        return {sid: st.on_call for sid, st in registry.items()}
    on = {sid for s in covering for sid in [s["primary"], *s["backups"]]}
    return {sid: sid in on for sid in registry}


def add_shift(shifts: list[dict], shift: dict, known_sids) -> list[dict]:
    """Append a validated shift (id auto-assigned). Returns a new list."""
    shift = validate_shift(shift, known_sids=known_sids, require_id=False)
    shift["id"] = _next_shift_id(shifts)
    return [*shifts, shift]


def upsert_shift(shifts: list[dict], shift: dict, known_sids) -> list[dict]:
    """Replace the shift with the same id, or append. Returns a new list."""
    shift = validate_shift(shift, known_sids=known_sids, require_id=True)
    out = []
    replaced = False
    for s in shifts:
        if s.get("id") == shift["id"]:
            out.append(shift)
            replaced = True
        else:
            out.append(s)
    if not replaced:
        raise RosterValidationError(f"no shift with id {shift['id']!r} to update")
    return out


def remove_shift(shifts: list[dict], shift_id: str) -> list[dict]:
    return [s for s in shifts if s.get("id") != shift_id]
