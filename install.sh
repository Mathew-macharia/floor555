#!/usr/bin/env bash
# install.sh — sets up media-fetcher on Ubuntu 22.04/24.04
set -euo pipefail

echo "==> Updating apt"
sudo apt-get update -qq

echo "==> Installing system dependencies"
sudo apt-get install -y ffmpeg python3 python3-pip python3-venv curl

echo "==> Creating Python virtualenv"
python3 -m venv .venv
source .venv/bin/activate

echo "==> Installing Python packages"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Installing yt-dlp"
pip install -U yt-dlp

echo "==> Installing spotdl"
pip install -U spotdl

echo ""
echo "Done. To run:"
echo "  source .venv/bin/activate"
echo "  uvicorn main:app --host 127.0.0.1 --port 8000"
