@echo off
chcp 65001 >nul
rem Сборка одного exe. Запускать на Windows, из папки проекта.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Сначала запусти "Поиск клиентов.bat": он создаст окружение.
    pause
    exit /b 1
)

echo Ставлю PyInstaller...
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller || goto :fail

echo Собираю...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean lead-finder.spec || goto :fail

echo.
echo Готово. Приложение лежит в dist\ПоискКлиентов\
echo Папку можно целиком перенести на другой компьютер.
pause
exit /b

:fail
echo.
echo Сборка не прошла. Прочитай сообщение выше.
pause
exit /b 1
