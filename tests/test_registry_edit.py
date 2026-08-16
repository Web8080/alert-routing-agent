# author: Victor Ibhafidon
# date: 2026-08-15
"""Registry editing: parse/save round-trip + ui write helpers."""

import json
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from alert_routing import settings, ui
from alert_routing.registry import (RegistryStore, RegistryValidationError,
                                    load_registry, parse_stakeholder,
                                    save_registry, stakeholder_to_dict)

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


class RegistryStoreTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.json_path = str(Path(self._tmp.name) / "registry.json")
        self.db_path = str(Path(self._tmp.name) / "registry.db")
        Path(self.json_path).write_text(json.dumps(SEED))

    def tearDown(self):
        self._tmp.cleanup()

    def test_seeds_db_from_json_when_empty(self):
        store = RegistryStore(self.json_path, self.db_path)
        reg = store.load()
        self.assertEqual(set(reg), {"STK-001", "STK-002"})
        with mock.patch("alert_routing.registry.load_registry") as m:
            m.side_effect = AssertionError("db should satisfy load()")
            again = RegistryStore(self.json_path, self.db_path).load()
        self.assertEqual(set(again), {"STK-001", "STK-002"})

    def test_edit_persists_across_instances(self):
        store = RegistryStore(self.json_path, self.db_path)
        reg = store.load()
        reg["STK-001"] = parse_stakeholder(dict(SEED["stakeholders"][0],
                                                title="Inventory Co-Lead"))
        store.save(reg)
        fresh = RegistryStore(self.json_path, self.db_path).load()
        self.assertEqual(fresh["STK-001"].title, "Inventory Co-Lead")

    def test_delete_persists(self):
        store = RegistryStore(self.json_path, self.db_path)
        reg = store.load()
        del reg["STK-002"]
        store.save(reg)
        fresh = RegistryStore(self.json_path, self.db_path).load()
        self.assertNotIn("STK-002", fresh)

    def test_save_refreshes_json_seed(self):
        store = RegistryStore(self.json_path, self.db_path)
        reg = store.load()
        reg["STK-001"] = parse_stakeholder(dict(SEED["stakeholders"][0],
                                                title="Inventory Co-Lead"))
        store.save(reg)
        on_disk = load_registry(self.json_path)
        self.assertEqual(on_disk["STK-001"].title, "Inventory Co-Lead")

    def test_env_override_for_db_path(self):
        alt_db = str(Path(self._tmp.name) / "alt.db")
        with mock.patch.dict("os.environ", {"ALERT_REGISTRY_DB": alt_db}):
            store = RegistryStore(self.json_path)
        self.assertEqual(store.db_path, alt_db)
        store.load()
        self.assertTrue(Path(alt_db).exists())

    def test_ui_helpers_accept_a_store(self):
        store = RegistryStore(self.json_path, self.db_path)
        st = ui._upsert_stakeholder(store, {
            "name": "Nina Voss", "title": "IC", "seniority": 2,
            "expertise": {"inventory": 3}, "on_call": False,
            "channels": [{"name": "email", "priority": 1, "endpoint": "nina@acme.dev"}]})
        self.assertEqual(st.id, "STK-003")
        self.assertIn("STK-003", RegistryStore(self.json_path, self.db_path).load())

    def test_edited_registry_reflected_in_dispatch(self):
        """An edit persisted to the DB (via one store instance) is seen by a
        fresh instance feeding a dispatch — the backend reads the store, not
        the stale JSON seed."""
        editor = RegistryStore(self.json_path, self.db_path)
        reg = editor.load()
        # SEED: STK-001 has slack priority 1 -> dispatch would go via slack.
        reg["STK-001"] = parse_stakeholder(dict(
            SEED["stakeholders"][0],
            channels=[{"name": "email", "priority": 1, "endpoint": "new@acme.dev"}]))
        editor.save(reg)

        alert = {"metric": "stock_level", "value": 12, "threshold": 20,
                 "severity": "HIGH", "domain": "inventory", "context": {}}
        with mock.patch.object(settings, "smtp_enabled", return_value=False), \
             mock.patch.object(settings, "slack_enabled", return_value=False):
            router, alert_id = ui.dispatch_custom(
                alert, RegistryStore(self.json_path, self.db_path))
        notifs = router.ledger.notifications_for(alert_id)
        self.assertTrue(notifs)
        self.assertEqual(notifs[0]["stakeholder_id"], "STK-001")
        self.assertEqual(notifs[0]["channel"], "email")
