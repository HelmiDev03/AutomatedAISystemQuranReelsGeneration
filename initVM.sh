#!/bin/bash
# Exit immediately if a command exits with a non-zero status
set -e

echo "=================================================="
echo " Starting VM Initialization for Quran Reels Gen "
echo "=================================================="

echo ">>> Step 1: Installing System Dependencies (apt)..."
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv ffmpeg

echo ">>> Step 2: Cloning Repository..."
if [ -d "AutomatedAISystemQuranReelsGeneration" ]; then
    echo "Directory AutomatedAISystemQuranReelsGeneration already exists, skipping clone."
    cd AutomatedAISystemQuranReelsGeneration
else
    git clone https://github.com/HelmiDev03/AutomatedAISystemQuranReelsGeneration.git
    cd AutomatedAISystemQuranReelsGeneration
fi

echo ">>> Step 3: Creating Python Virtual Environment (.venv)..."
python3 -m venv .venv

echo ">>> Step 4: Installing Python Dependencies (pip)..."
# Activate the virtual environment in this shell context
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=================================================="
echo " ACTION REQUIRED: Manual File Copy"
echo "=================================================="
echo "Please copy your '.env' and 'automate.db' files"
echo "into this directory: $(pwd)"
echo "=================================================="
echo ""

# Loop and check for the files before continuing
while [ ! -f .env ] || [ ! -f automate.db ]; do
    read -p "Press [Enter] after you have copied BOTH '.env' and 'automate.db' files to continue..."
    if [ ! -f .env ]; then
        echo "--> Missing: .env is still not found in $(pwd)"
    fi
    if [ ! -f automate.db ]; then
        echo "--> Missing: automate.db is still not found in $(pwd)"
    fi
done

echo ">>> Files verified! Continuing with database setup..."

echo ">>> Step 5: Seeding Quran Vector Database (ChromaDB)..."
# Downloads and embeds all 6,236 Quran verses into ./chroma_data
python seed_quran.py

echo ">>> Step 6: Pre-Downloading Quran Recitation Audio..."
# Downloads all recitation MP3s into ./audio_cache based on QURAN_RECITER in .env
python download_all_audio.py

echo "=================================================="
echo " Initialization Successful!"
echo " You can now run: python generate_reel.py"
echo "=================================================="
