"""Auto Commands

  * `--dry-run`     This is for automated testing and visually testing the output
  * `--offline`     This disables steps that require internet so you can work without Internet
"""

import json
import os

import click
from autocli import core, pod_index, registry, services, utils
from autocli.config import CONFIG
from autocli.pod_paths import parse_pod_name
from rich import print as rprint
from rich.progress import Progress

VERSION = "0.7.5"


# Global settings for click
CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "ignore_unknown_options": True,
}


def get_pod_names(ctx, param, incomplete):  # pylint: disable=unused-argument
    """Generate list of pods for shell autocompletion.

    Merges scoped names from the pod index with bare names from CONFIG.
    """
    try:
        names = set()

        # 1. Bare names from local.yaml CONFIG (backward compat)
        for item in CONFIG.get("pods", []):
            if isinstance(item, dict) and "repo" in item:
                p_name = item["repo"].split("/")[-1:][0].replace(".git", "")
                names.add(p_name)
            elif isinstance(item, str):
                names.add(item)

        # 2. Scoped keys from the pod index (includes subdir/repo-name entries)
        try:
            idx_data = pod_index.load_index()
            for scoped in idx_data.get("pods", {}):
                names.add(scoped)
        except Exception:  # pylint: disable=broad-except
            pass

        return sorted(n for n in names if n.startswith(incomplete))
    except Exception:  # pylint: disable=broad-except
        return []


def get_namespaces(ctx, param, incomplete):  # pylint: disable=unused-argument
    """Generate list of namespaces for shell autocompletion"""
    try:
        output = utils.run_and_return(
            "kubectl get ns -o jsonpath='{.items[*].metadata.name}'"
        )
        if not output:
            return []

        namespaces = output.split()
        return [ns for ns in namespaces if ns.startswith(incomplete)]
    except Exception:  # pylint: disable=broad-except
        return []


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(version=VERSION)
def auto():
    """Commandline utility to assist with creating/deleting clusters and
    starting/stopping pods."""
    return


@auto.group()
def index():
    """Manage the pod path index (~/.auto/config/pod-index.yaml)."""


@index.command(name="rebuild")
@click.pass_context
def index_rebuild(ctx):  # pylint: disable=unused-argument
    """Rebuild the pod index by scanning the code root; prune dead entries."""
    code_root = CONFIG["code"]
    rprint(f"Rebuilding pod index from {code_root} ...")
    result = pod_index.rebuild(code_root)

    added = result.get("added", [])
    kept = result.get("kept", [])
    pruned = result.get("pruned", [])

    for name in sorted(kept):
        rprint(f"  + {name} (kept)")
    for name in sorted(added):
        rprint(f"  + {name} (added)")
    for name in sorted(pruned):
        rprint(f"  - {name} (pruned: directory missing)")

    total = len(added) + len(kept)
    rprint(
        f"Index written: {total} pods "
        f"({len(added)} added, {len(pruned)} pruned)."
    )


@auto.command(name="images")
@click.pass_context
def images(self):  # pylint: disable=unused-argument
    """List unique container images running in the cluster (formatted for local.yaml)."""
    registry.list_cluster_images()


@auto.command()
@click.option("--shell", default="bash", help="Shell type (bash, zsh, or fish).")
@click.option(
    "--install",
    "do_install",
    is_flag=True,
    help="Automatically append to shell config (use with caution).",
)
def autocomplete(shell, do_install):
    """Display instructions to enable shell autocomplete (or install it)."""
    if shell == "bash":
        eval_line = 'eval "$(_AUTO_COMPLETE=bash_source auto)"'
        config_file = "~/.bashrc"
    elif shell == "zsh":
        eval_line = 'eval "$(_AUTO_COMPLETE=zsh_source auto)"'
        config_file = "~/.zshrc"
    elif shell == "fish":
        eval_line = "eval (env _AUTO_COMPLETE=fish_source auto)"
        config_file = "~/.config/fish/config.fish"
    else:
        raise click.BadOptionUsage("--shell", f"Unsupported shell: {shell}")

    click.echo(
        f'To enable {shell} completion for "auto", add this line to {config_file}:'
    )
    click.echo(eval_line)
    click.echo(f'\nThen reload your shell (e.g., "source {config_file}").')

    if do_install:
        click.confirm(
            f"\nAppend to {config_file} now? (This modifies your file)", abort=True
        )
        with open(os.path.expanduser(config_file), "a", encoding="utf-8") as f:
            f.write(f"\n# Autocomplete for auto CLI\n{eval_line}\n")
        click.echo(f'Added to {config_file}. Run "source {config_file}" to activate.')


