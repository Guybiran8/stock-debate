"""Checks files staged in a git repo for secrets before they are pushed.

Looks for the real key values from .env (full value, or its first or last 12 characters)
and for common key formats. Reports where it found something, never the value itself.
"""
import re
import subprocess
from pathlib import Path

PATTERNS = {
    # Not inside a word ("risk-isnt-...") and with a 16+ character run, which keys have and URL slugs do not.
    "sk- style key": r"(?<![A-Za-z0-9_\-])sk-[A-Za-z0-9_\-]*[A-Za-z0-9_]{16}[A-Za-z0-9_\-]*",
    "xai- key": r"xai-[A-Za-z0-9_\-]{16,}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{20,}",
    "Google API key": r"AIza[0-9A-Za-z_\-]{30,}",
    "AWS key": r"AKIA[0-9A-Z]{16}",
    "Bearer token": r"Bearer\s+[A-Za-z0-9_\-\.]{20,}",
    "key/token/password assignment": r"(?i)(api[_-]?key|secret|token|password|passwd)\s*[=:]\s*['\"][^'\"\s]{8,}['\"]",
}


def env_values(path):
    """{name: value} from a .env file. The values stay in memory only."""
    out = {}
    if not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            v = v.strip().strip("'\"")
            if len(v) >= 8:
                out[k.strip()] = v
    return out


def scan_text(text, secrets):
    hits = []
    for name, value in secrets.items():
        if value in text:
            hits.append(f"full value of {name}")
        elif value[:12] in text or value[-12:] in text:
            hits.append(f"part of {name}")
    for name, pattern in PATTERNS.items():
        n = len(re.findall(pattern, text))
        if n:
            hits.append(f"{name} pattern x{n}")
    return hits


def scan_staged(repo, secrets):
    """Returns [(file, [what was found]), ...] for files staged in repo."""
    files = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                           capture_output=True, text=True, check=True).stdout.split("\n")
    found = []
    for f in filter(None, files):
        blob = subprocess.run(["git", "-C", str(repo), "show", f":{f}"], capture_output=True, check=True).stdout
        hits = scan_text(blob.decode("utf-8", "replace"), secrets)
        if hits:
            found.append((f, hits))
    return found
