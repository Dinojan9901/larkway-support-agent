"""PDF -> section corpus.

PDF extraction breaks prose across lines, so normalisation is the first job.
The second job is attaching *authority metadata* to every section: the retrieval
layer ranks on authority and freshness, not similarity alone, so that a strong
lexical match inside a DEPRECATED policy can never quietly win an answer.

Run `python -m app.ingest` to rebuild `data/index/corpus.json`.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from pypdf import PdfReader

from ..config import CORPUS_PATH, INDEX_DIR, SOURCE_DOCS_DIR

# --- Authority model -------------------------------------------------------
# Straight out of the support policy, s1: agreement first, then policy and SOPs,
# then product documentation; past tickets and notes are background only.
TIER_AGREEMENT = 1
TIER_POLICY = 2
TIER_PRODUCT_DOC = 3
TIER_HISTORICAL = 4

TIER_LABELS = {
    TIER_AGREEMENT: "Signed customer agreement",
    TIER_POLICY: "Current policy / SOP",
    TIER_PRODUCT_DOC: "Current product documentation",
    TIER_HISTORICAL: "Historical context (not authoritative)",
}

# Registry of what each supplied file *is*. This is metadata about the pack, not
# an encoding of any answer: content is always read from the PDF itself.
DOC_REGISTRY: dict[str, dict] = {
    "01_Support_Policy_v5_CURRENT.pdf": {
        "doc_id": "support_policy_v5",
        "title": "Larkway Support Policy v5",
        "doc_type": "policy",
        "tier": TIER_POLICY,
        "status": "CURRENT",
        "effective": "2026-07-01",
        "visibility": "public",
        "topics": ["sla", "severity", "escalation", "source_precedence"],
    },
    "02_Support_Policy_v4_DEPRECATED.pdf": {
        "doc_id": "support_policy_v4",
        "title": "Larkway Support Policy v4",
        "doc_type": "policy",
        "tier": TIER_POLICY,
        "status": "DEPRECATED",
        "effective": "2025-03-01",
        "superseded_by": "support_policy_v5",
        "visibility": "public",
        "topics": ["sla", "severity"],
    },
    "03_Cancellation_and_Service_Credit_SOP_v3.pdf": {
        "doc_id": "cancellation_credit_sop_v3",
        "title": "Larkway Cancellation & Service Credit SOP v3",
        "doc_type": "sop",
        "tier": TIER_POLICY,
        "status": "CURRENT",
        "effective": "2026-08-01",
        "visibility": "public",
        "topics": ["cancellation", "service_credit", "approval"],
    },
    "04_Product_Operations_Guide.pdf": {
        "doc_id": "product_ops_guide",
        "title": "Larkway Product Operations Guide",
        "doc_type": "product_doc",
        "tier": TIER_PRODUCT_DOC,
        "status": "CURRENT",
        "effective": "2026-09-15",
        "visibility": "public",
        "topics": ["plan_capabilities", "known_issues", "shipment_status"],
    },
    "05_Halcyon_Foods_Enterprise_Agreement.pdf": {
        "doc_id": "agreement_cus_101",
        "title": "Halcyon Foods Enterprise Agreement",
        "doc_type": "agreement",
        "tier": TIER_AGREEMENT,
        "status": "ACTIVE",
        "effective": "2026-02-01",
        "account_id": "CUS-101",
        "visibility": "account:CUS-101",
        "topics": ["sla", "cancellation", "service_credit"],
    },
    "06_Brightmoor_Textiles_Service_Agreement.pdf": {
        "doc_id": "agreement_cus_102",
        "title": "Brightmoor Textiles Service Agreement",
        "doc_type": "agreement",
        "tier": TIER_AGREEMENT,
        "status": "ACTIVE",
        "effective": "2026-04-01",
        "account_id": "CUS-102",
        "visibility": "account:CUS-102",
        "topics": ["sla", "cancellation", "service_credit"],
    },
}

# Section numbers are 1-2 digits; the bound stops a stray "2026. Do not..." from
# being mistaken for a heading.
_SECTION_SPLIT = re.compile(r"(?m)^(?=(?:\d{1,2}\.\s+[A-Z]|KI-\d+))")
# A heading runs until the next capital letter that starts the body sentence, or
# until end of line for headings that sit alone above a bullet list.
_HEADING = re.compile(r"^(\d{1,2}\.\s+[A-Z][a-z][\w&/,\-' ]{1,58}?)(?=\s+[A-Z]|\s*[•:]|\s*$)")


@dataclass
class Section:
    section_id: str
    doc_id: str
    doc_title: str
    doc_type: str
    heading: str
    text: str
    tier: int
    tier_label: str
    status: str
    effective: str
    visibility: str
    account_id: str | None
    topics: list[str]
    superseded_by: str | None
    source_file: str

    @property
    def citation(self) -> str:
        return f"{self.doc_title} — {self.heading}"


def normalise(raw: str) -> str:
    """PDF extraction breaks prose across lines; rebuild readable paragraphs."""
    text = raw.replace(" ", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]*\n[ \t]*", " ", text)  # join the token-per-line layout
    text = re.sub(r"[ \t]+", " ", text)
    text = text.replace("●", "\n• ")  # bullets onto their own lines
    # Numbered headings onto their own line so section splitting can find them.
    text = re.sub(r"(?<=[\w.):•])\s+(?=\d{1,2}\.\s+[A-Z][a-z])", "\n", text)
    # Known-issue / resolved-issue blocks behave as headings too.
    text = re.sub(r"\s+(?=KI-\d+\s*-)", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _heading_of(chunk: str) -> tuple[str, str]:
    first, _, rest = chunk.partition("\n")
    first = first.strip()
    match = _HEADING.match(first)
    if match:
        heading = match.group(1).strip()
        body = (first[match.end():].strip() + "\n" + rest).strip()
        return heading, body or heading
    if first.startswith("KI-"):
        heading = first.split("Opened")[0].strip().rstrip(":")[:70]
        return heading, chunk
    return "Document header", chunk


def split_sections(text: str) -> list[tuple[str, str]]:
    """Return (heading, body) pairs; text before the first heading becomes Overview."""
    chunks = [c.strip() for c in _SECTION_SPLIT.split(text) if c.strip()]
    if not chunks:
        return [("Document header", text)]
    return [_heading_of(chunk) for chunk in chunks]


def build_corpus() -> list[Section]:
    sections: list[Section] = []
    for filename, meta in DOC_REGISTRY.items():
        path = SOURCE_DOCS_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing source document: {path}")
        reader = PdfReader(str(path))
        text = normalise("\n".join(page.extract_text() or "" for page in reader.pages))
        for idx, (heading, body) in enumerate(split_sections(text), start=1):
            sections.append(
                Section(
                    section_id=f"{meta['doc_id']}#s{idx}",
                    doc_id=meta["doc_id"],
                    doc_title=meta["title"],
                    doc_type=meta["doc_type"],
                    heading=heading,
                    text=body,
                    tier=meta["tier"],
                    tier_label=TIER_LABELS[meta["tier"]],
                    status=meta["status"],
                    effective=meta["effective"],
                    visibility=meta["visibility"],
                    account_id=meta.get("account_id"),
                    topics=list(meta["topics"]),
                    superseded_by=meta.get("superseded_by"),
                    source_file=filename,
                )
            )
    return sections


def write_corpus() -> list[Section]:
    sections = build_corpus()
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_text(
        json.dumps([asdict(s) for s in sections], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return sections


def load_corpus(rebuild: bool = False) -> list[Section]:
    if rebuild or not CORPUS_PATH.exists():
        return write_corpus()
    raw = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return [Section(**item) for item in raw]


def full_text(doc_id: str, sections: list[Section]) -> str:
    return " ".join(s.text for s in sections if s.doc_id == doc_id)
