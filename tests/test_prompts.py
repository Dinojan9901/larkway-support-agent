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
