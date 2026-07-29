"""Import a legacy settings document into a DatabaseStorage backend."""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

from zephyr.config import DATABASE_URL, REDIS_URL, SETTINGS_PATH
from zephyr.services.storage import DatabaseStorage, FileStorage, RedisStorage


def _read_source(args: argparse.Namespace) -> dict:
    source = args.source
    path = args.path or SETTINGS_PATH
    if source == "auto":
        source = "file" if Path(path).exists() else "redis"
    if source == "file":
        return FileStorage(path).load()
    if source == "redis":
        if not (args.redis_url or REDIS_URL):
            raise ValueError("--redis-url or REDIS_URL is required for Redis imports")
        return RedisStorage(args.redis_url).load()
    raise ValueError(f"Unsupported source: {source}")


def _merge(existing: dict, incoming: dict) -> dict:
    """Merge a legacy document without discarding settings already in Postgres."""
    result = copy.deepcopy(existing)
    result.update(copy.deepcopy(incoming))
    old_nested = existing.get("user_settings", {}) if isinstance(existing, dict) else {}
    new_nested = incoming.get("user_settings", {}) if isinstance(incoming, dict) else {}
    if isinstance(old_nested, dict) and isinstance(new_nested, dict):
        result["user_settings"] = {**copy.deepcopy(old_nested), **copy.deepcopy(new_nested)}
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import Zephyr settings into a database.")
    parser.add_argument("--source", choices=("auto", "file", "redis"), default="auto")
    parser.add_argument("--path", help="Legacy settings.json path")
    parser.add_argument("--redis-url", help="Source Redis connection URL")
    parser.add_argument("--database-url", help="Target database URL (never falls back)")
    parser.add_argument("--dry-run", action="store_true", help="Inspect without writing")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--merge", action="store_true", help="Merge with database contents (default)")
    mode.add_argument("--replace", action="store_true", help="Replace database contents")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = args.database_url or DATABASE_URL
    if not database_url:
        print("error: --database-url or DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        incoming = _read_source(args)
        if not isinstance(incoming, dict):
            raise ValueError("source did not contain a settings object")
        target = DatabaseStorage(url=database_url)
        payload = incoming if args.replace else _merge(target.load(), incoming)
        context_count, state_count = target._decompose(payload)
        print(f"Prepared {len(context_count)} context entries and {len(state_count)} state entries.")
        if args.dry_run:
            print("Dry run complete; database was not changed.")
            return 0
        target.save(payload)
        print("Import complete.")
        return 0
    except Exception as exc:
        print(f"error: import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
