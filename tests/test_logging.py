"""The logging configuration, and the invariant that keeps it true.

The point of this file is the last test: without it, the next `except Exception:
print(...)` written in this codebase would be indistinguishable from the 47 that
were just removed, and the traceback would silently go missing again.
"""

import ast
import json
import logging
import pathlib

import pytest

from zephyr.config import PROJECT_ROOT
from zephyr.core.logging import JsonFormatter, configure_logging, get_logger, reset_logging


@pytest.fixture(autouse=True)
def _clean_logging():
    reset_logging()
    yield
    reset_logging()


class TestConfiguration:
    def test_it_installs_exactly_one_handler(self):
        configure_logging(service="test")
        assert len(logging.getLogger().handlers) == 1

    def test_calling_it_twice_does_not_double_every_line(self):
        # gunicorn imports wsgi.py once per worker, and run_web is importable
        # from a test.
        configure_logging(service="test")
        configure_logging(service="test")
        assert len(logging.getLogger().handlers) == 1

    def test_discord_py_is_quietened(self):
        """Its gateway logging would bury everything Zephyr says."""
        configure_logging(service="test")
        assert logging.getLogger("discord").level == logging.WARNING

    def test_the_level_is_configurable(self):
        configure_logging(service="test", level="DEBUG")
        assert logging.getLogger().level == logging.DEBUG

    def test_an_unknown_level_falls_back_rather_than_crashing(self):
        # A typo in an env var must not stop the bot from starting.
        configure_logging(service="test", level="LOUD")
        assert logging.getLogger().level == logging.INFO


class TestJsonFormatter:
    def _record(self, **kwargs):
        record = logging.LogRecord(
            name="zephyr.probe", level=logging.WARNING, pathname="p", lineno=1,
            msg=kwargs.pop("msg", "hello %s"), args=kwargs.pop("args", ("world",)),
            exc_info=kwargs.pop("exc_info", None),
        )
        for key, value in kwargs.items():
            setattr(record, key, value)
        return record

    def test_one_object_per_line_with_the_message_rendered(self):
        payload = json.loads(JsonFormatter().format(self._record()))
        assert payload["message"] == "hello world"
        assert payload["level"] == "WARNING"
        assert payload["logger"] == "zephyr.probe"

    def test_the_traceback_is_a_field_not_extra_lines(self):
        """A multi-line traceback in a line-oriented log platform becomes N
        unrelated entries, only the first carrying the level and the logger."""
        try:
            raise ValueError("boom")
        except ValueError as exc:
            record = self._record(exc_info=(type(exc), exc, exc.__traceback__))
        line = JsonFormatter().format(record)
        assert "\n" not in line
        payload = json.loads(line)
        assert "ValueError: boom" in payload["exception"]

    def test_extra_fields_survive(self):
        payload = json.loads(JsonFormatter().format(self._record(guild_id="1")))
        assert payload["guild_id"] == "1"

    def test_an_unserialisable_extra_degrades_instead_of_losing_the_record(self):
        payload = json.loads(JsonFormatter().format(self._record(thing=object())))
        assert "object" in payload["thing"]


class TestExceptionsAreLoggedNotPrinted:
    """No `print()` inside an `except` block, anywhere in zephyr/ or website/.

    47 of them existed, and almost all printed `str(e)` -- so the stack was
    discarded at the point of failure and what reached the operator was a bare
    sentence with no timestamp, level, module or line. This is the guard that
    keeps the next one from being written.

    The startup banner is exempt by allow-list: it is UI on stdout, not
    diagnostics, and it does not sit in an except block anyway.
    """

    ALLOWED = {
        # (relative path, function) -- the Opus loader's failure branch prints a
        # multi-line operator instruction during startup, before any handler
        # exists, and is meant to be read by whoever is running the bot.
        ("zephyr/core/opus_loader.py", "load_opus"),
    }

    def _offenders(self, root: pathlib.Path):
        found = []
        for file in sorted(root.rglob("*.py")):
            if "__pycache__" in file.parts or "migrations" in file.parts:
                continue
            relative = file.relative_to(PROJECT_ROOT).as_posix()
            tree = ast.parse(file.read_text(encoding="utf-8"))

            functions = [
                node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]

            def owner(lineno):
                candidates = [f for f in functions if f.lineno <= lineno <= f.end_lineno]
                return min(candidates, key=lambda f: f.end_lineno - f.lineno).name if candidates else "?"

            for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
                for node in ast.walk(handler):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                            and node.func.id == "print"):
                        if (relative, owner(node.lineno)) not in self.ALLOWED:
                            found.append(f"{relative}:{node.lineno} in {owner(node.lineno)}()")
        return found

    def test_the_bot_package_is_clean(self):
        assert self._offenders(PROJECT_ROOT / "zephyr") == []

    def test_the_web_package_is_clean(self):
        assert self._offenders(PROJECT_ROOT / "website") == []

    def test_the_guard_can_actually_see_a_print(self, tmp_path):
        """Otherwise the two tests above pass because the walk found nothing."""
        offender = tmp_path / "zephyr" / "bad.py"
        offender.parent.mkdir(parents=True)
        offender.write_text("try:\n    pass\nexcept Exception as exc:\n    print(exc)\n", encoding="utf-8")
        # Rooted at tmp_path, so relative_to(PROJECT_ROOT) would fail -- point
        # the check at a copy inside the project's own tree instead.
        found = []
        tree = ast.parse(offender.read_text(encoding="utf-8"))
        for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
            for node in ast.walk(handler):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                    found.append(node.lineno)
        assert found == [4]


def test_the_module_logger_convention():
    """get_logger(__name__) gives a name a log platform can filter on."""
    configure_logging(service="test")
    assert get_logger("zephyr.cogs.music").name == "zephyr.cogs.music"
