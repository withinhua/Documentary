# Rebuild the New Coke documentary and watch it live in the Studio (Windows PowerShell).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$env:KOKORO_DIR = (Resolve-Path "models\kokoro").Path
$py = ".\.venv\Scripts\python.exe"
$p = "projects\new-coke"
& $py -m footage.fetch "$p\footage\picks.json" --candidates "$p\footage\candidates.json" --out "$p\footage\media"
& $py -m pipeline.produce $p --media "$p\footage\media" --out out\new-coke --feed editor\apps\web\public\studio-feed
Write-Host "Done: out\new-coke\final.mp4 (also in the Studio)" -ForegroundColor Green
