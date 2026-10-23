"""Telegram-бот: присылаете ссылку, PDF, TXT или текст — получаете MP3.

Работает на этом ПК, пока запущен (start_bot.bat). Отвечает только id из
TELEGRAM_ALLOWED_IDS, остальным сообщает их id и отказывает.
"""
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import traceback
from pathlib import Path

import requests

import say

say.load_env()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or exit("Нет TELEGRAM_BOT_TOKEN в .env — см. README")
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
ALLOWED = {s.strip() for s in os.environ.get("TELEGRAM_ALLOWED_IDS", "").split(",") if s.strip()}
OUT = say.ROOT / "out"
MAX_AUDIO = 48 * 1024 * 1024  # лимит Telegram на отправку ботом — 50 МБ
jobs = queue.Queue()


def tg(method, **kw):
    r = requests.post(f"{API}/{method}", timeout=kw.pop("_timeout", 60), **kw)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description')}")
    return data["result"]


def reply(chat, text):
    tg("sendMessage", data={"chat_id": chat, "text": text[:4000]})


def source_of(msg, tmp):
    """Из сообщения сделать то, что понимает say.extract: URL или путь к файлу."""
    if doc := msg.get("document"):
        name = doc.get("file_name") or "file.txt"
        if not name.lower().endswith((".pdf", ".txt", ".md")):
            raise ValueError("Принимаю PDF, TXT или MD")
        if doc.get("file_size", 0) > 20 * 1024 * 1024:
            raise ValueError("Файл больше 20 МБ — Telegram не отдаёт такие ботам")
        path = tg("getFile", data={"file_id": doc["file_id"]})["file_path"]
        local = Path(tmp) / name
        local.write_bytes(requests.get(f"{FILE_API}/{path}", timeout=120).content)
        return local
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if m := re.search(r"https?://\S+", text):
        if len(text) < len(m.group()) + 40:  # сообщение — по сути одна ссылка
            return m.group()
    if len(text) < 200:
        raise ValueError("Пришлите ссылку, PDF, TXT или текст длиннее пары предложений")
    local = Path(tmp) / f"{safe_title(text)}.txt"
    local.write_text(text, encoding="utf-8")
    return local


def safe_title(text):
    return say.safe_name(text.split("\n", 1)[0][:60])


def split_mp3(mp3, tmp):
    """Если MP3 больше лимита Telegram — порезать на части по времени без перекодирования."""
    if mp3.stat().st_size <= MAX_AUDIO:
        return [mp3]
    pattern = str(Path(tmp) / f"{mp3.stem[:40]}_%02d.mp3")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-f", "segment",
                    "-segment_time", "7200", "-c", "copy", pattern], check=True)
    return sorted(Path(tmp).glob(f"{mp3.stem[:40]}_*.mp3"))


def handle(msg):
    chat = msg["chat"]["id"]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            src = source_of(msg, tmp)
        except ValueError as e:
            return reply(chat, str(e))
        reply(chat, "Принял, готовлю аудио. Обычно 5–20 минут.")
        t = time.time()
        try:
            title, mp3 = say.run(str(src), OUT)
        except SystemExit as e:  # say.py сообщает об ошибках LLM/ключей через sys.exit
            if any(w in str(e).lower() for w in ("openrouter", "deepseek")):
                reply(chat, f"Чистка текста не удалась ({str(e)[:200]}). Озвучиваю без неё.")
                title, mp3 = say.run(str(src), OUT, no_llm=True)
            else:
                raise RuntimeError(str(e))
        parts = split_mp3(mp3, tmp)
        for i, part in enumerate(parts, 1):
            name = title if len(parts) == 1 else f"{title} ({i}/{len(parts)})"
            with open(part, "rb") as f:
                tg("sendAudio", data={"chat_id": chat, "title": name[:200], "performer": "tts-reader"},
                   files={"audio": (part.name, f, "audio/mpeg")}, _timeout=600)
        print(f"Отправлено за {time.time() - t:.0f} с: {title}", flush=True)


def worker():
    while True:
        msg = jobs.get()
        try:
            handle(msg)
        except Exception as e:
            traceback.print_exc()
            try:
                reply(msg["chat"]["id"], f"Ошибка: {e}")
            except Exception:
                pass


def main():
    try:
        me = tg("getMe")
    except RuntimeError as e:
        exit(f"{e} — проверьте TELEGRAM_BOT_TOKEN в .env")
    print(f"Бот @{me['username']} запущен. Разрешены id: {', '.join(ALLOWED) or 'никто'}", flush=True)
    threading.Thread(target=worker, daemon=True).start()
    offset = None
    while True:
        try:
            updates = tg("getUpdates", data={"timeout": 50, "offset": offset}, _timeout=70)
        except Exception as e:
            print(f"Нет связи с Telegram: {e}; повтор через 10 с", flush=True)
            time.sleep(10)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message")
            if not msg:
                continue
            uid = str(msg["from"]["id"])
            if uid not in ALLOWED:
                reply(msg["chat"]["id"], f"Ваш id: {uid}. Впишите его в .env "
                                         "(TELEGRAM_ALLOWED_IDS) и перезапустите бота.")
                continue
            if (msg.get("text") or "").startswith("/start"):
                reply(msg["chat"]["id"], "Пришлите ссылку на статью, PDF, TXT или просто текст — верну MP3.")
                continue
            if not jobs.empty():
                reply(msg["chat"]["id"], f"В очереди: {jobs.qsize()}, ваш запрос следующий.")
            jobs.put(msg)


if __name__ == "__main__":
    main()
