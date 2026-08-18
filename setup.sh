#!/bin/bash
# Setup script for LinkPretty — creates a virtual environment and installs deps.

set -e

cd "$(dirname "$0")"

echo "📦 Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

echo "📥 Installing dependencies..."
pip install -q rumps pyobjc-framework-Cocoa

echo "✅ Done! Run with:"
echo "   source .venv/bin/activate && python3 linkpretty.py"
echo ""
echo "To build a standalone .app bundle, see the README section"
echo "'Building the .app bundle' (uses PyInstaller)."
