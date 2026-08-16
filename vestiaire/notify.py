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
