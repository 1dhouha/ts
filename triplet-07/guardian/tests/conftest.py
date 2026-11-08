import pytest


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Tests must never read or write the real saved trust state."""
    monkeypatch.setenv("GUARDIAN_STATE_PATH", "")
