@echo off
chcp 65001 >nul
rem Двойной клик запускает приложение.
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" run.py
    exit /b
)

where python >nul 2>nul
if errorlevel 1 (
    echo Python не найден. Установи Python 3.10 или новее с python.org
    echo и при установке отметь галочку "Add python.exe to PATH".
    pause
    exit /b 1
)

echo Первый запуск: создаю окружение и ставлю зависимости.
python -m venv .venv || goto :fail
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
start "" ".venv\Scripts\pythonw.exe" run.py
exit /b

:fail
echo.
echo Не получилось подготовить окружение. Прочитай сообщение выше.
pause
exit /b 1
