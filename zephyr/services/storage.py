"""Portable settings storage.

Local development uses ``settings.json``. In the cloud, set ``REDIS_URL``
to share AI settings across multiple bot instances.

Both backends store the same JSON payload so behavior is identical regardless
of where the bot runs.
"""

import json
import os
from abc import ABC, abstractmethod

from zephyr.config import SETTINGS_PATH, REDIS_URL


class BaseStorage(ABC):
    """Abstract storage backend for Zephyr's persisted settings."""

    @abstractmethod
    def load(self) -> dict:
        """Load and return the settings dictionary."""

    @abstractmethod
    def save(self, data: dict) -> None:
        """Persist the settings dictionary."""


class FileStorage(BaseStorage):
    """Default local file-based storage."""

    def __init__(self, path: str = None):
        self.path = path or SETTINGS_PATH

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            print(f"[Storage] Failed to load {self.path}: {exc}")
            return {}

    def save(self, data: dict) -> None:
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as exc:
            print(f"[Storage] Failed to save {self.path}: {exc}")


class RedisStorage(BaseStorage):
    """Redis-backed storage for shared state across cloud instances."""

    KEY = "zephyr:settings"

    def __init__(self, url: str = None):
        import redis  # imported lazily so the dependency is optional

        self.client = redis.from_url(url or REDIS_URL)

    def load(self) -> dict:
        try:
            raw = self.client.get(self.KEY)
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            print(f"[Storage] Failed to load from Redis: {exc}")
            return {}

    def save(self, data: dict) -> None:
        try:
            self.client.set(self.KEY, json.dumps(data, indent=4))
        except Exception as exc:
            print(f"[Storage] Failed to save to Redis: {exc}")


def get_storage() -> BaseStorage:
    """Return the storage backend selected by the environment."""
    if REDIS_URL:
        try:
            return RedisStorage()
        except Exception as exc:
            print(f"[Storage] REDIS_URL is set but Redis is unavailable: {exc}")
            print("[Storage] Falling back to file storage.")
    return FileStorage()


# Module-level singleton used by the rest of the app.
storage = get_storage()
