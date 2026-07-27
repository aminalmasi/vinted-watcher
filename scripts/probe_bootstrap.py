"""Find an entry point that still hands us an anon token.

The homepage now 403s from every exit we have. Any page that sets
`access_token_web` will do just as well, so try the plausible ones cold, each
from a clean cookie jar.
"""

import logging
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

BROWSER = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

CANDIDATES = [
    (BASE + "/", "it homepage"),
    (BASE + "/catalog", "it catalog page"),
    (BASE + "/catalog?search_text=prada%20shoes", "it catalog + query"),
    (BASE + "/robots.txt", "it robots.txt"),
    ("https://www.vinted.com/", "com homepage"),
    ("https://www.vinted.com/catalog", "com catalog"),
    ("https://www.vinted.fr/", "fr homepage"),
]


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    client = VintedClient()

    print("Each attempt starts from a clean jar on a fresh exit.\n")
    winners = []
    for url, label in CANDIDATES:
        client.session.cookies.clear()
        client.session.close()
        client._next_gateway()
        try:
            r = client.session.get(url, headers=BROWSER, timeout=(15, 75))
        except requests.RequestException as exc:
            print(f"  {label:24s} -> {type(exc).__name__}")
            continue
        names = sorted({c.name for c in client.session.cookies})
        got_token = "access_token_web" in names
        print(f"  {label:24s} -> HTTP {r.status_code} {len(r.content):>9}B  "
              f"token={'YES' if got_token else 'no '}  cookies={names}")
        if r.status_code == 200 and got_token:
            winners.append((url, label))

    print(f"\nusable bootstrap entry points: {[w[1] for w in winners] or 'NONE'}")

    # If something worked, prove the token actually drives the catalog API.
    if winners:
        url, label = winners[0]
        client.session.cookies.clear()
        client.session.get(url, headers=BROWSER, timeout=(15, 75))
        r = client.session.get(
            BASE + "/api/v2/catalog/items",
            params={"search_text": "prada shoes", "order": "newest_first", "per_page": 24, "page": 1},
            headers={"Accept": "application/json", "Referer": BASE + "/catalog"},
            timeout=(15, 75),
        )
        n = len(r.json().get("items", [])) if r.status_code == 200 else 0
        print(f"catalog API via '{label}' token -> HTTP {r.status_code}, {n} listings")

    print(f"\n[probe] traffic {client.bytes_uncompressed / 1024:.0f} KB")


if __name__ == "__main__":
    main()
