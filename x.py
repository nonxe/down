# x.py
import os
import logging
import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, Response
from sqlalchemy import create_engine, Table, Column, Integer, String, MetaData, select
from sqlalchemy.exc import OperationalError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

# local helpers
from b import get_text, ytdlp_list_formats, ytdlp_download, youget_download, pytube_download, safe_cleanup, contains_url, scan_for_video

load_dotenv()  # local dev

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/")
APP_NAME = os.getenv("APP_NAME", "telegram-downloader-bot")
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "en")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///./data/db.sqlite")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required (set in env)")

PORT = int(os.environ.get("PORT", "8443"))
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

# Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# DB (SQLAlchemy simple)
engine = create_engine(DATABASE_URL, echo=False, future=True)
metadata = MetaData()
users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("tg_id", Integer, unique=True),
    Column("lang", String(8))
)
metadata.create_all(engine)

def get_user_lang(tg_id: int) -> str:
    with engine.connect() as conn:
        stmt = select(users.c.lang).where(users.c.tg_id == tg_id)
        res = conn.execute(stmt).fetchone()
        return res[0] if res and res[0] else DEFAULT_LANGUAGE

def set_user_lang(tg_id: int, lang: str):
    with engine.begin() as conn:
        # try update else insert
        try:
            conn.execute(users.update().where(users.c.tg_id == tg_id).values(lang=lang))
        except OperationalError:
            pass
        # insert if not exists
        stmt = select(users).where(users.c.tg_id == tg_id)
        if not conn.execute(stmt).first():
            conn.execute(users.insert().values(tg_id=tg_id, lang=lang))

# Telegram Application
app = Flask(__name__)
application = ApplicationBuilder().token(BOT_TOKEN).build()

# -- Handlers --
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    # language selection keyboard
    languages = [
        ("English","en"), ("Español","es"), ("Русский","ru"), ("עברית","he"),
        ("Français","fr"), ("日本語","ja"), ("한국어","ko"), ("Deutsch","de"), ("Nederlands","nl")
    ]
    buttons = [[InlineKeyboardButton(name, callback_data=f"lang|{code}")] for name,code in languages]
    await update.message.reply_text("Choose your language / בחר שפה / Elige idioma", reply_markup=InlineKeyboardMarkup(buttons))

async def lang_select_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    payload = q.data.split("|")
    if len(payload) == 2 and payload[0] == "lang":
        lang = payload[1]
        tg_id = q.from_user.id
        set_user_lang(tg_id, lang)
        await q.edit_message_text(get_text(lang,"ask_url"))
        return

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text or ""
    tg_id = update.effective_user.id
    lang = get_user_lang(tg_id)
    url = None
    # detect url
    from b import contains_url
    url = contains_url(txt)
    if not url:
        await update.message.reply_text(get_text(lang,"ask_url"))
        return
    # present action buttons
    buttons = [
        [InlineKeyboardButton("🎥 Video", callback_data=f"action|video|{url}")],
        [InlineKeyboardButton("🎵 Audio", callback_data=f"action|audio|{url}")]
    ]
    await update.message.reply_text(get_text(lang,"choose_action"), reply_markup=InlineKeyboardMarkup(buttons))

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    parts = data.split("|")
    if parts[0] == "action":
        # action|video|<url>  OR action|audio|<url>
        mode = parts[1]
        url = "|".join(parts[2:])  # allow pipes in url if any
        tg_id = q.from_user.id
        lang = get_user_lang(tg_id)
        await q.edit_message_text(get_text(lang,"processing"))
        if mode == "audio":
            await process_audio(q.message.chat_id, url, lang, context)
        else:
            # for video: list formats first using yt-dlp -F
            try:
                formats = ytdlp_list_formats(url)
                # select top unique heights
                seen = set()
                buttons = []
                for f in formats:
                    h = f.get("height") or 0
                    label = f"{h}p" if h > 0 else f.get("ext", "file")
                    if h not in seen:
                        seen.add(h)
                        buttons.append([InlineKeyboardButton(label, callback_data=f"format|{f['format_id']}|{url}")])
                if not buttons:
                    # fallback to direct download attempt
                    await q.message.reply_text(get_text(lang,"yt_dlp_failed"))
                    await process_video_download(q.message.chat_id, url, None, lang, context)
                else:
                    await q.message.reply_text(get_text(lang,"choose_format"), reply_markup=InlineKeyboardMarkup(buttons))
            except Exception as e:
                log.exception("yt-dlp list failed")
                await q.message.reply_text(get_text(lang,"yt_dlp_failed"))
                await process_video_download(q.message.chat_id, url, None, lang, context)
    elif parts[0] == "format":
        # format|<format_id>|<url>
        format_id = parts[1]
        url = "|".join(parts[2:])
        tg_id = q.from_user.id
        lang = get_user_lang(tg_id)
        await q.edit_message_text(get_text(lang,"downloading"))
        await process_video_download(q.message.chat_id, url, format_id, lang, context)

