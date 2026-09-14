#requires -Version 5
# Expands the macros in a source file through one built plugin and prints the result.
# This is the per macro verifier: write a small use site, run this, and compare the
# expansion against the post expansion API the SDK .swiftinterface records for the macro.
#
# Run build.ps1 first so the plugin tool exists in bin/.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet("SwiftUIMacros", "SwiftDataMacros", "PreviewsMacros", "FoundationModelsMacros", "StateReportingMacros", "TipKitMacros", "AppIntentsMacros")][string]$Framework,
    [Parameter(Mandatory)][string]$File
)

$ErrorActionPreference = "Stop"

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
    throw "vcvarsall.bat not found."
}

function Find-SwiftBin {
    $base = Join-Path $env:LOCALAPPDATA "Programs\Swift\Toolchains"
    $tc = Get-ChildItem $base -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Where-Object { Test-Path (Join-Path $_.FullName "usr\bin\swiftc.exe") } |
        Select-Object -First 1
    if (-not $tc) { throw "No Swift toolchain found." }
    return (Join-Path $tc.FullName "usr\bin")
}

$src = (Resolve-Path $File).Path
$tool = Join-Path $PSScriptRoot "bin\$Framework-tool.exe"
if (-not (Test-Path $tool)) { throw "Plugin not built: $tool. Run build.ps1 first." }

$vc = Find-VcVarsAll
$swiftBin = Find-SwiftBin

$swiftc = "swiftc -typecheck `"$src`" " +
          "-load-plugin-executable `"$tool#$Framework`" " +
          "-Xfrontend -dump-macro-expansions " +
          "-Xfrontend -disable-availability-checking"
$cmd = "call `"$vc`" x64 >nul && set `"PATH=$swiftBin;%PATH%`" && $swiftc"
& $env:ComSpec /c $cmd
