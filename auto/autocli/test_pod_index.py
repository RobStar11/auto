"""Tests for pod_index.py — Task 5.2

Covers:
  - load_index: missing file → {}; corrupt YAML → {}
  - resolve: 0-match raises NotFoundError, 1-match returns scoped key,
             2+-match raises CollisionError, already-scoped passthrough
  - add_entry / remove_entry upsert + delete
  - rebuild: flat-only tree, mixed flat+subdir, prune of vanished dir,
             depth > 2 ignored, .auto/config.yaml as pod marker
"""

import os
from unittest.mock import MagicMock, mock_open, patch

import pytest
import yaml
from autocli.pod_index import (
    CollisionError,
    NotFoundError,
    add_entry,
    load_index,
    rebuild,
    remove_entry,
    resolve,
    save_index,
)


# ---------------------------------------------------------------------------
# load_index
# ---------------------------------------------------------------------------


class TestLoadIndex:
    def test_missing_file_returns_empty(self):
        with patch("builtins.open", side_effect=OSError("not found")):
            result = load_index()
        assert result == {}

    def test_corrupt_yaml_returns_empty(self):
        with patch("builtins.open", mock_open(read_data=": : bad yaml :::")):
            with patch("yaml.safe_load", side_effect=yaml.YAMLError("bad")):
                result = load_index()
        assert result == {}

    def test_valid_index_returns_dict(self):
        data = {"version": 2, "short-name": {"portal": ["portal"]}}
        with patch("builtins.open", mock_open(read_data="")):
            with patch("yaml.safe_load", return_value=data):
                result = load_index()
        assert result["short-name"]["portal"] == ["portal"]

    def test_non_dict_yaml_returns_empty(self):
        with patch("builtins.open", mock_open(read_data="")):
            with patch("yaml.safe_load", return_value=["list", "not", "dict"]):
                result = load_index()
        assert result == {}


# ---------------------------------------------------------------------------
# save_index
# ---------------------------------------------------------------------------


class TestSaveIndex:
    def test_saves_atomically(self, tmp_path):
        """save_index should write a tempfile then rename it atomically."""
        idx_path = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx_path)):
            save_index({"version": 2, "short-name": {"portal": ["portal"]}})
        assert idx_path.exists()
        content = yaml.safe_load(idx_path.read_text())
        assert content["short-name"]["portal"] == ["portal"]

    def test_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "a" / "b" / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(nested)):
            save_index({"version": 2, "short-name": {}})
        assert nested.exists()


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


class TestResolve:
    def test_already_scoped_passthrough(self):
        """Input with '/' is returned unchanged — no index lookup needed."""
        result = resolve("customer-1/app-code")
        assert result == "customer-1/app-code"

    def test_zero_matches_raises_not_found(self):
        """0 matches → raise NotFoundError with informative message."""
        with patch(
            "autocli.pod_index.load_index",
            return_value={"short-name": {"portal": ["portal"]}},
        ):
            with pytest.raises(NotFoundError) as exc_info:
                resolve("unknown-pod")
        assert "unknown-pod" in str(exc_info.value)
        assert "auto index rebuild" in str(exc_info.value)

    def test_not_found_error_name_attribute(self):
        """NotFoundError.name contains the looked-up name."""
        with patch(
            "autocli.pod_index.load_index",
            return_value={"short-name": {}},
        ):
            with pytest.raises(NotFoundError) as exc_info:
                resolve("missing")
        assert exc_info.value.name == "missing"

    def test_one_match_returns_scoped_key(self):
        with patch(
            "autocli.pod_index.load_index",
            return_value={"short-name": {"app-code": ["customer-1/app-code"]}},
        ):
            result = resolve("app-code")
        assert result == "customer-1/app-code"

    def test_flat_pod_one_match(self):
        """Flat pod stored under its own name: resolve returns 'myapp'."""
        with patch(
            "autocli.pod_index.load_index",
            return_value={"short-name": {"myapp": ["myapp"]}},
        ):
            result = resolve("myapp")
        assert result == "myapp"

    def test_two_matches_raises_collision_error(self):
        with patch(
            "autocli.pod_index.load_index",
            return_value={
                "short-name": {
                    "app-code": ["customer-1/app-code", "customer-2/app-code"]
                }
            },
        ):
            with pytest.raises(CollisionError) as exc_info:
                resolve("app-code")
        msg = str(exc_info.value)
        assert "app-code" in msg
        assert "customer-1/app-code" in msg
        assert "customer-2/app-code" in msg
        assert "scoped name" in msg.lower() or "subdir" in msg.lower()

    def test_collision_error_contains_candidates(self):
        with patch(
            "autocli.pod_index.load_index",
            return_value={
                "short-name": {
                    "shared": ["a/shared", "b/shared", "c/shared"]
                }
            },
        ):
            with pytest.raises(CollisionError) as exc_info:
                resolve("shared")
        err = exc_info.value
        assert err.short_name == "shared"
        assert set(err.candidates) == {"a/shared", "b/shared", "c/shared"}

    def test_empty_short_name_map_raises_not_found(self):
        with patch("autocli.pod_index.load_index", return_value={}):
            with pytest.raises(NotFoundError):
                resolve("anything")


