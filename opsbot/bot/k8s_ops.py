"""Kubernetes API calls opsbot makes -- pod status, deployment restart, RCON exec.

In-cluster only: no kubeconfig file, no `kubectl` binary. Every call here
rides the opsbot-sa ServiceAccount token that Kubernetes projects into the
pod automatically (see ../40-deployment.yaml's automountServiceAccountToken:
true), authorized by exactly the Role/RoleBinding grants in ../20-rbac.yaml
— get/list/watch on pods, get/list/watch/patch on deployments (the three
whitelisted namespaces), plus the separate diagnostics ClusterRole for
allowlisted non-shell pods/exec in any namespace. Nothing here needs a Secret:
the RCON password lives in the
minecraft pod's own RCON_PASSWORD env var (see ../../minecraft/Rcon.java),
read by rcon.jar *inside* the pod after we exec into it -- opsbot itself
never sees or needs the password.
"""
from __future__ import annotations

import time

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

from util import MINECRAFT_NAMESPACE, rfc3339_now

RCON_JAR = "/opt/minecraft/rcon.jar"
MINECRAFT_CONTAINER = "minecraft"
# Same fix as scripts/minecraft_backup.py: the label selector alone can match
# a stale/evicted pod object still lingering from a prior incarnation, since
# an unfiltered list just takes items[0] with no status filtering. Filtering
# to Running is what makes "the live pod" unambiguous.
MINECRAFT_POD_LABEL_SELECTOR = "app.kubernetes.io/name=minecraft"

_authority_checker = lambda: None


def set_authority_checker(checker) -> None:
    """Install the live site-lease check used by protected operations."""
    global _authority_checker
    _authority_checker = checker


def require_authority() -> None:
    """Fail closed unless Opsbot still owns the site-scoped lease."""
    _authority_checker()


class NotFoundError(Exception):
    """A namespace/deployment/pod the caller asked for doesn't exist (or isn't Running)."""


class RolloutError(Exception):
    """A rollout didn't reach a healthy steady state within the timeout."""


def init() -> None:
    """Call once at startup, before any other function in this module."""
    config.load_incluster_config()


def list_pods(namespace: str) -> list[dict]:
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace)
    result = []
    for pod in pods.items:
        statuses = pod.status.container_statuses or []
        ready = bool(statuses) and all(s.ready for s in statuses)
        restarts = sum(s.restart_count for s in statuses)
        created = pod.metadata.creation_timestamp
        result.append(
            {
                "name": pod.metadata.name,
                "phase": pod.status.phase,
                "ready": ready,
                "restarts": restarts,
                # RFC3339 creation time, surfaced so the cumulative restart
                # count is self-explanatory: restart_count resets to 0 every
                # time a pod is recreated, so "restarts=0" alone is ambiguous
                # between 'never crashed' and 'recreated recently'. Age is the
                # missing context (see k8s-homelab-8ue).
                "created": created.isoformat() if created else None,
            }
        )
    return result


def list_deployment_names(namespace: str) -> list[str]:
    """For the /deploy restart autocomplete -- real, currently-existing
    Deployment names in a namespace, so the user picks from a live list
    instead of having to remember/spell one exactly."""
    apps = client.AppsV1Api()
    return [d.metadata.name for d in apps.list_namespaced_deployment(namespace).items]


def list_namespaces() -> list[str]:
    """Return namespaces visible to the bot for `/pods exec` autocomplete."""
    return sorted(n.metadata.name for n in client.CoreV1Api().list_namespace().items)


def list_pod_names(namespace: str) -> list[str]:
    """Return live pod names in a namespace for exec autocomplete."""
    return sorted(p.metadata.name for p in client.CoreV1Api().list_namespaced_pod(namespace).items)


def exec_pod(namespace: str, pod: str, container: str, argv: list[str]) -> str:
    """Run an allowlisted, non-shell command in a selected pod/container."""
    require_authority()
    v1 = client.CoreV1Api()
    try:
        v1.read_namespaced_pod(pod, namespace)
    except ApiException as e:
        if e.status == 404:
            raise NotFoundError(f"pod {pod!r} not found in namespace {namespace!r}") from e
        raise
    return stream(
        v1.connect_get_namespaced_pod_exec,
        pod,
        namespace,
        container=container,
        command=argv,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _request_timeout=30,
    )


