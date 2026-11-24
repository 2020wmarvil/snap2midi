<#
.SYNOPSIS
  Installs snap2midi by downloading its components from their official sources.

.DESCRIPTION
  Every download is pinned to the version this installer was built and tested
  with, and checked against its SHA-256 hash. Downloads are cached in
  %LOCALAPPDATA%\snap2midi\cache, so a rerun after a failure is quick.
  The install is assembled in "<Dir>.partial" and only moved into place when
  every step has succeeded.

.PARAMETER Dir
  Install folder. Default: %LOCALAPPDATA%\Programs\snap2midi

.PARAMETER Gpu
  auto (default): install the NVIDIA CUDA libraries only if an NVIDIA GPU is found.
  yes / no: force either way.

.PARAMETER Force
  Replace an existing install.
#>
param(
    [string]$Dir = (Join-Path $env:LOCALAPPDATA "Programs\snap2midi"),
    [ValidateSet("auto", "yes", "no")][string]$Gpu = "auto",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Here = $PSScriptRoot
$Manifest = Get-Content (Join-Path $Here "manifest.json") -Raw | ConvertFrom-Json
$Cache = Join-Path $env:LOCALAPPDATA "snap2midi\cache"
$Stage = "$Dir.partial"
$script:StepNo = 0

function Step([string]$Message) {
    $script:StepNo++
    Write-Host ""
    Write-Host "[$script:StepNo/7] $Message" -ForegroundColor Cyan
}

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "Install failed: $Message" -ForegroundColor Red
    Write-Host "Fix the problem and run the installer again; finished downloads are reused." -ForegroundColor Red
    exit 1
}

function Get-Verified([string]$Url, [string]$Sha256) {
    New-Item -ItemType Directory -Force $Cache | Out-Null
    $dest = Join-Path $Cache ([IO.Path]::GetFileName(([Uri]$Url).AbsolutePath))
    if ((Test-Path $dest) -and ((Get-FileHash $dest -Algorithm SHA256).Hash -eq $Sha256.ToUpper())) {
        Write-Host "  cached  $([IO.Path]::GetFileName($dest))"
        return $dest
    }
    for ($try = 1; $try -le 3; $try++) {
        try {
            Write-Host "  download $Url"
            Invoke-WebRequest -Uri $Url -OutFile "$dest.part" -UseBasicParsing
            $hash = (Get-FileHash "$dest.part" -Algorithm SHA256).Hash
            if ($hash -ne $Sha256.ToUpper()) { throw "checksum mismatch (got $hash)" }
            Move-Item -Force "$dest.part" $dest
            return $dest
        } catch {
            Write-Host "  attempt $try failed: $($_.Exception.Message)" -ForegroundColor Yellow
            Remove-Item -Force "$dest.part" -ErrorAction SilentlyContinue
            if ($try -eq 3) { Fail "could not download $Url" }
            Start-Sleep -Seconds (5 * $try)
        }
    }
}

function Invoke-Checked([string]$What, [scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { Fail "$What (exit code $LASTEXITCODE)" }
}

Write-Host "snap2midi $($Manifest.snap2midi) installer" -ForegroundColor Green
Write-Host "Install folder: $Dir"

if ((Test-Path $Dir) -and -not $Force) {
    Fail "$Dir already exists. Rerun with -Force to replace it."
}

# ------------------------------------------------------------------ GPU
$useGpu = $false
if ($Gpu -eq "yes") { $useGpu = $true }
elseif ($Gpu -eq "auto") {
    $nvidia = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "NVIDIA" }
    $useGpu = [bool]$nvidia
}
if ($useGpu) { Write-Host "NVIDIA GPU: yes, CUDA libraries will be installed (about 1.4 GB)" }
else { Write-Host "NVIDIA GPU: no, CPU only (rerun with -Gpu yes to override)" }

Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory $Stage | Out-Null
$PyDir = Join-Path $Stage "python"
$Py = Join-Path $PyDir "python.exe"
$Site = Join-Path $PyDir "Lib\site-packages"
$env:PYTHONNOUSERSITE = "1"
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue

# ------------------------------------------------------------------ 1. Python
Step "Python runtime"
$zip = Get-Verified $Manifest.python.url $Manifest.python.sha256
Expand-Archive -Path $zip -DestinationPath $PyDir -Force
$pth = Get-ChildItem $PyDir -Filter "python3*._pth" | Select-Object -First 1
$lines = Get-Content $pth.FullName | Where-Object { $_.Trim() -and $_.Trim() -ne "#import site" }
Set-Content -Path $pth.FullName -Value ($lines + "Lib\site-packages" + "import site") -Encoding ASCII
New-Item -ItemType Directory -Force $Site | Out-Null

# ------------------------------------------------------------------ 2. Packages
Step "Python packages"
$pipWheel = Get-Verified $Manifest.pip.url $Manifest.pip.sha256
$pip = Join-Path $pipWheel "pip"   # pip runs directly from its wheel
$pipArgs = @("install", "--disable-pip-version-check", "--no-warn-script-location", "--no-deps",
             "--require-hashes", "--target", $Site, "-r")
