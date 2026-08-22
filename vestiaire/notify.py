"""Telegram formatting for Vestiaire sales.

Delivery is shared with the Vinted watcher — same bot, same recipients, same
"one bad chat id must not silence the others" behaviour. Only the message body
differs, and it differs in one important way: Vestiaire STATES that an item
sold and when, so nothing here is hedged. The Vinted formatter had to carry
"probabile" and "visto in vendita per" because it was inferring; this one
reports facts.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone


def _days(sold_iso: str | None, created_epoch: int | None) -> float | None:
    if not sold_iso or not created_epoch:
        return None
    try:
        sold = datetime.fromisoformat(sold_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    created = datetime.fromtimestamp(created_epoch, tz=timezone.utc)
    return (sold - created).total_seconds() / 86400


def format_sale(rec: dict) -> str:
    e = lambda v: html.escape(str(v)) if v is not None else "—"
    bits = [f"🟢 <b>VENDUTO</b> — {e(rec.get('name'))}"]

    line = " · ".join(x for x in (e(rec.get("brand")),
                                  f"taglia {e(rec['size'])}" if rec.get("size") else None)
                      if x and x != "—")
    if line:
        bits.append(line)

    if rec.get("price") is not None:
        bits.append(f"💶 <b>{rec['price']:.2f} EUR</b>")

    d = _days(rec.get("sold_date"), rec.get("created_at"))
    if d is not None:
        amount = f"{d * 24:.0f} ore" if d < 2 else f"{d:.0f} giorni"
        bits.append(f"⏱ venduto dopo {amount}")

    if rec.get("url"):
        bits.append(f'\n<a href="{e(rec["url"])}">{e(rec["url"])}</a>')
    return "\n".join(bits)


def format_summary(counts: dict, sales: int, elapsed: float) -> str:
    rows = "\n".join(f"  {b}: {n}" for b, n in sorted(counts.items()) if n)
    return (f"📊 Vestiaire — {sales} vendite nuove in {elapsed/60:.0f} min\n"
            f"{rows or '  nessuna'}")


def format_digest(sales: list, hours: int = 24) -> str:
    """The daily 10:00 report.

    `sales` are the records detected since the last digest. Sale time is when we
    SAW the item turn sold, not Vestiaire's soldDate — the two differ by at most
    one sweep interval, which is invisible at the resolution this report uses.
    """
    e = lambda v: html.escape(str(v)) if v is not None else "—"
    if not sales:
        return f"📊 <b>Vestiaire</b> — nessuna vendita nelle ultime {hours}h."

    by_brand: dict[str, list] = {}
    for r in sales:
        by_brand.setdefault(r.get("brand") or "?", []).append(r)

    prices = [r["price"] for r in sales if r.get("price")]
    head = (f"📊 <b>Vestiaire — ultime {hours}h</b>\n"
            f"<b>{len(sales)}</b> vendite"
            + (f" · media <b>{sum(prices)/len(prices):.0f} €</b>" if prices else ""))

    rows = []
    for brand, rs in sorted(by_brand.items(), key=lambda kv: -len(kv[1])):
        ps = [r["price"] for r in rs if r.get("price")]
        avg = f"{sum(ps)/len(ps):.0f} €" if ps else "—"
        rows.append(f"  {e(brand)} — <b>{len(rs)}</b> · media {avg}")

    # The point of the whole exercise: what went fastest.
    timed = [r for r in sales if r.get("days") is not None]
    timed.sort(key=lambda r: r["days"])
    fast = []
    for r in timed[:5]:
        d = r["days"]
        amount = f"{d*24:.0f} ore" if d < 2 else f"{d:.1f} giorni"
        fast.append(f'  • <a href="{e(r.get("url"))}">{e(str(r.get("name"))[:38])}</a>'
                    f' — {r["price"]:.0f} € — {amount}')

    # Most-wanted: likes are the closest public stand-in for offers received.
    liked = sorted((r for r in sales if r.get("likes")),
                   key=lambda r: -r["likes"])[:5]
    hot = [f'  • <a href="{e(r.get("url"))}">{e(str(r.get("name"))[:34])}</a>'
           f' — {r["likes"]} ❤ — {r["price"]:.0f} €' for r in liked]

    parts = [head, "", "\n".join(rows)]
    if fast:
        parts += ["", "⚡ <b>Più veloci</b>", "\n".join(fast)]
    if hot:
        parts += ["", "❤️ <b>Più desiderati</b>", "\n".join(hot)]
    return "\n".join(parts)