# ---------------------------------------------------------------------------
# add_entry / remove_entry
# ---------------------------------------------------------------------------


class TestAddRemoveEntry:
    def test_add_entry_creates_list_entry(self, tmp_path):
        idx = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx)):
            add_entry("customer-1/app-code")
            with patch("autocli.pod_index.INDEX_PATH", str(idx)):
                result = load_index()
        assert "customer-1/app-code" in result["short-name"]["app-code"]

    def test_add_entry_idempotent(self, tmp_path):
        """Adding the same scoped name twice does not duplicate it."""
        idx = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx)):
            add_entry("myapp")
            add_entry("myapp")
            result = load_index()
        assert result["short-name"]["myapp"].count("myapp") == 1

    def test_add_entry_two_scoped_same_short(self, tmp_path):
        """Two scoped names sharing the same short name → list has both."""
        idx = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx)):
            add_entry("customer-1/app-code")
            add_entry("customer-2/app-code")
            result = load_index()
        entries = result["short-name"]["app-code"]
        assert "customer-1/app-code" in entries
        assert "customer-2/app-code" in entries

    def test_remove_entry_deletes_from_list(self, tmp_path):
        idx = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx)):
            add_entry("portal")
            remove_entry("portal")
            result = load_index()
        assert "portal" not in result.get("short-name", {})

    def test_remove_entry_keeps_key_when_siblings_remain(self, tmp_path):
        """Removing one scoped name keeps the key if siblings still exist."""
        idx = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx)):
            add_entry("customer-1/app-code")
            add_entry("customer-2/app-code")
            remove_entry("customer-1/app-code")
            result = load_index()
        assert result["short-name"]["app-code"] == ["customer-2/app-code"]

    def test_remove_entry_noop_if_absent(self, tmp_path):
        idx = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx)):
            # Should not raise
            remove_entry("does-not-exist")


# ---------------------------------------------------------------------------
# rebuild
# ---------------------------------------------------------------------------


