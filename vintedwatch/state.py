"""Watcher state — a single JSON file committed back to the repo each run.

Shape:
  {
    "version": 1,
    "token":  {"access_token_web": ..., "saved_at": ...},
    "items":  {"<id>": {...metadata..., "first_seen", "last_seen", "missing_since"}},
    "sold":   {"<id>": {"reported_at": ..., "price": ...}}
  }
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

log = logging.getLogger(__name__)

DEFAULT_PATH = os.environ.get("VINTED_STATE", "data/state.json")


def load(path: str = DEFAULT_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except FileNotFoundError:
        log.info("no state at %s — first run", path)
        state = {}
    state.setdefault("version", 1)
    state.setdefault("token", {})
    state.setdefault("items", {})
    state.setdefault("sold", {})
    return state


def save(state: dict, path: str = DEFAULT_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Atomic replace: a half-written state file would be committed to git.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("state saved: %d tracked, %d sold", len(state["items"]), len(state["sold"]))
