# Construit l'application autonome Ruche avec PyInstaller.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1
#
# Le résultat se trouve dans dist\Ruche\Ruche.exe

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Environnement introuvable : $python"
}

Write-Host "Installation de PyInstaller…"
& $python -m pip install --upgrade pyinstaller

Write-Host "Construction…"
Push-Location $root
try {
    & $python -m PyInstaller ruche.spec --noconfirm --clean
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Terminé : dist\Ruche\Ruche.exe"
