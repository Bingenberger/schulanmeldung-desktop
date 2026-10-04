#!/bin/bash
# Baut das Disk-Image der macOS-Fassung.
#
#     packaging/macos-dmg.sh 1.0.0 arm64
#
# Erwartet dist/SL-Office.app aus packaging/sl-office.spec. Das Image enthält
# die App und eine Verknüpfung auf "Programme" zum Hinüberziehen. Die Daten
# liegen getrennt davon in ~/Library/Application Support/SL-Office.
set -euo pipefail

version="${1:?Version fehlt}"
arch="${2:-$(uname -m)}"
cd "$(dirname "$0")/.."

inhalt="$(mktemp -d)"
trap 'rm -rf "$inhalt"' EXIT
ditto dist/SL-Office.app "$inhalt/SL-Office.app"
ln -s /Applications "$inhalt/Programme"

ziel="dist/SL-Office-${version}-macOS-${arch}.dmg"
rm -f "$ziel"
hdiutil create -volname "SL-Office ${version}" -srcfolder "$inhalt" -fs HFS+ -format UDZO -ov "$ziel"
echo "$ziel"
