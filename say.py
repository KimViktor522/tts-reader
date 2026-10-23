"""Озвучка статей: URL / PDF / TXT / stdin -> чистка LLM (OpenRouter) -> TTS -> один MP3.

    python say.py https://teletype.in/@x/abc
    python say.py статья.pdf --tts yandex
    python say.py текст.txt --no-llm
"""
import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent

PROMPT = """Подготовь текст статьи к озвучиванию синтезатором речи на русском языке.
Это НЕ пересказ: сохрани весь авторский текст, порядок, примеры и формулировки.
Правила:
- Убери служебные символы (#, *, |, >, маркеры списков, эмодзи), ссылки-URL,
  подписи к картинкам, призывы подписаться, навигацию и рекламу.
- Каждый заголовок — отдельный абзац, в конце заголовка поставь точку.
- Аббревиатуры расшифруй, если это не ломает смысл; если аббревиатура обычно
  читается по буквам, запиши её через дефисы (Эс-И-О), если словом — оставь.
- Числа, проценты, валюты, даты и единицы запиши так, чтобы их было удобно
  прочитать вслух (например «5%» -> «пять процентов» не нужно, но «$100» ->
  «100 долларов»).
- Таблицы и списки перескажи связными фразами, проговаривая заголовок столбца
  перед значением, чтобы было понятно на слух.
- Абзацы разделяй пустой строкой. Не добавляй ничего от себя, никаких
  вступлений и комментариев. Верни только готовый текст."""


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


# ---------- извлечение ----------

def extract(src):
    """Вернуть (заголовок, текст с абзацами через пустую строку)."""
    if src == "-":
        return "text", sys.stdin.read()
    if re.match(r"https?://", src):
        return extract_url(src)
    p = Path(src)
    if p.suffix.lower() == ".pdf":
        return p.stem, extract_pdf(p)
    return p.stem, p.read_text(encoding="utf-8-sig")


def extract_url(url):
    from bs4 import BeautifulSoup
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")
    og = soup.find("meta", property="og:title")
    title = og["content"] if og else (soup.title.string if soup.title else "article")
    root = (soup.find(attrs={"itemprop": "articleBody"}) or soup.find("article")
            or soup.find("main") or soup.body)
    for t in root(["script", "style", "noscript", "figure", "button", "nav", "iframe"]):
        t.decompose()
    parts = [title.strip()]
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "table"]):
        # таблицу берём целиком, её ячейки (p внутри) повторно не берём
        if el.name != "table" and el.find_parent("table"):
            continue
        if el.name in ("p", "li") and el.find_parent(["li", "blockquote"]):
            continue
        if el.name == "table":
            parts.append(table_to_text(el))
            continue
        txt = el.get_text(" ", strip=True)
        if not txt:
            continue
        if el.name.startswith("h") and not txt.endswith((".", "!", "?", ":")):
            txt += "."
        parts.append(txt)
    return title, "\n\n".join(parts)


def table_to_text(table):
    rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            for tr in table.find_all("tr")]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    head, body = rows[0], rows[1:]
    lines = [f"{h}: {v}" for row in body for h, v in zip(head, row)]
    return "Таблица. " + ". ".join(lines) + "." if body else ". ".join(head) + "."


def extract_pdf(path):
    from pypdf import PdfReader
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    text = re.sub(r"-\n(?=[а-яa-z])", "", text)  # перенос слова
    # строки внутри абзаца склеиваем; новый абзац — после точки/!/?/: в конце строки
    text = re.sub(r"(?<![.!?:»])\n(?!\n)", " ", text)
    return re.sub(r"\n+", "\n\n", text)


# ---------- чистка ----------

