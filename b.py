import subprocess, uuid, os

def download_with_format(url, format_code):
    filename = f"/tmp/{uuid.uuid4()}"
    ext = ".mp3" if format_code == "bestaudio" else ".mp4"
    filename += ext
    cmd = ["yt-dlp", "-f", format_code, "-o", filename, "--no-playlist", "--no-check-certificate", "--no-warnings", url]
    try:
        subprocess.run(cmd, check=True)
        return {"status": "ok", "path": filename}
    except subprocess.CalledProcessError:
        return try_fallback(url)

def try_fallback(url):
    try:
        subprocess.run(["you-get", "-o", "/tmp", url], check=True)
        for file in os.listdir("/tmp"):
            if file.endswith(".mp4"):
                return {"status": "ok", "path": f"/tmp/{file}"}
    except Exception:
        pass
    try:
        from pytube import YouTube
        yt = YouTube(url)
        stream = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first()
        path = stream.download(output_path="/tmp")
        return {"status": "ok", "path": path}
    except Exception as e:
        return {"status": "fail", "error": str(e)}
