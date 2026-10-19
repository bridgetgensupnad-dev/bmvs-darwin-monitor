#!/usr/bin/env python3
"""BMVS Darwin appointment checker for GitHub Actions.

Notification-only. It does not book, reserve, or submit an appointment and does
not attempt to bypass CAPTCHA/security checks.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Iterable

import requests
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright

BASE_URL = "https://bmvs.onlineappointmentscheduling.net.au/oasis/"
LOCATION = os.getenv("BMVS_LOCATION", "Darwin").strip() or "Darwin"
POSTCODE = os.getenv("BMVS_POSTCODE", "0800").strip() or "0800"
EXAM_CODES = [x.strip() for x in os.getenv("BMVS_EXAM_CODES", "501,502").split(",") if x.strip()]
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

TIME_RE = re.compile(r"\b(?:0?[1-9]|1[0-2]):[0-5]\d\s*(?:am|pm)\b|\b(?:[01]?\d|2[0-3]):[0-5]\d\b", re.I)
DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")


def log(msg: str) -> None:
    print(msg, flush=True)


def telegram_api(method: str, **payload) -> dict:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", data=payload, timeout=25)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error: {data.get('description', data)}")
    return data


def discover_chat_id() -> str:
    """Use TELEGRAM_CHAT_ID, or discover it from the user's /start message."""
    if CHAT_ID:
        return CHAT_ID
    data = telegram_api("getUpdates", timeout=0, allowed_updates=json.dumps(["message"]))
    for upd in reversed(data.get("result", [])):
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is not None:
            return str(cid)
    raise RuntimeError(
        "Telegram chat not found. Open your bot in Telegram, send /start, then run the workflow again."
    )


def send_message(chat_id: str, text: str) -> None:
    telegram_api("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true")


async def body_text(page: Page) -> str:
    try:
        return (await page.locator("body").inner_text(timeout=5000)).strip()
    except Exception:
        return ""


async def security_or_session_problem(page: Page) -> str | None:
    txt = (await body_text(page)).lower()
    url = page.url.lower()
    if "sessionexpired" in url or "session has expired" in txt:
        return "Bupa returned a session-expired page"
    for phrase in ("captcha", "verify you are human", "access denied", "unusual traffic", "security check"):
        if phrase in txt:
            return "Bupa displayed a CAPTCHA/security check"
    return None


async def click_text(page: Page, patterns: Iterable[str], timeout: int = 3500) -> bool:
    for p in patterns:
        rx = re.compile(p, re.I)
        for loc in (
            page.get_by_role("button", name=rx),
            page.get_by_role("link", name=rx),
            page.get_by_text(rx, exact=False),
        ):
            try:
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.click(timeout=timeout)
                    await page.wait_for_timeout(1000)
                    return True
            except Exception:
                continue
    return False


async def choose_new_individual_booking(page: Page) -> bool:
    return await click_text(page, [r"new\s+individual\s+booking", r"make\s+(?:a|an)\s+appointment", r"book\s+appointment"])


async def fill_postcode_and_state(page: Page) -> bool:
    changed = False
    # Postcode input.
    candidates = [
        page.get_by_label(re.compile(r"post\s*code|postcode", re.I)),
        page.locator('input[name*="post" i], input[id*="post" i], input[placeholder*="post" i]'),
    ]
    for loc in candidates:
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.fill(POSTCODE)
                changed = True
                break
        except Exception:
            pass

    # State dropdown, if present.
    for sel in (page.locator('select[name*="state" i]'), page.locator('select[id*="state" i]')):
        try:
            if await sel.count() and await sel.first.is_visible():
                opts = await sel.first.locator("option").all()
                selected = False
                for opt in opts:
                    text = (await opt.inner_text()).strip()
                    value = await opt.get_attribute("value")
                    if re.search(r"\bNT\b|Northern Territory", text, re.I):
                        await sel.first.select_option(value=value)
                        selected = True
                        changed = True
                        break
                if selected:
                    break
        except Exception:
            pass

    if changed:
        await click_text(page, [r"search", r"find", r"continue", r"next"])
    return changed


