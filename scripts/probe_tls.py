"""Is the .it block about our TLS fingerprint rather than our IP?

Every vinted.it HTML page now 403s from our exits — including robots.txt, which
no rate limiter would bother with. That pattern fits Cloudflare fingerprinting
the TLS handshake: python-requests has a distinctive JA3 that does not match the
Chrome User-Agent we send, and mismatches get blocked outright.

curl_cffi replays a real Chrome handshake. If it gets 200 from the same exits
and the same proxy, the block is fingerprint-based and the fix is the transport,
not the schedule, the headers, or the proxy.
"""

import os
import sys

PROXY = os.environ.get("PROXY_URL")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TARGETS = [
    ("https://www.vinted.it/", "it homepage"),
    ("https://www.vinted.it/items/9505849905", "it item page (known SOLD)"),
]


def main():
    if not PROXY:
        sys.exit("PROXY_URL required")
    proxies = {"http": PROXY, "https": PROXY}

    print("=== A. plain python-requests (what we use today) ===")
    import requests
    for url, label in TARGETS:
        try:
            r = requests.get(url, proxies=proxies, timeout=60,
                             headers={"User-Agent": UA,
                                      "Accept-Language": "it-IT,it;q=0.9"})
            print(f"  {label:32s} -> HTTP {r.status_code}  {len(r.content)}B")
        except Exception as exc:
            print(f"  {label:32s} -> {type(exc).__name__}: {str(exc)[:90]}")

    print("\n=== B. curl_cffi impersonating Chrome ===")
    try:
        from curl_cffi import requests as creq
    except ImportError:
        sys.exit("curl_cffi not installed")

    session = creq.Session(impersonate="chrome", proxies=proxies, timeout=60)
    for url, label in TARGETS:
        try:
            r = session.get(url)
            has_state = "item_closing_action" in r.text
            print(f"  {label:32s} -> HTTP {r.status_code}  {len(r.content)}B"
                  f"  state_json={has_state}")
            if has_state:
                i = r.text.find("item_closing_action")
                print(f"      {r.text[max(0, i - 220):i + 60]}"[-300:].replace("\n", " "))
        except Exception as exc:
            print(f"  {label:32s} -> {type(exc).__name__}: {str(exc)[:90]}")

    print("\n=== C. does the .it catalog API still work under curl_cffi? ===")
    try:
        r = session.get("https://www.vinted.it/api/v2/catalog/items",
                        params={"search_text": "prada shoes", "per_page": 5, "page": 1},
                        headers={"Accept": "application/json"})
        n = len(r.json().get("items", [])) if r.status_code == 200 else 0
        print(f"  catalog API -> HTTP {r.status_code}, {n} listings")
    except Exception as exc:
        print(f"  catalog API -> {type(exc).__name__}: {str(exc)[:90]}")


if __name__ == "__main__":
    main()
