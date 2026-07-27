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
import socket
import time
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

BASE = "https://www.vinted.it"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# Cookies that together authenticate an anonymous browser session.
TOKEN_COOKIES = ("access_token_web", "refresh_token_web", "anon_id", "v_udt")

# A live item's page carries these as `false`/`null`; a sold one flips them.
# NB: do NOT text-match "Venduto" — a live page contains it 6x in the JS bundle.
_STATE_RE = {
    key: re.compile(rf'\\?"{key}\\?"\s*:\s*(\\?"[^",}}]*\\?"|true|false|null|\d+)')
    for key in ("is_closed", "is_hidden", "is_reserved", "item_closing_action")
}


# gw.dataimpulse.com alternates between two DNS answer sets, and they are not
# equally alive. Measured 2026-07-27 across nine runs: every run that resolved to
# 185.209.176.103 / 69.67.149.191 succeeded, and every run that resolved to the
# 64.34.81.x block failed outright with RemoteDisconnected on every address.
# Resolution is stable within a process, so an unlucky run had no way out.
# We therefore always try the known-good addresses first, whatever DNS says.
KNOWN_GOOD_GATEWAYS = ("185.209.176.103", "69.67.149.191")


def _gateway_urls(proxy_url: str) -> list[str]:
    """Expand the proxy URL into one candidate per gateway address.

    Known-good addresses lead; anything else DNS offers follows as a fallback,
    so this still works if DataImpulse renumbers. The proxy hop is plain HTTP,
    so addressing it by IP mismatches no certificate.
    """
    parsed = urlsplit(proxy_url)
    host, port = parsed.hostname, parsed.port
    if not host:
        return [proxy_url]
    try:
        resolved = sorted({ai[4][0] for ai in socket.getaddrinfo(host, port, socket.AF_INET)})
    except OSError as exc:
        log.warning("could not resolve %s (%s)", host, exc)
        resolved = []
    ordered = list(KNOWN_GOOD_GATEWAYS) + [ip for ip in resolved if ip not in KNOWN_GOOD_GATEWAYS]
    auth = ""
    if parsed.username:
        auth = parsed.username + (f":{parsed.password}" if parsed.password else "") + "@"
    log.info("gateways: %s (DNS offered %s)",
             ", ".join(ordered), ", ".join(resolved) or "nothing")
    return [f"{parsed.scheme}://{auth}{ip}:{port}" for ip in ordered] or [proxy_url]


