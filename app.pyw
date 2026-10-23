"""Окно озвучки: вставить ссылку или текст / выбрать файл -> «Озвучить» -> MP3 в папке out.

Запуск — двойной клик по app.pyw (pythonw, без консоли).
"""
import os
import queue
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import say

VOICES = {"Светлана": "ru-RU-SvetlanaNeural", "Дмитрий": "ru-RU-DmitryNeural"}
OUT = say.ROOT / "out"


class QueueWriter:
    """Перехват print из say.py в окно журнала."""
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s.strip():
            self.q.put(s.rstrip())

    def flush(self):
        pass


class App:
    def __init__(self, root):
        self.root, self.log_q, self.busy = root, queue.Queue(), False
        root.title("Озвучка статей")
        root.geometry("860x520")
        f = ttk.Frame(root, padding=10)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Ссылка, путь к PDF/TXT или сам текст:").pack(anchor="w")
        self.input = tk.Text(f, height=8, wrap="word", undo=True)
        self.input.pack(fill="x", pady=(2, 6))
        self.input.focus_set()

        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Button(row, text="Выбрать файл…", command=self.pick).pack(side="left")
        ttk.Button(row, text="Вставить", command=self.paste).pack(side="left", padx=6)
        ttk.Label(row, text="Голос:").pack(side="left", padx=(12, 2))
        self.voice = ttk.Combobox(row, values=list(VOICES), width=10, state="readonly")
        self.voice.set("Светлана")
        self.voice.pack(side="left")
        self.no_llm = tk.BooleanVar()
        ttk.Checkbutton(row, text="без чистки (быстрее)", variable=self.no_llm).pack(side="left", padx=12)
        self.quote_voice = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="цитаты другим голосом", variable=self.quote_voice).pack(side="left")

        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=8)
        self.go = ttk.Button(row2, text="Озвучить", command=self.start)
        self.go.pack(side="left")
        ttk.Button(row2, text="Открыть папку с MP3", command=lambda: os.startfile(OUT)).pack(side="left", padx=6)
        self.status = ttk.Label(row2, text="")
        self.status.pack(side="left", padx=12)

        self.log = tk.Text(f, height=12, state="disabled", wrap="word", bg="#f4f4f4")
        self.log.pack(fill="both", expand=True)
        root.bind("<Control-Return>", lambda e: self.start())
        self.poll()

    def pick(self):
        p = filedialog.askopenfilename(filetypes=[("Статьи", "*.pdf *.txt *.md"), ("Все", "*.*")])
        if p:
            self.input.delete("1.0", "end")
            self.input.insert("1.0", p)

    def paste(self):
        try:
            self.input.insert("insert", self.root.clipboard_get())
        except tk.TclError:
            pass

    def write_log(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def poll(self):
        while not self.log_q.empty():
            item = self.log_q.get()
            if isinstance(item, tuple):  # ("done", путь | None)
                self.finish(item[1])
            else:
                self.write_log(item)
        self.root.after(200, self.poll)

    def start(self):
        raw = self.input.get("1.0", "end").strip().strip('"')
        if self.busy or not raw:
            return
        self.busy = True
        self.go.configure(state="disabled")
        self.status.configure(text="Работаю… (с чисткой 5–20 минут)")
        threading.Thread(target=self.work, args=(raw, VOICES[self.voice.get()], self.no_llm.get(),
                                                 self.quote_voice.get()), daemon=True).start()

    def work(self, raw, voice, no_llm, quote_voice):
        old = sys.stdout
        sys.stdout = QueueWriter(self.log_q)
        result = None
        try:
            say.load_env()
            src, tmp = source_of(raw)
            try:
                try:
                    _, result = say.run(src, OUT, voice=voice, no_llm=no_llm, quote_voice=quote_voice)
                except SystemExit as e:
                    if no_llm or not any(w in str(e).lower() for w in ("openrouter", "deepseek")):
                        raise
                    print(f"Чистка не удалась: {e}. Озвучиваю без неё.")
                    _, result = say.run(src, OUT, voice=voice, no_llm=True, quote_voice=quote_voice)
            finally:
                if tmp:
                    os.unlink(tmp)
        except SystemExit as e:
            print(f"Ошибка: {e}")
        except Exception as e:
            print(f"Ошибка: {type(e).__name__}: {e}")
        finally:
            sys.stdout = old
            self.log_q.put(("done", result))

    def finish(self, mp3):
        self.busy = False
        self.go.configure(state="normal")
        if mp3:
            self.status.configure(text=f"Готово: {mp3.name}")
            os.startfile(mp3.parent)
        else:
            self.status.configure(text="Не получилось — см. журнал")


def source_of(raw):
    """Ссылка или существующий файл — как есть; иначе это текст, кладём во временный .txt."""
    if raw.startswith(("http://", "https://")) and "\n" not in raw:
        return raw, None
    if "\n" not in raw and len(raw) < 300 and Path(raw).is_file():
        return raw, None
    # имя файла станет именем MP3, поэтому без случайного суффикса — во временной папке
    tmp = Path(tempfile.mkdtemp()) / (say.safe_name(raw.split("\n", 1)[0][:60]) + ".txt")
    tmp.write_text(raw, encoding="utf-8")
    return str(tmp), str(tmp)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
