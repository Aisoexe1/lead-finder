#!/bin/bash
# Собирает приложение и ставит его в Applications.
cd "$(dirname "$0")"
set -e

if [ ! -x .venv/bin/python3 ]; then
  echo "Нет окружения. Сначала:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -n 1 -s -r -p "Нажми любую клавишу"
  exit 1
fi

echo "Ставлю PyInstaller..."
.venv/bin/pip install -q --upgrade pyinstaller

echo "Собираю..."
.venv/bin/python3 -m PyInstaller --noconfirm --clean --log-level WARN lead-finder.spec

APP="Поиск клиентов.app"
if [ -w /Applications ]; then
  TARGET="/Applications"
else
  TARGET="$HOME/Applications"
  mkdir -p "$TARGET"
fi

rm -rf "$TARGET/$APP"
cp -R "dist/$APP" "$TARGET/"
# снимаем карантин, иначе Gatekeeper потребует подтверждения при первом запуске
xattr -dr com.apple.quarantine "$TARGET/$APP" 2>/dev/null || true

echo
echo "Готово: $TARGET/$APP"
echo "Настройки и база лежат отдельно, в ~/Library/Application Support/ПоискКлиентов"
echo
read -n 1 -s -r -p "Нажми любую клавишу, чтобы закрыть"
