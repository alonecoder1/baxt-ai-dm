"""
Baxt Mebellari — Instagram DM AI yordamchi (SendPulse -> Railway -> Claude -> Telegram)

SendPulse "API-запрос" bloki shu serverga POST yuboradi:
  URL:  https://<railway-domen>/ai?key=<WEBHOOK_SECRET>
  Body: {"contact_id": "...", "text": "...", "username": "...", "name": "..."}
Server qaytaradi: {"reply": "...", "stage": "hot|warm|cold"}
Mijoz raqam yozsa -> lead avtomatik Telegram guruhga ketadi.

Railway Variables:
  ANTHROPIC_API_KEY   — console.anthropic.com dan
  TELEGRAM_BOT_TOKEN  — lead bot tokeni
  LEAD_CHAT_ID        — lead guruh chat_id (masalan -100...)
  WEBHOOK_SECRET      — o'zingiz o'ylab topgan maxfiy so'z
  CLAUDE_MODEL        — ixtiyoriy, standart: claude-haiku-4-5-20251001
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

import httpx
from anthropic import Anthropic
from fastapi import FastAPI, Request

app = FastAPI()
client = Anthropic()  # ANTHROPIC_API_KEY ni o'zi o'qiydi

MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
LEAD_CHAT_ID = os.environ["LEAD_CHAT_ID"]
SECRET = os.getenv("WEBHOOK_SECRET", "")
MAX_MESSAGES = 20  # har bir mijoz uchun eslab qolinadigan oxirgi xabarlar
TASHKENT = timezone(timedelta(hours=5))

with open(os.path.join(os.path.dirname(__file__), "system_prompt.txt"), encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

histories: dict[str, list] = {}  # contact_id -> suhbat tarixi
sent_leads: set[str] = set()     # bir mijoz 2 marta yuborilmasligi uchun

FALLBACK_REPLY = (
    "Uzr, hozir javob bera olmadim 🙏 Raqamingizni qoldirsangiz, "
    "menejerimiz o'zi bog'lanadi. Yoki qo'ng'iroq qiling: +998 77 728 77 44"
)

PHONE_RE = re.compile(
    r"(?:\+?\s*998)?[\s\-().]*(\d{2})[\s\-().]*(\d{3})[\s\-().]*(\d{2})[\s\-().]*(\d{2})(?!\d)"
)


def normalize_phone(text: str | None) -> str | None:
    """Matndan O'zbekiston raqamini topib, +998 XX XXX XX XX ko'rinishiga keltiradi."""
    if not text:
        return None
    m = PHONE_RE.search(str(text))
    if not m:
        return None
    return "+998 " + " ".join(m.groups())


def parse_model_output(raw: str) -> dict:
    """Claude javobidan JSON ni ajratib oladi. Buzilgan bo'lsa, matnni reply sifatida oladi."""
    clean = raw.replace("```json", "").replace("```", "").strip()
    start, end = clean.find("{"), clean.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(clean[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {"reply": clean, "stage": "unknown"}


def send_lead_to_telegram(data: dict, result: dict, phone: str) -> None:
    stage = (result.get("stage") or "").lower()
    icon = {"hot": "🔥 HOT", "warm": "🌤 WARM", "cold": "❄️ COLD"}.get(stage, "📥")
    username = data.get("username") or ""
    lines = [
        f"{icon} YANGI LEAD — Instagram DM",
        f"👤 Ism: {data.get('name') or '—'}",
        f"📞 {phone}",
        f"🛋 Model: {result.get('model') or '—'}",
        f"📍 Hudud: {result.get('region') or '—'}",
        f"💬 Izoh: {result.get('summary') or '—'}",
        f"🔗 instagram.com/{username}" if username else "🔗 —",
        f"🕒 {datetime.now(TASHKENT).strftime('%d.%m.%Y %H:%M')}",
    ]
    try:
        httpx.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": LEAD_CHAT_ID, "text": "\n".join(lines)},
            timeout=10,
        )
    except Exception as e:
        print("Telegram xato:", e)


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/ai")
async def ai(req: Request):
    if SECRET and req.query_params.get("key") != SECRET:
        return {"reply": "", "stage": "forbidden"}

    data = await req.json()
    cid = str(data.get("contact_id") or data.get("username") or "unknown")
    text = str(data.get("text") or "").strip()
    if not text:
        return {"reply": "Assalomu alaykum! 😊 Qanday yordam bera olaman?", "stage": "cold"}

    hist = histories.setdefault(cid, [])
    hist.append({"role": "user", "content": text})
    del hist[:-MAX_MESSAGES]
    while hist and hist[0]["role"] != "user":
        hist.pop(0)

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=600, system=SYSTEM_PROMPT, messages=hist
        )
        raw = "".join(b.text for b in resp.content if b.type == "text")
        result = parse_model_output(raw)
        hist.append({"role": "assistant", "content": raw})
    except Exception as e:
        print("Claude xato:", e)
        hist.pop()  # muvaffaqiyatsiz xabarni tarixdan olib tashlaymiz
        result = {"reply": FALLBACK_REPLY, "stage": "unknown"}

    reply = (result.get("reply") or FALLBACK_REPLY).strip()

    # Raqam: avval mijoz matnidan, bo'lmasa AI topganidan
    phone = normalize_phone(text) or normalize_phone(result.get("phone"))
    if phone and cid not in sent_leads:
        send_lead_to_telegram(data, result, phone)
        sent_leads.add(cid)

    return {"reply": reply, "stage": result.get("stage", "")}
