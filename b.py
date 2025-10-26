# b.py
import os
import re
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from pytube import YouTube
from typing import Optional, List, Dict, Tuple

# Languages (9 languages). Keep simple mapping code->texts; expand as needed.
I18N = {
    "en": {
        "welcome": "Welcome! Select your language:",
        "ask_url": "Send me a direct video or audio link (any site).",
        "choose_action": "Choose: Download Video or Download Audio",
        "processing": "Processing your request — hang tight for a moment.",
        "yt_dlp_failed": "Primary downloader failed, trying fallback...",
        "all_failed": "Sorry — I couldn't fetch the media. Check the link or try later.",
        "choose_format": "Choose a format (resolution) to download:",
        "downloading": "Downloading...",
        "uploading": "Uploading..."
    },
    "es": { "welcome":"¡Bienvenido! Selecciona tu idioma:", "ask_url":"Envíame un enlace..." },
    "ru": { "welcome":"Добро пожаловать! Выберите язык:", "ask_url":"Отправьте ссылку..." },
    "he": { "welcome":"ברוכים הבאים! בחר שפה:", "ask_url":"שלח לי קישור..." },
    "fr": { "welcome":"Bienvenue! Choisissez votre langue:", "ask_url":"Envoyez un lien..." },
    "ja": { "welcome":"ようこそ！言語を選択してください:", "ask_url":"リンクを送ってください..." },
    "ko": { "welcome":"환영합니다! 언어를 선택하세요:", "ask_url":"링크를 보내세요..." },
    "de": { "welcome":"Willkommen! Wähle deine Sprache:", "ask_url":"Sende mir einen Link..." },
    "nl": { "welcome":"Welkom! Kies je taal:", "ask_url":"Stuur een link..." }
}

def get_text(lang: str, key: str) -> str:
    return I18N.get(lang, I18N["en"]).get(key, I18N["en"].get(key, ""))

# Helpers to run shell commands safely
def run_cmd(cmd: List[str], cwd: Optional[str] = None, timeout: Optional[int] = 300) -> Tuple[int, str, str]:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    out, err = proc.communicate(timeout=timeout)
    return proc.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")

def safe_cleanup(path: str):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

# 1) Primary: yt-dlp format list + download
def ytdlp_list_formats(url: str) -> List[Dict]:
    """
    Returns a list of available formats (id, height, ext, format_note).
    """
    cmd = ["yt-dlp", "-j", "--skip-download", url]
    code, out, err = run_cmd(cmd, timeout=30)
    if code != 0:
        raise RuntimeError(f"yt-dlp metadata failed: {err[:200]}")
    info = json.loads(out)
    formats = info.get("formats", [])
    # Filter unique heights and progressive (mp4) preference
    parsed = []
    for f in formats:
        parsed.append({
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "height": f.get("height") or 0,
            "format_note": f.get("format_note") or "",
            "acodec": f.get("acodec"),
            "vcodec": f.get("vcodec")
        })
    # sort descending by height
    parsed.sort(key=lambda x: (x["height"] or 0), reverse=True)
    return parsed

def ytdlp_download(url: str, format_id: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out_template = os.path.join(out_dir, "%(title).200s.%(ext)s")
    cmd = ["yt-dlp", "-f", format_id, "-o", out_template, url]
    code, out, err = run_cmd(cmd, timeout=900)
    if code != 0:
        raise RuntimeError(f"yt-dlp download failed: {err[:200]}")
    # find newest file in out_dir
    files = sorted(Path(out_dir).glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(files[0]) if files else ""

# 2) Fallback: you-get (generic)
def youget_download(url: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    # you-get will put files in out_dir
    cmd = ["you-get", "-o", out_dir, url]
    code, out, err = run_cmd(cmd, timeout=900)
    if code != 0:
        raise RuntimeError(f"you-get failed: {err[:200]}")
    files = sorted(Path(out_dir).glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(files[0]) if files else ""

# 3) Final fallback: pytube only for YouTube links to fetch highest progressive mp4
def pytube_download(url: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    yt = YouTube(url)
    stream = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc().first()
    if not stream:
        raise RuntimeError("No progressive mp4 found via pytube")
    out = stream.download(output_path=out_dir)
    return out

# Utility: scan tmp dir for typical video files
def scan_for_video(tmpdir: str) -> Optional[str]:
    p = Path(tmpdir)
    exts = [".mp4", ".mkv", ".webm", ".mp3", ".m4a"]
    files = [f for f in p.glob("*") if f.suffix.lower() in exts]
    files_sorted = sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)
    return str(files_sorted[0]) if files_sorted else None

# simple URL test
URL_RE = re.compile(r"https?://\S+")
def contains_url(text: str) -> Optional[str]:
    m = URL_RE.search(text)
    return m.group(0) if m else None
