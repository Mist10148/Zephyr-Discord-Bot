"""Tests for the Phase 7 /summarize throttle in zephyr.cogs.chat.

Pure cooldown arithmetic over module-level state -- no Discord, no Gemini. The
state is process-global (a slash command has nowhere else to keep it), so each
test clears it first.
"""

import pytest

from zephyr.cogs import chat


@pytest.fixture(autouse=True)
def clear_cooldowns():
    chat.summarize_user_cooldowns.clear()
    chat.summarize_guild_cooldowns.clear()
    yield
    chat.summarize_user_cooldowns.clear()
    chat.summarize_guild_cooldowns.clear()


class TestCooldown:
    def test_a_fresh_caller_may_summarize(self):
        allowed, remaining = chat._check_summarize_cooldown(1, 10)
        assert allowed is True
        assert remaining == 0

    def test_the_same_user_is_blocked_immediately_after(self):
        chat._update_summarize_cooldown(1, 10)
        allowed, remaining = chat._check_summarize_cooldown(1, 10)
        assert allowed is False
        assert 0 < remaining <= chat.SUMMARIZE_USER_COOLDOWN

    def test_a_different_user_hits_the_shorter_guild_gap(self):
        chat._update_summarize_cooldown(1, 10)
        allowed, remaining = chat._check_summarize_cooldown(2, 10)
        assert allowed is False
        # The guild gap, not the user gap: user 2 has no personal cooldown.
        assert remaining <= chat.SUMMARIZE_GUILD_COOLDOWN

    def test_a_user_in_another_guild_is_unaffected(self):
        chat._update_summarize_cooldown(1, 10)
        assert chat._check_summarize_cooldown(2, 99)[0] is True

    def test_a_dm_has_no_guild_gap(self):
        """guild_id is None in a DM; only the per-user cooldown applies."""
        chat._update_summarize_cooldown(1, None)
        assert chat._check_summarize_cooldown(2, None)[0] is True

    def test_it_frees_up_once_the_window_passes(self):
        chat._update_summarize_cooldown(1, 10)
        # Reach past the clock rather than sleeping: expire both entries by hand.
        chat.summarize_user_cooldowns[1] = 0
        chat.summarize_guild_cooldowns[10] = 0
        assert chat._check_summarize_cooldown(1, 10)[0] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
