"""Kubernetes API calls opsbot makes -- pod status, deployment restart, RCON exec.

In-cluster only: no kubeconfig file, no `kubectl` binary. Every call here
rides the opsbot-sa ServiceAccount token that Kubernetes projects into the
pod automatically (see ../40-deployment.yaml's automountServiceAccountToken:
true), authorized by exactly the Role/RoleBinding grants in ../20-rbac.yaml
-- get/list/watch on pods, get/list/watch/patch on deployments (all three
whitelisted namespaces), plus pods/exec create in the minecraft namespace
only. Nothing here needs a Secret: the RCON password lives in the
minecraft pod's own RCON_PASSWORD env var (see ../../minecraft/Rcon.java),
read by rcon.jar *inside* the pod after we exec into it -- opsbot itself
never sees or needs the password.
"""
from __future__ import annotations

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


class NotFoundError(Exception):
    """A namespace/deployment/pod the caller asked for doesn't exist (or isn't Running)."""


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
        result.append(
            {
                "name": pod.metadata.name,
                "phase": pod.status.phase,
                "ready": ready,
                "restarts": restarts,
            }
        )
    return result


def list_deployment_names(namespace: str) -> list[str]:
    """For the /deploy restart autocomplete -- real, currently-existing
    Deployment names in a namespace, so the user picks from a live list
    instead of having to remember/spell one exactly."""
    apps = client.AppsV1Api()
    return [d.metadata.name for d in apps.list_namespaced_deployment(namespace).items]


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
