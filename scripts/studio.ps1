# Start the Studio + dashboard on http://localhost:5173 (Windows PowerShell).
# First run installs dependencies (~3 min); later runs start in seconds. Ctrl+C to stop.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\editor")
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { npm install -g pnpm }
if (-not (Test-Path "node_modules")) { pnpm install }
if (-not (Test-Path "packages\core\src\wasm\fft\build")) { pnpm build:wasm }
Write-Host "Studio starting - open http://localhost:5173" -ForegroundColor Green
pnpm dev
