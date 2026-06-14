"""Tests for pod_paths.py — Task 5.1

Covers:
  - parse_pod_name: flat, scoped, split-on-first-slash only
  - get_host_path: flat == today's paths (regression guard), scoped includes subdir
  - get_cluster_path: same two cases
  - pod_identity: bare name for both flat and scoped inputs
"""

import pytest
from autocli.pod_paths import (
    get_cluster_path,
    get_host_path,
    parse_pod_name,
    pod_identity,
)

CODE_ROOT = "/home/rob/source"


# ---------------------------------------------------------------------------
# parse_pod_name
# ---------------------------------------------------------------------------


class TestParsePodName:
    def test_flat_name(self):
        subdir, name = parse_pod_name("myapp")
        assert subdir == ""
        assert name == "myapp"

    def test_scoped_name(self):
        subdir, name = parse_pod_name("customer-1/app-code")
        assert subdir == "customer-1"
        assert name == "app-code"

    def test_splits_on_first_slash_only(self):
        """Extra slashes go into the name segment — validation lives upstream."""
        subdir, name = parse_pod_name("a/b/c")
        assert subdir == "a"
        assert name == "b/c"

    def test_flat_with_hyphens_and_underscores(self):
        subdir, name = parse_pod_name("my_app-123")
        assert subdir == ""
        assert name == "my_app-123"

    def test_scoped_with_hyphens(self):
        subdir, name = parse_pod_name("client-co/api-service")
        assert subdir == "client-co"
        assert name == "api-service"


# ---------------------------------------------------------------------------
# pod_identity
# ---------------------------------------------------------------------------


class TestPodIdentity:
    def test_flat_returns_full_name(self):
        assert pod_identity("myapp") == "myapp"

    def test_scoped_returns_bare_name(self):
        assert pod_identity("customer-1/app-code") == "app-code"

    def test_triple_slash_returns_last_two_segments(self):
        """split on first slash: identity is everything after it for multi-slash."""
        assert pod_identity("a/b/c") == "b/c"


# ---------------------------------------------------------------------------
# get_host_path
# ---------------------------------------------------------------------------


class TestGetHostPath:
    def test_flat_host_path_backward_compat(self):
        """Flat name must produce byte-identical path to pre-change behavior."""
        result = get_host_path("myapp", CODE_ROOT)
        assert result == f"{CODE_ROOT}/myapp"

    def test_scoped_host_path_includes_subdir(self):
        result = get_host_path("customer-1/app-code", CODE_ROOT)
        assert result == f"{CODE_ROOT}/customer-1/app-code"

    def test_flat_no_double_slash(self):
        result = get_host_path("api", "/code/")
        # os.path.join strips trailing slash from base
        assert "//" not in result

    def test_scoped_no_double_slash(self):
        result = get_host_path("sub/api", "/code/")
        assert "//" not in result

    def test_uses_os_path_join_semantics(self):
        """Verify os.path.join is used — no naive string concatenation."""
        result = get_host_path("portal", "/srv/code")
        assert result == "/srv/code/portal"

    def test_scoped_uses_os_path_join_semantics(self):
        result = get_host_path("acme/portal", "/srv/code")
        assert result == "/srv/code/acme/portal"


# ---------------------------------------------------------------------------
# get_cluster_path
# ---------------------------------------------------------------------------


class TestGetClusterPath:
    def test_flat_cluster_path_backward_compat(self):
        """Flat name must produce byte-identical cluster path to pre-change behavior."""
        result = get_cluster_path("myapp")
        assert result == "/mnt/code/myapp"

    def test_scoped_cluster_path_includes_subdir(self):
        result = get_cluster_path("customer-1/app-code")
        assert result == "/mnt/code/customer-1/app-code"

    def test_flat_no_double_slash(self):
        result = get_cluster_path("portal")
        assert "//" not in result

    def test_scoped_no_double_slash(self):
        result = get_cluster_path("sub/portal")
        assert "//" not in result
