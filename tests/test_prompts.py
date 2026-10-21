"""The system prompt must agree with the documents it points the model at."""
import re

from app.access import DEMO_PRINCIPALS
from app.agent.prompts import build_system_prompt
from app.ingest.documents import DOC_REGISTRY


def test_prompt_never_cites_a_document_version_that_does_not_exist():
    titles = " ".join(meta["title"] for meta in DOC_REGISTRY.values())
    for principal in DEMO_PRINCIPALS.values():
        prompt = build_system_prompt(principal)
        for cited in re.findall(r"(?:Support Policy|SOP) v\d+", prompt):
            assert cited in titles, f"prompt cites {cited!r}, which is not in the dataset"


def test_every_role_is_told_to_name_known_issues_by_id():
    """Caveats carry known-issue ids the customer needs to track the problem."""
    for principal in DEMO_PRINCIPALS.values():
        prompt = build_system_prompt(principal)
        assert "known issue" in prompt and "by its ID" in prompt


def test_every_role_is_told_to_evaluate_a_described_situation_first():
    """gpt-oss-120b kept asking for an order id instead of using the hypothetical path."""
    for principal in DEMO_PRINCIPALS.values():
        prompt = " ".join(build_system_prompt(principal).split())  # ignore line wrapping
        assert "evaluate it as described" in prompt
        assert "Call it even when some facts are unknown" in prompt
        # ...without discarding what the customer did say (a live regression).
        assert "Pass every fact the customer stated or clearly implied" in prompt
