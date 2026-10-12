"""Golden-scenario runner.

    python -m evals.run              # offline: deterministic layer only, no API key
    python -m evals.run --live       # also drives the real agent and checks the prose
    python -m evals.run --report     # offline plus a readable answer report

Offline mode is the regression gate: it asserts the facts every answer must rest on.
Live mode is the quality check: it asserts that those facts survive into what the
customer actually reads, and that the specific wrong answers this domain invites
(the superseded SLA figures, the waived fee charged anyway, the plan limit that is
really a bug) do not appear.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.access import get_principal  # noqa: E402
from app.agent.tools import ToolContext, run_tool  # noqa: E402
from app.config import CHAT_DISABLED_REASON, CHAT_ENABLED  # noqa: E402

SCENARIOS = Path(__file__).with_name("scenarios.yaml")

GREEN, RED, YELLOW, DIM, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)


def dig(data: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


def check_offline(scenario: dict) -> tuple[bool, list[str]]:
    spec = scenario["offline"]
    principal = get_principal(scenario["user"])
    ctx = ToolContext(principal)
    result, is_error = run_tool(ctx, spec["call"], spec.get("args", {}))
    failures: list[str] = []

    expected_error = spec.get("expect_error")
    if expected_error:
        if not is_error:
            return False, [f"expected an error containing {expected_error!r}, got a result"]
        blob = f"{result.get('error', '')} {result.get('message', '')}".lower()
        if expected_error.lower() not in blob:
            failures.append(f"error {blob.strip()!r} does not mention {expected_error!r}")
        return not failures, failures

    if is_error:
        return False, [f"unexpected error: {result.get('message') or result.get('error')}"]

    for path, wanted in (spec.get("expect") or {}).items():
        actual = dig(result, path)
        if actual != wanted:
            failures.append(f"{path}: expected {wanted!r}, got {actual!r}")

    tier = spec.get("expect_citation_tier")
    if tier is not None:
        tiers = [c.get("authority_tier") for c in result.get("citations", [])]
        if tier not in tiers:
            failures.append(f"no tier-{tier} citation (got tiers {tiers})")

    if spec.get("expect_conflict") and not result.get("conflicts"):
        failures.append("expected a conflict to be flagged, none was")

    wanted_categories = spec.get("expect_signal_categories")
    if wanted_categories:
        found = {s["category"] for s in result.get("signals", [])}
        missing = set(wanted_categories) - found
        if missing:
            failures.append(f"missing signal categories: {sorted(missing)}")

    minimum = spec.get("expect_min_signals")
    if minimum and len(result.get("signals", [])) < minimum:
        failures.append(f"expected >= {minimum} signals, got {len(result.get('signals', []))}")

    return not failures, failures


def check_live(scenario: dict) -> tuple[bool, list[str], str]:
    from app.agent.loop import Conversation

    convo = Conversation(get_principal(scenario["user"]))
    text = ""
    tools_used: list[str] = []
    for event in convo.run(scenario["question"]):
        if event["type"] == "text":
            text += event["text"]
        elif event["type"] == "tool_start":
            tools_used.append(event["tool"])
        elif event["type"] == "error":
            return False, [f"agent error: {event['message']}"], text

    spec = scenario.get("live") or {}
    lowered = _normalise(text)
    failures = []
    for phrase in spec.get("must_mention", []):
        # A list is a set of acceptable phrasings; any one of them will do.
        options = phrase if isinstance(phrase, list) else [phrase]
        if not any(_normalise(o) in lowered for o in options):
            failures.append(f"answer never mentions {' / '.join(map(repr, options))}")
    for phrase in spec.get("must_not_mention", []):
        if _normalise(phrase) in lowered:
            failures.append(f"answer contains forbidden phrase {phrase!r}")
    # Refusing up front is fine for a request the caller may not make; the tool
    # layer's own refusal is proven separately in tests/test_access.py.
    if not tools_used and not spec.get("refusal_without_tool_ok"):
        failures.append("the agent answered without using any tool")
    return not failures, failures, text


_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_SPACES = dict.fromkeys(map(ord, "     "), " ")
_QUOTES = dict.fromkeys(map(ord, "‘’ʼ"), "'")


def _normalise(text: str) -> str:
    """Compare on meaning, not typography: models emit non-breaking hyphens and
    spaces ("TKT‑501", "4 hours") that look identical on screen."""
    return text.translate(_DASHES).translate(_SPACES).translate(_QUOTES).lower()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Larkway golden scenarios.")
    parser.add_argument("--live", action="store_true", help="also drive the real agent")
    parser.add_argument("--report", action="store_true", help="print each scenario's result")
    parser.add_argument("--only", help="run one scenario by id")
    args = parser.parse_args()

    scenarios = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
    if args.only:
        scenarios = [s for s in scenarios if s["id"] == args.only]
        if not scenarios:
            print(f"No scenario with id {args.only!r}")
            return 2

    if args.live and not CHAT_ENABLED:
        print(f"{YELLOW}--live needs a model ({CHAT_DISABLED_REASON}); running offline only.{OFF}\n")
        args.live = False

    print(f"{BOLD}Larkway golden scenarios{OFF}  "
          f"({len(scenarios)} scenarios, mode: {'offline + live' if args.live else 'offline'})\n")

    passed = failed = 0
    for scenario in scenarios:
        ok, failures = check_offline(scenario)
        live_text = ""
        if ok and args.live:
            live_ok, live_failures, live_text = check_live(scenario)
            ok, failures = live_ok, failures + live_failures

        mark = f"{GREEN}PASS{OFF}" if ok else f"{RED}FAIL{OFF}"
        print(f"  {mark}  {scenario['id']}")
        if args.report or not ok:
            print(f"        {DIM}{scenario['why'].strip()}{OFF}")
            print(f"        {DIM}q: {scenario['question']}{OFF}")
        for failure in failures:
            print(f"        {RED}- {failure}{OFF}")
        if args.report and live_text:
            snippet = " ".join(live_text.split())[:400]
            print(f"        {DIM}a: {snippet}{OFF}")
        passed, failed = (passed + ok, failed + (not ok))

    print(f"\n{BOLD}{passed} passed, {failed} failed{OFF}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
