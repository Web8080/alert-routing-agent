# author: Victor Ibhafidon
# date: 2026-08-15
"""Registry editing: parse/save round-trip + ui write helpers."""

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from alert_routing import ui
from alert_routing.registry import (RegistryValidationError, load_registry,
                                    parse_stakeholder, save_registry,
                                    stakeholder_to_dict)

SEED = {
    "stakeholders": [
        {"id": "STK-001", "name": "Sarah Chen", "title": "Inventory Lead",
         "seniority": 3, "expertise": {"inventory": 5}, "on_call": True,
         "channels": [{"name": "slack", "priority": 1, "endpoint": "sarah.slack"},
                      {"name": "email", "priority": 2, "endpoint": "sarah@acme.dev"}]},
        {"id": "STK-002", "name": "David Miller", "title": "Senior Ops",
         "seniority": 4, "expertise": {"logistics": 4}, "on_call": True,
         "channels": [{"name": "email", "priority": 1, "endpoint": "david@acme.dev"}]},
    ]
}


class ParseStakeholderTest(TestCase):
    def test_valid(self):
        st = parse_stakeholder(SEED["stakeholders"][0])
        self.assertEqual(st.id, "STK-001")
        self.assertTrue(st.on_call)
        self.assertEqual(st.channels[0].endpoint, "sarah.slack")

    def test_missing_id(self):
        item = dict(SEED["stakeholders"][0], id=None)
        with self.assertRaises(RegistryValidationError):
            parse_stakeholder(item)

    def test_bad_seniority(self):
        item = dict(SEED["stakeholders"][0], seniority=9)
        with self.assertRaises(RegistryValidationError):
            parse_stakeholder(item)

    def test_unknown_channel(self):
        item = dict(SEED["stakeholders"][0], channels=[{"name": "pager", "priority": 1,
                                                        "endpoint": "x"}])
        with self.assertRaises(RegistryValidationError):
            parse_stakeholder(item)

    def test_no_channels(self):
        item = dict(SEED["stakeholders"][0], channels=[])
        with self.assertRaises(RegistryValidationError):
            parse_stakeholder(item)


class SaveRegistryTest(TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(SEED))
            reg = load_registry(str(path))
            save_registry(str(path), reg)
            again = load_registry(str(path))
            self.assertEqual(set(again), set(reg))
            self.assertEqual(again["STK-001"].channels[0].endpoint, "sarah.slack")

    def test_stakeholder_to_dict_inverse(self):
        st = parse_stakeholder(SEED["stakeholders"][1])
        st2 = parse_stakeholder(stakeholder_to_dict(st))
        self.assertEqual(st2, st)


class UiWriteHelpersTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "registry.json")
        Path(self.path).write_text(json.dumps(SEED))

    def tearDown(self):
        self._tmp.cleanup()

    def test_upsert_assigns_new_id(self):
        st = ui._upsert_stakeholder(self.path, {
            "name": "Nina Voss", "title": "IC", "seniority": 2,
            "expertise": {"inventory": 3}, "on_call": False,
            "channels": [{"name": "email", "priority": 1, "endpoint": "nina@acme.dev"}]})
        self.assertEqual(st.id, "STK-003")
        reg = load_registry(self.path)
        self.assertIn("STK-003", reg)

    def test_upsert_updates_existing(self):
        item = dict(SEED["stakeholders"][0])
        item["on_call"] = False
        item["title"] = "Inventory Co-Lead"
        ui._upsert_stakeholder(self.path, item)
        reg = load_registry(self.path)
        self.assertFalse(reg["STK-001"].on_call)
        self.assertEqual(reg["STK-001"].title, "Inventory Co-Lead")
        self.assertEqual(len(reg), 2)

    def test_set_on_call(self):
        ui._set_on_call(self.path, "STK-002", False)
        reg = load_registry(self.path)
        self.assertFalse(reg["STK-002"].on_call)

    def test_set_on_call_unknown_sid(self):
        with self.assertRaises(KeyError):
            ui._set_on_call(self.path, "STK-999", True)

    def test_delete(self):
        ui._delete_stakeholder(self.path, "STK-002")
        reg = load_registry(self.path)
        self.assertNotIn("STK-002", reg)
        self.assertIn("STK-001", reg)

    def test_delete_unknown_sid(self):
        with self.assertRaises(KeyError):
            ui._delete_stakeholder(self.path, "STK-999")
