"""Why do API calls succeed while page requests 403, from the same IP?

Fire both kinds back to back on ONE connection so IP, TLS and timing are
identical, then print the response headers. Whatever differs is the mechanism.
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vintedwatch.client import BASE, VintedClient  # noqa: E402

INTERESTING = ("server", "cf-ray", "cf-cache-status", "x-datadome", "x-served-by",
               "via", "x-cache", "set-cookie", "content-type", "x-request-id",
               "x-envoy-upstream-service-time", "akamai-grn", "x-akamai-transformed")

TARGETS = [
    ("API   catalog search", "/api/v2/catalog/items?search_text=prada&per_page=5&page=1", "application/json"),
    ("API   seller profile", "/api/v2/users/32001697", "application/json"),
    ("API   seller wardrobe", "/api/v2/wardrobe/32001697/items?page=1&per_page=5", "application/json"),
    ("PAGE  item", "/items/9505849905", "text/html"),
    ("PAGE  homepage", "/", "text/html"),
    ("FILE  robots.txt", "/robots.txt", "text/plain"),
]


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    state = json.load(open("data/state.json"))
    client = VintedClient(token_cache=state.get("token"))

    for label, path, accept in TARGETS:
        r = client._get(BASE + path, tries=1, headers={"Accept": accept,
                                                       "Referer": BASE + "/"})
        if r is None:
            print(f"{label:22s} -> no response\n")
            continue
        print(f"{label:22s} -> HTTP {r.status_code}  {len(r.content)/1024:.0f} KB")
        hdrs = {k.lower(): v for k, v in r.headers.items()}
        shown = {k: v[:70] for k, v in hdrs.items() if k in INTERESTING}
        for k, v in sorted(shown.items()):
            print(f"      {k}: {v}")
        if r.status_code == 403:
            # The block page usually names the product doing the blocking.
            body = r.text[:1200].replace("\n", " ")
            for needle in ("cloudflare", "datadome", "akamai", "incapsula", "perimeterx",
                           "captcha", "challenge", "blocked", "Access denied", "Attention"):
                if needle.lower() in body.lower():
                    i = body.lower().find(needle.lower())
                    print(f"      BODY mentions {needle!r}: ...{body[max(0,i-70):i+90]}...")
        print()

    print(f"[probe] traffic {client.bytes_uncompressed/1024:.0f} KB")


if __name__ == "__main__":
    main()
