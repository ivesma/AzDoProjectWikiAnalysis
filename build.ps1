<#
.SYNOPSIS
    Build Azure DevOps Wiki Indexer: tests -> PyInstaller bundle -> Inno Setup installer.

.EXAMPLE
    .\build.ps1                    # full build
    .\build.ps1 -SkipTests         # skip pytest
    .\build.ps1 -SkipInstaller     # only build dist\AzureDevOpsWikiIndexer\
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [string]$Python = "py -3.12"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Step([string]$Name, [scriptblock]$Action) {
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Invoke-Step "Create virtual environment" { Invoke-Expression "$Python -m venv .venv" }
}
Invoke-Step "Install dependencies" { & $venvPython -m pip install --quiet --upgrade pip; & $venvPython -m pip install --quiet -r requirements-dev.txt }

$version = (& $venvPython -c "import sys; sys.path.insert(0, 'src'); import wiki_indexer; print(wiki_indexer.__version__)").Trim()
Write-Host "Version $version"

if (-not $SkipTests) {
    $env:QT_QPA_PLATFORM = "offscreen"
    Invoke-Step "Run tests (coverage >= 80%)" { & $venvPython -m pytest --cov --cov-report=term-missing:skip-covered -q }
    Remove-Item Env:QT_QPA_PLATFORM
}

if (-not (Test-Path "assets\app.ico")) {
    Invoke-Step "Generate icon" { & $venvPython installer\make_icon.py }
}

Invoke-Step "Build executable (PyInstaller)" {
    & $venvPython -m PyInstaller installer\AzureDevOpsWikiIndexer.spec --noconfirm --clean --distpath dist --workpath build
}
Write-Host "Executable: $(Resolve-Path dist\AzureDevOpsWikiIndexer\AzureDevOpsWikiIndexer.exe)" -ForegroundColor Green

if ($SkipInstaller) { return }

$iscc = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source,
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $iscc) {
    Write-Warning "Inno Setup 6 not found. Install it (e.g. 'winget install JRSoftware.InnoSetup') and re-run, or use -SkipInstaller."
    exit 1
}
Invoke-Step "Build installer (Inno Setup)" { & $iscc "/DMyAppVersion=$version" installer\AzureDevOpsWikiIndexer.iss }
Write-Host "Installer: $(Resolve-Path dist\installer\AzureDevOpsWikiIndexerSetup.exe)" -ForegroundColor Green
