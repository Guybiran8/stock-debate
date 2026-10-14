import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def _require(name):
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} is not set. Add it to your .env file.")
    return val.strip()


claude = anthropic.Anthropic(api_key=_require("ANTHROPIC_API_KEY"), timeout=300, max_retries=1)
chatgpt = OpenAI(api_key=_require("OPENAI_API_KEY"), timeout=300, max_retries=1)
grok = OpenAI(
    api_key=_require("XAI_API_KEY"),
    base_url="https://api.x.ai/v1",
    timeout=300,
    max_retries=1,
)
POLY_KEY = _require("POLYGON_API_KEY")

CLAUDE_MODEL = "claude-sonnet-5-5"
OPENAI_MODEL = "gpt-6-luna"
GROK_MODEL = "grok-4.7"

# How hard Grok thinks before judging: "low", "medium", "high" (xAI's default) or "xhigh".
# Lower is much faster. Only the two judge calls use this.
JUDGE_REASONING_EFFORT = "low"

# Keeps research from earlier runs so the same stock is not researched twice in one day.
CACHE_FILE = Path(__file__).parent / "research_cache.json"


def load_research_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_research_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")


RESEARCH_CACHE = load_research_cache()
