# tts-reader — озвучка статей в MP3

Вставляете ссылку на статью, PDF, TXT или просто текст — получаете один MP3
с озвучкой на русском. Можно слушать статьи как подкаст: в дороге, на
прогулке, за рулём.

Что происходит внутри:

1. **Извлечение текста** — со страницы (без меню и кнопок), из PDF или TXT.
2. **Чистка для слуха** через LLM на [OpenRouter](https://openrouter.ai) —
   без пересказа: убирает служебные символы и ссылки, раскрывает
   аббревиатуры («ФРС» → «Эф-Эр-Эс»), пишет числа и годы словами,
   проговаривает таблицы. По умолчанию — **бесплатные** модели.
3. **Озвучка** нейроголосами Microsoft через
   [edge-tts](https://github.com/rany2/edge-tts) — **бесплатно**. Запасной
   вариант — Yandex SpeechKit (платно).
4. **Цитаты другим голосом:** выделенные блоки-цитаты на странице, а в
   PDF/TXT — чужие слова, которые отметила LLM, звучат противоположным
   голосом (Светлана ↔ Дмитрий). Отключается галочкой или `--no-quote-voice`.
5. **Склейка** кусков в один MP3. Рядом сохраняется `.txt` — ровно то, что
   было озвучено.

Итого — 0 ₽ за статью. Примерно 900 символов = 1 минута аудио; статья на
19 тыс. символов (20 минут аудио) готовится за ~8 минут.

## Установка (Windows)

Понадобится:

- **Python 3.10+** — [python.org](https://www.python.org/downloads/), при
  установке отметить «Add python.exe to PATH».
- **ffmpeg** — для склейки MP3 (без него тоже работает, но плеер может
  показывать неверную длительность):

  ```powershell
  winget install Gyan.FFmpeg
  ```

Скачать проект и поставить зависимости:

```powershell
git clone https://github.com/KimViktor522/tts-reader.git
```

```powershell
cd tts-reader
```

```powershell
pip install -r requirements.txt
```

Или без git: зелёная кнопка **Code → Download ZIP** на GitHub, распаковать,
в папке выполнить `pip install -r requirements.txt`.

## Ключ OpenRouter (бесплатно)

1. Зарегистрироваться на [openrouter.ai](https://openrouter.ai).
2. **Settings → Keys → Create Key**, скопировать ключ `sk-or-...`.
3. В папке проекта скопировать `.env.example` в `.env` и вписать ключ:

   ```
   OPENROUTER_API_KEY=sk-or-...
   ```

`.env` хранится только у вас и в git не попадает (см. `.gitignore`).

Без ключа тоже можно — с галочкой «без чистки» (`--no-llm`): текст
озвучивается как есть, аббревиатуры и таблицы читаются буквально.

## Использование

### Окно приложения

Двойной клик по `app.pyw` (откроется без консоли).

- Вставить в поле ссылку, путь к файлу или сам текст — или нажать
  **«Выбрать файл…»**.
- Выбрать голос: Светлана или Дмитрий.
- Нажать **«Озвучить»** (или Ctrl+Enter). Ход работы виден в журнале; когда
  MP3 готов, откроется папка `out`.

Ярлык на рабочий стол (выполнить в PowerShell из папки проекта):

```powershell
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\Озвучка статей.lnk"); $s.TargetPath = (Join-Path (Split-Path (Get-Command python).Source) 'pythonw.exe'); $s.Arguments = "`"$PWD\app.pyw`""; $s.WorkingDirectory = "$PWD"; $s.IconLocation = "$env:SystemRoot\System32\SndVol.exe,0"; $s.Save()
```

### Командная строка

```powershell
python say.py https://teletype.in/@pirats_club/OfxIH8GulLd
```

```powershell
python say.py "статья.pdf" --voice ru-RU-DmitryNeural
```

Флаги:

| Флаг | Что делает |
|---|---|
| `--no-llm` | без LLM: только простая чистка (ссылки, разметка) |
| `--text-only` | только подготовить `.txt`, чтобы проверить чистку |
| `--voice` | edge: `ru-RU-SvetlanaNeural`, `ru-RU-DmitryNeural`; yandex: `alena`, `filipp`, `ermil`, `jane`, `omazh`, `zahar` |
| `--no-quote-voice` | цитаты тем же голосом, что и основной текст |
| `--tts yandex` | озвучка Yandex SpeechKit вместо edge-tts (нужен `YANDEX_API_KEY`) |
| `--out <папка>` | куда класть результат (по умолчанию `out`) |
| `--selftest` | самопроверка нарезки и чистки |

### Telegram-бот (необязательно)

Бот принимает ссылку, PDF, TXT или текст и отвечает MP3. Работает на вашем
ПК, пока он включён.

1. В Telegram: [@BotFather](https://t.me/BotFather) → `/newbot` → токен
   вписать в `.env` (`TELEGRAM_BOT_TOKEN=...`).
2. Запустить `start_bot.bat`.
3. Написать боту — он ответит вашим id. Вписать его в `.env`
   (`TELEGRAM_ALLOWED_IDS=...`) и перезапустить. Посторонним бот не отвечает.

## Настройки `.env`

| Переменная | Назначение |
|---|---|
| `OPENROUTER_API_KEY` | ключ OpenRouter для чистки текста |
| `OPENROUTER_MODEL` | модели через запятую, пробуются по порядку. По умолчанию `google/gemma-4-31b-it:free,nvidia/nemotron-3-ultra-550b-a55b:free`. Платный надёжный запас — добавить в конец `deepseek/deepseek-v4-flash` (~$0,01 за статью) |
| `DEEPSEEK_API_KEY` | вместо OpenRouter — DeepSeek напрямую |
| `YANDEX_API_KEY` | для `--tts yandex`: Yandex Cloud → сервисный аккаунт с ролью `ai.speechkit-tts.user` → API-ключ |
| `TTS_THREADS`, `LLM_THREADS` | сколько кусков обрабатывать параллельно (по умолчанию 4) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_IDS` | для бота |

## Ограничения

- **Бесплатные модели** OpenRouter бывают перегружены (ответ 429) — скрипт
  переключается на следующую модель, ждёт и повторяет. Если не вышло совсем,
  окно и бот озвучивают текст без чистки и предупреждают. Набор `:free`
  моделей меняется — актуальный список на
  [openrouter.ai/models](https://openrouter.ai/models?max_price=0).
- **Приватность:** текст статьи уходит в OpenRouter и провайдеру модели;
  бесплатные модели могут использовать запросы для обучения. Для личных
  документов — `--no-llm` или платная модель.
- **edge-tts** — неофициальный доступ к голосам Microsoft Edge; если
  перестанет работать — `--tts yandex`.
- Страницы, которые рисуются только JavaScript-ом или требуют входа,
  не извлекаются — сохраните текст в `.txt`.
- Сканы PDF без текстового слоя не поддерживаются (нужен OCR).
- Цитаты в PDF/TXT распознаёт LLM, поэтому без чистки (`--no-llm`) второго
  голоса там нет. На сайтах второй голос получают все блоки-цитаты, включая
  авторские врезки. В `.txt` такие абзацы помечены `[ЦИТАТА]` — маркер можно
  поставить и вручную.

## Лицензия

MIT — см. [LICENSE](LICENSE).
