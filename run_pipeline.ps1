# Get local directory
$PSScriptRoot = Split-Path -Parent -Path $MyInvocation.MyCommand.Definition
Set-Location $PSScriptRoot

# Create logs directory
if (!(Test-Path "logs")) {
    New-Item -ItemType Directory -Force -Path "logs" | Out-Null
}

$Today = Get-Date -Format "yyyyMMdd"
$LogFile = "logs/pipeline_$Today.log"

$Header = @"
==================================================
Starting Automation Pipeline at $(Get-Date)
==================================================
"@
$Header | Out-File -FilePath $LogFile -Append -Encoding utf8

# Setup Python paths
$PythonPath = "python"
if (Test-Path ".venv/Scripts/python.exe") {
    $PythonPath = ".venv/Scripts/python.exe"
}

# Helper function to log and execute
function Execute-Step($ScriptName, $LogPrefix) {
    "[$LogPrefix] Running $ScriptName..." | Out-File -FilePath $LogFile -Append -Encoding utf8
    Write-Host "[$LogPrefix] Running $ScriptName..."
    
    # Run the script and capture status
    Start-Process -FilePath $PythonPath -ArgumentList $ScriptName -Wait -NoNewWindow -RedirectStandardOutput "logs/temp_stdout.log" -RedirectStandardError "logs/temp_stderr.log"
    $ExitCode = $LASTEXITCODE
    
    if (Test-Path "logs/temp_stdout.log") {
        Get-Content "logs/temp_stdout.log" | Out-File -FilePath $LogFile -Append -Encoding utf8
        Remove-Item "logs/temp_stdout.log"
    }
    if (Test-Path "logs/temp_stderr.log") {
        Get-Content "logs/temp_stderr.log" | Out-File -FilePath $LogFile -Append -Encoding utf8
        Remove-Item "logs/temp_stderr.log"
    }
    
    return $ExitCode
}

# STAGE 1: Reel (Generate then Publish)
"=== STAGE 1: REEL ===" | Out-File -FilePath $LogFile -Append -Encoding utf8
Write-Host "=== STAGE 1: REEL ==="
$GenReelStatus = Execute-Step "generate_reel.py" "REEL-GEN"

if ($GenReelStatus -eq 0) {
    "[SUCCESS] Reel generated successfully." | Out-File -FilePath $LogFile -Append -Encoding utf8
    $PubReelStatus = Execute-Step "publish_to_instagram.py" "REEL-PUB"
    if ($PubReelStatus -eq 0) {
        "[SUCCESS] Reel published to Instagram successfully." | Out-File -FilePath $LogFile -Append -Encoding utf8
    } else {
        "[ERROR] Failed to publish Reel to Instagram." | Out-File -FilePath $LogFile -Append -Encoding utf8
    }
} else {
    "[ERROR] Reel generation failed. Skipping Reel publish step." | Out-File -FilePath $LogFile -Append -Encoding utf8
}

# STAGE 2: Hadith (Generate then Publish)
"=== STAGE 2: HADITH ===" | Out-File -FilePath $LogFile -Append -Encoding utf8
Write-Host "=== STAGE 2: HADITH ==="
$GenHadithStatus = Execute-Step "generate_hadith.py" "HADITH-GEN"

if ($GenHadithStatus -eq 0) {
    "[SUCCESS] Hadith generated successfully." | Out-File -FilePath $LogFile -Append -Encoding utf8
    $PubHadithStatus = Execute-Step "publish_hadith.py" "HADITH-PUB"
    if ($PubHadithStatus -eq 0) {
        "[SUCCESS] Hadith published to Instagram successfully." | Out-File -FilePath $LogFile -Append -Encoding utf8
    } else {
        "[ERROR] Failed to publish Hadith to Instagram." | Out-File -FilePath $LogFile -Append -Encoding utf8
    }
} else {
    "[ERROR] Hadith generation failed. Skipping Hadith publish step." | Out-File -FilePath $LogFile -Append -Encoding utf8
}

# STAGE 3: Dua (Generate then Publish)
"=== STAGE 3: DUA ===" | Out-File -FilePath $LogFile -Append -Encoding utf8
Write-Host "=== STAGE 3: DUA ==="
$GenDuaStatus = Execute-Step "generate_dua.py" "DUA-GEN"

if ($GenDuaStatus -eq 0) {
    "[SUCCESS] Dua generated successfully." | Out-File -FilePath $LogFile -Append -Encoding utf8
    $PubDuaStatus = Execute-Step "publish_dua.py" "DUA-PUB"
    if ($PubDuaStatus -eq 0) {
        "[SUCCESS] Dua published to Instagram successfully." | Out-File -FilePath $LogFile -Append -Encoding utf8
    } else {
        "[ERROR] Failed to publish Dua to Instagram." | Out-File -FilePath $LogFile -Append -Encoding utf8
    }
} else {
    "[ERROR] Dua generation failed. Skipping Dua publish step." | Out-File -FilePath $LogFile -Append -Encoding utf8
}

$Footer = @"
==================================================
Pipeline finished at $(Get-Date)
==================================================

"@
$Footer | Out-File -FilePath $LogFile -Append -Encoding utf8
Write-Host "Pipeline execution complete. Logs stored in $LogFile"
