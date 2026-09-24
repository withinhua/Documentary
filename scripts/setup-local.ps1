# One-time setup to MAKE videos on Windows (the Studio itself only needs scripts\studio.ps1).
# Needs Python 3.10+ and ffmpeg:  winget install Python.Python.3.12 Gyan.FFmpeg
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
foreach ($t in "python", "ffmpeg") {
  if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { throw "Missing $t - run: winget install Python.Python.3.12 Gyan.FFmpeg, then open a new terminal" }
}
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -q --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -q -r requirements-local.txt
New-Item -ItemType Directory -Force models\kokoro | Out-Null
foreach ($f in "kokoro-v1.0.onnx", "voices-v1.0.bin") {
  if (-not (Test-Path "models\kokoro\$f")) {
    Write-Host "Downloading voice model $f ..."
    Invoke-WebRequest "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f" -OutFile "models\kokoro\$f"
  }
}
& .\.venv\Scripts\python.exe house\make_house.py | Out-Null
# Show every production in the Studio right away (script only until it's rendered)
Get-ChildItem projects -Directory | Where-Object { Test-Path (Join-Path $_.FullName "script.json") } | ForEach-Object {
  & .\.venv\Scripts\python.exe -m pipeline.feed publish $_.FullName --feed editor\apps\web\public\studio-feed | Out-Null
}
Write-Host "Done. Keep scripts\studio.ps1 running, then run scripts\new-coke.ps1" -ForegroundColor Green
