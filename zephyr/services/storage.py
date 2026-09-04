"""Portable settings storage with database, Redis, and file backends.

All implementations intentionally expose the same small synchronous interface:
``load()`` returns a settings document and ``save()`` replaces it completely.
The bot imports this module before an event loop exists, so the database layer
must remain synchronous.
"""

import copy
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

from sqlalchemy import delete, select, text

from zephyr.config import (
    DATABASE_URL,
    DEFAULT_DATABASE_URL,
    REDIS_URL,
    SETTINGS_PATH,
    STORAGE_BACKEND,
)
from zephyr.db.engine import build_engine, create_schema, should_auto_create
from zephyr.db.models import AISettings, AppState


class BaseStorage(ABC):
    """Abstract storage backend for Zephyr's persisted settings."""

    @abstractmethod
    def load(self) -> dict:
        """Load and return the settings dictionary."""

    @abstractmethod
    def save(self, data: dict) -> None:
        """Persist the settings dictionary."""

    def close(self) -> None:
        """Release resources when applicable; file storage has nothing to close."""


class FileStorage(BaseStorage):
    """File-based fallback storage."""

    def __init__(self, path: str | None = None):
        self.path = path or SETTINGS_PATH

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            print(f"[Storage] Failed to load {self.path}: {exc}")
            return {}

    def save(self, data: dict) -> None:
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4)
        except Exception as exc:
            print(f"[Storage] Failed to save {self.path}: {exc}")


class RedisStorage(BaseStorage):
    """Redis-backed storage for the legacy shared settings document."""

    KEY = "zephyr:settings"

    def __init__(self, url: str | None = None):
        import redis  # imported lazily so the dependency remains optional

        self.client = redis.from_url(url or REDIS_URL)
        self.client.ping()

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

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass


class DatabaseStorage(BaseStorage):
    """SQL-backed full-document storage for persistent AI settings."""

    def __init__(self, url: str | None = None, *, auto_create: bool | None = None):
        self.url = url or DATABASE_URL or DEFAULT_DATABASE_URL
        self.engine = build_engine(self.url)
        if should_auto_create(self.url) if auto_create is None else auto_create:
            create_schema(self.engine)
        # Connect now: a lazy failure would look like settings disappearing later.
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        self._hint_if_empty()

    @staticmethod
    def _is_context_entry(value: object) -> bool:
        return isinstance(value, dict) and (
            "ai_model" in value or "response_format" in value
        )

    @classmethod
    def _decompose(cls, data: dict) -> tuple[dict[str, object], dict[str, object]]:
        payload = data if isinstance(data, dict) else {}
        contexts: dict[str, object] = {}
        nested = payload.get("user_settings")
        if isinstance(nested, dict):
            # The legacy writer duplicates every context here.  Preserve all
            # nested keys exactly instead of attempting to parse their shape.
            contexts.update(copy.deepcopy(nested))

        app_state: dict[str, object] = {}
        for key, value in payload.items():
            if key == "user_settings":
                continue
            if cls._is_context_entry(value):
                contexts[key] = copy.deepcopy(value)
            else:
                app_state[key] = copy.deepcopy(value)
        return contexts, app_state

    def _hint_if_empty(self) -> None:
        try:
            with self.engine.connect() as connection:
                empty = connection.execute(select(AISettings.context_key).limit(1)).first() is None
            if empty and Path(SETTINGS_PATH).exists():
                print("[Storage] Database is empty; run python -m scripts.import_settings to migrate settings.json.")
        except Exception:
            # Constructor connection verification has already surfaced failures.
            pass

    def load(self) -> dict:
        try:
            with self.engine.connect() as connection:
                contexts = connection.execute(
                    select(AISettings.context_key, AISettings.data).order_by(AISettings.context_key)
                ).all()
                state = connection.execute(
                    select(AppState.key, AppState.data).order_by(AppState.key)
                ).all()
            if not contexts and not state:
                return {}
            nested = {key: copy.deepcopy(value) for key, value in contexts}
            result: dict[str, object] = {"user_settings": copy.deepcopy(nested)}
            result.update({key: copy.deepcopy(value) for key, value in state})
            result.update(copy.deepcopy(nested))
            return result
        except Exception as exc:
            print(f"[Storage] Failed to load from database: {exc}")
            return {}

    def save(self, data: dict) -> None:
        try:
            contexts, app_state = self._decompose(data)
            with self.engine.begin() as connection:
                connection.execute(delete(AISettings))
                connection.execute(delete(AppState))
                if contexts:
                    connection.execute(
                        AISettings.__table__.insert(),
                        [{"context_key": key, "data": value} for key, value in contexts.items()],
                    )
                if app_state:
                    connection.execute(
                        AppState.__table__.insert(),
                        [{"key": key, "data": value} for key, value in app_state.items()],
                    )
        except Exception as exc:
            print(f"[Storage] Failed to save to database: {exc}")

    def close(self) -> None:
        self.engine.dispose()


def _database_storage(url: str | None = None) -> BaseStorage:
    return DatabaseStorage(url=url)


def get_storage() -> BaseStorage:
    """Select storage, falling back to the file backend if setup fails."""
    backend = STORAGE_BACKEND
    try:
        if backend == "file":
            return FileStorage()
        if backend == "redis":
            return RedisStorage()
        if backend in {"database", "db"}:
            return _database_storage()
        if backend not in {"auto", ""}:
            raise ValueError(f"unsupported STORAGE_BACKEND={backend!r}")

        # This order protects deployed Redis data until a database is explicitly
        # provisioned; local SQLite is only the final automatic choice.
        if DATABASE_URL:
            return _database_storage(DATABASE_URL)
        if REDIS_URL:
            return RedisStorage()
        return _database_storage(DEFAULT_DATABASE_URL)
    except Exception as exc:
        print(f"[Storage] {backend} storage is unavailable: {exc}")
        print("[Storage] Falling back to file storage.")
        return FileStorage()


# Module-level singleton used by the rest of the app.
storage = get_storage()
