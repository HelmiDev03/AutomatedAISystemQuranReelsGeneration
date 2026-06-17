#!/bin/bash

# Navigate to the directory where this script is located
cd "$(dirname "$0")" || exit

# Create logs directory if it doesn't exist
mkdir -p logs

# Define log file path with current date
LOG_FILE="logs/pipeline_$(date +%Y%m%d).log"

echo "==================================================" >> "$LOG_FILE"
echo "Starting Automation Pipeline at $(date)" >> "$LOG_FILE"
echo "==================================================" >> "$LOG_FILE"

# Determine python command (python3 or python)
PYTHON_CMD="python"
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
fi

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    if [ -f ".venv/bin/activate" ]; then
        source .venv/bin/activate
    elif [ -f ".venv/Scripts/activate" ]; then
        source .venv/Scripts/activate
    fi
fi

# Stage 1: Reel (Generate then Publish)
echo "=== STAGE 1: REEL ===" >> "$LOG_FILE"
echo "Running generate_reel.py..." >> "$LOG_FILE"
$PYTHON_CMD generate_reel.py >> "$LOG_FILE" 2>&1
GEN_REEL_STATUS=$?

if [ $GEN_REEL_STATUS -eq 0 ]; then
    echo "[SUCCESS] Reel generated successfully." >> "$LOG_FILE"
    echo "Running publish_to_instagram.py..." >> "$LOG_FILE"
    $PYTHON_CMD publish_to_instagram.py >> "$LOG_FILE" 2>&1
    PUB_REEL_STATUS=$?
    if [ $PUB_REEL_STATUS -eq 0 ]; then
        echo "[SUCCESS] Reel published to Instagram successfully." >> "$LOG_FILE"
    else
        echo "[ERROR] Failed to publish Reel to Instagram." >> "$LOG_FILE"
    fi
else
    echo "[ERROR] Reel generation failed. Skipping Reel publish step." >> "$LOG_FILE"
fi

# Stage 2: Hadith (Generate then Publish)
echo "=== STAGE 2: HADITH ===" >> "$LOG_FILE"
echo "Running generate_hadith.py..." >> "$LOG_FILE"
$PYTHON_CMD generate_hadith.py >> "$LOG_FILE" 2>&1
GEN_HADITH_STATUS=$?

if [ $GEN_HADITH_STATUS -eq 0 ]; then
    echo "[SUCCESS] Hadith generated successfully." >> "$LOG_FILE"
    echo "Running publish_hadith.py..." >> "$LOG_FILE"
    $PYTHON_CMD publish_hadith.py >> "$LOG_FILE" 2>&1
    PUB_HADITH_STATUS=$?
    if [ $PUB_HADITH_STATUS -eq 0 ]; then
        echo "[SUCCESS] Hadith published to Instagram successfully." >> "$LOG_FILE"
    else
        echo "[ERROR] Failed to publish Hadith to Instagram." >> "$LOG_FILE"
    fi
else
    echo "[ERROR] Hadith generation failed. Skipping Hadith publish step." >> "$LOG_FILE"
fi

# Stage 3: Dua (Generate then Publish)
echo "=== STAGE 3: DUA ===" >> "$LOG_FILE"
echo "Running generate_dua.py..." >> "$LOG_FILE"
$PYTHON_CMD generate_dua.py >> "$LOG_FILE" 2>&1
GEN_DUA_STATUS=$?

if [ $GEN_DUA_STATUS -eq 0 ]; then
    echo "[SUCCESS] Dua generated successfully." >> "$LOG_FILE"
    echo "Running publish_dua.py..." >> "$LOG_FILE"
    $PYTHON_CMD publish_dua.py >> "$LOG_FILE" 2>&1
    PUB_DUA_STATUS=$?
    if [ $PUB_DUA_STATUS -eq 0 ]; then
        echo "[SUCCESS] Dua published to Instagram successfully." >> "$LOG_FILE"
    else
        echo "[ERROR] Failed to publish Dua to Instagram." >> "$LOG_FILE"
    fi
else
    echo "[ERROR] Dua generation failed. Skipping Dua publish step." >> "$LOG_FILE"
fi

echo "==================================================" >> "$LOG_FILE"
echo "Pipeline finished at $(date)" >> "$LOG_FILE"
echo "==================================================" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
