"""Fail loudly if any policy-pack rule no longer matches its source PDF."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.policy.pack import verify_pack  # noqa: E402

if __name__ == "__main__":
    problems = verify_pack()
    if problems:
        print(f"FAIL: {len(problems)} policy-pack rule(s) no longer match their source document\n")
        for p in problems:
            print("  -", p)
        raise SystemExit(1)
    print("OK: every policy-pack quote was found verbatim in its source PDF.")
