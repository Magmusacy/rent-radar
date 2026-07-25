#!/usr/bin/env python3
"""Telegram front end for Rent Radar — the thing that actually runs on the server.

One process does everything: it holds the schedule (hourly 11:00–21:00), runs the
sweep, and answers commands. No cron, no systemd timers — python-telegram-bot's
JobQueue owns the clock, so a container restart restores the schedule and the
state lives in SQLite.

Commands (owner only):
    /skan   run a sweep right now
    /nowe   offers found in the last sweep
    /top    ten cheapest matches in the profile
    /stan   database size, last sweep, next sweep
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import time as dtime
from zoneinfo import ZoneInfo

from telegram import LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import refresh
import store

TZ = ZoneInfo("Europe/Warsaw")
SCAN_HOURS = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
DIGEST_AT = dtime(hour=21, minute=5, tzinfo=TZ)

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s — %(message)s",
                    level=logging.INFO)
log = logging.getLogger("rent-radar")

# One sweep at a time: a manual /skan must not race the scheduled one over the
# same database.
scan_lock = asyncio.Lock()
day_tally = {"scans": 0, "added": 0, "hits": []}


def owner_id() -> int:
    return int(os.environ.get("OWNER_ID") or 0)


def owner_only(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id != owner_id():
            log.warning("odrzucono komendę od %s", user.id if user else "?")
            return
        return await handler(update, context)
    return wrapper


# ---------------------------------------------------------------- formatting

def money(value) -> str:
    v = refresh.as_float(value)
    return f"{v:,.0f} zł".replace(",", " ") if v is not None else "? zł"


def reach(row: dict) -> str:
    """Whichever distance measure this row has — minutes on the laptop, km on the server."""
    minutes = refresh.as_float(row.get("commute_min"))
    if minutes is not None:
        return f"{minutes:.0f} min MPK"
    km = refresh.as_float(row.get("distance_km"))
    return f"{km:.1f} km" if km is not None else "? km"


def seller_mark(row: dict) -> str:
    return {"prywatny": "👤 właściciel",
            "agencja": "🏢 agencja (prowizja?)"}.get(row.get("seller") or "", "❔ nieznane")


def offer_line(i: int, row: dict) -> str:
    area = refresh.as_float(row.get("area_m2"))
    bits = [money(row.get("total_price")), reach(row)]
    if area:
        bits.append(f"{area:.0f} m²")
    where = html.escape((row.get("district") or row.get("street") or "Kraków")[:40])
    url = html.escape(row.get("url") or "")
    return (f"{i}. <b>{' · '.join(bits)}</b>\n"
            f"   {where} · {seller_mark(row)} · <a href=\"{url}\">otwórz →</a>")


def scan_message(result: refresh.ScanResult, when: str) -> str:
    if result.error:
        return (f"⚠️ <b>Runda {when} nie doszła do skutku</b>\n"
                f"<code>{html.escape(result.error[:300])}</code>")
    head = (f"🏠 <b>Rent Radar</b> · {when}\n\n"
            f"{result.added} nowych ogłoszeń (w bazie: {result.total})")
    if not result.hits:
        return head + "\nNic pod Twój profil w tej rundzie."
    profile = f"≤{refresh.ALERT_BELOW:.0f} zł, ≤{refresh.MAX_KM:.0f} km od Zabłocia"
    lines = [offer_line(i, r) for i, r in enumerate(result.hits[:10], 1)]
    return (f"{head}\nPasujące pod profil ({profile}): <b>{len(result.hits)}</b>\n\n"
            + "\n".join(lines))


async def send(app_or_ctx, text: str) -> None:
    await app_or_ctx.bot.send_message(
        chat_id=owner_id(), text=text, parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True))


# ---------------------------------------------------------------- scanning

async def do_scan(context: ContextTypes.DEFAULT_TYPE, announce_empty: bool = False):
    if scan_lock.locked():
        log.info("runda już trwa — pomijam")
        return None
    async with scan_lock:
        when = refresh.datetime.now(TZ).strftime("%H:%M")
        log.info("start rundy %s", when)
        # run_scan is blocking (network + subprocess), so keep the event loop free
        result = await asyncio.to_thread(refresh.run_scan)
        day_tally["scans"] += 1
        day_tally["added"] += result.added
        day_tally["hits"].extend(result.hits)

        if result.error or result.added or announce_empty:
            await send(context, scan_message(result, when))
        else:
            log.info("runda %s: 0 nowych — bez wiadomości", when)
        return result


async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    await do_scan(context)


async def daily_digest(context: ContextTypes.DEFAULT_TYPE):
    hits = day_tally["hits"]
    text = (f"🌙 <b>Podsumowanie dnia</b>\n\n"
            f"Rund: {day_tally['scans']} · nowych ogłoszeń: {day_tally['added']}\n"
            f"Trafień pod profil: {len(hits)}")
    if hits:
        cheapest = min(hits, key=lambda r: refresh.as_float(r.get("total_price")) or 1e9)
        text += "\n\nNajtańsze dziś:\n" + offer_line(1, cheapest)
    await send(context, text)
    day_tally.update(scans=0, added=0, hits=[])


# ---------------------------------------------------------------- commands

@owner_only
async def cmd_skan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if scan_lock.locked():
        await update.message.reply_text("Runda już trwa — daj jej skończyć.")
        return
    await update.message.reply_text("⏳ Skanuję OLX i Otodom…")
    await do_scan(context, announce_empty=True)


@owner_only
async def cmd_nowe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = store.connect()
    rows = [r for r in store.all_rows(conn) if str(r.get("days_known")) == "0"]
    conn.close()
    rows.sort(key=lambda r: refresh.as_float(r.get("total_price")) or 1e9)
    if not rows:
        await update.message.reply_text("Dziś nic nowego nie doszło.")
        return
    lines = [offer_line(i, r) for i, r in enumerate(rows[:10], 1)]
    await send(context, f"🆕 <b>Znalezione dzisiaj: {len(rows)}</b>\n\n" + "\n".join(lines))


@owner_only
async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = store.connect()
    rows = [r for r in store.all_rows(conn) if refresh.is_hit(r)]
    conn.close()
    rows.sort(key=lambda r: refresh.as_float(r.get("total_price")) or 1e9)
    if not rows:
        await update.message.reply_text("Nic nie pasuje pod profil. Poluzuj próg w .env.")
        return
    lines = [offer_line(i, r) for i, r in enumerate(rows[:10], 1)]
    await send(context, f"⭐ <b>Najtańsze pod profil ({len(rows)})</b>\n\n" + "\n".join(lines))


@owner_only
async def cmd_stan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = store.connect()
    stats = store.stats(conn)
    conn.close()
    jobs = context.job_queue.jobs()
    nxt = min((j.next_t for j in jobs if j.next_t), default=None)
    await send(context, (
        "📊 <b>Stan</b>\n\n"
        f"Ofert w bazie: {stats['offers']}\n"
        f"Znalezionych dziś: {stats['found_today']}\n"
        f"Rund dzisiaj: {day_tally['scans']} · nowych: {day_tally['added']}\n"
        f"Następna runda: {nxt.astimezone(TZ).strftime('%H:%M') if nxt else '—'}\n"
        f"Profil: ≤{refresh.ALERT_BELOW:.0f} zł, ≤{refresh.MAX_KM:.0f} km"))


@owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Rent Radar czuwa.\n\n"
        "/skan — przeskanuj teraz\n"
        "/nowe — znalezione dzisiaj\n"
        "/top — najtańsze pod profil\n"
        "/stan — stan bazy i harmonogramu")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("nieobsłużony błąd", exc_info=context.error)


def main():
    token = os.environ.get("BOT_TOKEN", "")
    if not token or not owner_id():
        raise SystemExit("Ustaw BOT_TOKEN i OWNER_ID w .env")
    if not os.environ.get(os.environ.get("LLM_KEY_ENV", "DEEPSEEK_API_KEY")):
        raise SystemExit("Brak klucza LLM w .env — skanowanie nie zadziała")

    app = Application.builder().token(token).build()
    for name, fn in (("start", cmd_start), ("skan", cmd_skan), ("nowe", cmd_nowe),
                     ("top", cmd_top), ("stan", cmd_stan)):
        app.add_handler(CommandHandler(name, fn))
    app.add_error_handler(on_error)

    for hour in SCAN_HOURS:
        app.job_queue.run_daily(scheduled_scan, time=dtime(hour=hour, tzinfo=TZ),
                                name=f"scan-{hour}")
    app.job_queue.run_daily(daily_digest, time=DIGEST_AT, name="digest")

    log.info("start · rundy o %s · podsumowanie %s",
             ", ".join(f"{h}:00" for h in SCAN_HOURS), DIGEST_AT.strftime("%H:%M"))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
