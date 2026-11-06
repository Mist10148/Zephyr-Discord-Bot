"""/image-gen reserves its cooldown up front and refunds it on failure."""

import pytest

from zephyr.cogs import chat


@pytest.fixture(autouse=True)
def clear_cooldowns():
    chat.image_gen_user_cooldowns.clear()
    chat.image_gen_guild_cooldowns.clear()
    yield
    chat.image_gen_user_cooldowns.clear()
    chat.image_gen_guild_cooldowns.clear()


def test_a_reservation_blocks_a_concurrent_request():
    assert chat._check_image_gen_cooldown(1, 10)[0]
    chat._update_image_gen_cooldown(1, 10)

    allowed, remaining = chat._check_image_gen_cooldown(1, 10)
    assert not allowed and remaining > 0


def test_a_failed_generation_is_refunded():
    reservation = chat._update_image_gen_cooldown(1, 10)
    chat._release_image_gen_cooldown(1, 10, reservation)

    assert chat._check_image_gen_cooldown(1, 10) == (True, 0)


def test_a_refund_restores_an_earlier_deadline():
    chat.image_gen_user_cooldowns[1] = 5.0
    reservation = chat._update_image_gen_cooldown(1, None)
    chat._release_image_gen_cooldown(1, None, reservation)

    assert chat.image_gen_user_cooldowns[1] == 5.0
