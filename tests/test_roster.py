# author: Victor Ibhafidon
# date: 2026-08-15
"""On-call roster: shift validation + effective on-call resolution."""

from unittest import TestCase

from alert_routing import roster
from alert_routing.models import Stakeholder
from alert_routing.roster import RosterValidationError


def make_registry():
    def st(sid, on_call):
        return Stakeholder(id=sid, name=f"Stak {sid}", title="", seniority=3,
                           expertise={}, on_call=on_call, channels=[])
    return {"STK-001": st("STK-001", True), "STK-002": st("STK-002", False),
            "STK-003": st("STK-003", True)}


class EffectiveOnCallTest(TestCase):
    def test_no_shifts_uses_registry_flags(self):
        reg = make_registry()
        eff = roster.effective_on_call(reg, [], "2026-08-15")
        self.assertEqual(eff, {"STK-001": True, "STK-002": False, "STK-003": True})

    def test_shift_covers_day_overrides_flags(self):
        reg = make_registry()
        shifts = [{"id": "sh-1", "start": "2026-08-10", "end": "2026-08-16",
                   "primary": "STK-002", "backups": []}]
        eff = roster.effective_on_call(reg, shifts, "2026-08-15")
        self.assertEqual(eff, {"STK-001": False, "STK-002": True, "STK-003": False})

    def test_primary_and_backups_all_on_call(self):
        reg = make_registry()
        shifts = [{"id": "sh-1", "start": "2026-08-10", "end": "2026-08-16",
                   "primary": "STK-001", "backups": ["STK-002"]}]
        eff = roster.effective_on_call(reg, shifts, "2026-08-15")
        self.assertTrue(eff["STK-001"])
        self.assertTrue(eff["STK-002"])
        self.assertFalse(eff["STK-003"])

    def test_outside_range_falls_back(self):
        reg = make_registry()
        shifts = [{"id": "sh-1", "start": "2026-08-10", "end": "2026-08-16",
                   "primary": "STK-002", "backups": []}]
        eff = roster.effective_on_call(reg, shifts, "2026-09-01")
        self.assertEqual(eff, {"STK-001": True, "STK-002": False, "STK-003": True})

    def test_covering_shifts_inclusive_bounds(self):
        shifts = [{"start": "2026-08-10", "end": "2026-08-16"}]
        self.assertEqual(len(roster.covering_shifts(shifts, "2026-08-10")), 1)
        self.assertEqual(len(roster.covering_shifts(shifts, "2026-08-16")), 1)
        self.assertEqual(len(roster.covering_shifts(shifts, "2026-08-17")), 0)


class ShiftValidationTest(TestCase):
    def test_valid_shift_normalized(self):
        out = roster.validate_shift(
            {"id": "sh-9", "start": "2026-08-10", "end": "2026-08-16",
             "primary": "STK-001", "backups": ["STK-002"]})
        self.assertEqual(out["id"], "sh-9")
        self.assertEqual(out["backups"], ["STK-002"])

    def test_bad_date_rejected(self):
        with self.assertRaises(RosterValidationError):
            roster.validate_shift({"start": "not-a-date", "end": "2026-08-16",
                                   "primary": "STK-001"})

    def test_start_after_end_rejected(self):
        with self.assertRaises(RosterValidationError):
            roster.validate_shift({"start": "2026-08-16", "end": "2026-08-10",
                                   "primary": "STK-001"})

    def test_missing_primary_rejected(self):
        with self.assertRaises(RosterValidationError):
            roster.validate_shift({"start": "2026-08-10", "end": "2026-08-16"})

    def test_primary_in_backups_rejected(self):
        with self.assertRaises(RosterValidationError):
            roster.validate_shift({"start": "2026-08-10", "end": "2026-08-16",
                                   "primary": "STK-001", "backups": ["STK-001"]})

    def test_unknown_stakeholder_rejected(self):
        with self.assertRaises(RosterValidationError):
            roster.validate_shift({"start": "2026-08-10", "end": "2026-08-16",
                                   "primary": "STK-999"}, known_sids={"STK-001"})

    def test_add_shift_assigns_id(self):
        shifts = [{"id": "sh-1", "start": "2026-08-10", "end": "2026-08-16",
                   "primary": "STK-001"}]
        out = roster.add_shift(shifts, {"start": "2026-08-17", "end": "2026-08-23",
                                        "primary": "STK-002"}, known_sids={"STK-001", "STK-002"})
        self.assertEqual(out[-1]["id"], "sh-2")

    def test_remove_shift(self):
        shifts = [{"id": "sh-1"}, {"id": "sh-2"}]
        out = roster.remove_shift(shifts, "sh-1")
        self.assertEqual([s["id"] for s in out], ["sh-2"])
