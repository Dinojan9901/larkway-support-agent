"""Severity classification against the support policy's own definitions.

Rule-based on purpose. Severity drives the SLA clock and the escalation path, so it
has to be explainable and stable — an ops lead must be able to read why a ticket was
called P1 and disagree with a specific rule, not with a vibe. Every rule names the
clause it implements and the phrase that matched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .pack import Citation, cite, policy


@dataclass
class SeverityCall:
    severity: str
    label: str
    rationale: str
    matched_signals: list[str] = field(default_factory=list)
    confidence: str = "high"
    citation: Citation | None = None
    alternatives: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "label": self.label,
            "rationale": self.rationale,
            "matched_signals": self.matched_signals,
            "confidence": self.confidence,
            "citation": self.citation.as_dict() if self.citation else None,
            "considered_instead": self.alternatives,
        }


# Ordered: the first rule that fires wins, so P1 rules come first.
RULES: list[dict[str, Any]] = [
    {
        "severity": "P1",
        "why": "confirmed security incident or suspected credential exposure",
        "any": [
            r"\bapi[ -]?key\b", r"\bcredential", r"\bsecret\b", r"\btoken\b.*\bexpos",
            r"\bexpos(ed|ure)\b", r"\bleak(ed)?\b", r"\bbreach\b", r"security incident",
        ],
    },
    {
        "severity": "P1",
        "why": "complete production outage preventing all shipment creation",
        "all": [
            r"\ball\b|\bevery\b|\bcomplete\b|\bentire\b|\bcannot\b|\bunable\b",
            r"shipment creation|creating (any |a )?shipment|create (any |a )?shipment|book(ing)? (any )?shipment",
        ],
        "any": [r"\bfail(s|ing|ed)?\b", r"\b5\d\d\b", r"\berror\b", r"\bdown\b", r"\boutage\b", r"\bcannot\b", r"\bunable\b"],
    },
    {
        "severity": "P2",
        "why": "major feature degraded but core operations still possible",
        "any": [
            r"bulk upload", r"\bcsv\b", r"\bapi\b.*\bslow\b", r"\bintegration\b.*\bfail",
            r"report(s|ing)? (are |is )?(not|un)available",
        ],
    },
    {
        "severity": "P3",
        "why": "how-to question or configuration request with limited operational impact",
        "any": [
            r"\bhow do (we|i)\b", r"\bhow to\b", r"\bchange the\b", r"\bupdate the\b",
            r"billing contact", r"\bconfigur", r"\brename\b", r"\bquestion\b",
            r"asked whether", r"\bcancellation fee\b",
        ],
    },
    {
        "severity": "P3",
        "why": "minor defect with limited operational impact (display or status lag)",
        "any": [
            r"still shows", r"not updat(ing|ed)", r"shows? (the )?wrong",
            r"status .* (incorrect|stale|wrong)", r"\bdisplay(s|ed)? incorrectly\b",
        ],
    },
]

# A workaround existing is the policy's own dividing line between P1 and P2.
WORKAROUND_SIGNALS = [
    r"one[- ]by[- ]one still works", r"still works", r"workaround", r"unaffected",
    r"can still", r"individual [^.]{0,40}(works|unaffected)",
]
# ...but read-only access is not a workaround for being unable to do the job.
READ_ONLY_WORDS = re.compile(r"view|read|see|display|browse", re.I)


def _hits(patterns: list[str], text: str) -> list[str]:
    out = []
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            out.append(m.group(0).strip())
    return out


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in re.split(r"[.;]\s*", text) if c.strip()]


def _real_workaround(text: str) -> str | None:
    """Return the clause describing a genuine workaround, if there is one.

    Judged clause-by-clause: 'Existing shipments can still be viewed' sits in a
    read-only clause and does not rescue a customer who cannot create shipments,
    whereas 'Creating shipments one-by-one still works' does.
    """
    for clause in _clauses(text):
        if not _hits(WORKAROUND_SIGNALS, clause):
            continue
        if READ_ONLY_WORDS.search(clause):
            continue
        return clause
    return None


def classify(subject: str, description: str = "", carrier_hint: str | None = None) -> SeverityCall:
    text = f"{subject or ''} {description or ''}".strip()
    defs = policy()["severity_definitions"]
    section = defs["section"]

    for rule in RULES:
        all_ok = True
        signals: list[str] = []
        if rule.get("all"):
            for pattern in rule["all"]:
                hit = _hits([pattern], text)
                if not hit:
                    all_ok = False
                    break
                signals += hit
        if not all_ok:
            continue
        any_hits = _hits(rule.get("any", []), text) if rule.get("any") else ["(structural match)"]
        if not any_hits:
            continue
        signals += any_hits

        severity = rule["severity"]
        alternatives: list[str] = []
        confidence = "high"
        rationale = f"Matches the support policy's {severity} definition: {rule['why']}."

        # Support policy s2: a usable workaround is the dividing line between P1 and P2.
        if severity == "P1" and "outage" in rule["why"]:
            workaround = _real_workaround(text)
            if workaround:
                alternatives.append(f"P1, before the reported workaround: \"{workaround}\"")
                severity = "P2"
                confidence = "medium"
                rationale = (
                    f"Reported as a full outage, but downgraded to P2 because a workaround "
                    f"was reported (\"{workaround}\") and the support policy (s2) puts a "
                    f"degraded-with-workaround case at P2."
                )

        return SeverityCall(
            severity=severity,
            label={"P1": "Critical", "P2": "High", "P3": "Normal"}[severity],
            rationale=rationale,
            matched_signals=sorted(set(s.lower() for s in signals if s)),
            confidence=confidence,
            citation=cite(policy()["doc_id"], section, defs["levels"][severity]["quote"]),
            alternatives=alternatives,
        )

    return SeverityCall(
        severity="P3",
        label="Normal",
        rationale=(
            "No P1 or P2 signal matched, so the support policy's default of P3 "
            "(minor defect / limited operational impact) applies. Worth a human check."
        ),
        matched_signals=[],
        confidence="low",
        citation=cite(policy()["doc_id"], section, defs["levels"]["P3"]["quote"]),
    )


def classify_ticket(ticket: dict[str, Any]) -> SeverityCall:
    return classify(ticket.get("subject", ""), ticket.get("description", ""))
