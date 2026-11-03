#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
LAUNCHER="$DATA_HOME/applications/aipool-manager.desktop"
ICON="$DATA_HOME/icons/hicolor/128x128/apps/aipool-manager.png"
case "${1:-}" in
    --uninstall)
        rm -f -- "$LAUNCHER" "$ICON"
        echo '已移除应用菜单入口（不删除程序、数据或开机自启设置）'
        exit 0 ;;
    '') ;;
    *) echo "Usage: $0 [--uninstall]" >&2; exit 1 ;;
esac
EXECUTABLE="$ROOT/AI号池管理器/AI号池管理器"
[[ -x "$EXECUTABLE" ]] || { echo "Executable not found: $EXECUTABLE" >&2; exit 1; }
ESCAPED="${EXECUTABLE//%/%%}"
ESCAPED="${ESCAPED//\\/\\\\}"
ESCAPED="${ESCAPED//\"/\\\"}"
ESCAPED="${ESCAPED//\`/\\\`}"
ESCAPED="${ESCAPED//\$/\\\$}"
ESCAPED="${ESCAPED//\\/\\\\}"
ESCAPED="${ESCAPED//$'\n'/\\n}"
ESCAPED="${ESCAPED//$'\r'/\\r}"
ESCAPED="${ESCAPED//$'\t'/\\t}"
mkdir -p -- "$(dirname "$LAUNCHER")" "$(dirname "$ICON")"
TEMPORARY="$(mktemp "${LAUNCHER}.XXXXXX")"
trap 'rm -f -- "$TEMPORARY"' EXIT
while IFS= read -r LINE || [[ -n "$LINE" ]]; do
    if [[ "$LINE" == 'Exec=@EXEC@' ]]; then
        printf 'Exec="%s"\n' "$ESCAPED"
    else
        printf '%s\n' "$LINE"
    fi
done < "$ROOT/aipool-manager.desktop" > "$TEMPORARY"
chmod 644 "$TEMPORARY"
install -m 644 "$ROOT/aipool-manager.png" "$ICON"
mv -f -- "$TEMPORARY" "$LAUNCHER"
echo "已安装：$LAUNCHER；移动解压目录后请重新运行本脚本。"