class TestRebuild:
    def _make_pod(self, base, *parts):
        """Create a .auto/config.yaml marker at base/parts."""
        pod_dir = base.joinpath(*parts)
        auto_dir = pod_dir / ".auto"
        auto_dir.mkdir(parents=True, exist_ok=True)
        (auto_dir / "config.yaml").write_text("name: test\n")
        return pod_dir

    def test_flat_only_tree(self, tmp_path):
        self._make_pod(tmp_path, "portal")
        self._make_pod(tmp_path, "api")
        idx = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx)):
            result = rebuild(str(tmp_path))
        short_name_map = result["index"]["short-name"]
        all_scoped = [s for entries in short_name_map.values() for s in entries]
        assert set(all_scoped) == {"portal", "api"}

    def test_mixed_flat_and_scoped(self, tmp_path):
        self._make_pod(tmp_path, "portal")
        self._make_pod(tmp_path, "customer-1", "app-code")
        idx = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx)):
            result = rebuild(str(tmp_path))
        short_name_map = result["index"]["short-name"]
        all_scoped = {s for entries in short_name_map.values() for s in entries}
        assert "portal" in all_scoped
        assert "customer-1/app-code" in all_scoped

    def test_prune_vanished_dir(self, tmp_path):
        # Seed index with a stale entry using new schema
        stale_idx = tmp_path / "pod-index.yaml"
        stale_data = {
            "version": 2,
            "short-name": {"old-pod": ["old-pod"]},
        }
        stale_idx.write_text(yaml.safe_dump(stale_data))
        self._make_pod(tmp_path, "portal")
        with patch("autocli.pod_index.INDEX_PATH", str(stale_idx)):
            result = rebuild(str(tmp_path))
        assert "old-pod" in result["pruned"]
        all_scoped = {
            s
            for entries in result["index"]["short-name"].values()
            for s in entries
        }
        assert "old-pod" not in all_scoped

    def test_depth_greater_than_2_ignored(self, tmp_path):
        """A pod at depth 3 (subdir/subsubdir/repo) must NOT be indexed."""
        deep_dir = tmp_path / "subdir" / "subsubdir" / "deep-pod" / ".auto"
        deep_dir.mkdir(parents=True)
        (deep_dir / "config.yaml").write_text("name: deep\n")
        idx = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx)):
            result = rebuild(str(tmp_path))
        all_scoped = {
            s
            for entries in result["index"]["short-name"].values()
            for s in entries
        }
        assert "deep-pod" not in all_scoped
        assert "subsubdir/deep-pod" not in all_scoped
        assert "subdir/subsubdir/deep-pod" not in all_scoped

    def test_auto_config_yaml_is_pod_marker(self, tmp_path):
        """A dir without .auto/config.yaml is NOT counted as a pod."""
        (tmp_path / "not-a-pod").mkdir()
        self._make_pod(tmp_path, "real-pod")
        idx = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx)):
            result = rebuild(str(tmp_path))
        all_scoped = {
            s
            for entries in result["index"]["short-name"].values()
            for s in entries
        }
        assert "real-pod" in all_scoped
        assert "not-a-pod" not in all_scoped

    def test_rebuild_summary_counts(self, tmp_path):
        """added/kept/pruned lists are correct."""
        existing_idx = tmp_path / "pod-index.yaml"
        existing_data = {
            "version": 2,
            "short-name": {
                "portal": ["portal"],
                "old": ["old"],
            },
        }
        existing_idx.write_text(yaml.safe_dump(existing_data))
        self._make_pod(tmp_path, "portal")  # kept
        self._make_pod(tmp_path, "api")     # added
        # "old" dir is missing → pruned
        with patch("autocli.pod_index.INDEX_PATH", str(existing_idx)):
            result = rebuild(str(tmp_path))
        assert "api" in result["added"]
        assert "portal" in result["kept"]
        assert "old" in result["pruned"]

    def test_collision_grouped_in_rebuild(self, tmp_path):
        """Two scoped pods with same short name are grouped under one key."""
        self._make_pod(tmp_path, "customer-1", "app-code")
        self._make_pod(tmp_path, "customer-2", "app-code")
        idx = tmp_path / "pod-index.yaml"
        with patch("autocli.pod_index.INDEX_PATH", str(idx)):
            result = rebuild(str(tmp_path))
        entries = result["index"]["short-name"]["app-code"]
        assert set(entries) == {"customer-1/app-code", "customer-2/app-code"}
