"""Central configuration. Everything tunable lives here so nothing is buried in code."""
from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SOURCE_DOCS_DIR = DATA_DIR / "source_docs"
POLICY_PACK_DIR = DATA_DIR / "policy_pack"
INDEX_DIR = DATA_DIR / "index"
# Serverless hosts (Vercel) mount the code read-only and allow writes only under
# /tmp, so runtime state goes there. It is per-instance and lost on a cold start,
# which is acceptable for a demo; a real deployment would use a database.
RUNTIME_DIR = Path(
    os.getenv("PP_RUNTIME_DIR")
    or ("/tmp/larkway" if os.getenv("VERCEL") else DATA_DIR / "runtime")
)
WORKBOOK_PATH = DATA_DIR / "larkway_dataset.xlsx"
CORPUS_PATH = INDEX_DIR / "corpus.json"
ACTION_LOG_PATH = RUNTIME_DIR / "actions.json"
AUDIT_LOG_PATH = RUNTIME_DIR / "audit.jsonl"

# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
# The workbook README fixes the reference time for every time-based question.
# We read it from the workbook at load time; this is the fallback/expected value.
IST = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
FALLBACK_SNAPSHOT = "2026-09-19 14:00"

# ASSUMPTION (not stated in the documents): Larkway support business hours.
# Documented in ARCHITECTURE.md; surfaced in every SLA answer that depends on it.
BUSINESS_HOURS_START = 9      # 09:00 IST
BUSINESS_HOURS_END = 18       # 18:00 IST
BUSINESS_DAYS = (0, 1, 2, 3, 4)  # Mon-Fri (Python weekday numbering)

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
# Two backends, one loop:
#   anthropic      Claude via the Anthropic SDK (paid API)
#   openai_compat  any OpenAI-compatible endpoint — Gemini's free tier, Groq,
#                  OpenRouter, or a local Ollama — selected by PP_BASE_URL
PROVIDER = os.getenv("PP_PROVIDER", "anthropic").strip().lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
COMPAT_BASE_URL = os.getenv("PP_BASE_URL", "").strip()
COMPAT_API_KEY = os.getenv("PP_API_KEY", "").strip()
# Tried in order when the main model is rate-limited (openai_compat only).
COMPAT_FALLBACK_MODELS = [
    m.strip() for m in os.getenv("PP_FALLBACK_MODELS", "").split(",") if m.strip()
]

_DEFAULT_MODEL = {"anthropic": "claude-opus-5", "openai_compat": "gemini-3.8-flash"}
MODEL = os.getenv("PP_MODEL", "").strip() or _DEFAULT_MODEL.get(PROVIDER, "")
EFFORT = os.getenv("PP_EFFORT", "high")

if PROVIDER == "anthropic":
    CHAT_ENABLED = bool(ANTHROPIC_API_KEY)
    CHAT_DISABLED_REASON = "ANTHROPIC_API_KEY is not set"
elif PROVIDER == "openai_compat":
    # A local Ollama needs no key, so only the endpoint is mandatory.
    CHAT_ENABLED = bool(COMPAT_BASE_URL)
    CHAT_DISABLED_REASON = "PP_BASE_URL is not set"
else:
    CHAT_ENABLED = False
    CHAT_DISABLED_REASON = f"PP_PROVIDER={PROVIDER!r} is not a known provider"
MAX_TOKENS = int(os.getenv("PP_MAX_TOKENS", "8000"))
MAX_AGENT_STEPS = int(os.getenv("PP_MAX_AGENT_STEPS", "10"))
REFUSAL_FALLBACK = os.getenv("PP_REFUSAL_FALLBACK", "true").lower() in {"1", "true", "yes"}
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"

HOST = os.getenv("PP_HOST", "0.0.0.0")
PORT = int(os.getenv("PP_PORT", "8000"))

CURRENCY = "INR"

for _d in (INDEX_DIR, RUNTIME_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # read-only deploy; the index ships prebuilt in data/index/
