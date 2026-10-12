"""`python -m app.ingest` — rebuild the document index and show what was parsed."""
from .documents import write_corpus

if __name__ == "__main__":
    sections = write_corpus()
    docs = {s.doc_id for s in sections}
    print(f"Indexed {len(sections)} sections from {len(docs)} documents\n")
    for s in sections:
        print(f"  tier {s.tier} {s.status:<10} {s.section_id:<30} {s.heading[:52]:<54} {len(s.text):>5} chars")
