"""Session cookie renewal, conditional /commands, cache eviction and the SPA route."""


def _set_cookie_names(response):
    return [header.split("=", 1)[0] for header in response.headers.getlist("Set-Cookie")]


class TestSlidingCookies:
    def test_an_authenticated_request_renews_both_cookies(self, app, client, logged_in, fake_redis):
        """The Redis TTL slides on every load; the cookies must slide with it."""
        response = client.get("/api/v1/me")

        names = _set_cookie_names(response)
        assert app.config["AUTH_COOKIE_NAME"] in names
        assert app.config["CSRF_COOKIE_NAME"] in names
        session_cookie = next(h for h in response.headers.getlist("Set-Cookie")
                              if h.startswith(app.config["AUTH_COOKIE_NAME"] + "="))
        assert f"Max-Age={app.config['AUTH_SESSION_TTL']}" in session_cookie
        assert "HttpOnly" in session_cookie

    def test_an_anonymous_request_sets_no_cookies(self, app, client, fake_redis):
        assert _set_cookie_names(client.get("/api/v1/commands")) == []

    def test_logout_still_clears_rather_than_renews(self, app, client, logged_in, fake_redis):
        response = client.post("/api/v1/auth/logout", headers={"X-Zephyr-CSRF": logged_in.csrf})

        session_cookies = [h for h in response.headers.getlist("Set-Cookie")
                           if h.startswith(app.config["AUTH_COOKIE_NAME"] + "=")]
        assert len(session_cookies) == 1
        assert "Max-Age=0" in session_cookies[0] or "Expires=Thu, 01 Jan 1970" in session_cookies[0]


class TestCommandsEtag:
    def test_a_matching_if_none_match_is_a_304(self, client, fake_redis):
        first = client.get("/api/v1/commands")
        etag = first.headers["ETag"]

        again = client.get("/api/v1/commands", headers={"If-None-Match": etag})

        assert again.status_code == 304


class TestCacheEviction:
    def test_a_recently_read_key_survives_eviction(self):
        from website.api.cache import TTLCache

        cache = TTLCache(maxsize=2)
        cache.get_or_load("a", 60, lambda: 1)
        cache.get_or_load("b", 60, lambda: 2)
        cache.get_or_load("a", 60, lambda: 99)  # a hit: "a" is now most recent
        cache.get_or_load("c", 60, lambda: 3)  # evicts the least recent: "b"

        assert cache.get_or_load("a", 60, lambda: "reloaded") == 1
        assert cache.get_or_load("b", 60, lambda: "reloaded") == "reloaded"


class TestSpaRoute:
    def test_a_traversal_path_gets_the_same_answer_as_a_missing_file(self, client, fake_redis):
        """Whether a file outside static/ exists must not change the response."""
        existing = client.get("/..%2F..%2Fwsgi.py")
        missing = client.get("/..%2F..%2Fno-such-file.py")

        assert existing.status_code == missing.status_code
