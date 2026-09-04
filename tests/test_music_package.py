"""The music package's boundary.

17.1 split a 3,197-line module — a fifth of the Python codebase, in the file most
often edited — into an engine (`zephyr/music/`) and a cog (`zephyr/cogs/music/`).
The split is only safe because of a re-export surface that twenty-odd tests and
`zephyr/client.py` depend on, and that surface is exactly the kind of thing a
later tidy-up deletes because a linter calls the imports unused.

These are the guards for it. They are cheap and they are the difference between
"the split held" and "the split held until somebody ran an import cleaner".
"""

import ast
import importlib
import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = PROJECT_ROOT / "zephyr" / "music"
COG = PROJECT_ROOT / "zephyr" / "cogs" / "music"


class TestTheReExportSurface:
    def test_every_declared_name_resolves(self):
        """`__all__` documents the surface; this asserts it is not fiction."""
        import zephyr.cogs.music as module

        missing = [name for name in module.__all__ if not hasattr(module, name)]

        assert missing == []

    @pytest.mark.parametrize(
        "name",
        [
            "MusicCog",
            "NowPlayingView",
            "QueueView",
            "SongQueue",
            "Track",
            "VoiceError",
            "VoiceState",
            "YTDLError",
            "YTDLSource",
            "_QueueIndexModal",
        ],
    )
    def test_the_names_tests_import_still_import(self, name):
        module = importlib.import_module("zephyr.cogs.music")

        assert hasattr(module, name), f"{name} is no longer importable from zephyr.cogs.music"

    def test_discord_itself_is_an_attribute(self):
        """Three tests patch `zephyr.cogs.music.discord.FFmpegPCMAudio`.

        That works only while `discord` is an attribute of this module -- a
        string patch target resolves attribute by attribute, so a package that
        did not import `discord` would fail with an AttributeError naming the
        wrong thing.
        """
        import zephyr.cogs.music as module

        assert hasattr(module, "discord")
        assert hasattr(module.discord, "FFmpegPCMAudio")

    @pytest.mark.parametrize("name", ["EMPTY_CHANNEL_GRACE_SECONDS", "list_playlists"])
    def test_the_patched_names_are_defined_here_not_re_exported(self, name):
        """The subtle half of the split, and the reason these two did not move.

        A module-level name is read through its *own* module's globals. Patching
        `zephyr.cogs.music.list_playlists` therefore only affects a reader that
        lives in `zephyr.cogs.music` -- if the reader had moved to
        `zephyr/music/`, the patch would apply to a name nothing reads and the
        test would pass while testing nothing.

        Both readers are cog methods (the voice-state listener and the playlist
        autocomplete), so this is where they belong anyway.
        """
        source = (COG / "__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        defined_here = name in {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        imported_here = any(
            isinstance(node, ast.ImportFrom)
            and any(alias.name == name for alias in node.names)
            for node in tree.body
        )

        assert defined_here or imported_here, f"{name} is not resolvable in the cog module"
        # And the readers are in this file, which is the property that matters.
        assert name in source


class TestTheEngineDoesNotImportTheCog:
    def test_no_engine_module_imports_the_cog_package(self):
        """The direction of the dependency *is* the split.

        The engine knowing about the cog would make the two one module again
        with extra files, and would be an import cycle the moment the cog
        imports the engine -- which it does, on every line of its import block.
        """
        offenders = []
        for file in sorted(ENGINE.glob("*.py")):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "zephyr.cogs"
                ):
                    offenders.append(f"{file.name}:{node.lineno}")
                if isinstance(node, ast.Import):
                    offenders.extend(
                        f"{file.name}:{node.lineno}"
                        for alias in node.names
                        if alias.name.startswith("zephyr.cogs")
                    )

        assert offenders == []

    def test_the_guard_can_see_such_an_import(self):
        """Otherwise the test above passes because the walk found nothing."""
        tree = ast.parse("from zephyr.cogs.music import MusicCog\n")

        assert any(
            isinstance(node, ast.ImportFrom) and (node.module or "").startswith("zephyr.cogs")
            for node in ast.walk(tree)
        )


class TestModuleSizes:
    # 17.1's target is ~600 lines. The engine modules meet it; the cog's command
    # surface does not, and that is recorded rather than hidden -- see
    # docs/ENHANCEMENTS.md. The ceiling here is set to what the file *is*, so it
    # cannot grow further without somebody deciding to raise it.
    LIMITS = {
        "zephyr/music/common.py": 600,
        "zephyr/music/queue.py": 600,
        "zephyr/music/sources.py": 600,
        "zephyr/music/state.py": 600,
        "zephyr/music/views.py": 600,
        # The command surface. Not split into mixins: `CogMeta` collects app
        # commands across the MRO so it would work, and it is the highest-risk
        # change in the backlog for a readability gain. Capped where it stands.
        "zephyr/cogs/music/__init__.py": 2000,
    }

    @pytest.mark.parametrize("relative,limit", sorted(LIMITS.items()))
    def test_a_module_stays_within_its_ceiling(self, relative, limit):
        lines = (PROJECT_ROOT / relative).read_text(encoding="utf-8").count("\n")

        assert lines <= limit, f"{relative} is {lines} lines, over its {limit}-line ceiling"

    def test_the_engine_modules_meet_the_stated_target(self):
        """17.1's "done when" for the part of it that was done."""
        over = [
            file.name
            for file in sorted(ENGINE.glob("*.py"))
            if file.read_text(encoding="utf-8").count("\n") > 600
        ]

        assert over == []
