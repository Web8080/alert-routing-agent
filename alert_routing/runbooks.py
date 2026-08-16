# author: Victor Ibhafidon
# date: 2026-08-15
"""Deterministic runbook retrieval (post-decision only).

A tiny, zero-dependency "RAG-shaped" slice: we score runbook documents against
the ALERT METADATA (metric + domain primary, severity as a tie-breaker),
deterministically, and attach the best runbook's step-list to the post-incident
summary. The routing decision never touches runbooks — retrieval happens AFTER
the decision and only feeds the human-facing summary. No embeddings, no LLM in
the loop, so determinism is preserved even when the AI summary layer is on.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Sequence

_RUNBOOK_DIR = Path(__file__).resolve().parents[1] / "runbooks"
_WORD = re.compile(r"[a-z0-9_]+")


def _terms(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _score(alert, doc: str) -> int:
    """Deterministic overlap score between alert metadata and a runbook doc.

    Metric + domain terms are the PRIMARY signal; severity only breaks ties
    among domain-matching docs. Severity can never win a retrieval on its own —
    otherwise a runbook that merely mentions "high" would out-rank the correct
    domain runbook.
    """
    query = _terms(f"{alert.metric} {alert.domain}")
    doc_terms = _terms(doc)
    matches = len(query & doc_terms)
    if matches == 0:
        return 0
    severity = 1 if _terms(alert.severity) & doc_terms else 0
    return matches * 10 + severity


def retrieve(alert, docs: Optional[Sequence[Path]] = None) -> Optional[str]:
    """Return the top runbook markdown, or None if no runbook matches.

    `docs` is injectable for tests; default is the repo's `runbooks/` dir.
    """
    paths = list(docs) if docs is not None else sorted(_RUNBOOK_DIR.glob("*.md"))
    best, best_score = None, 0
    generic = None
    for path in paths:
        try:
            text = path.read_text()
        except OSError:
            continue
        if "generic" in path.name.lower():
            generic = text
        score = _score(alert, text)
        if score > best_score:
            best, best_score = text, score
    return best or generic


def runbook_snippet(alert, docs: Optional[Sequence[Path]] = None,
                    max_chars: int = 600) -> str:
    """Deterministic, trimmed runbook context for a summary prompt."""
    doc = retrieve(alert, docs)
    if not doc:
        return ""
    lines = [ln for ln in doc.splitlines() if ln.strip()]
    title = lines[0].lstrip("#").strip() if lines else "runbook"
    body = "\n".join(lines[1:]).strip()
    if len(body) > max_chars:
        body = body[:max_chars].rsplit("\n", 1)[0] + "\n…"
    return f"runbook: {title}\n{body}"
