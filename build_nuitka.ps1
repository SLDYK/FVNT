param(
    [string]$PythonExe = ".venv/Scripts/python.exe",
    [string]$OutputDir = "build/nuitka"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pythonPath = Join-Path $projectRoot $PythonExe
if (-not (Test-Path $pythonPath)) {
    throw "Python executable not found: $pythonPath"
}

$wordNinjaData = (& $pythonPath -c "import os, wordninja; print(os.path.join(os.path.dirname(wordninja.__file__), 'wordninja', 'wordninja_words.txt.gz'))").Trim()
if (-not (Test-Path $wordNinjaData)) {
    throw "wordninja data file not found: $wordNinjaData"
}

$certifiPem = (& $pythonPath -c "import certifi; print(certifi.where())").Trim()
if (-not (Test-Path $certifiPem)) {
    throw "certifi certificate bundle not found: $certifiPem"
}

$resolvedOutputDir = Join-Path $projectRoot $OutputDir
if (Test-Path $resolvedOutputDir) {
    Remove-Item $resolvedOutputDir -Recurse -Force
}

$exePath = Join-Path $resolvedOutputDir "FVNT-Translator.exe"

$nuitkaArgs = @(
    "-m", "nuitka",
    "--onefile",
    "--assume-yes-for-downloads",
    "--remove-output",
    "--windows-console-mode=disable",
    "--enable-plugin=tk-inter",
    "--windows-icon-from-ico=T.ico",
    "--output-dir=$resolvedOutputDir",
    "--output-filename=FVNT-Translator.exe",
    "--include-data-files=$wordNinjaData=wordninja/wordninja_words.txt.gz",
    "--include-data-files=$certifiPem=certifi/cacert.pem",
    "Translation.py"
)

& $pythonPath @nuitkaArgs

if ($LASTEXITCODE -ne 0) {
    throw "Nuitka build failed with exit code $LASTEXITCODE"
}

Copy-Item (Join-Path $projectRoot "config.json") (Join-Path $resolvedOutputDir "config.json") -Force

Write-Host "Build completed:" $exePath