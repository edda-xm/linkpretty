#!/usr/bin/env python3
"""LinkPretty — macOS menubar app that converts copied URLs to rich links.

When a plain URL is copied, the page title is looked up (preferably from the
browser that is already showing the page) and the clipboard is replaced with a
rich link: HTML for apps like Teams/Outlook/Confluence, plus a markdown
fallback for plain-text targets.
"""

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from html import escape as html_escape
from html.parser import HTMLParser

import AppKit
import rumps

APP_NAME = "LinkPretty"
APP_VERSION = "1.1.0"
# GitHub owner/repo — used for update checks (public repo).
GITHUB_REPO = "edda-xm/linkpretty"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
POLL_INTERVAL = 0.5
FEEDBACK_SECONDS = 1.0

ICON_ACTIVE = "🔗"
ICON_DISABLED = "⛓️"
ICON_SUCCESS = "✓"

URL_PATTERN = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)

# Bundle identifier -> (display name, AppleScript to read the active tab).
# Chromium-based browsers share the same scripting dictionary; Safari differs.
_CHROMIUM_SCRIPT = '''
    tell application id "{bundle_id}"
        set tabURL to URL of active tab of front window
        set tabTitle to title of active tab of front window
        return tabURL & "|||" & tabTitle
    end tell
'''

_SAFARI_SCRIPT = '''
    tell application id "com.apple.Safari"
        set tabURL to URL of current tab of front window
        set tabTitle to name of current tab of front window
        return tabURL & "|||" & tabTitle
    end tell
'''

BROWSERS = {
    "com.microsoft.edgemac": ("Microsoft Edge", _CHROMIUM_SCRIPT),
    "com.microsoft.edgemac.Beta": ("Microsoft Edge Beta", _CHROMIUM_SCRIPT),
    "com.google.Chrome": ("Google Chrome", _CHROMIUM_SCRIPT),
    "com.google.Chrome.beta": ("Google Chrome Beta", _CHROMIUM_SCRIPT),
    "com.google.Chrome.canary": ("Google Chrome Canary", _CHROMIUM_SCRIPT),
    "com.brave.Browser": ("Brave Browser", _CHROMIUM_SCRIPT),
    "com.vivaldi.Vivaldi": ("Vivaldi", _CHROMIUM_SCRIPT),
    "com.operasoftware.Opera": ("Opera", _CHROMIUM_SCRIPT),
    "company.thebrowser.Browser": ("Arc", _CHROMIUM_SCRIPT),
    "com.apple.Safari": ("Safari", _SAFARI_SCRIPT),
}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

class Settings:
    """Small JSON-backed settings store in Application Support."""

    DEFAULTS = {
        # Off by default: the HTTP lookup sends a request to every copied URL
        # that is not open in a browser, which leaks the URL to its server and
        # can consume single-use tokens (magic links, unsubscribe links).
        "http_fallback": False,
    }

    def __init__(self):
        base = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
        os.makedirs(base, exist_ok=True)
        self._path = os.path.join(base, "settings.json")
        self._values = dict(self.DEFAULTS)
        self._load()

    def _load(self):
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                for key in self.DEFAULTS:
                    if key in stored:
                        self._values[key] = stored[key]
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self._values, fh, indent=2)
        except OSError:
            pass

    def get(self, key):
        return self._values.get(key, self.DEFAULTS.get(key))

    def set(self, key, value):
        self._values[key] = value
        self._save()


# --------------------------------------------------------------------------- #
# Title lookup
# --------------------------------------------------------------------------- #

class TitleParser(HTMLParser):
    """Minimal HTML parser that extracts the <title> tag content."""

    def __init__(self):
        super().__init__()
        self._in_title = False
        self.title = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title" and self.title is None:
            self._in_title = True
            self.title = ""

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _normalize(url: str) -> str:
    return url.rstrip("/")


# Titles longer than this get shortened — some sites (GitHub, for instance) put
# an entire repository description in the <title> tag.
MAX_TITLE_LENGTH = 90
MIN_SEGMENT_LENGTH = 15
# Separators sites use between the page name and a trailing description.
TITLE_SEPARATORS = (": ", " – ", " — ", " | ", " · ", " - ")


def clean_title(raw: str) -> str | None:
    """Collapse whitespace and shorten overly long page titles."""
    title = " ".join(raw.split())
    if not title:
        return None
    if len(title) <= MAX_TITLE_LENGTH:
        return title

    # Prefer cutting at a separator, so the result stays a meaningful phrase.
    best = None
    for separator in TITLE_SEPARATORS:
        head = title.split(separator, 1)[0].strip()
        if MIN_SEGMENT_LENGTH <= len(head) <= MAX_TITLE_LENGTH:
            if best is None or len(head) < len(best):
                best = head
    if best:
        return best

    return title[: MAX_TITLE_LENGTH - 1].rstrip() + "…"


