#!/bin/bash
# Setup script for LinkPretty

set -e

cd "$(dirname "$0")"

echo "📦 Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

echo "📥 Installing dependencies..."
pip install -q rumps pyobjc-framework-Cocoa

echo "✅ Done! Run with:"
echo "   source ~/Projects/linkpretty/.venv/bin/activate && python3 ~/Projects/linkpretty/linkpretty.py"
echo ""
echo "To build as a standalone .app, run:"
echo "   pip install py2app && python3 setup_app.py py2app"
