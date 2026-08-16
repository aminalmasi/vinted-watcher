"""Telegram delivery for the Vinted watcher (@vinted_ads_bot)."""

from __future__ import annotations

import html
import logging
import os

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"


def _creds():
    """Token plus every recipient.

    TELEGRAM_CHAT_ID may hold several ids separated by commas. Telegram bots do
    not broadcast: a bot can only message a chat that has already started a
    conversation with it, so each person must press Start and then be added
    here. For more than a handful of people, post to a channel instead and let
    them join it.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    raw = os.environ.get("TELEGRAM_CHAT_ID") or ""
    chat_ids = [c.strip() for c in raw.split(",") if c.strip()]
    return token, chat_ids


def send(text: str) -> bool:
    """Deliver to every recipient. Telegram is reached directly, not via proxy.

    Returns True if at least one recipient got it; one bad id must not silence
    everyone else.
    """
    token, chat_ids = _creds()
    if not token or not chat_ids:
        log.warning("Telegram not configured (recipients=%r) — message dropped:\n%s",
                    chat_ids, text)
        return False

    delivered = 0
    for chat_id in chat_ids:
        try:
            r = requests.post(
                API.format(token=token),
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            log.error("Telegram send to %s failed: %s", chat_id, exc)
            continue
        if r.status_code != 200:
            # 403 here almost always means that person never pressed Start.
            log.error("Telegram HTTP %d for %s: %s", r.status_code, chat_id, r.text[:200])
            continue
        delivered += 1
    # Log every outcome, not just failures. A silent success and a message that
    # went to the wrong chat look identical from the outside, which cost an
    # evening of "is it working?" — so say who actually received it. Ids are
    # masked to the last 4 digits: enough to tell recipients apart in a public
    # log without publishing them.
    log.info("Telegram: delivered to %d/%d (%s)", delivered, len(chat_ids),
             ", ".join("…" + c[-4:] for c in chat_ids) or "none")
    if delivered < len(chat_ids):
        log.warning("delivered to %d/%d recipients", delivered, len(chat_ids))
    return delivered > 0


def format_sold(item: dict, hours_listed: float | None,
                exact: bool = False, probable: bool = False) -> str:
    """The ONLY message this bot sends — a listing that left the search.

    `exact`    we watched the listing appear, so the elapsed time is real.
    `probable` the sale was inferred from disappearance rather than read off the
               item page, so it could also be a seller deleting the listing.
    """
    e = lambda v: html.escape(str(v)) if v is not None else "—"
    head = "🔴 <b>VENDUTO (probabile)</b>" if probable else "🔴 <b>VENDUTO</b>"
    bits = [f"{head} — {e(item.get('title'))}"]
    line = " · ".join(
        str(x)
        for x in (
            e(item.get("brand")),
            f"taglia {e(item['size'])}" if item.get("size") else None,
            e(item.get("condition")),
        )
        if x and x != "—"
    )
    if line:
        bits.append(line)
    price = item.get("price")
    if price:
        cur = item.get("currency", "EUR")
        total = item.get("total_price")
        bits.append(
            f"💶 <b>{e(price)} {e(cur)}</b>"
            + (f"  (con protezione: {e(total)} {e(cur)})" if total and total != price else "")
        )
    if hours_listed is not None:
        # Only claim a real time-to-sale for listings we watched appear; for the
        # rest we merely know how long we happened to be watching.
        label = "venduto dopo" if exact else "visto in vendita per"
        amount = (f"~{hours_listed:.0f} h" if hours_listed < 48
                  else f"~{hours_listed / 24:.0f} giorni")
        bits.append(f"⏱ {label} {amount}")
    if item.get("url"):
        bits.append(f'\n<a href="{e(item["url"])}">{e(item["url"])}</a>')
    if probable:
        bits.append("<i>Sparito dalla ricerca — apri il link per verificare.</i>")
    return "\n".join(bits)
