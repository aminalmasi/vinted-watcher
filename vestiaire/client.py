"""Vestiaire Collective API client.

Two hosts, two jobs:

  search.vestiairecollective.com/v1/product/search
      brand-filtered discovery. Answers a GitHub Actions IP directly; 403s the
      university IP, so this half cannot run on the cluster.

  apiv2.vestiairecollective.com/products/{id}
      one listing in full, including soldDate — which search results omit.
      Answers everyone, cluster included.

Neither needs a proxy or a token. The binding constraint is not bandwidth (a
search page is 56 KB for 48 items) but REQUEST RATE: probing showed 429s from
the ninth request at ~1 req/2 s, and none at all at 6-10 s. So every call goes
through _pace(), and a 429 backs off hard rather than retrying tightly.
"""

from __future__ import annotations

import logging
import random
import time

import requests

log = logging.getLogger(__name__)

SEARCH = "https://search.vestiairecollective.com/v1/product/search"
APIV2 = "https://apiv2.vestiairecollective.com/products/{id}"
SITE = "https://www.vestiairecollective.com"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Straight from their own bundle (fenx v4.102.2). Asking for fields they do not
# publish gets the whole request rejected, so this list is copied, not invented.
FIELDS = ["name", "description", "brand", "price", "link", "sold", "likes",
          "seller", "pictures", "size", "stock", "universeId", "createdAt"]
LOCALE = {"country": "IT", "currency": "EUR", "language": "it", "sizeType": "women"}

PAGE = 48                # 56 KB, 1.2 KB/item — verified
OFFSET_CAP = 960         # offset 960 works, 1500 is a 400
MIN_GAP, MAX_GAP = 6.0, 10.0

# Prices are in cents, and `gte` is the shape the API honours — confirmed by
# watching the count actually move (8088 sold shoes -> 5744 above EUR 150).
# This floor is not only a preference: without it Gucci's 30-day sold count sits
# right on the ~1000 pagination ceiling, and we would silently lose sales.
FLOOR_CENTS = 15000


class Vestiaire:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept-Language": "it-IT,it;q=0.9",
            "Origin": SITE,
            "Referer": SITE + "/",
            "x-usecase": "catalog",
            "Content-Type": "application/json",
        })
        self.bytes = 0
        self.requests = 0
        self._last = 0.0
        self.throttled = False

    def _pace(self) -> None:
        """Never issue two calls closer together than the measured safe gap."""
        wait = self._last + random.uniform(MIN_GAP, MAX_GAP) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _call(self, fn, *a, **kw):
        """One paced request, with a hard back-off on 429.

        A 429 means we have already been impolite; the right response is to wait
        much longer, not to retry immediately. Two in a row and we give up for
        this sweep and let the next one pick up where we stopped — nothing is
        lost, because sold listings do not disappear.
        """
        for attempt in range(2):
            self._pace()
            try:
                r = fn(*a, **kw)
            except requests.RequestException as exc:
                log.warning("%s: %s", type(exc).__name__, exc)
                return None
            self.requests += 1
            self.bytes += len(r.content)
            if r.status_code == 429:
                self.throttled = True
                back = 60 * (attempt + 1)
                log.warning("HTTP 429 — backing off %ds", back)
                time.sleep(back)
                continue
            if r.status_code != 200:
                log.warning("HTTP %d for %s", r.status_code, getattr(r, "url", "?"))
                return None
            try:
                return r.json()
            except ValueError:
                return None
        return None

    # ---------------------------------------------------------------- search

    def sold_page(self, brand_id: str, category_id: str, created_gte: int,
                  offset: int) -> tuple[list, int]:
        """One page of SOLD listings for a brand, newest listing first.

        `sold` must be a bare boolean — {"sold": [true]} is a 500.
        Returns (items, total_hits).
        """
        body = {
            "pagination": {"offset": offset, "limit": PAGE},
            "fields": FIELDS,
            "filters": {
                "brand.id": [brand_id],
                "categoryLvl0.id": [category_id],
                "sold": True,
                "createdAt": {"gte": created_gte},
                "price": {"gte": FLOOR_CENTS},
            },
            "locale": LOCALE,
            "sort": "recency",
        }
        j = self._call(self.s.post, SEARCH, json=body, timeout=45)
        if not j:
            return [], 0
        stats = j.get("paginationStats") or {}
        return (j.get("items") or []), int(stats.get("totalHits") or 0)

    def sold_since(self, brand_id: str, category_id: str,
                   created_gte: int) -> tuple[list, bool]:
        """Every sold listing in the window, paging until the cap or the end.

        Returns (items, complete). `complete` is False when the window holds
        more than the ~1000 the API will paginate through, which means the
        window needs narrowing rather than the result being trusted.
        """
        out, offset, total = [], 0, None
        while offset <= OFFSET_CAP:
            items, hits = self.sold_page(brand_id, category_id, created_gte, offset)
            if total is None:
                total = hits
            if not items:
                break
            out.extend(items)
            if len(out) >= (total or 0):
                break
            offset += PAGE
        complete = total is not None and len(out) >= total
        if not complete:
            log.warning("brand %s: read %d of %s sold — window too wide for the "
                        "pagination cap", brand_id, len(out), total)
        return out, complete

    # --------------------------------------------------------------- product

    def product(self, pid: str | int) -> dict | None:
        """Full record for one listing — the only place soldDate exists."""
        j = self._call(self.s.get, APIV2.format(id=pid),
                       params={"isoCountry": "IT", "x-siteid": "12",
                               "x-language": "it", "x-currency": "EUR"},
                       timeout=45)
        return (j or {}).get("data")


def url_of(item: dict) -> str:
    link = item.get("link") or ""
    return SITE + link if link.startswith("/") else link
