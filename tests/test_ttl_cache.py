"""TTLCache coalesces concurrent loads, including failed ones."""

import threading
import time

import pytest

from website.api.cache import TTLCache


def _race(cache, loader, callers=5):
    results, start = [], threading.Barrier(callers)

    def call():
        start.wait()
        try:
            results.append(cache.get_or_load("key", 60, loader))
        except Exception as exc:  # noqa: BLE001 - the error is the result here
            results.append(exc)

    threads = [threading.Thread(target=call) for _ in range(callers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    return results


def test_concurrent_callers_share_one_successful_load():
    calls = []

    def loader():
        calls.append(1)
        time.sleep(0.1)
        return "value"

    assert _race(TTLCache(), loader) == ["value"] * 5
    assert len(calls) == 1


def test_a_failed_load_is_shared_with_waiters_rather_than_retried_serially():
    calls = []

    def loader():
        calls.append(1)
        time.sleep(0.1)
        raise RuntimeError("upstream down")

    results = _race(TTLCache(), loader)

    assert len(results) == 5
    assert all(isinstance(result, RuntimeError) for result in results)
    assert len(calls) == 1


def test_the_next_call_after_a_failure_tries_again():
    cache, outcomes = TTLCache(), iter([RuntimeError("down"), "recovered"])

    def loader():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with pytest.raises(RuntimeError):
        cache.get_or_load("key", 60, loader)
    assert cache.get_or_load("key", 60, loader) == "recovered"


def test_a_stale_value_answers_when_the_refresh_fails():
    cache = TTLCache()
    cache.get_or_load("key", 0, lambda: "stale")

    def failing():
        raise RuntimeError("down")

    assert cache.get_or_load("key", 60, failing) == "stale"
