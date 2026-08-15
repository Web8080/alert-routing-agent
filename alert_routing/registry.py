# author: Victor Ibhafidon
# date: 2026-08-14
"""Stakeholder registry loader (JSON, read-only at dispatch time)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from .models import CHANNELS, ChannelPref, Stakeholder


class RegistryValidationError(ValueError):
    pass


def _load_path(path: Union[str, Path]) -> Path:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"registry not found: {p}")
    return p


def parse_stakeholder(item: dict) -> Stakeholder:
    """Validate + normalize one registry entry. Raises RegistryValidationError."""
    sid = item.get("id")
    if not sid:
        raise RegistryValidationError("stakeholder missing 'id'")
    name = item.get("name")
    if not name:
        raise RegistryValidationError(f"{sid}: missing 'name'")
    if not isinstance(item.get("seniority"), int) or not 1 <= item["seniority"] <= 5:
        raise RegistryValidationError(f"{sid}: 'seniority' must be int 1..5")
    expertise = item.get("expertise") or {}
    if not isinstance(expertise, dict):
        raise RegistryValidationError(f"{sid}: 'expertise' must be an object")
    for dom, prof in expertise.items():
        if not isinstance(prof, int) or not 1 <= prof <= 5:
            raise RegistryValidationError(f"{sid}: expertise[{dom}] must be int 1..5")

    channels: list[ChannelPref] = []
    for pref in item.get("channels") or []:
        ch = pref.get("name")
        if ch not in CHANNELS:
            raise RegistryValidationError(f"{sid}: unknown channel {ch!r}")
        priority = pref.get("priority")
        endpoint = pref.get("endpoint")
        if not isinstance(priority, int) or priority < 1:
            raise RegistryValidationError(f"{sid}: channel {ch} priority must be int >= 1")
        if not endpoint:
            raise RegistryValidationError(f"{sid}: channel {ch} missing endpoint")
        channels.append(ChannelPref(name=ch, priority=priority, endpoint=endpoint))
    if not channels:
        raise RegistryValidationError(f"{sid}: must declare at least one channel")

    return Stakeholder(
        id=sid,
        name=name,
        title=item.get("title", ""),
        seniority=item["seniority"],
        expertise=expertise,
        on_call=bool(item.get("on_call", False)),
        channels=channels,
    )


def stakeholder_to_dict(st: Stakeholder) -> dict:
    """Inverse of parse_stakeholder — used when persisting edits."""
    return {
        "id": st.id,
        "name": st.name,
        "title": st.title,
        "seniority": st.seniority,
        "expertise": dict(st.expertise),
        "on_call": bool(st.on_call),
        "channels": [{"name": c.name, "priority": c.priority, "endpoint": c.endpoint}
                     for c in st.channels],
    }


def save_registry(path: Union[str, Path], stakeholders: dict[str, Stakeholder]) -> None:
    payload = {"stakeholders": [stakeholder_to_dict(s)
                                for s in sorted(stakeholders.values(), key=lambda s: s.id)]}
    Path(path).write_text(json.dumps(payload, indent=2) + "\n")


def load_registry(path: Union[str, Path]) -> dict[str, Stakeholder]:
    raw = json.loads(_load_path(path).read_text())
    stakeholders = raw.get("stakeholders", raw if isinstance(raw, list) else [])
    out: dict[str, Stakeholder] = {}
    for item in stakeholders:
        st = parse_stakeholder(item)
        out[st.id] = st
    if not out:
        raise RegistryValidationError("registry contains no stakeholders")
    return out