Invoke-Checked "installing core packages" { & $Py $pip @pipArgs (Join-Path $Here "requirements-core.txt") }

# ------------------------------------------------------------------ 3. GPU packages
Step "NVIDIA CUDA libraries"
if ($useGpu) {
    Invoke-Checked "installing CUDA packages" { & $Py $pip @pipArgs (Join-Path $Here "requirements-gpu.txt") }
    # Not loaded by onnxruntime or cuDNN: measured by listing the DLLs mapped into a GPU run.
    Get-ChildItem $Site -Recurse -Include "curand64_*.dll", "nvJitLink_*.dll" | Remove-Item -Force
} else {
    Write-Host "  skipped (no NVIDIA GPU)"
}
$wheel = Join-Path $Here $Manifest.wheel
Invoke-Checked "installing snap2midi" {
    & $Py $pip install --disable-pip-version-check --no-warn-script-location --no-deps --target $Site $wheel
}
Get-ChildItem $Site -Directory -Filter "bin" | Remove-Item -Recurse -Force

# ------------------------------------------------------------------ 4. Models
Step "Recognition models"
$gpuFlag = if ($useGpu) { "True" } else { "False" }
$code = @"
import numpy as np
from homr.main import download_weights
if ${gpuFlag}:
    download_weights(True, True, False)
download_weights(False, False, False)
from rapidocr import RapidOCR
RapidOCR()(np.full((64, 256, 3), 255, np.uint8))
print('models ok')
"@
Invoke-Checked "downloading models" { & $Py -c $code }

# ------------------------------------------------------------------ 5. Audiveris
Step "Audiveris $($Manifest.audiveris.version)"
$msi = Get-Verified $Manifest.audiveris.url $Manifest.audiveris.sha256
$unpack = Join-Path $env:TEMP "snap2midi-audiveris-$PID"
Remove-Item -Recurse -Force $unpack -ErrorAction SilentlyContinue
# Administrative install = unpack the files only; nothing is registered on the system.
$p = Start-Process msiexec.exe -ArgumentList "/a", "`"$msi`"", "/qn", "TARGETDIR=`"$unpack`"" -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "unpacking Audiveris (msiexec exit code $($p.ExitCode))" }
$exe = Get-ChildItem $unpack -Recurse -Filter "Audiveris.exe" | Select-Object -First 1
if (-not $exe) { Fail "Audiveris.exe not found in the unpacked MSI" }
Move-Item $exe.Directory.FullName (Join-Path $Stage "audiveris")
Remove-Item -Recurse -Force $unpack -ErrorAction SilentlyContinue

# ------------------------------------------------------------------ 6. Launcher, skill, licenses
Step "Launcher, agent skill and licenses"
$launcher = @"
@echo off
setlocal
set "SNAP2MIDI_AUDIVERIS=%~dp0audiveris\Audiveris.exe"
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
set "PYTHONHOME="
"%~dp0python\python.exe" -X utf8 -m snap2midi.cli %*
exit /b %ERRORLEVEL%
"@
Set-Content -Path (Join-Path $Stage "snap2midi.cmd") -Value $launcher -Encoding ASCII
Copy-Item (Join-Path $Here "SKILL.md") $Stage
$lic = Join-Path $Stage "licenses"
New-Item -ItemType Directory $lic | Out-Null
Copy-Item (Join-Path $Here "Audiveris-LICENSE.txt") $lic
Get-ChildItem $Site -Directory -Filter "*.dist-info" | ForEach-Object {
    $files = Get-ChildItem $_.FullName -Recurse -File | Where-Object { $_.Name -match "^(LICEN|COPYING|NOTICE)" }
    if ($files) {
        $d = Join-Path $lic ($_.Name -split "-")[0]
        New-Item -ItemType Directory -Force $d | Out-Null
        $files | Copy-Item -Destination $d
    }
}
Copy-Item (Join-Path $PyDir "LICENSE.txt") (Join-Path $lic "Python-LICENSE.txt") -ErrorAction SilentlyContinue

# ------------------------------------------------------------------ 7. Check and finish
Step "Self-check"
$env:SNAP2MIDI_AUDIVERIS = Join-Path $Stage "audiveris\Audiveris.exe"
$check = "import snap2midi.cli, homr, music21; from snap2midi.recognize import cuda_gpu_count, find_audiveris; " +
         "assert find_audiveris(), 'Audiveris missing'; print('  GPUs usable:', cuda_gpu_count())"
Invoke-Checked "self-check" { & $Py -X utf8 -c $check }

if (Test-Path $Dir) { Remove-Item -Recurse -Force $Dir }
Move-Item $Stage $Dir
$size = (Get-ChildItem $Dir -Recurse -File | Measure-Object Length -Sum).Sum / 1GB
Write-Host ""
Write-Host ("Installed snap2midi to {0} ({1:N2} GB)" -f $Dir, $size) -ForegroundColor Green
Write-Host "Run:   `"$Dir\snap2midi.cmd`" transcribe shot1.png shot2.png"
Write-Host "Agent skill: $Dir\SKILL.md"
