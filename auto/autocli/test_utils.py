"""Tests for auto.autocli.utils and auto.autocli.config"""

import subprocess
from unittest.mock import MagicMock, mock_open, patch

from autocli import config, utils
from autocli.pod_paths import get_host_path


@patch("autocli.config.Confirm.ask")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("os.path.isfile")
@patch("builtins.open", new_callable=mock_open, read_data="code: /tmp/code")
@patch("yaml.safe_load")
def test_load_config(
    mock_yaml, _mock_file, mock_isfile, mock_exists, mock_makedirs, mock_confirm
):
    """Test loading configuration safely routes from config.py"""
    mock_yaml.return_value = {"code": "/tmp/code"}

    # Case 1: Config exists, code dir exists
    mock_isfile.return_value = True
    mock_exists.return_value = True

    res_config = config.load_config()
    assert res_config["code"] == "/tmp/code"

    # Case 2: Config exists, code folder MISSING (User says YES to create)
    mock_exists.return_value = False
    mock_confirm.return_value = True

    res_config = config.load_config()
    assert res_config["code"] == "/tmp/code"
    mock_makedirs.assert_called_with("/tmp/code")

    # Case 3: Config does not exist (should trigger create_initial_config)
    mock_isfile.return_value = False
    mock_exists.return_value = True
    with patch("autocli.config.create_initial_config") as mock_create:
        config.load_config()
        mock_create.assert_called()

    # Case 4: Code directory missing, user denies creation (triggers fatal error)
    mock_isfile.return_value = True
    mock_exists.return_value = False
    mock_confirm.return_value = False
    with patch("autocli.config._fatal_error") as mock_error:
        config.load_config()
        mock_error.assert_called_with("Code directory missing. Cannot proceed.")


@patch("subprocess.run")
def test_run_and_wait_success(mock_run):
    """Test successful command execution"""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"success\n")

    result = utils.run_and_wait("echo test")
    assert result == 1
    mock_run.assert_called_with(
        "echo test", capture_output=True, shell=True, check=True, cwd=None
    )


@patch("subprocess.run")
def test_run_and_wait_check_result(mock_run):
    """Test checking output result"""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"found me\n")

    result = utils.run_and_wait("echo test", check_result="found")
    assert result == 1

    result = utils.run_and_wait("echo test", check_result="missing")
    assert result == 0


@patch("subprocess.run")
def test_run_and_wait_failure(mock_run):
    """Test command failure handling"""
    mock_run.side_effect = subprocess.CalledProcessError(1, "cmd", stderr=b"error")

    result = utils.run_and_wait("fail_cmd")
    assert result == 0


@patch("subprocess.run")
def test_get_full_pod_name(mock_run):
    """Test getting full pod name with only_running filter"""
    mock_run.return_value = MagicMock(stdout=b"mypod-12345\n")

    # Case 1: Default (only_running=True)
    name = utils.get_full_pod_name("mypod")
    assert name == "mypod-12345"
    cmd = mock_run.call_args[0][0][0]
    assert "kubectl get pods" in cmd
    assert "grep Running" in cmd

    # Case 2: only_running=False
    utils.get_full_pod_name("mypod", only_running=False)
    cmd = mock_run.call_args[0][0][0]
    assert "grep Running" not in cmd


@patch("os.getcwd", return_value="/tmp")
@patch("os.chdir")
@patch("os.path.exists")
@patch("autocli.utils.run_and_wait")
def test_pull_repo(mock_run, mock_exists, mock_chdir, _mock_getcwd):
    """Test pulling repositories"""
    repo = {"repo": "git@github.com:org/repo.git", "branch": "main"}

    mock_exists.return_value = True
    mock_run.side_effect = [True, True]

    utils.pull_repo(repo, "/code")
    assert mock_chdir.call_count >= 2

    mock_exists.return_value = False
    mock_chdir.reset_mock()
    mock_run.side_effect = [True, True]

    utils.pull_repo(repo, "/code")
    assert "git clone" in mock_run.call_args_list[2][0][0]


