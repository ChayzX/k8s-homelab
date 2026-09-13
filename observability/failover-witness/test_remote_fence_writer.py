"""Contract tests for the ChaseBot forced-command fence relay."""

from pathlib import Path


SCRIPT = Path(__file__).with_name("remote-fence-writer.sh")


def test_accepts_only_the_gcp_confirmed_operation() -> None:
    text = SCRIPT.read_text()
    assert '"fence-pantry-postgres --confirm")' in text
    assert "fence-pantry-postgres.sh --confirm" in text
    assert "fence-writer-domain.sh" not in text


if __name__ == "__main__":
    test_accepts_only_the_gcp_confirmed_operation()
    print("test_remote_fence_writer: all assertions passed")
