# author: Victor Ibhafidon
# date: 2026-08-14
"""SQLite-backed ledger: source of truth for alerts, snapshots, plans,
notifications, and the decision log.

The check-then-claim protocol lives here: a send is claimed (INTENT inserted) in
the same transaction that checks for existing deliveries, so no re-route or
escalation sequence can ever produce a duplicate.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from .models import (
    Alert,
    ChannelState,
    NotificationStatus,
    Plan,
    PlanState,
    SnapshotEntry,
    make_notification_id,
)

_CLAIMABLE = (NotificationStatus.INTENT, NotificationStatus.SENT,
              NotificationStatus.DELIVERED, NotificationStatus.ESCALATED)


class LedgerError(RuntimeError):
    pass


class SnapshotAlreadyExists(LedgerError):
    pass


class DuplicateNotification(LedgerError):
    pass


class Ledger:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                alert_id   TEXT PRIMARY KEY,
                metric     TEXT NOT NULL,
                value      REAL NOT NULL,
                threshold  REAL NOT NULL,
                severity   TEXT NOT NULL,
                domain     TEXT NOT NULL,
                context    TEXT NOT NULL,
                ts         TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                alert_id        TEXT NOT NULL REFERENCES alerts(alert_id),
                stakeholder_id  TEXT NOT NULL,
                name            TEXT NOT NULL,
                qualification   REAL NOT NULL,
                online          INTEGER NOT NULL,
                channel_health  TEXT NOT NULL,
                gated           INTEGER NOT NULL,
                eval_ts         TEXT NOT NULL,
                PRIMARY KEY (alert_id, stakeholder_id)
            );

            CREATE TABLE IF NOT EXISTS plans (
                alert_id        TEXT PRIMARY KEY REFERENCES alerts(alert_id),
                plan_state      TEXT NOT NULL,
                escalation_cap  INTEGER NOT NULL,
                level           INTEGER NOT NULL,
                created_ts      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                notification_id TEXT PRIMARY KEY,
                alert_id        TEXT NOT NULL REFERENCES alerts(alert_id),
                stakeholder_id  TEXT NOT NULL,
                stakeholder_name TEXT NOT NULL,
                channel         TEXT NOT NULL,
                status          TEXT NOT NULL,
                escalation_level INTEGER NOT NULL,
                body            TEXT NOT NULL,
                sent_ts         TEXT,
                UNIQUE (alert_id, stakeholder_id, channel, escalation_level)
            );

            CREATE TABLE IF NOT EXISTS decision_log (
                entry_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id   TEXT NOT NULL REFERENCES alerts(alert_id),
                seq        INTEGER NOT NULL,
                code       TEXT NOT NULL,
                action     TEXT NOT NULL,
                target     TEXT,
                rationale  TEXT NOT NULL,
                logged_ts  TEXT NOT NULL,
                UNIQUE (alert_id, seq)
            );
            """
        )
        self.conn.commit()

    # ------------------------------------------------------------------ alerts

    def create_alert(self, alert: Alert) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?,?,?,?)",
                (alert.alert_id, alert.metric, alert.value, alert.threshold,
                 alert.severity, alert.domain, json.dumps(alert.context), alert.ts),
            )

    def alert_exists(self, alert_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM alerts WHERE alert_id=?", (alert_id,))
        return cur.fetchone() is not None

    # --------------------------------------------------------------- snapshots

    def insert_snapshot(self, alert_id: str, entry: SnapshotEntry) -> None:
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?)",
                    (alert_id, entry.stakeholder_id, entry.name, entry.qualification,
                     int(entry.online), json.dumps(
                         {k: v.value for k, v in entry.channel_health.items()}),
                     int(entry.gated), entry.eval_ts),
                )
        except sqlite3.IntegrityError as exc:
            raise SnapshotAlreadyExists(
                f"stakeholder {entry.stakeholder_id} already evaluated for {alert_id}"
            ) from exc

    def load_snapshots(self, alert_id: str) -> dict[str, SnapshotEntry]:
        cur = self.conn.execute(
            "SELECT * FROM snapshots WHERE alert_id=?", (alert_id,))
        out: dict[str, SnapshotEntry] = {}
        for row in cur.fetchall():
            out[row["stakeholder_id"]] = SnapshotEntry(
                stakeholder_id=row["stakeholder_id"],
                name=row["name"],
                qualification=row["qualification"],
                online=bool(row["online"]),
                channel_health={k: ChannelState(v)
                                for k, v in json.loads(row["channel_health"]).items()},
                gated=bool(row["gated"]),
                eval_ts=row["eval_ts"],
            )
        return out

    # ------------------------------------------------------------------ plans

    def save_plan(self, plan: Plan, created_ts: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO plans VALUES (?,?,?,?,?)",
                (plan.alert_id, plan.state.value, plan.escalation_cap, plan.level, created_ts),
            )

    def set_plan_state(self, alert_id: str, state: PlanState) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE plans SET plan_state=? WHERE alert_id=?", (state.value, alert_id))

    def set_plan_level(self, alert_id: str, level: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE plans SET level=? WHERE alert_id=?", (level, alert_id))

    def plan_state(self, alert_id: str) -> Optional[str]:
        cur = self.conn.execute("SELECT plan_state FROM plans WHERE alert_id=?", (alert_id,))
        row = cur.fetchone()
        return row["plan_state"] if row else None

    # ------------------------------------------------------------ notifications

    def claim(
        self,
        alert_id: str,
        sid: str,
        sname: str,
        channel: str,
        level: int,
        body: str,
    ) -> str:
        """Check-then-claim. Returns notification_id on success, None on dup."""
        notification_id = make_notification_id(alert_id, sid, channel, level)
        with self.conn:
            # I1/I2 guard: no other non-cancelled notification for this stakeholder/alert.
            dup = self.conn.execute(
                "SELECT 1 FROM notifications WHERE alert_id=? AND stakeholder_id=?"
                " AND status IN (?,?,?,?) LIMIT 1",
                (alert_id, sid, *_CLAIMABLE),
            ).fetchone()
            if dup is not None:
                return None
            try:
                self.conn.execute(
                    "INSERT INTO notifications VALUES (?,?,?,?,?,?,?,?,NULL)",
                    (notification_id, alert_id, sid, sname, channel,
                     NotificationStatus.INTENT.value, level, body),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateNotification(str(exc)) from exc
        return notification_id

    def has_been_notified(self, alert_id: str, sid: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM notifications WHERE alert_id=? AND stakeholder_id=?"
            " AND status IN (?,?,?,?) LIMIT 1",
            (alert_id, sid, *_CLAIMABLE),
        )
        return cur.fetchone() is not None

    def set_status(self, notification_id: str, status: NotificationStatus, sent_ts: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE notifications SET status=?, sent_ts=? WHERE notification_id=?",
                (status.value, sent_ts, notification_id),
            )

    def notification_status(self, notification_id: str) -> Optional[str]:
        cur = self.conn.execute(
            "SELECT status FROM notifications WHERE notification_id=?", (notification_id,))
        row = cur.fetchone()
        return row["status"] if row else None

    def notifications_for(self, alert_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM notifications WHERE alert_id=? ORDER BY escalation_level", (alert_id,))
        return [dict(row) for row in cur.fetchall()]

    def delivered_sids(self, alert_id: str) -> frozenset[str]:
        cur = self.conn.execute(
            "SELECT stakeholder_id FROM notifications WHERE alert_id=?"
            " AND status IN (?,?)",
            (alert_id, NotificationStatus.DELIVERED.value,
             NotificationStatus.ESCALATED.value),
        )
        return frozenset(r["stakeholder_id"] for r in cur.fetchall())

    def notified_sids(self, alert_id: str) -> frozenset[str]:
        cur = self.conn.execute(
            "SELECT stakeholder_id FROM notifications WHERE alert_id=?"
            " AND status IN (?,?,?,?)",
            (alert_id, *_CLAIMABLE),
        )
        return frozenset(r["stakeholder_id"] for r in cur.fetchall())

    def attempted_sids(self, alert_id: str, level: int) -> frozenset[str]:
        """Stakeholders with ANY claim at the given level — even CANCELLED.

        A cancelled slot still consumes the UNIQUE (alert, sid, channel, level)
        key, so same-level reroutes must never re-pick a stakeholder who has
        already been attempted (R1/R2 reroute). Escalations target level+1
        (fresh slots), so they are unaffected by this set."""
        cur = self.conn.execute(
            "SELECT DISTINCT stakeholder_id FROM notifications"
            " WHERE alert_id=? AND escalation_level=?", (alert_id, level))
        return frozenset(r["stakeholder_id"] for r in cur.fetchall())

    # ----------------------------------------------------------- decision log

    def log_decision(self, alert_id: str, seq: int, code: str, action: str,
                     target: Optional[str], rationale: str, ts: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO decision_log (alert_id, seq, code, action,"
                " target, rationale, logged_ts) VALUES (?,?,?,?,?,?,?)",
                (alert_id, seq, code, action, target, rationale, ts),
            )

    def decision_log(self, alert_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT seq, code, action, target, rationale, logged_ts FROM decision_log"
            " WHERE alert_id=? ORDER BY seq", (alert_id,))
        return [dict(r) for r in cur.fetchall()]

    def next_seq(self, alert_id: str) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) AS n FROM decision_log WHERE alert_id=?", (alert_id,))
        return int(cur.fetchone()["n"]) + 1
