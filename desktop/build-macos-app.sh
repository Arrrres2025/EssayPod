#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="$PROJECT_ROOT/dist"
APP_PATH="$OUTPUT_DIR/EssayPod.app"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/essaypod-app.XXXXXX")"
STAGED_APP="$TEMP_DIR/EssayPod.app"
CONTENTS_DIR="$STAGED_APP/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

cleanup() {
  /bin/rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

if [[ ! -x "$PROJECT_ROOT/../.venv/bin/python" ]]; then
  print -u2 "找不到 $PROJECT_ROOT/../.venv/bin/python"
  exit 1
fi

if [[ ! -x "$PROJECT_ROOT/web_ui/node_modules/.bin/vinext" ]]; then
  print -u2 "前端依赖尚未安装，请先在 web_ui 目录运行 npm install。"
  exit 1
fi

print "正在检查 Web UI 构建…"
(
  cd "$PROJECT_ROOT/web_ui"
  NEXT_PUBLIC_API_BASE="http://127.0.0.1:8010" npm run build
)

mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"
cp "$SCRIPT_DIR/Info.plist" "$CONTENTS_DIR/Info.plist"
/usr/libexec/PlistBuddy \
  -c "Set :EssayPodProjectRoot $PROJECT_ROOT" \
  "$CONTENTS_DIR/Info.plist"

print "正在编译 macOS 窗口壳…"
clang \
  -fobjc-arc \
  -O2 \
  -mmacosx-version-min=13.0 \
  -framework AppKit \
  -framework UniformTypeIdentifiers \
  -framework WebKit \
  "$SCRIPT_DIR/main.m" \
  -o "$MACOS_DIR/EssayPod"

ICON_GENERATOR="$TEMP_DIR/EssayPodIcon"
clang \
  -fobjc-arc \
  -O2 \
  -mmacosx-version-min=13.0 \
  -framework AppKit \
  "$SCRIPT_DIR/AppIcon.m" \
  -o "$ICON_GENERATOR"
"$ICON_GENERATOR" "$RESOURCES_DIR/AppIcon.icns"

touch "$STAGED_APP"
xattr -cr "$STAGED_APP"
codesign --force --deep --sign - "$STAGED_APP"

mkdir -p "$OUTPUT_DIR"
if [[ -d "$APP_PATH" ]]; then
  PREVIOUS_APP="$OUTPUT_DIR/EssayPod.previous.app"
  if [[ -d "$PREVIOUS_APP" ]]; then
    /bin/rm -rf "$PREVIOUS_APP"
  fi
  mv "$APP_PATH" "$PREVIOUS_APP"
fi
mv "$STAGED_APP" "$APP_PATH"

print
print "已生成：$APP_PATH"
