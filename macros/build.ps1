#requires -Version 5
# Builds the seven native Windows compiler plugins and installs them into bin/.
#
# Two environment facts this encodes, each learned by failing without it:
#   1. Plugins are host Windows builds, so they need the MSVC + Windows SDK C headers.
#      We activate vcvarsall x64 before building.
#   2. The default Swift Build system does not pass INCLUDE to its clang subprocess and
#      fails compiling swift-syntax C shims. The classic native build system honors it.
#
# The build tree is the package's own .build. bin/ is the only thing iosc reads, and
# bin/plugins.json pins a sha256 per module so a stale exe cannot pass for a fresh one.
[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$pkgDir = $PSScriptRoot
$buildDir = Join-Path $pkgDir ".build"
$binDir = Join-Path $pkgDir "bin"
$triple = "x86_64-unknown-windows-msvc"

$modules = @(
    "SwiftUIMacros",
    "SwiftDataMacros",
    "PreviewsMacros",
    "FoundationModelsMacros",
    "StateReportingMacros",
    "TipKitMacros",
    "AppIntentsMacros"
)

function Find-VcVarsAll {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $root = & $vswhere -latest -products * `
            -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
            -property installationPath 2>$null | Select-Object -First 1
        if ($root) {
            $cand = Join-Path $root "VC\Auxiliary\Build\vcvarsall.bat"
            if (Test-Path $cand) { return $cand }
        }
    }
    $fallbacks = Get-ChildItem "${env:ProgramFiles(x86)}\Microsoft Visual Studio" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName "BuildTools\VC\Auxiliary\Build\vcvarsall.bat" }
    foreach ($f in $fallbacks) { if (Test-Path $f) { return $f } }
    throw "vcvarsall.bat not found. Install Visual Studio Build Tools with the C++ workload."
}

function Find-SwiftBin {
    $base = Join-Path $env:LOCALAPPDATA "Programs\Swift\Toolchains"
    $tc = Get-ChildItem $base -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Where-Object { Test-Path (Join-Path $_.FullName "usr\bin\swiftc.exe") } |
        Select-Object -First 1
    if (-not $tc) { throw "No Swift toolchain with swiftc.exe under $base" }
    return (Join-Path $tc.FullName "usr\bin")
}

$vc = Find-VcVarsAll
$swiftBin = Find-SwiftBin
$swiftVersion = (& (Join-Path $swiftBin "swiftc.exe") --version 2>&1 | Select-Object -First 1).ToString().Trim()

Write-Host "vcvars    : $vc"
Write-Host "swift bin : $swiftBin"
Write-Host "swift     : $swiftVersion"
Write-Host "build     : $buildDir"
Write-Host "install   : $binDir"
Write-Host "modules   : $($modules.Count)"
Write-Host ""

$cmd = "call `"$vc`" x64 >nul && set `"PATH=$swiftBin;%PATH%`" && cd /d `"$pkgDir`" && " +
       "swift build --build-system native"
& $env:ComSpec /c $cmd
if ($LASTEXITCODE -ne 0) { throw "swift build failed ($LASTEXITCODE)" }

$toolDir = Join-Path $buildDir "$triple\debug"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null

$entries = @()
foreach ($m in $modules) {
    $name = "$m-tool.exe"
    $src = Join-Path $toolDir $name
    if (-not (Test-Path $src)) {
        throw "plugin $m did not build. Expected $src"
    }
    Copy-Item $src (Join-Path $binDir $name) -Force
    $hash = (Get-FileHash (Join-Path $binDir $name) -Algorithm SHA256).Hash.ToLower()
    $entries += [ordered]@{ module = $m; file = $name; sha256 = $hash }
}

# Anything left in bin/ that this build did not produce is a stale plugin from an
# earlier layout, and would otherwise be loaded as if it were current
$expected = $modules | ForEach-Object { "$_-tool.exe" }
Get-ChildItem $binDir -Filter *.exe | Where-Object { $expected -notcontains $_.Name } | ForEach-Object {
    Write-Host "removing stale $($_.Name)"
    Remove-Item $_.FullName -Force
}

$manifest = [ordered]@{
    swift_version = $swiftVersion
    triple        = $triple
    built         = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    plugins       = $entries
}
$manifestPath = Join-Path $binDir "plugins.json"
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $manifestPath -Encoding utf8

Write-Host ""
Write-Host "installed $($entries.Count) plugins into bin/"
foreach ($e in $entries) {
    Write-Host ("  {0,-22} {1}" -f $e.module, $e.sha256.Substring(0, 16))
}

if ($Clean) {
    Write-Host ""
    Write-Host "removing build tree"
    Remove-Item $buildDir -Recurse -Force -ErrorAction SilentlyContinue
}
