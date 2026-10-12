"""Authority-aware document retrieval.

BM25 over the ingested sections, then a re-rank that folds in *authority* and
*freshness*. That second stage is the point of this module: with six one-page
documents, plain similarity is easy and useless — the hard part is that a strongly
matching passage in the DEPRECATED v2 policy must never outrank the current one.

Rules implemented here:
  * DEPRECATED sources are excluded from the answer set by default. They are still
    searchable via `include_superseded`, and the searcher reports when a superseded
    document matched, so a conflict can be named rather than silently dropped.
  * Agreements outrank policy outranks product docs (support policy, s1).
  * An agreement belonging to another account is invisible, not down-ranked — that
    filter is access control, applied before scoring.

BM25 is hand-rolled (~40 lines) rather than pulled in as a dependency: the corpus is
26 sections, and a transparent scorer is easier to defend than an opaque one.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ..access import Principal
from ..ingest.documents import Section, load_corpus

_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-_%]*")

# Domain synonyms: a customer says "refund", the SOP says "service credit".
SYNONYMS: dict[str, list[str]] = {
    "refund": ["credit", "service", "compensation"],
    "compensation": ["credit", "service"],
    "sla": ["response", "target", "first-response"],
    "response": ["sla", "target"],
    "late": ["delay", "delayed", "past", "missed"],
    "cancel": ["cancellation", "cancelled"],
    "cancellation": ["cancel", "cancelled", "fee"],
    "fee": ["charge", "cancellation"],
    "escalate": ["escalation"],
    "outage": ["down", "failure", "critical"],
    "priority": ["severity", "p1", "p2", "p3"],
    "severity": ["priority", "p1", "p2", "p3"],
    "upload": ["bulk", "csv", "rows"],
    "csv": ["bulk", "upload", "rows"],
    "pickup": ["collect", "collected", "window"],
    "contract": ["agreement"],
    "agreement": ["contract"],
}

K1 = 1.4
B = 0.72

# Multipliers applied after BM25. Tuned so authority breaks near-ties but cannot
# manufacture a match out of an irrelevant section.
TIER_WEIGHT = {1: 1.35, 2: 1.15, 3: 1.0, 4: 0.6}
STATUS_WEIGHT = {"CURRENT": 1.0, "ACTIVE": 1.0, "DEPRECATED": 0.25}


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def expand(tokens: list[str]) -> list[str]:
    out = list(tokens)
    for token in tokens:
        out.extend(SYNONYMS.get(token, []))
    return out


@dataclass
class Hit:
    section: Section
    score: float
    bm25: float
    reason: str

    def as_dict(self, snippet_chars: int = 700) -> dict[str, Any]:
        s = self.section
        return {
            "section_id": s.section_id,
            "document": s.doc_title,
            "section": s.heading,
            "text": s.text[:snippet_chars],
            "authority_tier": s.tier,
            "authority": s.tier_label,
            "status": s.status,
            "effective": s.effective,
            "doc_type": s.doc_type,
            "account_id": s.account_id,
            "relevance": round(self.score, 3),
            "ranking_note": self.reason,
        }


class SectionIndex:
    def __init__(self, sections: list[Section]):
        self.sections = sections
        self.docs = [expand(tokenize(f"{s.heading} {s.text}")) for s in sections]
        self.lengths = [len(d) for d in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        self.freq: list[dict[str, int]] = []
        self.df: dict[str, int] = {}
        for doc in self.docs:
            counts: dict[str, int] = {}
            for token in doc:
                counts[token] = counts.get(token, 0) + 1
            self.freq.append(counts)
            for token in counts:
                self.df[token] = self.df.get(token, 0) + 1
        self.n = len(sections)

    def _bm25(self, query_tokens: list[str], i: int) -> float:
        score = 0.0
        counts = self.freq[i]
        length = self.lengths[i] or 1
        for token in query_tokens:
            tf = counts.get(token, 0)
            if not tf:
                continue
            df = self.df.get(token, 0)
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            denom = tf + K1 * (1 - B + B * length / (self.avg_len or 1))
            score += idf * (tf * (K1 + 1)) / denom
        return score

    def search(
        self,
        principal: Principal,
        query: str,
        top_k: int = 5,
        doc_types: list[str] | None = None,
        include_superseded: bool = False,
    ) -> tuple[list[Hit], list[Hit]]:
        """Return (visible ranked hits, superseded hits that also matched)."""
        tokens = expand(tokenize(query))
        if not tokens:
            return [], []

        primary: list[Hit] = []
        superseded: list[Hit] = []
        for i, section in enumerate(self.sections):
            # Access control runs before scoring, never as a ranking penalty.
            if not principal.can_see_document(section.visibility):
                continue
            if doc_types and section.doc_type not in doc_types:
                continue
            raw = self._bm25(tokens, i)
            if raw <= 0:
                continue

            is_stale = section.status == "DEPRECATED"
            weight = TIER_WEIGHT.get(section.tier, 1.0) * STATUS_WEIGHT.get(section.status, 1.0)
            reasons = [f"tier {section.tier} ({section.tier_label})"]
            if is_stale:
                reasons.append(f"DEPRECATED, superseded by {section.superseded_by}")
            if section.account_id:
                reasons.append(f"specific to {section.account_id}")
            hit = Hit(section, raw * weight, raw, "; ".join(reasons))

            if is_stale and not include_superseded:
                superseded.append(hit)
            else:
                primary.append(hit)

        primary.sort(key=lambda h: (-h.score, h.section.tier))
        superseded.sort(key=lambda h: -h.score)

        # Contract-first guarantee. Because a signed agreement can override anything
        # below it, an agreement section that matched at all must reach the answer
        # set — a short, lexically dense policy paragraph should not be able to crowd
        # out the clause that actually governs this customer.
        top = primary[:top_k]
        best_agreement = next((h for h in primary if h.section.tier == 1), None)
        if best_agreement and best_agreement not in top:
            best_agreement.reason += "; promoted (signed agreement outranks general policy)"
            top = [best_agreement] + top[: max(0, top_k - 1)]
        return top, superseded[:3]


@lru_cache(maxsize=1)
def get_index() -> SectionIndex:
    return SectionIndex(load_corpus())


def reset_index() -> None:
    get_index.cache_clear()
