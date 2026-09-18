"""Load a local `.env` for developer runs. No dependency, no magic.

Why hand-rolled rather than python-dotenv: this project's deployment target is
a cloud routine where secrets arrive as REAL environment variables, so `.env`
is a developer convenience only. Adding a dependency for that -- one more thing
to install correctly in a sandbox that already had a pip failure take out a
whole run -- is not worth it.

The one rule that matters: a real environment variable ALWAYS wins. In cloud
there is no `.env` at all, and if one ever appeared it must not shadow what the
routine was configured with.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_local_env(path: Path | None = None) -> list[str]:
    """Set any variable named in `.env` that is not already set.

    Returns the names it filled in, so a run can say what it picked up without
    ever printing a value. Missing file is normal and silent.
    """
    p = path or ENV_FILE
    if not p.is_file():
        return []
    filled: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if not key:
            continue
        # Tolerate quotes people paste in by habit, but do not require them.
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if not val:
            continue                      # blank means "not set", not "empty"
        if os.environ.get(key):
            continue                      # a real env var always wins
        os.environ[key] = val
        filled.append(key)
    return filled


#: Secrets adapters are allowed to read, mapped straight from the environment.
#: Listed explicitly so a run cannot hand an adapter something unrelated.
ADAPTER_SECRET_KEYS = (
    "SHADOWFAX_API_TOKEN",
    "DELHIVERY_API_TOKEN",
    "BLUEDART_LOGIN_ID",
    "BLUEDART_LICENSE_KEY",
    "BLUEDART_CLIENT_ID",
    "BLUEDART_CLIENT_SECRET",
    # DTDC REST v4. The static customer key is what UC actually holds; the
    # login pair is the vendor-documented alternative. DTDC_API_KEY is gone --
    # it belonged to the unconfirmed Shipsy endpoint the adapter no longer uses.
    "DTDC_ACCESS_TOKEN",
    "DTDC_USERNAME",
    "DTDC_PASSWORD",
)


def adapter_secrets() -> dict[str, str]:
    """Whatever adapter credentials the environment actually carries."""
    return {k: os.environ[k] for k in ADAPTER_SECRET_KEYS if os.environ.get(k)}
