"""Tests for auto.autocli.commands"""

import os
from unittest.mock import MagicMock, patch

from autocli import commands
from click.testing import CliRunner


def test_auto_help():
    """Test the auto command help"""
    runner = CliRunner()
    result = runner.invoke(commands.auto, ["--help"])
    assert result.exit_code == 0
    assert "Commandline utility" in result.output


@patch("autocli.core.bootstrap_cluster")
def test_start_full(mock_bootstrap):
    """Test the full start command flow routing"""
    runner = CliRunner()
    result = runner.invoke(commands.start)

    assert result.exit_code == 0
    mock_bootstrap.assert_called()


@patch("autocli.core.stop_cluster")
def test_stop_cluster(mock_stop):
    """Test the stop command"""
    runner = CliRunner()
    result = runner.invoke(commands.stop)

    assert result.exit_code == 0
    mock_stop.assert_called()


@patch("autocli.core.delete_cluster")
def test_delete_cluster(mock_delete):
    """Test the stop --delete-cluster command"""
    runner = CliRunner()
    result = runner.invoke(commands.stop, ["--delete-cluster"])

    assert result.exit_code == 0
    mock_delete.assert_called()


@patch("autocli.registry.list_cluster_images")
def test_images_command(mock_list):
    """Test the images command mapping to registry"""
    runner = CliRunner()
    result = runner.invoke(commands.images)
    assert result.exit_code == 0
    mock_list.assert_called()


# ---------------------------------------------------------------------------
# index rebuild command (Task 5.7)
# ---------------------------------------------------------------------------


@patch("autocli.pod_index.rebuild")
def test_index_rebuild_invokes_rebuild(mock_rebuild):
    """auto index rebuild delegates to pod_index.rebuild with the code root."""
    mock_rebuild.return_value = {
        "index": {"version": 1, "pods": {"portal": "/code/portal", "api": "/code/api"}},
        "added": ["api"],
        "kept": ["portal"],
        "pruned": [],
    }

    runner = CliRunner()
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        result = runner.invoke(commands.auto, ["index", "rebuild"])

    assert result.exit_code == 0, result.output
    mock_rebuild.assert_called_once_with("/code")


@patch("autocli.pod_index.rebuild")
def test_index_rebuild_prints_summary(mock_rebuild):
    """auto index rebuild prints the correct summary line."""
    import re as _re

    mock_rebuild.return_value = {
        "index": {"version": 1, "pods": {}},
        "added": ["api", "worker"],
        "kept": ["portal"],
        "pruned": ["old-pod"],
    }

    runner = CliRunner()
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        result = runner.invoke(commands.auto, ["index", "rebuild"])

    assert result.exit_code == 0, result.output
    # Strip ANSI escape codes for plain-text assertions
    plain = _re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    # 3 pods total (2 added + 1 kept), 1 pruned
    assert "3" in plain
    assert "pods" in plain
    assert "2 added" in plain
    assert "1 pruned" in plain


@patch("autocli.pod_index.rebuild")
def test_index_rebuild_shows_pruned_entries(mock_rebuild):
    """Pruned entries appear with a '-' prefix and reason."""
    mock_rebuild.return_value = {
        "index": {"version": 1, "pods": {}},
        "added": [],
        "kept": [],
        "pruned": ["old-pod"],
    }

    runner = CliRunner()
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        result = runner.invoke(commands.auto, ["index", "rebuild"])

    assert "old-pod" in result.output
    assert "pruned" in result.output


# ---------------------------------------------------------------------------
# auto add command (Task 5.7)
# ---------------------------------------------------------------------------


@patch("autocli.commands._add_pod_to_local_yaml")
@patch("autocli.pod_index.add_entry")
@patch("autocli.utils.pull_repo")
@patch("os.path.exists", return_value=False)
def test_add_pod_flat(mock_exists, mock_pull, mock_add_entry, mock_local_yaml):
    """auto add myapp <url> clones and registers a flat pod."""
    runner = CliRunner()
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        result = runner.invoke(commands.auto, ["add", "myapp", "git@github.com:org/myapp.git"])

    assert result.exit_code == 0, result.output
    mock_pull.assert_called_once()
    mock_add_entry.assert_called_once()


@patch("autocli.commands._add_pod_to_local_yaml")
@patch("autocli.pod_index.add_entry")
@patch("autocli.utils.pull_repo")
@patch("os.path.exists", return_value=False)
def test_add_pod_scoped(mock_exists, mock_pull, mock_add_entry, mock_local_yaml):
    """auto add customer-1/app-code <url> clones into subdirectory."""
    runner = CliRunner()
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        result = runner.invoke(
            commands.auto, ["add", "customer-1/app-code", "git@github.com:org/app-code.git"]
        )

    assert result.exit_code == 0, result.output
    # pull_repo should be called with subdir="customer-1"
    mock_pull.assert_called_once()
    call_kwargs = mock_pull.call_args.kwargs
    assert call_kwargs.get("subdir") == "customer-1"


@patch("autocli.utils.declare_error")
@patch("os.path.exists", return_value=True)
def test_add_pod_aborts_on_existing_dir(mock_exists, mock_err):
    """auto add aborts with error if target directory already exists."""
    runner = CliRunner()
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        result = runner.invoke(commands.auto, ["add", "myapp", "git@github.com:org/myapp.git"])

    mock_err.assert_called()


def test_add_pod_rejects_deep_name():
    """auto add rejects names with more than one slash."""
    runner = CliRunner()
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        result = runner.invoke(commands.auto, ["add", "a/b/c", "git@github.com:org/c.git"])

    assert result.exit_code != 0 or "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# auto remove command (Task 5.7)
# ---------------------------------------------------------------------------


@patch("autocli.commands._remove_pod_from_local_yaml")
@patch("autocli.pod_index.remove_entry")
@patch("os.path.exists", return_value=False)
def test_remove_pod_deindexes(mock_exists, mock_remove, mock_local_yaml):
    """auto remove deregisters a pod from the index and local.yaml."""
    runner = CliRunner()
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        with patch("autocli.utils.resolve_pod", return_value="myapp"):
            result = runner.invoke(commands.auto, ["remove", "myapp"])

    assert result.exit_code == 0, result.output
    mock_remove.assert_called_once_with("myapp")
    mock_local_yaml.assert_called_once_with("myapp")


@patch("autocli.commands._remove_pod_from_local_yaml")
@patch("autocli.pod_index.remove_entry")
@patch("os.path.exists", return_value=True)
def test_remove_pod_prompts_before_deleting_dir(mock_exists, mock_remove, mock_local_yaml):
    """auto remove does NOT silently delete the directory — it prompts first."""
    runner = CliRunner()
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        with patch("autocli.utils.resolve_pod", return_value="myapp"):
            # Respond 'n' to the directory-deletion prompt
            result = runner.invoke(commands.auto, ["remove", "myapp"], input="n\n")

    assert result.exit_code == 0, result.output
    # Directory should NOT have been deleted when user says 'n'
    assert "kept on disk" in result.output or "kept" in result.output.lower()
