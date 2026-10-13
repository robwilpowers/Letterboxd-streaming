"""Minimal HTTP helper on top of urllib (no third-party dependencies)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 letterboxd-streaming/0.1"
)


class HTTPError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url
        self.body = body


def get(url: str, headers: dict | None = None, retries: int = 3, timeout: float = 20) -> str:
    """GET a URL and return the body as text. Retries on 429 and 5xx."""
    all_headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    all_headers.update(headers or {})
    delay = 1.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=all_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            if attempt < retries and (e.code == 429 or e.code >= 500):
                retry_after = e.headers.get("Retry-After") if e.headers else None
                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                delay *= 2
                continue
            raise HTTPError(e.code, url, body) from None
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise AssertionError("unreachable")


def get_json(url: str, headers: dict | None = None, **kw):
    return json.loads(get(url, headers=headers, **kw))