async def choose_darwin(page: Page) -> bool:
    """Choose an interactive Darwin location result using surrounding text."""
    return bool(await page.evaluate(
        """
        (locationName) => {
          const norm = s => String(s || '').replace(/\\s+/g,' ').trim();
          const els = [...document.querySelectorAll('label, tr, li, .location, .result, div')];
          const hit = els.find(el => new RegExp(locationName, 'i').test(norm(el.innerText)) && norm(el.innerText).length < 500);
          if (!hit) return false;
          const target = hit.querySelector('input[type=radio], input[type=checkbox], button, a') ||
                         (hit.matches('label,button,a') ? hit : null);
          if (!target) return false;
          if (target.matches('input')) target.click(); else target.click();
          return true;
        }
        """,
        LOCATION,
    ))


async def choose_exams(page: Page) -> int:
    selected = 0
    for code in EXAM_CODES:
        try:
            done = await page.evaluate(
                """
                (code) => {
                  const nodes = [...document.querySelectorAll('label,tr,li,div')];
                  const hit = nodes.find(el => new RegExp('(^|\\\\D)'+code+'(\\\\D|$)').test((el.innerText||'')) && (el.innerText||'').length < 700);
                  if (!hit) return false;
                  const input = hit.querySelector('input[type=checkbox],input[type=radio]');
                  if (!input || input.disabled) return false;
                  if (!input.checked) input.click();
                  return true;
                }
                """,
                code,
            )
            if done:
                selected += 1
        except Exception:
            pass
    return selected


async def fill_optional_identity(page: Page) -> int:
    """Fill identity fields only when matching optional GitHub Secrets exist."""
    values = {
        r"hap\s*id|ime|uci": os.getenv("BUPA_HAP_ID", "").strip(),
        r"first\s*name|given\s*name": os.getenv("BUPA_FIRST_NAME", "").strip(),
        r"surname|last\s*name|family\s*name": os.getenv("BUPA_LAST_NAME", "").strip(),
        r"date\s*of\s*birth|dob": os.getenv("BUPA_DOB", "").strip(),
        r"passport": os.getenv("BUPA_PASSPORT", "").strip(),
    }
    filled = 0
    for label_pattern, value in values.items():
        if not value:
            continue
        rx = re.compile(label_pattern, re.I)
        for loc in (page.get_by_label(rx), page.locator(f'input[aria-label*="{label_pattern.split("|")[0]}" i]')):
            try:
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.fill(value)
                    filled += 1
                    break
            except Exception:
                continue
    return filled


async def has_calendar(page: Page) -> bool:
    if await page.locator("#divPaginationNavigation button").count():
        return True
    txt = (await body_text(page)).lower()
    return "appointment" in txt and any(k in txt for k in ("available", "calendar", "select a date", "appointment date", "time")) and bool(
        await page.locator('button, input[type="radio"], td, a').count()
    )


async def advance_to_calendar(page: Page) -> None:
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1200)

    if not await choose_new_individual_booking(page):
        log("Start page did not expose the expected New Individual Booking control; continuing with current page.")

    for step in range(10):
        await page.wait_for_timeout(800)
        problem = await security_or_session_problem(page)
        if problem:
            raise RuntimeError(problem)
        if await has_calendar(page):
            return

        text = (await body_text(page)).lower()
        moved = False

        if LOCATION.lower() not in text or "postcode" in text or "post code" in text:
            moved = await fill_postcode_and_state(page) or moved
            await page.wait_for_timeout(800)

        # Select Darwin whenever a location result is visible.
        if LOCATION.lower() in (await body_text(page)).lower():
            try:
                if await choose_darwin(page):
                    moved = True
                    await page.wait_for_timeout(500)
            except Exception:
                pass

        exam_hits = await choose_exams(page)
        if exam_hits:
            moved = True

        identity_filled = await fill_optional_identity(page)
        if identity_filled:
            moved = True

        # Advance only after trying page-specific selections.
        if await click_text(page, [r"continue", r"next", r"search", r"find\s+appointments?", r"show\s+appointments?"]):
            moved = True

        if not moved:
            # If the page requires an unconfigured personal field, make the error actionable.
            lower = (await body_text(page)).lower()
            if any(k in lower for k in ("hap id", "date of birth", "passport", "first name", "surname")):
                raise RuntimeError(
                    "Bupa is asking for personal booking details before showing the calendar. "
                    "Add the optional BUPA_* secrets described in README.md, then rerun."
                )
            raise RuntimeError(f"Could not automatically continue through the Bupa booking flow (step {step + 1}, URL {page.url}).")

    raise RuntimeError("Reached the navigation limit before the appointment calendar appeared.")


