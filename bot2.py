import os
import sqlite3
import random
import asyncio
import requests
import time

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

from fastapi import FastAPI, Request
import uvicorn
from contextlib import asynccontextmanager

import httpx

# =========================
# 🔐 ENV VARIABLES
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

OH_API_KEY = os.getenv("OH_API_KEY")
CHARACTER_ID = os.getenv("OH_CHARACTER_ID")

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN")
if not WEBHOOK_URL:
    raise ValueError("Missing WEBHOOK_URL")
if not OH_API_KEY:
    raise ValueError("Missing OH_API_KEY")
if not CHARACTER_ID:
    raise ValueError("Missing OH_CHARACTER_ID")

BASE_URL = "https://api.oh.xyz"

HEADERS = {
    "X-API-Key": OH_API_KEY,
    "Content-Type": "application/json"
}

# =========================
# 🧠 DATABASE
# =========================
conn = sqlite3.connect("/tmp/bot_memory.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    relationship INTEGER DEFAULT 0
)
""")
conn.commit()

# =========================
# HTTP CLIENT (ASYNC)
# =========================
client = httpx.AsyncClient(timeout=30)

# =========================
# 💕 RELATIONSHIP SYSTEM
# =========================
def create_user(user_id):
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, relationship) VALUES (?, 0)",
        (user_id,)
    )
    conn.commit()

def update_relationship(user_id):
    cursor.execute(
        "UPDATE users SET relationship = relationship + 1 WHERE user_id=?",
        (user_id,)
    )
    conn.commit()

# =========================
# 🧠 ROOM SYSTEM (OH API)
# =========================

user_rooms = {}

def create_room():
    res = requests.post(
        f"{BASE_URL}/api/v1/rooms",
        headers=HEADERS,
        json={"character_id": CHARACTER_ID}
    )

    data = res.json()
    print("ROOM RESPONSE:", data)

    return (
        data.get("room_id")
        or data.get("roomId")
        or (data.get("room") or {}).get("id")
    )


def get_room(user_id):
    if user_id not in user_rooms:
        room_id = create_room()

        if not room_id:
            raise Exception("Room creation failed: no room_id returned")

        user_rooms[user_id] = room_id

    return user_rooms[user_id]

# =========================
# 💬 CHAT (ASYNC FIXED)
# =========================
async def chat(user_id, message):
    room_id = await get_room(user_id)

    res = await client.post(
        f"{BASE_URL}/api/v1/text",
        headers=HEADERS,
        json={
            "room_id": room_id,
            "character_id": CHARACTER_ID,
            "message": message
        }
    )

    data = res.json()
    return data.get("response", "hmm... say that again?")

# =========================
# 🎨 IMAGE (ASYNC FIXED)
# =========================
async def generate_image(prompt):
    res = await client.post(
        f"{BASE_URL}/api/v1/images",
        headers=HEADERS,
        json={
            "character_id": CHARACTER_ID,
            "prompt": prompt
        }
    )

    job_id = res.json()["job_id"]

    for _ in range(30):
        status = await client.get(
            f"{BASE_URL}/api/v1/jobs/{job_id}/status",
            headers=HEADERS
        )

        data = status.json()

        if data["status"] == "completed":
            return data["presigned_url"]

        if data["status"] == "failed":
            raise Exception("Image failed")

        await asyncio.sleep(2)

    raise Exception("Image timeout")

# =========================
# 🎥 VIDEO (ASYNC FIXED)
# =========================
async def generate_video(prompt):
    res = await client.post(
        f"{BASE_URL}/api/v1/videos/create",
        headers=HEADERS,
        json={
            "character_id": CHARACTER_ID,
            "prompt": prompt
        }
    )

    job_id = res.json()["job_id"]

    for _ in range(60):
        status = await client.get(
            f"{BASE_URL}/api/v1/jobs/{job_id}/status",
            headers=HEADERS
        )

        data = status.json()

        if data["status"] == "completed":
            return data["presigned_url"]

        if data["status"] == "failed":
            raise Exception("Video failed")

        await asyncio.sleep(2)

    raise Exception("Video timeout")

# =========================
# 🔊 AUDIO (ASYNC FIXED)
# =========================
async def generate_audio(text):
    res = await client.post(
        f"{BASE_URL}/api/v1/audio/notes",
        headers=HEADERS,
        json={
            "character_id": CHARACTER_ID,
            "text": text
        }
    )

    job_id = res.json()["job_id"]

    for _ in range(30):
        status = await client.get(
            f"{BASE_URL}/api/v1/jobs/{job_id}/status",
            headers=HEADERS
        )

        data = status.json()

        if data["status"] == "completed":
            return data["presigned_url"]

        if data["status"] == "failed":
            raise Exception("Audio failed")

        await asyncio.sleep(2)

    raise Exception("Audio timeout")

# =========================
# 🤖 TELEGRAM HANDLER (ASYNC FIXED)
# =========================
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    create_user(user_id)
    update_relationship(user_id)

    try:
        if text.startswith("/image"):
            prompt = text.replace("/image", "").strip()
            url = await generate_image(prompt)
            await update.message.reply_photo(url)
            return

        if text.startswith("/video"):
            prompt = text.replace("/video", "").strip()
            url = await generate_video(prompt)
            await update.message.reply_video(url)
            return

        if text.startswith("/audio"):
            prompt = text.replace("/audio", "").strip()
            url = await generate_audio(prompt)
            await update.message.reply_audio(url)
            return

        reply = await chat(user_id, text)
        await update.message.reply_text(reply)

    except Exception as e:
        print("Error:", e)
        await update.message.reply_text("ugh something broke 😩 try again")

# =========================
# 🌐 FASTAPI + WEBHOOK
# =========================
ptb = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
ptb.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

@asynccontextmanager
async def lifespan(app: FastAPI):
    await ptb.initialize()
    await ptb.start()
    await ptb.bot.set_webhook(url=WEBHOOK_URL)
    yield
    await ptb.stop()
    await client.aclose()

app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, ptb.bot)
    await ptb.process_update(update)
    return {"ok": True}

@app.get("/")
async def home():
    return {"status": "running"}

# =========================
# 🚀 RUN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
    
