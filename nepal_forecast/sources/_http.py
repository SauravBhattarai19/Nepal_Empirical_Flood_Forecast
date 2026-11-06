# -*- coding: utf-8 -*-
"""Tiny HTTP helper: timeouts, retries with back-off, a user agent."""

import json
import time
import urllib.error
import urllib.request

from . import FeedError

USER_AGENT = "nepal-empirical-flood-forecast/0.1 (+https://github.com/SauravBhattarai19/Nepal_Empirical_Flood_Forecast)"


def get(url, timeout=60, retries=4, headers=None, binary=False):
    hdr = {"User-Agent": USER_AGENT}
    hdr.update(headers or {})
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                return data if binary else data.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            last = f"HTTP {exc.code} {exc.reason} {body}".strip()
            if exc.code < 500 and exc.code != 429:
                break
            time.sleep(min(5 * 2 ** attempt, 60) if exc.code == 429 else 2 ** attempt)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = str(getattr(exc, "reason", exc))
            time.sleep(2 ** attempt)
    raise FeedError(f"{url.split('?')[0]} did not answer ({last})")


def get_json(url, **kw):
    text = get(url, **kw)
    try:
        return json.loads(text)
    except ValueError:
        raise FeedError(f"{url.split('?')[0]} sent something that is not JSON: {text[:120]!r}")
