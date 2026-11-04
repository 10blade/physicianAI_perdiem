"""A small HTTP client with retries, shared by every job board."""

from __future__ import annotations

from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__
from .util import redact

USER_AGENT = f"physicianai-perdiem/{__version__} (+https://github.com/10blade/physicianai_perdiem)"


class HttpError(Exception):
    """A request failed. ``status`` is None when no HTTP response came back."""

    def __init__(self, status: Optional[int], url: str, detail: str = ""):
        self.status = status
        self.url = url.split("?", 1)[0]
        what = f"HTTP {status}" if status else "request failed"
        super().__init__(redact(f"{what} for {self.url}" + (f": {detail}" if detail else "")))


class Http:
    def __init__(self, timeout: float = 30.0, retries: int = 2):
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    def _get(self, url: str, params: Optional[dict], accept: str) -> requests.Response:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout, headers={"Accept": accept})
        except requests.RequestException as exc:
            raise HttpError(None, url, type(exc).__name__) from None
        if response.status_code != 200:
            raise HttpError(response.status_code, url)
        return response

    def get_json(self, url: str, params: Optional[dict] = None) -> Any:
        response = self._get(url, params, "application/json")
        try:
            return response.json()
        except ValueError:
            raise HttpError(response.status_code, url, "response was not JSON") from None

    def get_text(self, url: str, params: Optional[dict] = None) -> str:
        response = self._get(url, params, "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8")
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response.text
