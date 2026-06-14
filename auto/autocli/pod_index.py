"""Persistent pod path index at ~/.auto/config/pod-index.yaml.

Schema (version 2)::

    version: 2
    short-name:
      app-code:
        - customer-1/app-code
        - customer-2/app-code
      portal:
        - portal

Keys are bare short names (last path segment).
Values are lists of full scoped names (``subdir/repo-name`` or plain
``repo-name``). Host paths are NOT stored; they are computed on demand via
``get_host_path``.

The file is a non-authoritative cache — the filesystem is the source of
truth. A missing or corrupt file is treated as empty (no error surfaced to
callers). All writes are atomic (tempfile + rename) to prevent half-written
state.

Resolution uses ``pod_identity()`` (last segment) for short-name matching:
  - 0 matches → raise ``NotFoundError``
  - 1 match   → return the full scoped key
  - 2+ matches → raise ``CollisionError`` with the candidates listed
"""

import os
import tempfile

import yaml
from autocli.pod_paths import get_host_path, pod_identity

INDEX_PATH = os.path.expanduser("~/.auto/config/pod-index.yaml")


class CollisionError(Exception):
    """Raised when a short pod name matches entries in two or more subdirs.

    Instructs the user to use the full scoped name to disambiguate.
    """

    def __init__(self, short_name: str, candidates: list):
        candidates_str = ", ".join(candidates)
        super().__init__(
            f"Ambiguous pod name '{short_name}': found in {candidates_str}. "
            "Use the full scoped name (subdir/repo-name)."
        )
        self.short_name = short_name
        self.candidates = candidates


class NotFoundError(Exception):
    """Raised when a short pod name has 0 matches in the index."""

    def __init__(self, name: str):
        super().__init__(
            f"Pod '{name}' not found. Run 'auto index rebuild' if the pod exists on disk."
        )
        self.name = name


# ---------------------------------------------------------------------------
# Index I/O
# ---------------------------------------------------------------------------


def load_index() -> dict:
    """Load the pod index from disk. Missing or corrupt → returns ``{}``."""
    try:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if isinstance(data, dict):
            return data
        return {}
    except (OSError, yaml.YAMLError):
        return {}


def save_index(index: dict) -> None:
    """Atomically write *index* to ``INDEX_PATH``.

    Creates the parent directory if absent. Uses a tempfile + rename to
    prevent partial writes.
    """
    parent = os.path.dirname(INDEX_PATH)
    os.makedirs(parent, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(index, fh, default_flow_style=False, allow_unicode=True)
        os.replace(tmp_path, INDEX_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Lifecycle write operations
# ---------------------------------------------------------------------------


def add_entry(scoped_name: str) -> None:
    """Append *scoped_name* to the list under its short-name key.

    If *scoped_name* is already present in the list the call is a no-op
    (idempotent).
    """
    data = load_index()
    short_name_map = data.get("short-name", {})
    short = pod_identity(scoped_name)
    entries = short_name_map.get(short, [])
    if scoped_name not in entries:
        entries.append(scoped_name)
    short_name_map[short] = entries
    data["version"] = 2
    data["short-name"] = short_name_map
    save_index(data)


def remove_entry(scoped_name: str) -> None:
    """Remove *scoped_name* from its short-name list (no-op if absent).

    Removes the key entirely when the list becomes empty after removal.
    """
    data = load_index()
    short_name_map = data.get("short-name", {})
    short = pod_identity(scoped_name)
    entries = short_name_map.get(short, [])
    if scoped_name in entries:
        entries.remove(scoped_name)
    if entries:
        short_name_map[short] = entries
    else:
        short_name_map.pop(short, None)
    data["short-name"] = short_name_map
    save_index(data)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve(name: str, code_root: str = "") -> str:
    """Resolve a possibly-partial name to a full scoped name.

    * If ``name`` contains ``/``: treat as already scoped → return as-is.
    * Else look up ``name`` in the ``short-name`` map:

      - exactly 1 entry → return that full scoped name
      - 2+ entries      → raise ``CollisionError``
      - 0 entries / key missing → raise ``NotFoundError``
    """
    if "/" in name:
        # Already scoped — no ambiguity possible.
        return name

    data = load_index()
    short_name_map = data.get("short-name", {})
    candidates = short_name_map.get(name, [])

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) >= 2:
        raise CollisionError(name, sorted(candidates))
    # 0 matches → not found
    raise NotFoundError(name)


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------


def rebuild(code_root: str) -> dict:
    """Scan *code_root* and rebuild the index from disk state.

    Detection rule: a directory is a pod if it contains ``.auto/config.yaml``.
    Depth 1: flat pods directly under ``code_root``.
    Depth 2: scoped pods one level deeper (``subdir/repo-name``).
    Dirs deeper than depth 2 are ignored.

    Entries in the old index whose scoped names no longer exist on disk are
    pruned (they simply vanish because ``found`` won't contain them).

    Returns a summary dict::

        {
            "index": {"version": 2, "short-name": {...}},
            "added": ["portal", "customer-1/app-code"],
            "kept":  [],
            "pruned": ["old-pod"],
        }
    """
    old_data = load_index()
    old_short_name_map = old_data.get("short-name", {})
    # Flatten old index to a set of scoped names for comparison.
    old_scoped = {
        scoped
        for entries in old_short_name_map.values()
        for scoped in entries
    }

    found_scoped = []  # ordered list of scoped names discovered on disk

    try:
        entries = list(os.scandir(code_root))
    except OSError:
        entries = []

    for entry in entries:
        if not entry.is_dir():
            continue

        # Check if this immediate child is itself a pod (depth-1 / flat).
        if os.path.isfile(os.path.join(entry.path, ".auto", "config.yaml")):
            found_scoped.append(entry.name)
        else:
            # Treat as a potential subdir group; scan one level deeper.
            try:
                sub_entries = list(os.scandir(entry.path))
            except OSError:
                continue
            for sub in sub_entries:
                if sub.is_dir() and os.path.isfile(
                    os.path.join(sub.path, ".auto", "config.yaml")
                ):
                    found_scoped.append(f"{entry.name}/{sub.name}")

    # Build the new short-name map.
    new_short_name_map: dict = {}
    for scoped in found_scoped:
        short = pod_identity(scoped)
        bucket = new_short_name_map.get(short, [])
        bucket.append(scoped)
        new_short_name_map[short] = bucket

    new_index = {"version": 2, "short-name": new_short_name_map}
    save_index(new_index)

    found_set = set(found_scoped)
    pruned = sorted(old_scoped - found_set)
    kept = sorted(old_scoped & found_set)
    added = sorted(found_set - old_scoped)

    return {
        "index": new_index,
        "added": added,
        "kept": kept,
        "pruned": pruned,
    }
