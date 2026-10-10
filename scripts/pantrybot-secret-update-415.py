#!/usr/bin/env python3
"""Stage and promote Oracle PantryBot DB URLs without exposing credential values.

Run interactively on the Oracle node. This changes Kubernetes Secrets only;
it never changes PostgreSQL role passwords or restarts workloads.
"""

import argparse
import base64
import getpass
import json
import subprocess
import sys
from urllib.parse import unquote, urlsplit


NAMESPACE = "pantry-bot"
PLATFORM_SECRET = "pantry-bot-platform"
URL_KEY = "PANTRY_DATABASE_URL"
STAGED = {
    "old": "pantry-bot-db-url-old-415",
    "temp": "pantry-bot-db-url-temp-415",
    "final": "pantry-bot-db-url-final-415",
}
EXPECTED_USERS = {"old": "pantry", "temp": "pantry_rotation_415", "final": "pantry"}


def kubectl(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    # Fixed executable and argv; credential manifests travel only on stdin.
    return subprocess.run(["sudo", "-n", "k3s", "kubectl", "-n", NAMESPACE, *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        shell=False,
        timeout=60,
    )


def get_secret(name: str) -> dict:
    result = kubectl("get", "secret", name, "-o", "json")
    if result.returncode:
        raise RuntimeError(f"Could not read Secret {name} (exit {result.returncode}).")
    return json.loads(result.stdout)


def checked_url(encoded: str, expected_user: str) -> None:
    try:
        value = base64.b64decode(encoded, validate=True).decode()
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "postgresql"
            and parsed.hostname == "pantry-postgres.pantry-bot.svc.cluster.local"
            and parsed.port == 5432
            and parsed.path == "/pantry"
            and parsed.username == expected_user
            and parsed.password
            and not parsed.query
            and not parsed.fragment
        )
    except (ValueError, UnicodeDecodeError):
        valid = False
    if not valid:
        raise RuntimeError("Staged URL target, role, or shape is unexpected.")


def create_staged(name: str, encoded: str) -> None:
    parsed = urlsplit(base64.b64decode(encoded).decode())
    connection_fields = {
        "PGHOST": parsed.hostname or "",
        "PGPORT": str(parsed.port),
        "PGUSER": parsed.username or "",
        "PGPASSWORD": unquote(parsed.password or ""),
        "PGDATABASE": parsed.path.lstrip("/"),
    }
    obj = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "type": "Opaque",
        "data": {
            URL_KEY: encoded,
            **{key: base64.b64encode(value.encode()).decode()
               for key, value in connection_fields.items()},
        },
    }
    result = kubectl("create", "-f", "-", input_text=json.dumps(obj))
    if result.returncode:
        raise RuntimeError(f"Could not create staged Secret {name} (exit {result.returncode}).")
    print(f"Created {name}; value omitted.")


def replace_key(name: str, key: str, encoded: str) -> None:
    secret = get_secret(name)
    if key not in secret.get("data", {}):
        raise RuntimeError(f"{name} is missing key {key}.")
    metadata = secret["metadata"]
    # kubectl apply records a full Secret data snapshot here. Keeping it would
    # retain an older encoded credential after the data field is rotated.
    annotations = {
        k: v for k, v in metadata.get("annotations", {}).items()
        if k != "kubectl.kubernetes.io/last-applied-configuration"
    }
    replacement = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            k: metadata[k]
            for k in ("name", "namespace", "resourceVersion", "labels")
            if k in metadata
        },
        "type": secret["type"],
        "data": {**secret["data"], key: encoded},
    }
    if annotations:
        replacement["metadata"]["annotations"] = annotations
    if "immutable" in secret:
        replacement["immutable"] = secret["immutable"]
    result = kubectl("replace", "-f", "-", input_text=json.dumps(replacement))
    if result.returncode:
        raise RuntimeError(f"Could not replace Secret {name} (exit {result.returncode}).")
    print(f"Updated {name}/{key}; value omitted.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=(
        "snapshot-old", "stage-temp", "stage-final", "promote-old",
        "promote-temp", "promote-final", "set-bootstrap-password",
    ))
    args = parser.parse_args()

    try:
        if args.action == "snapshot-old":
            current = get_secret(PLATFORM_SECRET)["data"][URL_KEY]
            checked_url(current, "pantry")
            create_staged(STAGED["old"], current)
        elif args.action.startswith("stage-"):
            if not sys.stdin.isatty():
                raise RuntimeError("An interactive terminal is required.")
            stage = args.action.removeprefix("stage-")
            value = getpass.getpass(f"{stage} PostgreSQL URL (input hidden): ")
            if not value:
                raise RuntimeError("Empty URL refused.")
            encoded = base64.b64encode(value.encode()).decode()
            checked_url(encoded, EXPECTED_USERS[stage])
            old_password = get_secret(STAGED["old"])["data"]["PGPASSWORD"]
            new_password = base64.b64encode(unquote(urlsplit(value).password or "").encode()).decode()
            if new_password == old_password:
                raise RuntimeError("New password matches the staged old password; refusing reuse.")
            if stage == "final" and new_password == get_secret(STAGED["temp"])["data"]["PGPASSWORD"]:
                raise RuntimeError("Final password matches the temporary password; refusing reuse.")
            create_staged(STAGED[stage], encoded)
        elif args.action.startswith("promote-"):
            stage = args.action.removeprefix("promote-")
            encoded = get_secret(STAGED[stage])["data"][URL_KEY]
            checked_url(encoded, EXPECTED_USERS[stage])
            replace_key(PLATFORM_SECRET, URL_KEY, encoded)
        elif args.action == "set-bootstrap-password":
            if not sys.stdin.isatty():
                raise RuntimeError("An interactive terminal is required.")
            value = getpass.getpass("New pantry role password (input hidden): ")
            if not value:
                raise RuntimeError("Empty password refused.")
            encoded = base64.b64encode(value.encode()).decode()
            if encoded != get_secret(STAGED["final"])["data"]["PGPASSWORD"]:
                raise RuntimeError("Password does not match staged final URL; Secret unchanged.")
            replace_key("pantry-bot-postgres-standby", "POSTGRES_PASSWORD", encoded)
    except subprocess.TimeoutExpired:
        print("Kubernetes operation timed out; inspect state before retrying.", file=sys.stderr)
        return 1
    except (KeyError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
