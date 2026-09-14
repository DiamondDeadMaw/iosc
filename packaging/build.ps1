$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venv = Join-Path $root ".venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creating $venv"
    $launcher = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
    & $launcher -3 -m venv $venv
}

Write-Host "Installing build requirements"
& $python -m pip install --quiet --disable-pip-version-check -e . pyinstaller

$gxx = Get-Command g++ -ErrorAction SilentlyContinue
if (-not $gxx) { throw "g++ (MinGW-w64) not found on PATH, required to compile the ADI native bridge" }

$adiVendorDir = Join-Path $root "vendor\adi"
New-Item -ItemType Directory -Force -Path $adiVendorDir | Out-Null
$adiDll = Join-Path $adiVendorDir "adi_native.dll"

Write-Host "Compiling ADI native bridge"
& $gxx.Source -shared -O2 -fno-exceptions -fno-asynchronous-unwind-tables `
    -o $adiDll `
    (Join-Path $root "native\adi\adi_native.cpp") (Join-Path $root "native\adi\elfloader.cpp") `
    -static -static-libgcc -static-libstdc++
if ($LASTEXITCODE -ne 0) { throw "ADI native bridge compile failed" }

Write-Host "Building macro plugins"
& (Join-Path $root "macros\build.ps1")
if ($LASTEXITCODE -ne 0) { throw "macro plugin build failed" }
Set-Location $root

$pluginManifest = Join-Path $root "macros\bin\plugins.json"
if (-not (Test-Path $pluginManifest)) { throw "macro plugins missing at $pluginManifest" }

Write-Host "Building dist\iosc.exe"
& (Join-Path $venv "Scripts\pyinstaller.exe") `
    (Join-Path $root "packaging\iosc.spec") `
    --noconfirm --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build\pyinstaller")

$exe = Join-Path $root "dist\iosc.exe"
if (-not (Test-Path $exe)) { throw "build finished but $exe is missing" }

& $exe dev paths --json | Out-Null
if ($LASTEXITCODE -ne 0) { throw "$exe was built but does not run" }

"{0}  {1:N1} MB" -f $exe, ((Get-Item $exe).Length / 1MB)