@auto.command()
@click.pass_context
@click.argument("pod", required=False, shell_complete=get_pod_names)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--offline", is_flag=True, default=False)
def start(self, pod, dry_run, offline):  # pylint: disable=unused-argument
    """Start a new k3s/k3d cluster or an individual pod"""
    core.bootstrap_cluster(pod, dry_run, offline)


@auto.command()
@click.pass_context
@click.argument("pod", required=False, shell_complete=get_pod_names)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--delete-cluster", is_flag=True, default=False)
def stop(self, pod, dry_run, delete_cluster):  # pylint: disable=unused-argument
    """Stop the cluster (or delete it)"""
    if pod:
        rprint(f"[steel_blue]Stopping the [/]{pod}[steel_blue] pod")
        core.stop_pod(pod)
    else:
        with Progress(transient=False) as progress:
            task = progress.add_task("Cluster Shutdown", total=100)
            if not dry_run:
                if delete_cluster:
                    core.delete_cluster(progress, task)
                else:
                    core.stop_cluster(progress, task)
            else:
                progress.update(task, advance=50)
            progress.update(task, advance=50)


@auto.command()
@click.pass_context
@click.argument("pod", required=True, shell_complete=get_pod_names)
def restart(self, pod):  # pylint: disable=unused-argument
    """Restart (stop / start) a pod"""
    rprint(f"[steel_blue]Restarting [/]{pod}[steel_blue] pod")
    core.restart_pod(pod)


@auto.command()
@click.pass_context
@click.argument("pod", required=True, shell_complete=get_pod_names)
def seed(self, pod):  # pylint: disable=unused-argument
    """Seed a pod's databases"""
    rprint(f"[steel_blue]Initializing[/] {pod}[steel_blue] pod")
    services.init_pod_db(pod)
    rprint()
    rprint(f"[steel_blue]Seeding [/]{pod}[steel_blue] pod")
    services.seed_pod(pod)


@auto.command()
@click.pass_context
@click.argument("pod", required=True, shell_complete=get_pod_names)
def init(self, pod):  # pylint: disable=unused-argument
    """Init a pod's databases"""
    rprint(f"[steel_blue]Initializing [/]{pod}[steel_blue] pod database")
    services.init_pod_db(pod)


@auto.command()
@click.pass_context
def mysql(self):  # pylint: disable=unused-argument
    """Connect to the mysql database"""
    services.connect_to_mysql()


@auto.command()
@click.pass_context
def postgres(self):  # pylint: disable=unused-argument
    """Connect to the postgres database"""
    services.connect_to_postgres()


