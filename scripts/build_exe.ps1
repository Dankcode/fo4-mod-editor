[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Create .venv with Python 3.11+ before building."
}

Push-Location $ProjectRoot
try {
    & $Python -m pip install -e ".[packaging]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

    & $Python -m PyInstaller `
        --clean `
        --noconfirm `
        --onefile `
        --name "for4-mod-editor" `
        --paths "src" `
        "scripts\for4_entry.py"
    if ($LASTEXITCODE -ne 0) { throw "Executable build failed." }

    Write-Host "Built $ProjectRoot\dist\for4-mod-editor.exe"
}
finally {
    Pop-Location
}