def _run_applescript(script: str, timeout: float = 3.0) -> str | None:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _ask_browser(bundle_id: str, url: str) -> str | None:
    """Ask one browser for the title of its active tab, if the URL matches."""
    _, script_template = BROWSERS[bundle_id]
    output = _run_applescript(script_template.format(bundle_id=bundle_id))
    if not output or "|||" not in output:
        return None
    tab_url, tab_title = output.strip().split("|||", 1)
    if _normalize(tab_url) == _normalize(url):
        return clean_title(tab_title)
    return None


def _running_browser_bundle_ids() -> list[str]:
    """Running browsers, frontmost first. Uses NSWorkspace (no subprocesses)."""
    workspace = AppKit.NSWorkspace.sharedWorkspace()
    ordered = []

    frontmost = workspace.frontmostApplication()
    if frontmost is not None:
        bundle_id = frontmost.bundleIdentifier()
        if bundle_id in BROWSERS:
            ordered.append(bundle_id)

    for app in workspace.runningApplications():
        bundle_id = app.bundleIdentifier()
        if bundle_id in BROWSERS and bundle_id not in ordered:
            ordered.append(bundle_id)

    return ordered


def fetch_title_browser(url: str) -> str | None:
    """Get the title from a running browser showing this URL in its active tab."""
    for bundle_id in _running_browser_bundle_ids():
        title = _ask_browser(bundle_id, url)
        if title:
            return title
    return None


def fetch_title_http(url: str, timeout: float = 5.0) -> str | None:
    """Fetch the page title over HTTP. Public pages only; opt-in."""
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"{APP_NAME}/1.0 (+macOS menubar link formatter)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ("text/html", "application/xhtml+xml"):
                return None
            raw = response.read(65536)
            charset = response.headers.get_content_charset() or "utf-8"
        markup = raw.decode(charset, errors="replace")

        parser = TitleParser()
        parser.feed(markup)
        if parser.title:
            return clean_title(parser.title)
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# Update checker
# --------------------------------------------------------------------------- #

def check_for_update() -> tuple[str, str] | None:
    """Check GitHub for a newer release. Returns (version, html_url) or None.

    Returns None both when up-to-date and on failure (network error, timeout,
    etc.). Raises a ValueError with the releases URL on HTTP 403 (rate limit)
    so the caller can offer to open the page instead.
    """
    try:
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        tag = data.get("tag_name", "").lstrip("v")
        html_url = data.get("html_url", "")
        if tag and _version_newer(tag, APP_VERSION):
            return (tag, html_url)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise ValueError(GITHUB_RELEASES_URL)
    except Exception:
        pass
    return None


def _version_newer(remote: str, local: str) -> bool:
    """Simple semver comparison: is remote > local?"""
    try:
        return [int(x) for x in remote.split(".")] > [int(x) for x in local.split(".")]
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #

def on_main_thread(func):
    """Run func on the main thread — AppKit and rumps are not thread safe."""
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(func)