@auto.command()
@click.pass_context
def minio(self):  # pylint: disable=unused-argument
    """Open Connection to MinIO Server"""
    services.connect_to_minio()


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def logs(self, pod):  # pylint: disable=unused-argument
    """Output logs for a pod to the terminal"""
    core.output_logs(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def tag(self, pod):  # pylint: disable=unused-argument
    """Build, Tag, and Load a pod container image in the local repository"""
    registry.tag_pod_docker_image(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def upgrade(self, pod):  # pylint: disable=unused-argument
    """Remove container registry, create it again, then repopulate it, then restart the cluster"""
    registry.tag_pod_docker_image(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def migrate(self, pod):  # pylint: disable=unused-argument
    """Run database migrations in a pod (using smalls)"""
    core.migrate_with_smalls(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.argument("number")
@click.pass_context
def rollback(self, pod, number):  # pylint: disable=unused-argument
    """Rollback database migrations in a pod (using smalls)"""
    core.rollback_with_smalls(pod, number)


@auto.command()
@click.pass_context
@click.argument("git_repo", required=True)
def install(self, git_repo):  # pylint: disable=unused-argument
    """Install "parent" configuration file from git repo"""
    core.install_config_from_repo(git_repo)


@auto.command()
@click.pass_context
@click.option(
    "--namespace",
    "-n",
    default="default",
    help="Namespace to show pods for",
    shell_complete=get_namespaces,
)
@click.option(
    "--all-namespaces",
    "-a",
    is_flag=True,
    default=False,
    help="Show pods from all namespaces",
)
@click.option(
    "--watch",
    "-w",
    is_flag=True,
    default=False,
    help="Watch the status (refresh every 3s)",
)
def status(self, namespace, all_namespaces, watch):  # pylint: disable=unused-argument
    """Show the status of the cluster and pods"""
    core.show_status(namespace, all_namespaces, watch)


@auto.command()
@click.pass_context
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Force update even if already at the latest version.",
)
def update(self, force):  # pylint: disable=unused-argument
    """Update auto CLI to the latest version"""
    latest_version_json = utils.run_and_return(
        "curl -s https://api.github.com/repos/devocho/auto/releases/latest"
    )
    if latest_version_json:
        try:
            latest_version_data = json.loads(latest_version_json)
            latest_version = latest_version_data["tag_name"].lstrip("v")
            if VERSION == latest_version and not force:
                rprint(f"[green]Current version ({VERSION}) is already the latest.[/]")
                rprint(
                    """
⠀⠀⠀⠀⠀⠀⠀⠀⣠⣴⣶⡋⠉⠙⠒⢤⡀⠀⠀⠀⠀⠀⢠⠖⠉⠉⠙⠢⡄⠀
⠀⠀⠀⠀⠀⠀⢀⣼⣟⡒⠒⠀⠀⠀⠀⠀⠙⣆⠀⠀⠀⢠⠃⠀⠀⠀⠀⠀⠹⡄
⠀⠀⠀⠀⠀⠀⣼⠷⠖⠀⠀⠀⠀⠀⠀⠀⠀⠘⡆⠀⠀⡇⠀⠀⠀⠀⠀⠀⠀⢷
⠀⠀⠀⠀⠀⠀⣷⡒⠀⠀⢐⣒⣒⡒⠀⣐⣒⣒⣧⠀ ⡇⠀⠀⢠⢤⢠⡠⠀⢸⠀
⠀⠀⠀⠀⠀⢰⣛⣟⣂⠀⠘⠤⠬⠃⠰⠑⠥⠊⣿⠀ ⡇⠀⠀⠓⠃⠋⠂⠀⢸⠀
⠀⠀⠀⠀⠀⢸⣿⡿⠤⠀⢸⠁⠀⠀⢀⡆⠀⠀⣿⠀⠀⡇⠀⠀⠀⠀⠀⠀⠀⣸
⠀⠀⠀⠀⠀⠈⠿⣯⡭⠀⠸⡀⠀⢀⣀⠀⠀⠀⡟⠀⠀⢸⠀⠀⠀⠀⠀⠀⢠⠏
⠀⠀⠀⠀⠀⠀⠀⠈⢯⡥⠄⢱⠀⠀⠀⠀⠀⡼⠁⠀⠀⠀⠳⢄⣀⣀⣀⡴⠃⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⢱⡦⣄⣀⣀⣀⣠⠞⠁⠀⠀⠀⠀⠀⠀⠈⠉⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⢀⣤⣾⠛⠃⠀⠀⠀⢹⠳⡶⣤⡤⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⣠⢴⣿⣿⣿⡟⡷⢄⣀⣀⣀⡼⠳⡹⣿⣷⠞⣳⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⢰⡯⠭⠹⡟⠿⠧⠷⣄⣀⣟⠛⣦⠔⠋⠛⠛⠋⠙⡆⠀⠀⠀⠀⠀⠀⠀
⠀⠀⢸⣿⠭⠉⠀⢠⣤⠀⠀⠀⠘⡷⣵⢻⠀⠀⠀⠀⣼⠀⣇⠀⠀⠀⠀⠀⠀⠀
⠀⠀⡇⣿⠍⠁⠀⢸⣗⠂⠀⠀⠀⣧⣿⣼⠀⠀⠀⠀⣯⠀⢸⠀⠀⠀⠀⠀⠀⠀
    """
                )
                return
            rprint(f"[steel_blue]Updating from {VERSION} to {latest_version}...[/]")
        except Exception:  # pylint: disable=broad-except
            pass
    os.system("curl -fsSL https://www.devocho.com/auto.sh | bash")


def _validate_pod_name(pod_name: str) -> tuple:
    """Parse and validate a pod name; returns (subdir, name) on success.

    Raises ``click.UsageError`` with a descriptive message on any of:
      - more than one '/' (too many levels)
      - empty segment (leading/trailing '/' or empty name)
      - disallowed characters outside ``[a-zA-Z0-9_-]``
    """
    import re

    subdir, name = parse_pod_name(pod_name)

    # Reject 2+ levels: if name still contains a '/' after the first split
    if "/" in name:
        raise click.UsageError(
            f"Pod name '{pod_name}' is invalid: only one level of nesting is "
            "allowed (subdir/repo-name)."
        )

    segment_re = re.compile(r"^[a-zA-Z0-9_-]+$")
    for segment in ([subdir, name] if subdir else [name]):
        if not segment or not segment_re.match(segment):
            raise click.UsageError(
                f"Pod name '{pod_name}' is invalid: each segment must be "
                "non-empty and contain only [a-zA-Z0-9_-]."
            )

    return subdir, name


@auto.command(name="add")
@click.argument("pod_name")
@click.argument("url")
@click.pass_context
def add_pod(ctx, pod_name, url):  # pylint: disable=unused-argument
    """Clone a git repo and register it in the pod index.

    POD_NAME may be a flat name (myapp) or scoped name (subdir/myapp).
    URL is the git repository URL to clone.
    """
    subdir, _ = _validate_pod_name(pod_name)

    from autocli.pod_paths import get_host_path

    host_path = get_host_path(pod_name, CONFIG["code"])

    if os.path.exists(host_path):
        utils.declare_error(
            f"Directory '{host_path}' already exists. "
            "Remove it first or choose a different name."
        )
        return

    # Clone the repository with subdir context so the parent dir is created.
    repo = {"repo": url, "branch": "main"}
    utils.pull_repo(repo, CONFIG["code"], subdir=subdir)

    # Register in the pod index.
    pod_index.add_entry(pod_name)
    rprint(f"  + [bright_cyan]{pod_name}[/] added to pod index.")

    # Write pod entry to local.yaml pods list.
    _add_pod_to_local_yaml(pod_name, url)


@auto.command(name="remove")
@click.argument("pod_name")
@click.pass_context
def remove_pod(ctx, pod_name):  # pylint: disable=unused-argument
    """Remove a pod from the pod index (and optionally delete its directory).

    POD_NAME may be a flat name or scoped name. Resolves via the pod index.
    """
    resolved = utils.resolve_pod(pod_name)

    from autocli.pod_paths import get_host_path

    host_path = get_host_path(resolved, CONFIG["code"])

    # Remove from pod index.
    pod_index.remove_entry(resolved)
    rprint(f"  - [bright_cyan]{resolved}[/] removed from pod index.")

    # Remove from local.yaml.
    _remove_pod_from_local_yaml(resolved)

    # Offer optional directory deletion.
    if os.path.exists(host_path):
        try:
            if click.confirm(
                f"\nAlso delete the source directory '{host_path}'?", default=False
            ):
                import shutil

                shutil.rmtree(host_path)
                rprint(f"  - Directory '{host_path}' deleted.")
            else:
                rprint(f"  [dim]Directory '{host_path}' kept on disk.[/dim]")
        except click.exceptions.Abort:
            rprint(f"  [dim]Directory '{host_path}' kept on disk.[/dim]")


def _add_pod_to_local_yaml(pod_name: str, url: str) -> None:
    """Append a pod entry to the pods list in local.yaml (best-effort)."""
    import yaml

    local_yaml_path = os.path.expanduser("~/.auto/config/local.yaml")
    if not os.path.isfile(local_yaml_path):
        return
    try:
        with open(local_yaml_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        pods = data.get("pods", [])
        already_registered = any(
            (isinstance(p, dict) and p.get("name") == pod_name)
            or (isinstance(p, str) and p == pod_name)
            for p in pods
        )
        if not already_registered:
            pods.append({"repo": url, "branch": "main", "name": pod_name})
            data["pods"] = pods
            with open(local_yaml_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, default_flow_style=False)
    except (OSError, Exception):  # pylint: disable=broad-except
        pass


def _remove_pod_from_local_yaml(pod_name: str) -> None:
    """Remove *pod_name* from the pods list in local.yaml (best-effort)."""
    import yaml

    local_yaml_path = os.path.expanduser("~/.auto/config/local.yaml")
    if not os.path.isfile(local_yaml_path):
        return
    try:
        with open(local_yaml_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        pods = data.get("pods", [])
        pods = [
            p for p in pods
            if not (
                (isinstance(p, str) and p == pod_name)
                or (isinstance(p, dict) and p.get("name") == pod_name)
            )
        ]
        data["pods"] = pods
        with open(local_yaml_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False)
    except (OSError, Exception):  # pylint: disable=broad-except
        pass