async def detect_slots(page: Page) -> list[str]:
    """Read available date/time controls. The old BMVS UI uses #divPaginationNavigation buttons."""
    found: list[str] = []

    # Known BMVS available-day selector from the public historical automation.
    for btn in await page.locator("#divPaginationNavigation button").all():
        try:
            if not await btn.is_visible() or not await btn.is_enabled():
                continue
            value = (await btn.get_attribute("data-value") or "").strip()
            text = (await btn.inner_text()).strip()
            item = value or text
            if item:
                found.append(item)
        except Exception:
            pass

    # Generic fallback: enabled time radios/buttons or green/clickable date-like elements.
    generic = await page.evaluate(
        """
        () => {
          const visible = el => { const s=getComputedStyle(el), r=el.getBoundingClientRect(); return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0; };
          const rgb = s => { const m=String(s||'').match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/i); return m ? m.slice(1,4).map(Number) : null; };
          const green = c => c && c[1] >= 80 && c[1] >= c[0]*1.15 && c[1] >= c[2]*1.10;
          const out=[];
          for (const el of document.querySelectorAll('button,a,input,td,label')) {
            if (!visible(el) || el.disabled || el.getAttribute('aria-disabled')==='true') continue;
            const t=((el.innerText||el.value||el.getAttribute('data-text')||el.getAttribute('aria-label')||'')+'').replace(/\\s+/g,' ').trim();
            if (!t || t.length>120) continue;
            const meta=((el.className||'')+' '+(el.id||'')+' '+(el.getAttribute('data-status')||'')).toLowerCase();
            const cs=getComputedStyle(el);
            const time=/\\b(?:0?[1-9]|1[0-2]):[0-5]\\d\\s*(?:am|pm)\\b|\\b(?:[01]?\\d|2[0-3]):[0-5]\\d\\b/i.test(t);
            const date=/^\\d{1,2}$/.test(t) || /\\b\\d{1,2}[\\/-]\\d{1,2}(?:[\\/-]\\d{2,4})?\\b/.test(t);
            const explicit=/available|bookable|selectable/.test(meta) && !/unavailable|disabled|full/.test(meta);
            if (time || explicit || (date && (green(rgb(cs.backgroundColor)) || green(rgb(cs.color))))) out.push(t);
          }
          return out;
        }
        """
    )
    found.extend(str(x).strip() for x in generic if str(x).strip())

    # De-duplicate while preserving order.
    unique = []
    seen = set()
    for x in found:
        key = x.lower()
        if key not in seen:
            seen.add(key)
            unique.append(x)
    return unique[:20]


async def run() -> int:
    if not TOKEN:
        log("ERROR: TELEGRAM_BOT_TOKEN GitHub Secret is missing.")
        return 2

    try:
        # Verify token and discover recipient before launching a browser.
        telegram_api("getMe")
        chat_id = discover_chat_id()
    except Exception as e:
        log(f"Telegram setup error: {e}")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1360, "height": 900})
            page = await context.new_page()
            try:
                await advance_to_calendar(page)
                slots = await detect_slots(page)
                if slots:
                    details = "\n".join(f"• {s}" for s in slots[:12])
                    send_message(
                        chat_id,
                        f"🚨 BMVS DARWIN SLOT FOUND\n\n{details}\n\nOpen Bupa now to verify/book.\nChecked: {stamp}",
                    )
                    log(f"Availability detected: {', '.join(slots[:5])}")
                else:
                    log(f"No Darwin slot detected ({stamp}).")
                return 0
            finally:
                await context.close()
                await browser.close()
    except PlaywrightTimeoutError as e:
        msg = f"BMVS check timed out. GitHub will try again on the next run. ({type(e).__name__})"
        log(msg)
        try:
            send_message(chat_id, "⚠️ " + msg)
        except Exception:
            pass
        return 1
    except Exception as e:
        msg = f"BMVS checker needs attention: {e}"
        log(msg)
        try:
            send_message(chat_id, "⚠️ " + msg)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
