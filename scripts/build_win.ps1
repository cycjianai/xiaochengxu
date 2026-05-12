# Requires PowerShell 5+.
# Run from the project root or anywhere — the script resolves paths relative
# to its own location.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RootDir = Resolve-Path (Join-Path $ScriptDir "..")
$VenvDir = Join-Path $RootDir ".venv-build"

if (-not $IsWindows) {
    Write-Error "This build script must run on Windows."
    exit 1
}

Write-Host "Using Python:"
python --version

if (-not (Test-Path $VenvDir)) {
    python -m venv $VenvDir
}

$Activate = Join-Path $VenvDir "Scripts\Activate.ps1"
. $Activate

python -m pip install --upgrade pip
python -m pip install -r (Join-Path $RootDir "requirements.txt")
if (Test-Path (Join-Path $RootDir "requirements-build.txt")) {
    python -m pip install -r (Join-Path $RootDir "requirements-build.txt")
}

Set-Location $RootDir
if (-not $env:WX_SNIFFER_PRODUCT_NAME) {
    $env:WX_SNIFFER_PRODUCT_NAME = "MTCenter"
}

pyinstaller --clean --noconfirm (Join-Path $RootDir "wx-sniffer.spec")

Write-Host ""
Write-Host "Build complete."
Write-Host ("Output: " + (Join-Path $RootDir ("dist\" + $env:WX_SNIFFER_PRODUCT_NAME)))