def restart_deployment(namespace: str, name: str) -> None:
    """Equivalent of `kubectl rollout restart deployment/<name> -n <namespace>`:
    fetch to confirm it exists (clean 404 instead of a confusing patch error)
    and to see its current annotations, then JSON-Patch (RFC 6902) only
    spec.template.metadata.annotations. The Deployment controller's own
    ReplicaSet-controller privileges do the actual rolling update from
    there -- opsbot never touches replicasets or pods to make this happen,
    matching ../20-rbac.yaml's verb list exactly.

    JSON-Patch, not strategic-merge-patch: the installed kubernetes client
    (31.0.0) has no override for the generated patch_* methods' Content-Type
    negotiation (confirmed by reading apps_v1_api.py directly -- no
    _content_type kwarg exists on this version, unlike some other releases).
    It always picks the FIRST supported type, application/json-patch+json,
    so the patch has to be expressed as JSON-Patch ops rather than fought
    into a different content type. Two paths depending on whether
    `annotations` already exists on the pod template (confirmed live:
    jmusicbot-release-notifier and jmusicbot already have one; minecraft and
    pantry-bot have none at all) -- "add" to a key under a missing parent
    object fails, so an empty/absent annotations map needs its own "add" of
    the whole object first. Confirmed live: jmusicbot and pantry-bot already
    have a template-level annotations map (config-revision / a prior
    restartedAt respectively); jmusicbot-release-notifier and minecraft have
    none at all -- both branches are real, not theoretical.
    """
    require_authority()
    apps = client.AppsV1Api()
    try:
        current = apps.read_namespaced_deployment(name, namespace)
    except ApiException as e:
        if e.status == 404:
            raise NotFoundError(f"deployment {name!r} not found in namespace {namespace!r}") from e
        raise

    timestamp = rfc3339_now()
    existing_annotations = current.spec.template.metadata.annotations
    if existing_annotations:
        patch = [{
            "op": "add",
            "path": "/spec/template/metadata/annotations/kubectl.kubernetes.io~1restartedAt",
            "value": timestamp,
        }]
    else:
        patch = [{
            "op": "add",
            "path": "/spec/template/metadata/annotations",
            "value": {"kubectl.kubernetes.io/restartedAt": timestamp},
        }]
    apps.patch_namespaced_deployment(name=name, namespace=namespace, body=patch)


def wait_for_rollout(
    namespace: str, name: str, timeout_seconds: int = 120, poll_interval: float = 2.5
) -> None:
    """Blocking equivalent of `kubectl rollout status deployment/<name>`: poll
    until the NEW ReplicaSet is fully up, or raise. Blocking (time.sleep, not
    asyncio) on purpose -- callers on the event loop must run this via
    asyncio.to_thread so a 2-minute poll doesn't stall the gateway connection
    and every other command in flight.

    observed_generation >= metadata.generation guards the same race
    `kubectl rollout status` guards: read a status snapshot taken before the
    controller has even seen our patch, and readyReplicas can still show the
    OLD (pre-restart) pods as ready -- a false positive returned instantly.
    updated_replicas == spec.replicas is what actually pins this to the new
    ReplicaSet specifically, not just "some replicas somewhere are ready".
    replicas == spec.replicas on top of that means the old ReplicaSet has
    finished scaling down too, matching kubectl's "fully available" bar.
    """
    apps = client.AppsV1Api()
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            dep = apps.read_namespaced_deployment(name, namespace)
        except ApiException as e:
            if e.status == 404:
                raise NotFoundError(f"deployment {name!r} not found in namespace {namespace!r}") from e
            raise
        spec_replicas = dep.spec.replicas or 0
        status = dep.status
        observed_current = (status.observed_generation or 0) >= (dep.metadata.generation or 0)
        if (
            observed_current
            and (status.updated_replicas or 0) == spec_replicas
            and (status.ready_replicas or 0) == spec_replicas
            and (status.replicas or 0) == spec_replicas
        ):
            return
        if time.monotonic() >= deadline:
            raise RolloutError(
                f"rollout of {namespace}/{name} did not complete within {timeout_seconds}s "
                f"(ready={status.ready_replicas}/{spec_replicas} "
                f"updated={status.updated_replicas}/{spec_replicas} "
                f"total={status.replicas}/{spec_replicas})"
            )
        time.sleep(poll_interval)


def get_running_minecraft_pod() -> str:
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(
        MINECRAFT_NAMESPACE,
        label_selector=MINECRAFT_POD_LABEL_SELECTOR,
        field_selector="status.phase=Running",
    )
    if not pods.items:
        raise NotFoundError("no Running minecraft pod found (is the deployment up?)")
    return pods.items[0].metadata.name


def exec_rcon(command: str) -> str:
    """Run an RCON console command by exec'ing into the live minecraft pod
    and invoking the same rcon.jar the preStop hook and minecraft_backup.py
    already use -- `kubernetes.stream.stream` against pods/exec, not a raw
    RCON socket implementation and not a `kubectl` subprocess (there's no
    kubeconfig in this pod to give one). This is exactly the mechanism
    ../20-rbac.yaml's opsbot-rcon-exec Role (pods/exec, minecraft namespace
    only) was scoped for.
    """
    require_authority()
    pod_name = get_running_minecraft_pod()
    v1 = client.CoreV1Api()
    exec_command = ["java", "-jar", RCON_JAR, *command.split()]
    output = stream(
        v1.connect_get_namespaced_pod_exec,
        pod_name,
        MINECRAFT_NAMESPACE,
        container=MINECRAFT_CONTAINER,
        command=exec_command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
    return output
