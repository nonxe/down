import os, logging, subprocess, uuid
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from b import download_with_format

logging.basicConfig(level=logging.INFO)

LANGUAGES = ["English", "Spanish", "Russian", "Hebrew", "French", "Japanese", "Korean", "German", "Dutch"]
user_lang = {}
format_cache = {}

async def ask_language(update: Update):
    buttons = [LANGUAGES[i:i+3] for i in range(0, len(LANGUAGES), 3)]
    await update.message.reply_text("🌍 Choose your language:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))

async def set_language(update: Update, lang: str):
    user_lang[update.effective_user.id] = lang
    await update.message.reply_text(f"✅ Language set to {lang}. Now send a video link.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_lang:
        await ask_language(update)
    else:
        await update.message.reply_text(
            "👋 Welcome!\nSend a video link or use /audio <url>.\nChoose resolution after link.",
            reply_markup=ReplyKeyboardMarkup([["🌐 Change Language"]], resize_keyboard=True)
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    if text == "🌐 Change Language":
        await ask_language(update)
    elif text in LANGUAGES:
        await set_language(update, text)
    elif text.startswith("http"):
        await fetch_formats(update, context, text, uid)
    elif text.isdigit():
        await handle_format_selection(update, context, text, uid)

async def fetch_formats(update: Update, context, url: str, uid: int):
    try:
        result = subprocess.run(["yt-dlp", "-F", url], capture_output=True, text=True, timeout=30)
        lines = result.stdout.splitlines()
        formats = []
        for line in lines:
            if line.strip().startswith(tuple("0123456789")) and "audio only" not in line:
                parts = line.split()
                if len(parts) >= 3:
                    code = parts[0]
                    label = parts[-1]
                    formats.append((code, label))
        if not formats:
            await update.message.reply_text("❌ No formats found.")
            return
        format_cache[uid] = {"url": url, "formats": formats}
        buttons = [[f"{code}"] for code, _ in formats[:10]]
        await update.message.reply_text("🎥 Choose resolution:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
    except Exception as e:
        await update.message.reply_text(f"❌ Error fetching formats:\n{str(e)}")

async def handle_format_selection(update: Update, context, code: str, uid: int):
    if uid not in format_cache:
        await update.message.reply_text("❌ No video link found. Send a link first.")
        return
    url = format_cache[uid]["url"]
    await update.message.reply_text(f"🔄 Downloading format {code}...")
    result = download_with_format(url, code)
    if result["status"] == "ok":
        await update.message.reply_video(video=open(result["path"], "rb"))
    else:
        await update.message.reply_text("❌ Download failed.\n" + result["error"])

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /audio <URL>")
        return
    url = context.args[0]
    await update.message.reply_text("🎵 Downloading audio...")
    result = download_with_format(url, "bestaudio")
    if result["status"] == "ok":
        await update.message.reply_audio(audio=open(result["path"], "rb"))
    else:
        await update.message.reply_text("❌ Audio download failed.\n" + result["error"])

def main():
    token = os.getenv("TELEGRAM_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("audio", handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