def llm_config():
    """OpenRouter приоритетнее; DeepSeek напрямую — если есть только его ключ."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return ("OpenRouter", "https://openrouter.ai/api/v1/chat/completions",
                os.environ["OPENROUTER_API_KEY"],
                os.environ.get("OPENROUTER_MODEL", "google/gemma-4-31b-it:free,"
                               "nvidia/nemotron-3-ultra-550b-a55b:free"))
    if os.environ.get("DEEPSEEK_API_KEY"):
        return ("DeepSeek", "https://api.deepseek.com/chat/completions",
                os.environ["DEEPSEEK_API_KEY"],
                os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    sys.exit("Нет OPENROUTER_API_KEY в .env — укажите ключ или запустите с --no-llm")


def ask_llm(name, url, key, models, piece):
    """Перебрать модели по кругу: бесплатные часто отвечают 429/404 при перегрузке."""
    for wait in (30, 90, None):
        for model in models:
            r = requests.post(
                url, headers={"Authorization": f"Bearer {key}"},
                json={"model": model,
                      "messages": [{"role": "system", "content": PROMPT},
                                   {"role": "user", "content": piece}],
                      "temperature": 0.2,
                      "usage": {"include": True}},  # OpenRouter вернёт стоимость
                timeout=600)
            data = r.json() if r.status_code == 200 else {}
            msg = ((data.get("choices") or [{}])[0].get("message")) or {}
            # бесплатные модели иногда отдают 200 с пустым content — считаем отказом
            if (msg.get("content") or "").strip():
                print(f"    модель: {model}", flush=True)
                return data
            if r.status_code in (401, 402):  # ключ/баланс — перебор не поможет
                break
            why = "пустой ответ" if r.status_code == 200 else r.status_code
            print(f"    {model}: {why}, пробую дальше", flush=True)
        if wait is None or r.status_code in (401, 402):
            break
        print(f"    все модели заняты, жду {wait} с", flush=True)
        time.sleep(wait)
    sys.exit(f"{name} {r.status_code}: {r.text[:300]}")


def clean(text, limit=6000):
    name, url, key, model = llm_config()
    models = [m.strip() for m in model.split(",") if m.strip()]
    out, cost, tokens = [], 0.0, 0
    pieces = split(text, limit)
    print(f"  {name}: {len(pieces)} кусков", flush=True)
    # бесплатные модели медленные — куски чистятся параллельно, map сохраняет порядок
    with ThreadPoolExecutor(int(os.environ.get("LLM_THREADS", "4"))) as pool:
        results = list(pool.map(lambda p: ask_llm(name, url, key, models, p), pieces))
    for data in results:
        out.append(data["choices"][0]["message"]["content"].strip())
        usage = data.get("usage") or {}
        tokens += usage.get("total_tokens", 0)
        cost += usage.get("cost") or 0
    print(f"  LLM: {tokens} токенов" + (f", ${cost:.4f}" if cost else ""), flush=True)
    result = "\n\n".join(out)
    if len(result) < 0.85 * len(text):
        print(f"  ВНИМАНИЕ: после чистки {len(result)} из {len(text)} символов "
              "— проверьте .txt, не потерян ли текст", flush=True)
    return result


def basic_clean(text):
    """Без LLM: убрать URL, markdown-символы и лишние пробелы."""
    text = re.sub(r"https?://\S+|\b[\w.-]+\.(?:ru|com|org|net|io|me|in|su)/\S*", "", text)
    text = re.sub(r"[#*_|>`~]+|-{3,}|—\s*—", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# ---------- нарезка ----------

def split(text, limit):
    """Куски <= limit символов; режем по абзацам, затем предложениям, затем словам."""
    units = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = para.strip()
        if len(para) <= limit:
            units.append(para)
            continue
        for sent in re.split(r"(?<=[.!?…])\s+", para):
            while len(sent) > limit:
                cut = sent.rfind(" ", 0, limit)
                cut = cut if cut > 0 else limit
                units.append(sent[:cut])
                sent = sent[cut:].strip()
            units.append(sent)
    chunks, cur = [], ""
    for u in filter(None, units):
        if cur and len(cur) + 2 + len(u) > limit:
            chunks.append(cur)
            cur = u
        else:
            cur = f"{cur}\n\n{u}" if cur else u
    if cur:
        chunks.append(cur)
    return chunks


# ---------- озвучка ----------

def tts_edge(chunk, path, voice):
    import edge_tts
    asyncio.run(edge_tts.Communicate(chunk, voice or "ru-RU-SvetlanaNeural").save(str(path)))


def tts_yandex(chunk, path, voice):
    key = os.environ.get("YANDEX_API_KEY")
    if not key:
        sys.exit("Нет YANDEX_API_KEY в .env — см. README, раздел Yandex SpeechKit")
    r = requests.post(
        "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize",
        headers={"Authorization": f"Api-Key {key}"},
        data={"text": chunk, "lang": "ru-RU", "voice": voice or "alena",
              "format": "mp3", "speed": os.environ.get("YANDEX_SPEED", "1.0")},
        timeout=120)
    if r.status_code != 200:
        sys.exit(f"Yandex SpeechKit {r.status_code}: {r.text[:300]}")
    path.write_bytes(r.content)


ENGINES = {"edge": (tts_edge, 3000), "yandex": (tts_yandex, 4900)}


def synth(text, out, engine, voice):
    fn, limit = ENGINES[engine]
    chunks = split(text, limit)
    with tempfile.TemporaryDirectory() as tmp:
        files = [Path(tmp) / f"{i:04}.mp3" for i in range(len(chunks))]

        def one(i):
            for attempt in range(3):
                try:
                    fn(chunks[i], files[i], voice)
                    print(f"  {engine}: кусок {i + 1}/{len(chunks)}", flush=True)
                    return
                except Exception as e:
                    if attempt == 2:
                        raise
                    print(f"    кусок {i + 1}: повтор после ошибки: {e}", flush=True)

        # куски синтезируются параллельно, порядок задаёт список files
        with ThreadPoolExecutor(int(os.environ.get("TTS_THREADS", "4"))) as pool:
            list(pool.map(one, range(len(chunks))))
        concat(files, out, Path(tmp))


def concat(files, out, tmp):
    if shutil.which("ffmpeg"):
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", str(out)], check=True)
    else:  # MP3 допускает побайтовую склейку, длительность в плеере может врать
        out.write_bytes(b"".join(f.read_bytes() for f in files))


# ---------- main ----------

def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|«»]+', "", s).strip(" .")
    return re.sub(r"\s+", "_", s)[:80] or "article"


def selftest():
    t = "Заголовок.\n\n" + "Очень длинное предложение номер один. " * 200 + "\n\nКоротко."
    ch = split(t, 500)
    assert all(len(c) <= 500 for c in ch), "кусок длиннее лимита"
    joined = re.sub(r"\s+", " ", " ".join(ch))
    assert joined == re.sub(r"\s+", " ", t).strip(), "при нарезке потерян текст"
    assert split("а" * 1200, 500) and all(len(c) <= 500 for c in split("а" * 1200, 500))
    c = basic_clean("см. https://x.ru/a ## **Важно** — sponsr.ru/crim/39071 —\n\n------")
    assert "http" not in c and "sponsr" not in c and "--" not in c and "#" not in c, c
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description="Озвучка статьи в MP3")
    ap.add_argument("source", nargs="?", help="URL, .pdf, .txt или - (stdin)")
    ap.add_argument("--tts", choices=ENGINES, default="edge")
    ap.add_argument("--voice", help="edge: ru-RU-SvetlanaNeural / ru-RU-DmitryNeural; "
                                    "yandex: alena, filipp, ermil, jane, omazh, zahar")
    ap.add_argument("--no-llm", action="store_true", help="без LLM, только простая чистка")
    ap.add_argument("--text-only", action="store_true", help="только подготовить .txt")
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.source:
        ap.error("укажите источник")
    load_env()
    run(a.source, Path(a.out), a.tts, a.voice, a.no_llm, a.text_only)


def run(source, out_dir, tts="edge", voice=None, no_llm=False, text_only=False):
    """Весь конвейер; возвращает (заголовок, путь к .mp3 или .txt). Используется и ботом."""
    if tts == "yandex" and not text_only and not os.environ.get("YANDEX_API_KEY"):
        sys.exit("Нет YANDEX_API_KEY в .env — см. README, раздел Yandex SpeechKit")
    title, raw = extract(source)
    print(f"Извлечено: {len(raw)} символов — {title}")
    text = basic_clean(raw) if no_llm else clean(raw)
    print(f"К озвучке: {len(text)} символов")
    out_dir.mkdir(parents=True, exist_ok=True)
    # не with_suffix: точки в заголовке («0,25 п.п.») он принял бы за расширение
    base = str(out_dir / safe_name(title))
    txt, mp3 = Path(base + ".txt"), Path(base + ".mp3")
    txt.write_text(text, encoding="utf-8")
    if text_only:
        print(f"Текст: {txt}")
        return title, txt
    synth(text, mp3, tts, voice)
    print(f"Готово: {mp3}")
    return title, mp3


if __name__ == "__main__":
    main()
