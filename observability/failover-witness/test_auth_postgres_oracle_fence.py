from pathlib import Path


SCRIPT = Path(__file__).with_name("fence-auth-postgres-oracle.sh")


def test_auth_oracle_fencer_is_narrow_and_confirmed():
    text = SCRIPT.read_text()
    assert 'NAMESPACE="auth"' in text
    assert 'STATEFULSET="auth-postgresql-standby"' in text
    assert 'SERVICE="auth-postgresql-standby"' in text
    assert '"${1:-}" == "--confirm" && "$#" == 1' in text
    assert 'scale statefulset "$STATEFULSET" --replicas=0' not in text
    assert 'patch statefulset "$STATEFULSET" --type=merge' in text
    assert 'delete pod "$pod" --grace-period=0 --force' in text
    assert 'get endpoints "$SERVICE"' in text


if __name__ == "__main__":
    test_auth_oracle_fencer_is_narrow_and_confirmed()
    print("test_auth_postgres_oracle_fence: all assertions passed")
