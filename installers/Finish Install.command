#!/bin/bash
set -euo pipefail
APP="/Applications/PhotoResize.app"

if [ ! -d "$APP" ]; then
  osascript -e 'display alert "PhotoResize Installer" message "Move PhotoResize.app to the Applications folder first, then run this again." as critical'
  exit 1
fi

xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
osascript -e 'display notification "PhotoResize is ready to use." with title "Installation Complete"'
open "$APP"
