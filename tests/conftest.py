"""Test helpers. All postings in these tests are synthetic, shaped like each API's documented format."""

from __future__ import annotations

import pytest

from perdiem_finder.http import HttpError


class FakeHttp:
    """Answers requests from a dict of URL -> JSON (or text, or an int status code)."""

    def __init__(self, json_routes=None, text_routes=None):
        self.json_routes = json_routes or {}
        self.text_routes = text_routes or {}
        self.calls: list[tuple[str, dict | None]] = []

    def _answer(self, routes, url, params):
        self.calls.append((url, params))
        if url not in routes:
            raise HttpError(404, url)
        value = routes[url]
        if isinstance(value, int):
            raise HttpError(value, url)
        if isinstance(value, Exception):
            raise value
        return value

    def get_json(self, url, params=None):
        return self._answer(self.json_routes, url, params)

    def get_text(self, url, params=None):
        return self._answer(self.text_routes, url, params)


@pytest.fixture
def fake_http():
    return FakeHttp
