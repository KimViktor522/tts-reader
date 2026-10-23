@echo off
rem Запуск Telegram-бота озвучки. Окно можно свернуть; закрыть окно = остановить бота.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
:loop
python bot.py
echo Бот остановился, перезапуск через 15 секунд (Ctrl+C - выйти)...
timeout /t 15 >nul
goto loop