class VintedClient:
    def __init__(self, token_cache: dict | None = None):
        proxy = os.environ.get("PROXY_URL")
        if not proxy:
            raise RuntimeError("PROXY_URL is required — Vinted blocks datacenter IPs")
        self._gateways = _gateway_urls(proxy)
        self._gw = 0
        self.session = requests.Session()
        self._apply_gateway()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.bytes_uncompressed = 0
        self.token_cache = dict(token_cache or {})
        self._restore_cookies()

    def _apply_gateway(self):
        url = self._gateways[self._gw % len(self._gateways)]
        self.session.proxies = {"http": url, "https": url}

    def _next_gateway(self):
        if len(self._gateways) > 1:
            self._gw += 1
            self._apply_gateway()
            log.info("switched to proxy gateway #%d", self._gw % len(self._gateways))

    # ---- token cache -----------------------------------------------------

    def _restore_cookies(self):
        """Reuse the cached anon token regardless of age.

        Cold-loading the homepage is the one request Vinted readily 403s, so a
        token we already hold is precious. There is no need to guess when it
        expires: the API answers 401/403 when it has, and that path
        re-bootstraps. Assuming a 90-minute lifetime only threw away working
        tokens and bought extra chances to be blocked.
        """
        saved_at = self.token_cache.get("saved_at", 0)
        if not saved_at:
            return
        for name in TOKEN_COOKIES:
            value = self.token_cache.get(name)
            if value:
                self.session.cookies.set(name, value, domain=".vinted.it")
        log.info("restored cached anon token (age %ds)", int(time.time() - saved_at))

    def _cookie(self, name: str) -> str | None:
        """Last value for a cookie name.

        Restored cookies are scoped to `.vinted.it` while Vinted sets its own on
        `www.vinted.it`, so the jar can legitimately hold two of each and
        `cookies.get()` raises CookieConflictError. Later entries win, matching
        what the server most recently told us.
        """
        value = None
        for cookie in self.session.cookies:
            if cookie.name == name:
                value = cookie.value
        return value

    def _snapshot_cookies(self) -> dict:
        snap = {"saved_at": time.time()}
        for name in TOKEN_COOKIES:
            value = self._cookie(name)
            if value:
                snap[name] = value
        return snap

    @property
    def has_token(self) -> bool:
        return bool(self._cookie("access_token_web"))

    def bootstrap(self):
        """Fetch the homepage to mint a fresh anon token (~250 KB gzipped).

        Vinted blocks a share of residential exits from loading the site cold —
        a 403 here means "this exit is unwelcome", not "we are banned", so it
        must be retried from a different address. Everything downstream depends
        on this succeeding, so it gets the most attempts.
        """
        # Start from an empty jar so restored and freshly-issued cookies cannot
        # pile up as duplicates across domains — but keep the old token aside,
        # because a failed bootstrap must not leave us with no credentials at
        # all. The one we already hold usually still works.
        previous = self._snapshot_cookies()
        self.session.cookies.clear()
        r = self._get(BASE + "/", tries=6, retry_statuses=(403, 429))
        if r is None or r.status_code != 200:
            self.token_cache = previous
            self._restore_cookies()
            raise RuntimeError(f"bootstrap failed: {r.status_code if r else 'no response'}")
        self.token_cache = self._snapshot_cookies()
        log.info("bootstrapped anon token; cookies=%s", sorted(self.session.cookies.keys()))

    # ---- transport -------------------------------------------------------

    def _get(self, url, tries=5, retry_statuses=(), **kw):
        kw.setdefault("timeout", (15, 75))  # (connect, read) — residential exits are slow
        for attempt in range(tries):
            try:
                r = self.session.get(url, **kw)
                if r.status_code in retry_statuses and attempt < tries - 1:
                    # Vinted is throttling this exit IP. A different gateway
                    # gives us a different residential address to come from.
                    log.warning("HTTP %d from %.60s — rotating exit, retry %d/%d",
                                r.status_code, url, attempt + 1, tries)
                    self.session.close()
                    self._next_gateway()
                    time.sleep(min(4 * 2**attempt, 30))
                    continue
            except requests.RequestException as exc:
                log.warning(
                    "GET %s failed (%s: %.400s), retry %d/%d",
                    url, type(exc).__name__, exc, attempt + 1, tries,
                )
                # The pooled CONNECT tunnel is dead — reusing it just fails
                # again. Dropping the pool forces a new tunnel, and with it a
                # new residential exit IP. Move to the next gateway too, in
                # case this one is the sick address.
                self.session.close()
                self._next_gateway()
                time.sleep(min(3 * 2**attempt, 30))
                continue
            # Vinted replies chunked, so content-length is absent and the raw
            # stream is already drained by the time we look. This is therefore
            # the DECOMPRESSED size — an upper bound on the metered traffic,
            # which is gzipped and roughly 5-10x smaller.
            try:
                self.bytes_uncompressed += len(r.content)
            except (AttributeError, TypeError):
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
            try:
                self.bootstrap()
            except RuntimeError as exc:
                # Vinted is refusing to hand out a fresh token from these exits.
                # The token we already had may still be good, so retry with it
                # rather than losing the whole run.
                log.warning("%s — retrying with the existing token", exc)
                self._restore_cookies()
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
        # Ask for the page the way a browser would; a bare GET gets thrown a
        # 403 far more readily.
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": BASE + "/catalog",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        }
        target = url or f"{BASE}/items/{item_id}"
        r = self._get(target, tries=2, retry_statuses=(403, 429), headers=headers)

        # Vinted serves the item page happily to a session that has *just*
        # loaded the homepage, and 403s one carrying a stale anon token. So a
        # 403 means "re-establish the session", not "give up".
        if r is not None and r.status_code in (403, 429):
            log.info("item %s: HTTP %d — refreshing the session and retrying",
                     item_id, r.status_code)
            try:
                self.bootstrap()
            except RuntimeError as exc:
                log.warning("re-bootstrap failed: %s", exc)
                return "unknown"
            r = self._get(target, tries=2, retry_statuses=(429,), headers=headers)

        # Last resort: the same listing on the .com domain, which is served by
        # the same backend but is policed separately.
        if r is not None and r.status_code == 403:
            log.info("item %s: still 403 — trying vinted.com", item_id)
            r = self._get(f"https://www.vinted.com/items/{item_id}", tries=1, headers=headers)

        if r is None:
            return "unknown"
        if r.status_code in (404, 410):
            return "removed"
        if r.status_code != 200:
            log.warning("item %s: HTTP %d", item_id, r.status_code)
            return "unknown"

        html = r.text

        # The listing's state lives in an "item_status" plugin block that also
        # carries its item_id — confirmed against two real sales:
        #   {"name":"item_status",...,"data":{"item_id":9505849905,...,
        #    "is_closed":true,"item_closing_action":"sold",...}}
        # Anchoring on that block and checking the id means we cannot read a
        # photo's is_hidden (there are ~53 per page) or another listing's state.
        anchor = html.find(f'\\"item_id\\":{item_id}')
        if anchor == -1:
            anchor = html.find(f'"item_id":{item_id}')
        if anchor == -1:
            anchor = html.find("item_closing_action")
            log.warning("item %s: no item_status block for this id; falling back", item_id)
        window = html[max(0, anchor - 500):anchor + 1200] if anchor != -1 else html

        found = {}
        for key, rx in _STATE_RE.items():
            m = rx.search(window)
            if m:
                found[key] = m.group(1).strip('\\"')
        log.info("item %s page state: %s", item_id, found or "(no state json)")

        if not found:
            return "unknown"

        # The 'sold' branch has never been observed against a real sale, so when
        # anything other than 'live' comes back, record the raw evidence. The
        # first genuine sale is the only chance to confirm this mapping.
        if found.get("item_closing_action") not in ("null", None) or found.get("is_closed") == "true":
            log.warning("item %s NON-LIVE state, raw window for verification: %s",
                        item_id, window[max(0, window.find("item_closing_action") - 400):
                                        window.find("item_closing_action") + 400])
        # Decide only on item-level fields. `item_closing_action` occurs exactly
        # once on the page and only on the listing, so it is definitive.
        # `is_closed` is item-level too. `is_hidden` is NOT usable: it appears
        # ~53 times because every photo carries one, and no window reliably
        # separates the listing's from a photo's — reading it produced
        # contradictory verdicts for the same listing an hour apart.
        closing = found.get("item_closing_action")
        if closing not in (None, "null", ""):
            return "sold" if "sold" in closing.lower() else "removed"
        if found.get("is_closed") == "true":
            return "sold"
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
