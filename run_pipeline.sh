#!/bin/bash

# Navigate to the directory where this script is located
cd "$(dirname "$0")" || exit

# Create logs directory if it doesn't exist
mkdir -p logs

# Define log file path with current date
LOG_FILE="logs/pipeline_$(date +\%Y\%m\%d).log"

echo "==================================================" >> "$LOG_FILE"
echo "Starting Automation Pipeline at $(date)" >> "$LOG_FILE"
echo "==================================================" >> "$LOG_FILE"

# Activate the Python virtual environment
source .venv/bin/activate

# Step 1: Generate the Reel
echo "[1/2] Running generate_reel.py..." >> "$LOG_FILE"
python3 generate_reel.py >> "$LOG_FILE" 2>&1
GEN_STATUS=$?

if [ $GEN_STATUS -eq 0 ]; then
    echo "[SUCCESS] Reel generated successfully." >> "$LOG_FILE"
    
    # Step 2: Publish to Instagram
    echo "[2/2] Running publish_to_instagram.py..." >> "$LOG_FILE"
    python3 publish_to_instagram.py >> "$LOG_FILE" 2>&1
    PUB_STATUS=$?
    
    if [ $PUB_STATUS -eq 0 ]; then
        echo "[SUCCESS] Published to Instagram successfully." >> "$LOG_FILE"
    else
        echo "[ERROR] Failed to publish to Instagram." >> "$LOG_FILE"
    fi
else
    echo "[ERROR] Reel generation failed. Skipping publish step." >> "$LOG_FILE"
fi

echo "Pipeline finished at $(date)" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
