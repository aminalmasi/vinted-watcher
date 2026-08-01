"""Daily statistics digest.

Answers the question that decides which brands deserve a tighter sweep: which
ones actually sell. Ranking by 7-day sales rather than by one day, because a
single day is noisy and the decision (halving a brand's window) costs proxy
budget.

Everything here is computed from state — no network calls.
"""

from __future__ import annotations

import html
import statistics
import time

try:
    from zoneinfo import ZoneInfo
    ROME = ZoneInfo("Europe/Rome")
except Exception:  # tzdata missing on some minimal images
    ROME = None

DAY = 86400


def local_now(ts: float | None = None):
    ts = time.time() if ts is None else ts
    if ROME is not None:
        from datetime import datetime
        return datetime.fromtimestamp(ts, ROME)
    from datetime import datetime, timedelta, timezone
    # Italy is UTC+2 in summer; only used if tzdata is unavailable.
    return datetime.fromtimestamp(ts, timezone(timedelta(hours=2)))


def _short(brand: str) -> str:
    """'christian louboutin shoes' -> 'louboutin'."""
    return (brand or "?").replace(" shoes", "").replace("christian ", "").replace("maison ", "")


def brand_of(sale: dict, searches: list[str]) -> str:
    """Which brand a sale belongs to.

    Sales recorded before `search` was stored have to be inferred from the
    title, otherwise the first reports would show every brand at zero while the
    total said otherwise.
    """
    if sale.get("search"):
        return sale["search"]
    title = (sale.get("title") or "").lower()
    for s in searches:
        key = s.replace(" shoes", "")
        # match on the distinctive word: "margiela", "louboutin", "ferragamo"
        word = key.split()[-1]
        if word in title or key in title:
            return s
    return "?"


def _prices(sales):
    out = []
    for s in sales:
        try:
            out.append(float(s.get("price")))
        except (TypeError, ValueError):
            pass
    return out


def build(state: dict, searches: list[str], hours: int = 24) -> str:
    now = time.time()
    cutoff = now - hours * 3600
    items = state.get("items", {})
    sold = state.get("sold", {})
    daily = state.get("daily", {})

    recent = [s for s in sold.values() if s.get("reported_at", 0) >= cutoff]

    # tracked listings per brand, right now
    tracked = {}
    for rec in items.values():
        tracked[rec.get("search", "?")] = tracked.get(rec.get("search", "?"), 0) + 1

    # 24h sales + new listings per brand
    sales_by, new_by = {}, {}
    for s in recent:
        b = brand_of(s, searches)
        sales_by[b] = sales_by.get(b, 0) + 1
    for day, brands in daily.items():
        if day >= time.strftime("%Y-%m-%d", time.gmtime(cutoff)):
            for b, d in brands.items():
                new_by[b] = new_by.get(b, 0) + d.get("new", 0)

    d = local_now()
    lines = [f"📊 <b>Riepilogo {d.strftime('%d/%m/%Y')}</b> — ultime {hours} h", ""]

    lines.append("<b>Per marca</b>  <i>(seguiti · nuovi · venduti · prezzo medio)</i>")
    ranked = sorted(searches, key=lambda b: -sales_by.get(b, 0))
    for b in ranked:
        ps = _prices([s for s in recent if brand_of(s, searches) == b])
        avg = f"{statistics.mean(ps):.0f}€" if ps else "—"
        lines.append(f"• <b>{html.escape(_short(b))}</b>: "
                     f"{tracked.get(b, 0)} · +{new_by.get(b, 0)} · "
                     f"<b>{sales_by.get(b, 0)} venduti</b> · {avg}")

    all_p = _prices(recent)
    lines += ["", "<b>Totali</b>"]
    lines.append(f"• annunci seguiti: <b>{len(items)}</b>")
    lines.append(f"• venduti (24 h): <b>{len(recent)}</b>")
    unknown = sales_by.get("?", 0)
    if unknown:
        lines.append(f"• <i>{unknown} senza marca riconosciuta (titolo generico)</i>")
    if all_p:
        lines.append(f"• prezzo medio: <b>{statistics.mean(all_p):.0f}€</b> · "
                     f"mediana {statistics.median(all_p):.0f}€ · "
                     f"min {min(all_p):.0f}€ · max {max(all_p):.0f}€")
        lines.append(f"• valore totale venduto: <b>{sum(all_p):.0f}€</b>")

    supp = [v for v in state.get("suppressed", {}).values()
            if v.get("at", 0) >= cutoff]
    if supp:
        lines.append(f"• <i>{len(supp)} spariti ma NON venduti "
                     f"(nascosti/riservati/cancellati) — non inviati</i>")

    # Only listings we watched appear have a real time-to-sale.
    timed = [s for s in recent if s.get("hours_exact") and s.get("hours_listed")]
    if timed:
        fast = min(timed, key=lambda s: s["hours_listed"])
        med = statistics.median([s["hours_listed"] for s in timed])
        lines += ["", "<b>Velocità di vendita</b> <i>(solo annunci visti nascere)</i>"]
        lines.append(f"• tempo mediano: <b>{med:.1f} h</b>  (su {len(timed)} annunci)")
        lines.append(f"• più veloce: <b>{fast['hours_listed']:.1f} h</b> — "
                     f"{html.escape(str(fast.get('title'))[:40])} "
                     f"({fast.get('price')}€)")

    # 7-day ranking: what the top-2 decision should actually rest on.
    week = {}
    since_day = time.strftime("%Y-%m-%d", time.gmtime(now - 7 * DAY))
    for day, brands in daily.items():
        if day >= since_day:
            for b, dd in brands.items():
                week[b] = week.get(b, 0) + dd.get("sales", 0)
    if any(week.values()):
        top = sorted(week.items(), key=lambda kv: -kv[1])
        lines += ["", "<b>Vendite su 7 giorni</b> <i>(per scegliere le 2 marche da seguire più spesso)</i>"]
        for b, n in top:
            lines.append(f"• {html.escape(_short(b))}: <b>{n}</b>")

    return "\n".join(lines)


def due(state: dict, hour: int = 10) -> bool:
    """True once per day, on the first run at or after `hour` local time."""
    d = local_now()
    today = d.strftime("%Y-%m-%d")
    if state.get("last_report_date") == today:
        return False
    return d.hour >= hour
