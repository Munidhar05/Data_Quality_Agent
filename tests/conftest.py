import pytest
import backend.auth as auth

@pytest.fixture(autouse=True)
def isolated_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "accounts.sqlite3")
