# 🔗 LinkPretty

A lightweight macOS menubar app that turns copied URLs into rich, clickable links. Automatically fetches the page title from your browser and replaces the clipboard with an HTML link (for Teams, Outlook, Confluence) and a markdown fallback for text editors.

## Features

- **Rich links** — paste clickable titled links in Teams, Outlook, Confluence, and other rich-text apps
- **Markdown fallback** — plain-text paste gives you `[Title](url)` for editors and terminals
- **Browser-aware** — reads the page title directly from your browser via AppleScript (no network request needed)
- **Privacy-friendly** — no data leaves your machine unless you opt in to network lookups
- **Lightweight** — sits in the menubar, 0% CPU when idle
- **Easy updates** — "Check for updates" opens the releases page in your browser

## Supported Browsers

| Browser | Supported |
|---------|-----------|
| Microsoft Edge | ✅ |
| Google Chrome | ✅ |
| Safari | ✅ |
| Brave | ✅ |
| Arc | ✅ |
| Vivaldi | ✅ |
| Opera | ✅ |
| Firefox | ❌ (no AppleScript support) |

## Installation

### From DMG (recommended)

1. Download `LinkPretty.dmg` from [Releases](https://github.com/edda-xm/linkpretty/releases)
2. Open the DMG and drag `LinkPretty.app` to Applications
3. Launch from Applications
4. (Optional) Add to **System Settings → General → Login Items** for auto-start

### From source

Requires access to this repository and authenticated git credentials
(`gh auth login`, a personal access token, or an SSH key).

```bash
git clone git@github.com:edda-xm/linkpretty.git   # or the HTTPS URL
cd linkpretty
python3 -m venv .venv
source .venv/bin/activate
pip install rumps pyobjc-framework-Cocoa
python3 linkpretty.py
```

Requires Python 3.10+.

## Usage

1. Copy a URL from your browser's address bar
2. LinkPretty detects it, asks the browser for the page title, and replaces the clipboard
3. Paste — you get a clickable link in rich-text apps, or `[Title](url)` in plain-text apps
4. The menubar icon briefly shows ✓ as confirmation

## Settings

Access settings from the menubar icon:

| Setting | Description | Default |
|---------|-------------|---------|
| Enabled | Toggle link conversion on/off | On |
| Look up titles over the network | Fetch titles via HTTP for URLs not open in a browser tab | Off |

Settings are stored in `~/Library/Application Support/LinkPretty/settings.json`.

## Building the .app bundle

```bash
source .venv/bin/activate
pip install pyinstaller
pyinstaller --name "LinkPretty" --windowed --onedir \
  --osx-bundle-identifier "com.eaxmedd.linkpretty" \
  --hidden-import rumps --hidden-import AppKit \
  --icon icon.icns \
  linkpretty.py
```

Then set `LSUIElement` to hide from Dock:

```bash
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" dist/LinkPretty.app/Contents/Info.plist
codesign --force --deep --sign - dist/LinkPretty.app
```

## Creating a DMG

```bash
brew install create-dmg
create-dmg \
  --volname "LinkPretty" --volicon "icon.icns" \
  --window-pos 200 120 --window-size 600 400 --icon-size 100 \
  --icon "LinkPretty.app" 150 185 --app-drop-link 450 185 \
  --no-internet-enable \
  "LinkPretty.dmg" "dist/LinkPretty.app"
```

## Privacy & Security

- **No telemetry** — the app does not phone home, track usage, or collect any data
- **Network requests** — only made when "Look up titles over the network" is enabled (off by default), and only to the URL you copied
- **Update checks** — "Check for updates" simply opens the GitHub releases page in your browser. The app makes no API calls and sends no data. The repository is private, so viewing releases requires access
- **Clipboard access** — reads clipboard only to detect URLs; writes back only when a title is successfully resolved

## License

No license specified. All rights reserved until further notice.