class LinkPrettyApp(rumps.App):
    def __init__(self):
        super().__init__(APP_NAME, icon=None, title=ICON_ACTIVE, quit_button="Quit")

        self.settings = Settings()
        self.enabled = True
        self._lookup_running = False
        self._feedback_until = 0.0

        self._pasteboard = AppKit.NSPasteboard.generalPasteboard()
        self._seen_change_count = self._pasteboard.changeCount()

        enabled_item = rumps.MenuItem("Enabled", callback=self.toggle_enabled)
        enabled_item.state = True

        fallback_item = rumps.MenuItem(
            "Look up titles over the network",
            callback=self.toggle_http_fallback,
        )
        fallback_item.state = bool(self.settings.get("http_fallback"))

        version_item = rumps.MenuItem(f"Version {APP_VERSION}")
        self._check_update_item = rumps.MenuItem(
            "Check for updates…", callback=self._check_for_update_clicked
        )

        self.menu = [
            enabled_item, None,
            fallback_item, None,
            self._check_update_item, version_item,
        ]

        # Polling runs on the main thread; it is only an integer comparison.
        self._timer = rumps.Timer(self._tick, POLL_INTERVAL)
        self._timer.start()

    # -- updates ------------------------------------------------------------ #

    def _check_for_update_clicked(self, sender):
        """User-initiated update check against GitHub Releases API."""
        sender.title = "Checking…"
        threading.Thread(target=self._run_update_check, daemon=True).start()

    def _run_update_check(self):
        rate_limited_url = None
        try:
            result = check_for_update()
        except ValueError as e:
            # 403 rate limit — fall back to opening the releases page
            result = None
            rate_limited_url = str(e)

        def finish():
            self._check_update_item.title = "Check for updates…"
            self._check_update_item.set_callback(self._check_for_update_clicked)

            if rate_limited_url:
                rumps.notification(
                    APP_NAME, "Could not check automatically",
                    "Opening the releases page instead (API rate limit).",
                )
                self._open_url(rate_limited_url)
                return

            if result is None:
                rumps.notification(
                    APP_NAME, "No update found",
                    f"You are running the latest version ({APP_VERSION}).",
                )
                return

            version, url = result
            self._check_update_item.title = f"⬆️ Download v{version}"
            self._check_update_item.set_callback(lambda _: self._open_url(url))
            rumps.notification(
                APP_NAME, f"Version {version} available",
                "Click the menu item to download.",
            )

        on_main_thread(finish)

    def _open_url(self, url: str):
        AppKit.NSWorkspace.sharedWorkspace().openURL_(
            AppKit.NSURL.URLWithString_(url)
        )

    # -- menu actions ------------------------------------------------------- #

    def toggle_enabled(self, sender):
        self.enabled = not self.enabled
        sender.state = self.enabled
        self.title = ICON_ACTIVE if self.enabled else ICON_DISABLED
        # Ignore anything copied while disabled.
        self._seen_change_count = self._pasteboard.changeCount()

    def toggle_http_fallback(self, sender):
        new_value = not bool(self.settings.get("http_fallback"))
        self.settings.set("http_fallback", new_value)
        sender.state = new_value

    # -- clipboard ---------------------------------------------------------- #

    def _read_clipboard_string(self) -> str:
        value = self._pasteboard.stringForType_(AppKit.NSPasteboardTypeString)
        return value or ""

    def _write_link(self, title: str, url: str):
        """Replace the clipboard with a rich link plus markdown fallback."""
        safe_title = html_escape(title, quote=True)
        safe_url = html_escape(url, quote=True)
        markup = f'<a href="{safe_url}">{safe_title}</a>'
        markup_bytes = markup.encode("utf-8")
        data = AppKit.NSData.dataWithBytes_length_(markup_bytes, len(markup_bytes))

        # clearContents() is required before writing; declare both flavors up
        # front so the HTML and plain-text representations stay in sync.
        self._pasteboard.clearContents()
        self._pasteboard.declareTypes_owner_(
            [AppKit.NSPasteboardTypeHTML, AppKit.NSPasteboardTypeString], None
        )
        self._pasteboard.setData_forType_(data, AppKit.NSPasteboardTypeHTML)
        self._pasteboard.setString_forType_(
            f"[{title}]({url})", AppKit.NSPasteboardTypeString
        )

        # Our own write bumps the change count — do not reprocess it.
        self._seen_change_count = self._pasteboard.changeCount()

    # -- main loop ---------------------------------------------------------- #

    def _tick(self, _timer):
        if self._feedback_until and time.monotonic() >= self._feedback_until:
            self._feedback_until = 0.0
            self.title = ICON_ACTIVE if self.enabled else ICON_DISABLED

        if not self.enabled or self._lookup_running:
            return

        change_count = self._pasteboard.changeCount()
        if change_count == self._seen_change_count:
            return
        self._seen_change_count = change_count

        content = self._read_clipboard_string().strip()
        if not content or not URL_PATTERN.match(content):
            return

        self._lookup_running = True
        self._lookup_change_count = change_count
        threading.Thread(
            target=self._lookup_and_apply, args=(content, change_count), daemon=True
        ).start()

    def _lookup_and_apply(self, url: str, original_change_count: int):
        """Runs off the main thread: AppleScript and HTTP can both block."""
        try:
            title = fetch_title_browser(url)
            if not title and self.settings.get("http_fallback"):
                title = fetch_title_http(url)
        except Exception:
            title = None

        def finish():
            self._lookup_running = False
            if not title:
                return
            # Don't overwrite clipboard if it changed while we were looking up,
            # or if the user disabled the app in the meantime.
            if not self.enabled:
                return
            if self._pasteboard.changeCount() != original_change_count:
                return
            self._write_link(title, url)
            self.title = ICON_SUCCESS
            self._feedback_until = time.monotonic() + FEEDBACK_SECONDS

        on_main_thread(finish)


if __name__ == "__main__":
    app = LinkPrettyApp()

    # Hide from Dock AFTER the status item is created — macOS 26 requires
    # flavor=3 (Foreground) registration for the icon to appear, so we start
    # as a normal app and switch to Accessory policy before entering the
    # run loop.
    nsapp = AppKit.NSApplication.sharedApplication()
    nsapp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    app.run()
