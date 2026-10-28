import asyncio
import hashlib
import json
import os
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = Path("course_state.json")

TARGET_URLS = [
    {
        "name": "Course Finder (EN - Available Seats Only)",
        "url": "https://www.goethe.de/ins/bd/en/spr/kur/tup.cfm?f=%7B%22pspElement%22%3A%5B%5D%2C%22custom25110069%22%3A%5B%5D%2C%22startDate%22%3A%5B%5D%2C%22eventNameObjectId%22%3A%5B%5D%2C%22languageLevelGroup%22%3A%5B%5D%2C%22category%22%3A%5B%5D%2C%22filter_locationId%22%3A%5B%5D%2C%22languageSubLevel%22%3A%5B%5D%2C%22age%22%3A%22ER%22%2C%22fullAvailability%22%3A%22sure%22%7D",
    },
    {
        "name": "Course Finder (EN - All Adult Courses)",
        "url": "https://www.goethe.de/ins/bd/en/spr/kur/tup.cfm?f=%7B%22pspElement%22%3A%5B%5D%2C%22custom25110069%22%3A%5B%5D%2C%22startDate%22%3A%5B%5D%2C%22eventNameObjectId%22%3A%5B%5D%2C%22languageLevelGroup%22%3A%5B%5D%2C%22category%22%3A%5B%5D%2C%22filter_locationId%22%3A%5B%5D%2C%22languageSubLevel%22%3A%5B%5D%2C%22age%22%3A%22ER%22%7D",
    },
    {
        "name": "Course Finder (DE - All Adult Courses)",
        "url": "https://www.goethe.de/ins/bd/de/spr/kur/tup.cfm?f=%7B%22pspElement%22%3A%5B%5D%2C%22custom25110069%22%3A%5B%5D%2C%22startDate%22%3A%5B%5D%2C%22eventNameObjectId%22%3A%5B%5D%2C%22languageLevelGroup%22%3A%5B%5D%2C%22category%22%3A%5B%5D%2C%22filter_locationId%22%3A%5B%5D%2C%22languageSubLevel%22%3A%5B%5D%2C%22age%22%3A%22ER%22%7D",
    },
    {
        "name": "Dhaka In-Person Overview (A1-B2)",
        "url": "https://www.goethe.de/ins/bd/en/spr/kur/all.html",
    },
    {
        "name": "Online Courses Overview (A1-B2)",
        "url": "https://www.goethe.de/ins/bd/en/spr/kur/onl.html",
    },
]

BOOKING_KEYWORDS = [
    "book", "buchen", "choose course", "kurs wählen",
    "available", "places available", "plätze frei"
]


def send_telegram_alert(text: str, button_url: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram credentials missing!")
        return
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [[{"text": "🔗 সরাসরি বুকিং পেজে যান", "url": button_url}]]
        },
    }
    requests.post(api_url, json=payload, timeout=15)


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_courses(html: str, source_name: str, source_url: str):
    soup = BeautifulSoup(html, "html.parser")
    extracted = []
    selectors = ["tr", "article", "div[class*='course']", "div[class*='kur']"]
    candidates = []
    for sel in selectors:
        candidates.extend(soup.select(sel))

    seen = set()
    for item in candidates:
        text = " ".join(item.get_text(separator=" ", strip=True).split())
        if len(text) < 25 or len(text) > 1500:
            continue

        lower_text = text.lower()
        if not any(k in lower_text for k in ["a1", "a2", "b1", "b2", "c1", "intensive", "bdt"]):
            continue

        is_bookable = any(kw in lower_text for kw in BOOKING_KEYWORDS)
        if any(bad in lower_text for bad in ["not available", "ausgebucht", "fully booked"]):
            is_bookable = False

        item_id = hashlib.md5(f"{source_name}:{text[:160]}".encode("utf-8")).hexdigest()
        if item_id in seen:
            continue
        seen.add(item_id)

        extracted.append({
            "id": item_id,
            "source_name": source_name,
            "source_url": source_url,
            "summary": text[:300],
            "is_bookable": is_bookable,
        })
    return extracted


async def main():
    state = load_state()
    is_first_run = len(state) == 0
    all_courses = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"
        )
        for target in TARGET_URLS:
            page = await context.new_page()
            try:
                await page.goto(target["url"], wait_until="networkidle", timeout=45000)
                await page.wait_for_timeout(3000)
                html = await page.content()
                all_courses.extend(parse_courses(html, target["name"], target["url"]))
            except Exception as e:
                print(f"Error checking {target['name']}: {e}")
            finally:
                await page.close()
        await browser.close()

    for course in all_courses:
        cid = course["id"]
        prev = state.get(cid)

        should_notify = False
        header = ""

        if prev is None and not is_first_run:
            should_notify = True
            header = "🆕 <b>নতুন কোর্স যুক্ত হয়েছে!</b>"
        elif prev and not prev.get("is_bookable") and course["is_bookable"]:
            should_notify = True
            header = "🟢 <b>কোর্সের সিট খালি হয়েছে (বুকিং চলছে)!</b>"

        state[cid] = course

        if should_notify:
            status_str = "✅ বুকিং করা যাচ্ছে" if course["is_bookable"] else "ℹ️ তালিকায় যুক্ত হয়েছে"
            msg = (
                f"{header}\n\n"
                f"📌 <b>পেজ:</b> {course['source_name']}\n"
                f"🏷 <b>স্ট্যাটাস:</b> {status_str}\n"
                f"📝 <b>বিবরণ:</b>\n<code>{course['summary']}</code>"
            )
            send_telegram_alert(msg, course["source_url"])

    if is_first_run and all_courses:
        send_telegram_alert(
            "✅ <b>Goethe-Institut Bangladesh Bot সক্রিয় হয়েছে!</b>\n"
            f"মোট {len(all_courses)}টি কোর্স ব্লক ট্র্যাক করা শুরু হয়েছে। নতুন সিট বা কোর্স আসামাত্রই এখানে নোটিফিকেশন পাবেন।",
            TARGET_URLS[0]["url"],
        )

    save_state(state)


if __name__ == "__main__":
    asyncio.run(main())
