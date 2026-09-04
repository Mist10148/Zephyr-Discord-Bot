"""The music engine: sources, queue, playback state and the Discord views.

Split out of ``zephyr/cogs/music.py``, which was 3,197 lines -- a fifth of the
Python codebase in the file most often edited. The cog package keeps the command
surface and re-exports everything here, so every existing import of
``zephyr.cogs.music`` still resolves.

The seam was already there to be found: ``VoiceState``'s own docstring says it
takes *callbacks* rather than a back reference to the cog, "so this class still
knows nothing about Redis and nothing about the button view's permission model".
That is exactly the boundary this split follows.
"""
