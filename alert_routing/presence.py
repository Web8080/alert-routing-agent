# author: Victor Ibhafidon
# date: 2026-08-14
"""Simulated presence service + in-process event emitter.

This is the ONLY source of time-varying availability. Events are delivered
synchronously (total order) to subscribers, which is what makes dispatch
deterministic and replayable. In production this interface maps 1:1 onto a real
presence/event source (Slack presence API, channel health probes, Kafka).
"""

from __future__ import annotations

from typing import Callable

from .models import ChangeNotice, ChannelState

Subscriber = Callable[[ChangeNotice], None]

EVENT_PRESENCE = "presence.changed"
EVENT_CANDIDATE = "candidate.available"
EVENT_CHANNEL = "channel.failed"


class Presence:
    def __init__(self) -> None:
        self._online: dict[str, bool] = {}
        self._health: dict[str, dict[str, ChannelState]] = {}
        self._subscribers: list[Subscriber] = []

    def seed(self, online: dict[str, bool] | None = None,
             health: dict[str, dict[str, str]] | None = None) -> None:
        self._online = dict(online or {})
        self._health = {
            sid: {ch: ChannelState(st) for ch, st in health.get(sid, {})}
            for sid in list(health or {})
        }

    def seed_defaults(self, channels_by_sid: dict[str, list[str]]) -> None:
        """Fill missing state with the safe default (online, all channels OK).

        Called once at router init so a scenario only needs to override what it
        cares about. Defaults never fire events."""
        for sid, channels in channels_by_sid.items():
            self._online.setdefault(sid, True)
            h = self._health.setdefault(sid, {})
            for ch in channels:
                h.setdefault(ch, ChannelState.OK)

    # ------------------------------------------------------------ reads (snapshot phase ONLY)
    def online(self, sid: str) -> bool:
        return self._online.get(sid, True)

    def channel_health(self, sid: str) -> dict[str, ChannelState]:
        return dict(self._health.get(sid, {}))

    # ---------------------------------------------------------- writes (event source)
    def set_online(self, sid: str, online: bool) -> None:
        prev = self._online.get(sid, True)
        self._online[sid] = online
        if prev != online:
            self._notify(ChangeNotice(
                event=EVENT_PRESENCE, stakeholder_id=sid, payload={"online": online}))

    def set_channel_health(self, sid: str, channel: str, state: str) -> None:
        h = self._health.setdefault(sid, {})
        state_enum = ChannelState(state)
        if h.get(channel) != state_enum:
            h[channel] = state_enum
            self._notify(ChangeNotice(
                event=EVENT_CHANNEL, stakeholder_id=sid,
                payload={"channel": channel, "state": state_enum.value}))

    # ------------------------------------------------------------ subscription
    def subscribe(self, cb: Subscriber) -> None:
        self._subscribers.append(cb)

    def _notify(self, notice: ChangeNotice) -> None:
        for cb in list(self._subscribers):
            cb(notice)


def mark_candidate_available(presence: Presence, sid: str) -> None:
    """Fire a candidate.available event (used by scenario driver for clarity)."""
    for cb in list(presence._subscribers):  # noqa: SLF001
        cb(ChangeNotice(event=EVENT_CANDIDATE, stakeholder_id=sid, payload={"online": True}))
