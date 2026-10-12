"""Load and verify the policy pack.

The pack is policy-as-code: the numbers the engine calculates with (fees, free
windows, credit thresholds and amounts, approval limits) live in YAML alongside the verbatim clause they
came from. `verify_pack()` re-reads the PDFs and asserts every quote is still
present, so a document change breaks the build loudly instead of producing a
confidently wrong answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from ..config import POLICY_PACK_DIR
from ..ingest.documents import load_corpus


@dataclass(frozen=True)
class Citation:
    """A pointer back to the clause that authorises a decision."""

    doc_id: str
    doc_title: str
    section: str
    quote: str
    tier: int
    status: str
    effective: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "document": self.doc_title,
            "section": self.section,
            "quote": self.quote,
            "authority_tier": self.tier,
            "status": self.status,
            "effective": self.effective,
        }


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


@lru_cache(maxsize=1)
def load_pack() -> dict[str, Any]:
    pack: dict[str, Any] = {}
    for path in sorted(POLICY_PACK_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        pack[path.stem] = data
    missing = {"support_policy", "cancellation_credit_sop", "product_ops_guide",
               "agreements"} - set(pack)
    if missing:
        raise RuntimeError(f"Policy pack incomplete, missing: {sorted(missing)}")
    return pack


def _walk_quotes(node: Any, doc_id: str | None, trail: str = "") -> list[tuple[str, str, str]]:
    """Yield (doc_id, path, quote) for every `quote`/`*_quote` key in the pack."""
    found: list[tuple[str, str, str]] = []
    if isinstance(node, dict):
        local_doc = node.get("doc_id", doc_id)
        for key, value in node.items():
            path = f"{trail}.{key}" if trail else key
            if isinstance(value, str) and (key == "quote" or key.endswith("_quote")):
                found.append((local_doc, path, value))
            else:
                found.extend(_walk_quotes(value, local_doc, path))
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            found.extend(_walk_quotes(item, doc_id, f"{trail}[{idx}]"))
    return found


def verify_pack() -> list[str]:
    """Return a list of problems; empty means every rule still matches its source."""
    corpus = load_corpus()
    by_doc: dict[str, str] = {}
    for section in corpus:
        by_doc[section.doc_id] = by_doc.get(section.doc_id, "") + " " + section.text
    by_doc = {k: _squash(v) for k, v in by_doc.items()}

    problems: list[str] = []
    pack = load_pack()

    # agreements.yaml is keyed by account, so seed doc_id per account block.
    for name, data in pack.items():
        roots = data.values() if name == "agreements" else [data]
        for root in roots:
            doc_id = root.get("doc_id") if isinstance(root, dict) else None
            for found_doc, path, quote in _walk_quotes(root, doc_id):
                target = by_doc.get(found_doc or "")
                if target is None:
                    problems.append(f"{name}:{path} -> unknown doc_id {found_doc!r}")
                    continue
                if _squash(quote) not in target:
                    problems.append(
                        f"{name}:{path} -> quote not found in {found_doc}: {_squash(quote)[:90]!r}"
                    )
    return problems


# ---------------------------------------------------------------------------
# Convenience accessors used by the engine
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _doc_meta() -> dict[str, tuple[str, int, str, str]]:
    """Citation metadata per doc_id, read from the pack itself so it cannot drift."""
    pack = load_pack()
    roots = [pack["support_policy"], pack["cancellation_credit_sop"], pack["product_ops_guide"]]
    meta = {r["doc_id"]: (r["title"], r["tier"], r["status"], r["effective"]) for r in roots}
    for ag in pack["agreements"].values():
        meta[ag["doc_id"]] = (ag["title"], ag["tier"], ag["status"], ag["term_start"])
    return meta


def cite(doc_id: str, section: str, quote: str) -> Citation:
    title, tier, status, effective = _doc_meta()[doc_id]
    return Citation(doc_id, title, section, _squash(quote), tier, status, effective)


def policy() -> dict[str, Any]:
    return load_pack()["support_policy"]


def sop() -> dict[str, Any]:
    return load_pack()["cancellation_credit_sop"]


def product() -> dict[str, Any]:
    return load_pack()["product_ops_guide"]


def agreement(account_id: str | None) -> dict[str, Any] | None:
    if not account_id:
        return None
    return load_pack()["agreements"].get(account_id.upper())