@patch("os.path.isfile")
@patch("builtins.open", new_callable=mock_open, read_data="name: test\n")
@patch("yaml.safe_load")
def test_get_pod_config(mock_yaml, _mock_file, mock_isfile):
    """Test fetching pod config mapped straight to global CONFIG object"""
    mock_isfile.return_value = True
    mock_yaml.return_value = {"name": "test"}

    # Simulate dynamically patched dictionary entry
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        res_config = utils.get_pod_config("mypod")
        assert res_config["name"] == "test"

    mock_isfile.return_value = False
    with patch("autocli.utils.declare_error") as mock_err:
        with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
            utils.get_pod_config("mypod")
            mock_err.assert_called()


# ---------------------------------------------------------------------------
# Scoped-name path site tests (Task 5.3)
# ---------------------------------------------------------------------------


@patch("os.getcwd", return_value="/tmp")
@patch("os.makedirs")
@patch("os.chdir")
@patch("os.path.exists")
@patch("os.path.dirname", wraps=__import__("os.path", fromlist=["dirname"]).dirname)
@patch("autocli.utils.run_and_wait")
def test_pull_repo_scoped_creates_parent_dir(
    mock_run, mock_dirname, mock_exists, mock_chdir, mock_makedirs, _mock_cwd
):
    """Scoped clone must create the parent subdirectory before cloning."""
    repo = {"repo": "git@github.com:org/app-code.git", "branch": "main"}
    mock_exists.return_value = False  # target does not yet exist
    mock_run.side_effect = [True, True]

    utils.pull_repo(repo, "/code", subdir="customer-1")

    # makedirs should have been called with the parent path (customer-1 dir)
    makedirs_calls = [str(c) for c in mock_makedirs.call_args_list]
    assert any("customer-1" in s for s in makedirs_calls)


@patch("os.path.isfile")
@patch("builtins.open", new_callable=mock_open, read_data="name: test\n")
@patch("yaml.safe_load")
def test_get_pod_config_scoped_path(mock_yaml, _mock_file, mock_isfile):
    """get_pod_config uses get_host_path so scoped pods resolve correctly."""
    mock_isfile.return_value = True
    mock_yaml.return_value = {"name": "test"}

    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        # The config file path must include the subdir
        utils.get_pod_config("customer-1/app-code")
        call_args = mock_isfile.call_args[0][0]
        assert "customer-1" in call_args
        assert "app-code" in call_args
        assert ".auto/config.yaml" in call_args or os.sep.join([".auto", "config.yaml"]) in call_args


@patch("os.path.isfile")
@patch("builtins.open", new_callable=mock_open, read_data="name: test\n")
@patch("yaml.safe_load")
def test_get_pod_config_flat_path_unchanged(mock_yaml, _mock_file, mock_isfile):
    """Flat pod names produce the same path as before the change (regression guard)."""
    import os as _os

    mock_isfile.return_value = True
    mock_yaml.return_value = {"name": "test"}

    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        utils.get_pod_config("myapp")
        call_args = mock_isfile.call_args[0][0]
        expected = _os.path.join("/code", "myapp", ".auto", "config.yaml")
        assert call_args == expected


@patch("autocli.pod_index.resolve")
def test_resolve_pod_delegates_to_pod_index(mock_resolve):
    """resolve_pod is a thin wrapper that calls pod_index.resolve."""
    mock_resolve.return_value = "customer-1/app-code"
    result = utils.resolve_pod("app-code")
    mock_resolve.assert_called_once_with("app-code")
    assert result == "customer-1/app-code"


@patch("autocli.pod_index.resolve")
def test_resolve_pod_passthrough_for_scoped(mock_resolve):
    """resolve_pod passes scoped names through unchanged."""
    mock_resolve.return_value = "customer-1/app-code"
    result = utils.resolve_pod("customer-1/app-code")
    assert result == "customer-1/app-code"


import os
