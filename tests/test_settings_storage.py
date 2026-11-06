"""A failed settings load must never lead to the stored settings being wiped."""

import pytest

from zephyr.services import gemini
from zephyr.services.storage import FileStorage


class FlakyStorage:
    def __init__(self, stored):
        self.stored = stored
        self.available = True
        self.saved = []

    def load(self):
        if not self.available:
            raise ConnectionError("database unreachable")
        return dict(self.stored)

    def save(self, data):
        if not self.available:
            raise ConnectionError("database unreachable")
        self.saved.append(data)
        self.stored = data


@pytest.fixture
def flaky(monkeypatch):
    store = FlakyStorage({"user_settings": {"SERVER-1": {"ai_model": "gemini-2.5-flash", "response_format": "text"}},
                          "SERVER-1": {"ai_model": "gemini-2.5-flash", "response_format": "text"}})
    monkeypatch.setattr(gemini, "storage", store)
    monkeypatch.setattr(gemini, "settings_store", {})
    monkeypatch.setattr(gemini, "user_settings", {})
    monkeypatch.setattr(gemini, "_settings_loaded", False)
    return store


def test_a_save_after_a_failed_boot_load_keeps_other_guilds(flaky):
    flaky.available = False
    gemini.load_user_settings()
    flaky.available = True

    gemini.set_context_settings(server_id=2, user_id=9, settings={"ai_model": "gemini-2.5-flash", "response_format": "embed"})
    assert gemini.save_user_settings() is True

    saved = flaky.saved[-1]["user_settings"]
    assert "SERVER-1" in saved and "SERVER-2" in saved


def test_saving_is_refused_while_storage_is_still_unreadable(flaky):
    flaky.available = False
    gemini.load_user_settings()

    gemini.set_context_settings(server_id=2, user_id=9, settings={"ai_model": "gemini-2.5-flash", "response_format": "embed"})

    assert gemini.save_user_settings() is False
    assert flaky.saved == []


def test_a_failed_save_is_reported(flaky):
    gemini.load_user_settings()
    flaky.available = False
    assert gemini.save_user_settings() is False


def test_a_corrupt_settings_file_raises_rather_than_reading_as_empty(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        FileStorage(str(path)).load()


def test_file_storage_round_trip(tmp_path):
    storage = FileStorage(str(tmp_path / "nested" / "settings.json"))
    storage.save({"a": 1})
    assert storage.load() == {"a": 1}
