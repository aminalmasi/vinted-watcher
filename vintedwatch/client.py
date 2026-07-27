"""Vinted IT client: anon-token bootstrap, catalog search, sold confirmation.

Everything goes through the Italy residential proxy, which rotates exits and
therefore hands us a dead connection now and then — hence `_get` retries.

Traffic matters: the €5 proxy plan is metered by GB, so the anon token is
cached in the state file and we only re-bootstrap when the API rejects us.
"""

from __future__ import annotations

import logging
import os
import re
import time

import requests

log = logging.getLogger(__name__)

BASE = "https://www.vinted.it"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# Cookies that together authenticate an anonymous browser session.
TOKEN_COOKIES = ("access_token_web", "refresh_token_web", "anon_id", "v_udt")
TOKEN_MAX_AGE = 90 * 60  # re-bootstrap after 90 min even if nothing 401s

# A live item's page carries these as `false`/`null`; a sold one flips them.
# NB: do NOT text-match "Venduto" — a live page contains it 6x in the JS bundle.
_STATE_RE = {
    key: re.compile(rf'\\?"{key}\\?"\s*:\s*(\\?"[^",}}]*\\?"|true|false|null|\d+)')
    for key in ("is_closed", "is_hidden", "is_reserved", "item_closing_action")
}


class VintedClient:
    def __init__(self, token_cache: dict | None = None):
        self.session = requests.Session()
        proxy = os.environ.get("PROXY_URL")
        if not proxy:
            raise RuntimeError("PROXY_URL is required — Vinted blocks datacenter IPs")
        self.session.proxies = {"http": proxy, "https": proxy}
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.bytes_on_wire = 0
        self.token_cache = dict(token_cache or {})
        self._restore_cookies()

    # ---- token cache -----------------------------------------------------

    def _restore_cookies(self):
        saved_at = self.token_cache.get("saved_at", 0)
        if not saved_at or time.time() - saved_at > TOKEN_MAX_AGE:
            return
        for name in TOKEN_COOKIES:
            value = self.token_cache.get(name)
            if value:
                self.session.cookies.set(name, value, domain=".vinted.it")
        log.info("restored cached anon token (age %ds)", int(time.time() - saved_at))

    def _snapshot_cookies(self) -> dict:
        snap = {"saved_at": time.time()}
        for name in TOKEN_COOKIES:
            value = self.session.cookies.get(name)
            if value:
                snap[name] = value
        return snap

    @property
    def has_token(self) -> bool:
        return bool(self.session.cookies.get("access_token_web"))

    def bootstrap(self):
        """Fetch the homepage to mint a fresh anon token (~250 KB gzipped)."""
        r = self._get(BASE + "/")
        if r is None or r.status_code != 200:
            raise RuntimeError(f"bootstrap failed: {r.status_code if r else 'no response'}")
        self.token_cache = self._snapshot_cookies()
        log.info("bootstrapped anon token; cookies=%s", sorted(self.session.cookies.keys()))

    # ---- transport -------------------------------------------------------

    def _get(self, url, tries=4, **kw):
        kw.setdefault("timeout", 45)
        for attempt in range(tries):
            try:
                r = self.session.get(url, **kw)
            except requests.RequestException as exc:
                log.warning("GET %s failed (%s), retry %d/%d", url, type(exc).__name__, attempt + 1, tries)
                time.sleep(2 * (attempt + 1))
                continue
            # Vinted replies chunked, so content-length is usually absent;
            # raw.tell() counts the *compressed* bytes actually read, which is
            # what the proxy meters.
            try:
                r.content  # force the body to be consumed before measuring
                self.bytes_on_wire += r.raw.tell() or int(r.headers.get("content-length") or 0)
            except (AttributeError, ValueError, TypeError):
                pass
            return r
        return None

    # ---- API -------------------------------------------------------------

    def search(self, params: dict, page: int = 1) -> list[dict] | None:
        """One page of the catalog feed. Only ever returns *live* listings.

        Returns None if the page could not be fetched — the caller must not
        confuse that with an empty page, which means "end of results".
        """
        if not self.has_token:
            self.bootstrap()
        query = dict(params, page=page)
        url = BASE + "/api/v2/catalog/items"
        headers = {"Accept": "application/json", "Referer": BASE + "/catalog"}
        r = self._get(url, params=query, headers=headers)
        if r is not None and r.status_code in (401, 403):
            log.info("catalog returned %d — re-bootstrapping token", r.status_code)
            self.bootstrap()
            r = self._get(url, params=query, headers=headers)
        if r is None:
            log.error("catalog page %d: no response", page)
            return None
        if r.status_code != 200:
            log.error("catalog page %d: HTTP %d", page, r.status_code)
            return None
        try:
            return r.json().get("items", [])
        except ValueError:
            log.error("catalog page %d: body was not JSON", page)
            return None

    def check_sold(self, item_id: int, url: str) -> str:
        """Classify a listing that vanished from the feed.

        Returns 'sold', 'removed', 'live', or 'unknown'.
        """
        r = self._get(url or f"{BASE}/items/{item_id}")
        if r is None:
            return "unknown"
        if r.status_code in (404, 410):
            return "removed"
        if r.status_code != 200:
            log.warning("item %s: HTTP %d", item_id, r.status_code)
            return "unknown"

        html = r.text
        found = {}
        for key, rx in _STATE_RE.items():
            m = rx.search(html)
            if m:
                found[key] = m.group(1).strip('\\"')
        log.info("item %s page state: %s", item_id, found or "(no state json)")

        if not found:
            return "unknown"
        closing = found.get("item_closing_action")
        if closing not in (None, "null", ""):
            # e.g. "sold" — the seller closed it via a transaction.
            return "sold" if "sold" in closing.lower() else "removed"
        if found.get("is_closed") == "true":
            return "sold"
        if found.get("is_hidden") == "true":
            return "removed"
        return "live"


def parse_item(raw: dict) -> dict:
    """Keep only metadata — never images (brief: keep stored data small)."""
    price = raw.get("price") or {}
    total = raw.get("total_item_price") or {}
    photo = raw.get("photo") or {}
    hi = photo.get("high_resolution") or {}
    return {
        "id": raw.get("id"),
        "title": raw.get("title"),
        "brand": raw.get("brand_title"),
        "size": raw.get("size_title"),
        "condition": raw.get("status"),
        "price": price.get("amount"),
        "total_price": total.get("amount"),
        "currency": price.get("currency_code") or "EUR",
        "url": raw.get("url") or (BASE + (raw.get("path") or "")),
        "seller": (raw.get("user") or {}).get("login"),
        # The main photo's upload time is the best available proxy for
        # "when was this listed" — the feed carries no created_at.
        "photo_ts": hi.get("timestamp"),
    }
