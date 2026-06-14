"""Pure path helpers for scoped pod names.

A pod name is either:
  - flat:   ``repo-name``           -> subdir="", name="repo-name"
  - scoped: ``subdir/repo-name``    -> subdir="subdir", name="repo-name"

One level of nesting is the contract. Validation (rejecting 2+ slashes or bad
characters) lives in callers (``pod_index.resolve`` / ``add`` command) so that
``parse_pod_name`` stays a total function.

THE CRITICAL DISTINCTION
------------------------
Three different name/path concepts derive from one scoped pod name:

1. **Identity** (bare name, last segment):
   Used for kubectl deployment names, docker image tags, k8s resource names,
   helm release names, and registry repo paths. NEVER prefixed with subdir
   because a ``/`` in a docker tag or k8s resource name is invalid / changes
   meaning.

2. **Host path** (``{code_root}/{subdir}/{name}`` or ``{code_root}/{name}``):
   Used for git clone/pull location, docker build context, reading
   ``.auto/config.yaml``, and cwd for kubectl/helm exec.

3. **Cluster path** (``/mnt/code/{subdir}/{name}`` or ``/mnt/code/{name}``):
   Used for pod ``workingDir`` and script args like
   ``/mnt/code/{...}/smalls.py``.
"""

import os

CLUSTER_MOUNT = "/mnt/code"


def parse_pod_name(pod_name: str) -> tuple:
    """Return (subdir, name) by splitting on the first '/' only.

    Examples:
        'app'            -> ('', 'app')
        'customer-1/app' -> ('customer-1', 'app')
        'a/b/c'          -> ('a', 'b/c')   # caller validates extra levels

    This function is intentionally total — validation lives upstream.
    """
    if "/" in pod_name:
        subdir, name = pod_name.split("/", 1)
        return subdir, name
    return "", pod_name


def pod_identity(pod_name: str) -> str:
    """Return the bare identity name for k8s/docker/helm.

    This is the last segment after the subdir (or the whole name for flat pods).
    NEVER use subdir as part of any docker tag, k8s resource name, or helm
    release name — a '/' is invalid in all those contexts.
    """
    return parse_pod_name(pod_name)[1]


def get_host_path(pod_name: str, code_root: str) -> str:
    """Return the absolute host filesystem path for the pod's source directory.

    Flat:   ``{code_root}/{name}``          (byte-identical to pre-change behavior)
    Scoped: ``{code_root}/{subdir}/{name}``
    """
    subdir, name = parse_pod_name(pod_name)
    if subdir:
        return os.path.join(code_root, subdir, name)
    return os.path.join(code_root, name)


def get_cluster_path(pod_name: str) -> str:
    """Return the in-cluster mount path for the pod's source directory.

    Flat:   ``/mnt/code/{name}``
    Scoped: ``/mnt/code/{subdir}/{name}``
    """
    subdir, name = parse_pod_name(pod_name)
    if subdir:
        return f"{CLUSTER_MOUNT}/{subdir}/{name}"
    return f"{CLUSTER_MOUNT}/{name}"
