"""Section-level topic tagging.

Document-level topics are too coarse to decide whether two passages actually
disagree — every clause in an agreement would "share a topic" with every clause in
the SOP. These tags are derived from the section text itself, so conflict detection
only fires when two sources speak to the *same* thing.
"""
from __future__ import annotations

import re

TOPIC_PATTERNS: dict[str, list[str]] = {
    "sla": [r"first[- ]response", r"response target", r"\bp1\b.*\bp2\b", r"\bminutes?, 24x7",
            r"business hours", r"response targets"],
    "severity": [r"severity", r"critical", r"\bp1 -", r"\bp2 -", r"\bp3 -"],
    "cancellation": [r"cancel", r"cancellation"],
    "service_credit": [r"service credit", r"credit", r"failed[- ]pickup"],
    "escalation": [r"escalat"],
    "approval": [r"manager approval", r"requires approval"],
    "precedence": [r"sources conflict", r"supersede", r"precedence", r"context only"],
    "plan_capability": [r"bulk upload", r"plan capabilities", r"rows per csv"],
    "known_issue": [r"\bki-\d+", r"workaround", r"known issue"],
    "shipment_status": [r"\bbooked\b", r"picked_up", r"delivered", r"pickup confirmation"],
}


def topics_of(text: str) -> set[str]:
    lowered = (text or "").lower()
    return {
        topic
        for topic, patterns in TOPIC_PATTERNS.items()
        if any(re.search(p, lowered) for p in patterns)
    }


def query_topics(query: str) -> set[str]:
    return topics_of(query)
