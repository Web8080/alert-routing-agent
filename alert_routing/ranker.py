# author: Victor Ibhafidon
# date: 2026-08-14
"""Qualification ranking.

The qualification score is the ONLY ordering key. Availability and on-call are
gates applied later (snapshotter/planner); they never reorder candidates. This is
the structural guarantee behind the no-downgrade requirement.
"""

from __future__ import annotations

from typing import Optional

from .models import Alert, Stakeholder

_SENIORITY_STEP = 0.15


def seniority_weight(seniority: int) -> float:
    return 1.0 + (seniority - 1) * _SENIORITY_STEP


def qualification(stakeholder: Stakeholder, domain: str) -> float:
    return stakeholder.expertise.get(domain, 0) * seniority_weight(stakeholder.seniority)


def rank(
    alert: Alert,
    stakeholders: dict[str, Stakeholder],
) -> list[tuple[Stakeholder, float]]:
    """Return all stakeholders sorted by qualification desc (stable, id tie-break)."""
    scored = [(s, qualification(s, alert.domain)) for s in stakeholders.values()]
    return sorted(scored, key=lambda t: (-t[1], t[0].id))