# Processing functions
async def process_audio(chat_id: int, url: str, lang: str, context: ContextTypes.DEFAULT_TYPE):
    tmpdir = f"/tmp/{chat_id}"
    safe_cleanup(tmpdir)
    os.makedirs(tmpdir, exist_ok=True)
    try:
        # try yt-dlp bestaudio
        try:
            out = ytdlp_download(url, "bestaudio", tmpdir)
        except Exception as e1:
            log.exception("yt-dlp audio failed")
            # fallback you-get
            try:
                out = youget_download(url, tmpdir)
            except Exception as e2:
                log.exception("you-get failed")
                # final fallback (if youtube)
                if "youtube.com" in url or "youtu.be" in url:
                    try:
                        out = pytube_download(url, tmpdir)
                    except Exception:
                        out = None
                else:
                    out = None
        if not out:
            await application.bot.send_message(chat_id, get_text(lang,"all_failed"))
            return
        # upload file (if big, send as document; else use send_audio)
        fsize = Path(out).stat().st_size
        caption = None
        if fsize < 50 * 1024 * 1024:
            await application.bot.send_audio(chat_id, audio=InputFile(out), caption=caption)
        else:
            await application.bot.send_document(chat_id, document=InputFile(out), caption=caption)
    finally:
        safe_cleanup(tmpdir)

async def process_video_download(chat_id: int, url: str, format_id: str, lang: str, context: ContextTypes.DEFAULT_TYPE):
    tmpdir = f"/tmp/{chat_id}"
    safe_cleanup(tmpdir)
    os.makedirs(tmpdir, exist_ok=True)
    try:
        out = None
        # If format_id provided use yt-dlp
        if format_id:
            try:
                out = ytdlp_download(url, format_id, tmpdir)
            except Exception:
                log.exception("yt-dlp format download failed")
                out = None
        # If no output try generic best via yt-dlp
        if not out:
            try:
                out = ytdlp_download(url, "bestvideo+bestaudio/best", tmpdir)
            except Exception:
                log.exception("yt-dlp best fallback failed")
                # try you-get
                try:
                    out = youget_download(url, tmpdir)
                except Exception:
                    log.exception("you-get failed")
                    if "youtube.com" in url or "youtu.be" in url:
                        try:
                            out = pytube_download(url, tmpdir)
                        except Exception:
                            out = None
                    else:
                        out = None
        if not out:
            # try scanning
            scan = scan_for_video(tmpdir)
            if scan:
                out = scan
        if not out:
            await application.bot.send_message(chat_id, get_text(lang,"all_failed"))
            return
        fsize = Path(out).stat().st_size
        if fsize < 50 * 1024 * 1024:
            await application.bot.send_video(chat_id, video=InputFile(out))
        else:
            await application.bot.send_document(chat_id, document=InputFile(out))
    finally:
        safe_cleanup(tmpdir)

# Set up handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(lang_select_cb, pattern=r"^lang\|"))
application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_message))
application.add_handler(CallbackQueryHandler(callback_router, pattern=r"^(action|format)\|"))

# Flask app for Heroku webhook
@app.route("/", methods=["GET"])
def home():
    return f"OK - {APP_NAME}"

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    # Process update in the background loop
    asyncio.run(application.update_queue.put(update))
    return Response("OK", status=200)

# startup: set webhook if WEBHOOK_BASE_URL provided
if __name__ == "__main__":
    if WEBHOOK_BASE_URL:
        webhook_url = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"
        # set webhook
        async def _run_webhook():
            await application.bot.set_webhook(webhook_url)
            log.info(f"Webhook set to {webhook_url}")
            # run flask server
            from waitress import serve
            serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", PORT)))
        asyncio.run(_run_webhook())
    else:
        # fallback to polling for local dev
        application.run_polling()
