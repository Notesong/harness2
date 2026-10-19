# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Trial identity: was this rollout finished, and by this configuration?

Completion is recorded explicitly by the code that observed the run return; presence of output
is not enough. The marker also fingerprints the harness that produced the rollout, so a rollout
from a different configuration is never reused.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from harness2 import config

MARK = ".adapt_complete"


VERSION = 1


def _ignored(p: Path) -> bool:
    """Byproducts that are not part of the harness and must not change its identity."""
    return "__pycache__" in p.parts or p.suffix == ".pyc" or p.name == ".DS_Store"


def fingerprint(harness_dir: Path | None) -> str:
    """A stable digest of the harness tree that produced a rollout; `None` means a base
    rollout on the inherited harness and digests to the shared value "base".
    """
    if harness_dir is None:
        return "base"
    root = config.long_path(harness_dir)
    if not root.is_dir():
        return "absent"
    h = hashlib.sha1()
    for p in sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()):
        if not p.is_file() or p.name == MARK or _ignored(p):
            continue
        h.update(p.relative_to(root).as_posix().encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()[:16]


def mark_complete(
    where: Path, *, run_id: str, harness_dir: Path | None = None, extra: dict | None = None
) -> Path:
    """Record that `run_id` finished. Call only after the runner returned normally."""
    d = config.long_path(where)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": VERSION,
        "run_id": run_id,
        "at": time.time(),
        "harness": fingerprint(harness_dir),
        **(extra or {}),
    }
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                     dir=d, prefix=f"{MARK}.", suffix=".tmp",
                                     delete=False) as stream:
        tmp = Path(stream.name)
        json.dump(payload, stream, indent=2, sort_keys=True)
    final = d / MARK
    try:
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)
    return Path(where) / MARK


def read_mark(where: Path) -> dict | None:
    try:
        value = json.loads((config.long_path(where) / MARK).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def is_complete(
    where: Path, *, harness_dir: Path | None = None, match_harness: bool = True
) -> bool:
    """Whether the artifacts at `where` may be reused: a current marker naming the same
    harness, unless `match_harness` is off.
    """
    m = read_mark(where)
    if not m or m.get("version") != VERSION:
        return False
    if not match_harness:
        return True
    return m.get("harness") == fingerprint(harness_dir)


def why_not(where: Path, *, harness_dir: Path | None = None) -> str:
    """A sentence for a log line explaining why `where` is or is not reusable."""
    m = read_mark(where)
    if not m:
        return f"no {MARK} -- never finished, or finished before completion was recorded"
    if m.get("version") != VERSION:
        return f"marker version {m.get('version')} != {VERSION}"
    want, got = fingerprint(harness_dir), m.get("harness")
    if want != got:
        return f"harness fingerprint {got} != {want} -- produced by a different configuration"
    return "reusable"
