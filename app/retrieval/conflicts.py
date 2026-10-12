"""Conflict detection across retrieved sources.

Retrieval that just returns the best passages hides disagreement. This module looks
at what came back and names the disagreements explicitly, so the answer can say
"these two sources disagree, here is which one governs and why" instead of silently
picking one.

Three kinds of conflict are detected:
  1. superseded  — a DEPRECATED document matched the same question as a current one.
  2. precedence  — an agreement and a general policy both speak to the topic.
  3. historical  — a past ticket resolution contradicts a current authoritative source.
"""
from __future__ import annotations

import re
from typing import Any

from ..ingest.documents import TIER_AGREEMENT, TIER_POLICY
from .index import Hit
from .topics import topics_of

_NUMBER = re.compile(r"\b(?:INR\s?)?\d[\d,]*(?:\s?(?:minutes?|hours?|business hours?|business days?|days?|rows?|%))?\b", re.I)


def _numbers(text: str) -> set[str]:
    return {m.group(0).strip().lower() for m in _NUMBER.finditer(text or "")}


def detect(
    hits: list[Hit],
    superseded: list[Hit],
    historical_notes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []

    # 1. A superseded document answered the same question.
    for stale in superseded:
        overlap = _numbers(stale.section.text)
        stale_topics = topics_of(stale.section.text)
        competing = [h for h in hits if h.section.status in {"CURRENT", "ACTIVE"}]
        differing = [
            h for h in competing
            if overlap
            and _numbers(h.section.text)
            and overlap != _numbers(h.section.text)
            # Only a real conflict if both passages speak to the same topic.
            and (topics_of(h.section.text) & stale_topics)
        ]
        if differing:
            winner = min(differing, key=lambda h: h.section.tier)
            conflicts.append(
                {
                    "type": "superseded_document",
                    "topic": stale.section.heading,
                    "losing_source": f"{stale.section.doc_title} ({stale.section.status})",
                    "winning_source": f"{winner.section.doc_title} — {winner.section.heading}",
                    "resolution": (
                        f"{stale.section.doc_title} is superseded by "
                        f"{stale.section.superseded_by}; its figures were not used."
                    ),
                }
            )

    # 2. An agreement and a general policy both speak to the topic.
    agreements = [h for h in hits if h.section.tier == TIER_AGREEMENT]
    policies = [h for h in hits if h.section.tier == TIER_POLICY]
    for ag in agreements:
        ag_topics = topics_of(ag.section.text)
        for pol in policies:
            shared = ag_topics & topics_of(pol.section.text)
            # Ignore the generic tags that almost every clause carries.
            shared -= {"shipment_status", "precedence"}
            if not shared:
                continue
            if not (_numbers(ag.section.text) and _numbers(pol.section.text)):
                continue
            if _numbers(ag.section.text) == _numbers(pol.section.text):
                continue
            conflicts.append(
                {
                    "type": "agreement_overrides_policy",
                    "topic": ", ".join(sorted(shared)),
                    "losing_source": f"{pol.section.doc_title} — {pol.section.heading}",
                    "winning_source": f"{ag.section.doc_title} — {ag.section.heading}",
                    "resolution": (
                        "The support policy (s1) puts the signed customer agreement ahead of "
                        "the general policy, so the agreement's terms govern."
                    ),
                }
            )

    # 3. Historical ticket guidance contradicting a current source.
    for note in historical_notes or []:
        conflicts.append(note)

    # De-duplicate on the pair of sources involved.
    seen: set[tuple] = set()
    unique = []
    for c in conflicts:
        key = (c.get("type"), c.get("losing_source"), c.get("winning_source"), c.get("source"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique


def historical_contradictions(query: str, tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Closed tickets whose recorded resolution is relevant to this query.

    These are surfaced as *background only* with an explicit warning, per the support
    policy (s1) and the workbook README.
    """
    terms = {t for t in re.findall(r"[a-z]{4,}", (query or "").lower())}
    out = []
    for ticket in tickets:
        resolution = ticket.get("historical_resolution")
        if not resolution:
            continue
        haystack = f"{ticket.get('subject', '')} {ticket.get('description', '')} {resolution}".lower()
        if not terms or not any(t in haystack for t in terms):
            continue
        out.append(
            {
                "type": "historical_guidance",
                "source": f"{ticket['ticket_id']} ({ticket.get('created_at')})",
                "claim": resolution,
                "resolution": (
                    "Background only. The support policy (s1) states past tickets may "
                    "contain incorrect past guidance and never override current sources."
                ),
            }
        )
    return out
