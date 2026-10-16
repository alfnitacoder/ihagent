# Install IHAgent on Windows.
#
# From a clone of this repository, in PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
#
# One line (clones or updates the repo, then installs):
#   irm https://raw.githubusercontent.com/alfnitacoder/ihagent/master/install.ps1 | iex
#
# If a saved install.ps1 will not run:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#
# Python 3.10-3.13. py -3.12 is preferred (Python install manager). A newer
# interpreter warns and still runs, because some wheels may be missing.
# The iheye extra is macOS-only and is not installed.
#
# IHAGENT_HOME overrides the clone directory used when this script is not
# already inside a checkout (default: ~\ihagent).

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$RepoUrl = "https://github.com/alfnitacoder/ihagent.git"
$RepoBranch = "master"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message"
}

function Write-Warn([string]$Message) {
    Write-Host "warning: $Message"
}

function Fail([string]$Message) {
    Write-Host "error: $Message"
    if ($script:ScriptPath) { exit 1 }
    throw $Message
}

$script:ScriptPath = $PSCommandPath
if (-not $script:ScriptPath) {
    $script:ScriptPath = $MyInvocation.MyCommand.Path
}

function Show-Help {
    @"
Install IHAgent on Windows.

  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall

From the network (clones or updates ~\ihagent, unless IHAGENT_HOME is set,
or the ihagent.cmd shim already points at a checkout):

  irm https://raw.githubusercontent.com/alfnitacoder/ihagent/master/install.ps1 | iex

If Activate.ps1 or this file is blocked:

  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

Python 3.10-3.13 is required. py -3.12 is preferred. Newer Pythons warn
and still run; some wheels may be missing. iheye is macOS-only.
"@ | Write-Host
}

$Uninstall = $false
$Help = $false
if ($env:IHAGENT_UNINSTALL -eq "1") { $Uninstall = $true }
foreach ($arg in $args) {
    switch ($arg) {
        "-Uninstall" { $Uninstall = $true }
        "--uninstall" { $Uninstall = $true }
        "-h" { $Help = $true }
        "--help" { $Help = $true }
        "-?" { $Help = $true }
        default { Fail "Unknown argument: $arg" }
    }
}
if ($Help) {
    Show-Help
}

function Get-PythonVersion {
    param([string]$Exe, [string[]]$Prefix)
    $code = "import sys; print('{0}.{1}'.format(sys.version_info[0], sys.version_info[1]))"
    $out = & $Exe @Prefix -c $code 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $text = @($out) | Select-Object -Last 1
    if (-not $text) { return $null }
    return $text.ToString().Trim()
}

function Test-VersionInRange([string]$Version) {
    $parts = $Version.Split(".")
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    return ($major -eq 3 -and $minor -ge 10 -and $minor -le 13)
}

function Test-VersionTooNew([string]$Version) {
    $parts = $Version.Split(".")
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    return (($major -gt 3) -or ($major -eq 3 -and $minor -gt 13))
}

function Find-Python {
    $newer = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($spec in @("-3.12", "-3.13", "-3.11", "-3.10")) {
            $ver = Get-PythonVersion -Exe "py" -Prefix @($spec)
            if ($ver -and (Test-VersionInRange $ver)) {
                return @{ Exe = "py"; Prefix = @($spec); Version = $ver }
            }
        }
        $ver = Get-PythonVersion -Exe "py" -Prefix @("-3")
        if ($ver -and (Test-VersionInRange $ver)) {
            return @{ Exe = "py"; Prefix = @("-3"); Version = $ver }
        }
        if ($ver -and (Test-VersionTooNew $ver)) {
            $newer = @{ Exe = "py"; Prefix = @("-3"); Version = $ver }
        }
    }
    foreach ($name in @("python", "python3")) {
        if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { continue }
        $ver = Get-PythonVersion -Exe $name -Prefix @()
        if ($ver -and (Test-VersionInRange $ver)) {
            return @{ Exe = $name; Prefix = @(); Version = $ver }
        }
        if ($ver -and (Test-VersionTooNew $ver) -and -not $newer) {
            $newer = @{ Exe = $name; Prefix = @(); Version = $ver }
        }
    }
    if ($newer) {
        Write-Warn "Python $($newer.Version) is newer than 3.13. Some wheels may be missing. Python 3.12 is the safest choice."
        return $newer
    }
    Fail "Python 3.10-3.13 is required. Install Python 3.12 (py -3.12, or https://www.python.org/downloads/) and re-run."
}

function Get-RootFromShim {
    $cmd = Join-Path $env:USERPROFILE ".local\bin\ihagent.cmd"
    if (-not (Test-Path $cmd)) { return $null }
    $text = Get-Content -Raw -Path $cmd -ErrorAction SilentlyContinue
    if (-not $text) { return $null }
    $match = [regex]::Match($text, "([A-Za-z]:\\[^`"\r\n]+)\\\.venv\\Scripts\\ihagent\.exe")
    if ($match.Success) { return $match.Groups[1].Value }
    return $null
}

function Resolve-InstallRoot {
    param([bool]$AllowClone)
    if ($script:ScriptPath -and (Test-Path $script:ScriptPath)) {
        $scriptDir = Split-Path -Parent $script:ScriptPath
        $marker = Join-Path $scriptDir "ihagent\pyproject.toml"
        if (Test-Path $marker) { return (Resolve-Path $scriptDir).Path }
    }
    $dest = Get-RootFromShim
    if (-not $dest) {
        if ($env:IHAGENT_HOME) { $dest = $env:IHAGENT_HOME }
        else { $dest = Join-Path $env:USERPROFILE "ihagent" }
    }
    $marker = Join-Path $dest "ihagent\pyproject.toml"
    $gitDir = Join-Path $dest ".git"
    if ((Test-Path $marker) -and (Test-Path $gitDir)) {
        if ($AllowClone) {
            Write-Step "Updating $dest"
            & git -C $dest pull --ff-only | Out-Host
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "git pull failed. Installing the copy already in $dest."
            }
        }
    } elseif (Test-Path $marker) {
        Write-Step "Using existing checkout at $dest"
    } elseif (Test-Path $dest) {
        Fail "$dest exists but is not an IHAgent checkout. Set IHAGENT_HOME, or move that directory aside."
    } elseif ($AllowClone) {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Fail "git is required to clone IHAgent. Install Git for Windows and re-run."
        }
        Write-Step "Cloning $RepoUrl into $dest"
        & git clone --branch $RepoBranch $RepoUrl $dest | Out-Host
        if ($LASTEXITCODE -ne 0) { Fail "git clone failed." }
    }
    if (Test-Path $dest) { return (Resolve-Path $dest).Path }
    return $dest
}

function Ensure-Venv($Python, [string]$Root) {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $have = Get-PythonVersion -Exe $venvPython -Prefix @()
        if ($have -and $have -ne $Python.Version) {
            Write-Step "Replacing .venv (Python $have) with Python $($Python.Version)"
            Remove-Item -Recurse -Force (Join-Path $Root ".venv")
        }
    }
    if (-not (Test-Path $venvPython)) {
        Write-Step "Creating .venv with $($Python.Exe) $($Python.Prefix) (Python $($Python.Version))"
        $pyArgs = @()
        $pyArgs += $Python.Prefix
        $pyArgs += @("-m", "venv", (Join-Path $Root ".venv"))
        & $Python.Exe @pyArgs
        if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtualenv." }
    } else {
        Write-Step "Using existing .venv (Python $($Python.Version))"
    }
}

function Install-Package([string]$Root) {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    Write-Step "Installing ihagent[browser]"
    Push-Location $Root
    try {
        & $venvPython -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed." }
        # Double quotes: cmd.exe does not treat single quotes as quotes.
        & $venvPython -m pip install -e "ihagent[browser]"
        if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
    } finally {
        Pop-Location
    }
}

function Install-Playwright([string]$Root) {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    Write-Step "Installing Playwright browser (chrome)"
    & $venvPython -m playwright install chrome
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "playwright install chrome failed. Trying chromium."
        & $venvPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { Fail "playwright browser install failed." }
    }
}

function Write-UserEnv {
    $dir = Join-Path $env:USERPROFILE ".hfagent"
    $dest = Join-Path $dir ".env"
    if (Test-Path $dest) {
        Write-Step "Leaving existing $dest unchanged"
        return
    }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $content = @"
# IHAgent user settings (~/.hfagent/.env).
# The installer creates this file once and never overwrites it.
# Blank values are ignored. HF_TOKEN is also read for the Hugging Face router.
# Hugging Face token: https://huggingface.co/settings/tokens
# Ollama Cloud key:   https://ollama.com/settings/keys

HF_API_KEY=
OLLAMA_API_KEY=
HF_BASE_URL=https://router.huggingface.co/v1
HFAGENT_MODEL=Qwen/Qwen2.5-Coder-32B-Instruct
"@
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $normalized = ($content -replace "`r`n", "`n") -replace "`n", "`r`n"
    [System.IO.File]::WriteAllText($dest, $normalized, $utf8)
    Write-Step "Created $dest"
}

function Add-UserPath([string]$Dir) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { $userPath = "" }
    $norm = $Dir.TrimEnd("\")
    foreach ($entry in ($userPath -split ";")) {
        if (-not $entry) { continue }
        if ($entry.TrimEnd("\") -ieq $norm) { return $false }
    }
    if ($userPath) { $updated = "$Dir;$userPath" } else { $updated = $Dir }
    [Environment]::SetEnvironmentVariable("Path", $updated, "User")
    return $true
}

function Remove-UserPath([string]$Dir) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { return }
    $norm = $Dir.TrimEnd("\")
    $kept = @()
    $removed = $false
    foreach ($entry in ($userPath -split ";")) {
        if (-not $entry) { continue }
        if ($entry.TrimEnd("\") -ieq $norm) { $removed = $true; continue }
        $kept += $entry
    }
    if ($removed) {
        [Environment]::SetEnvironmentVariable("Path", ($kept -join ";"), "User")
        Write-Step "Removed $Dir from your user PATH"
    }
}

function Install-Shim([string]$Root) {
    $bin = Join-Path $env:USERPROFILE ".local\bin"
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    foreach ($name in @("ihagent", "hfagent")) {
        $exe = Join-Path $Root ".venv\Scripts\$name.exe"
        if (-not (Test-Path $exe)) { Fail "Expected $exe after install." }
        $cmdPath = Join-Path $bin "$name.cmd"
        $body = "@echo off`r`n`"$exe`" %*`r`n"
        Set-Content -Path $cmdPath -Value $body -Encoding Ascii
        Write-Step "Wrote $cmdPath"
    }
    $added = Add-UserPath $bin
    if ($env:Path -notlike "*$bin*") {
        $env:Path = "$bin;$env:Path"
    }
    if ($added) {
        Write-Host "Added $bin to your user PATH. Open a new terminal so ihagent is found."
    } else {
        Write-Host "$bin is already on your user PATH."
    }
}

function Remove-Shim([string]$Root) {
    $bin = Join-Path $env:USERPROFILE ".local\bin"
    foreach ($name in @("ihagent", "hfagent")) {
        $cmdPath = Join-Path $bin "$name.cmd"
        if (-not (Test-Path $cmdPath)) { continue }
        $text = Get-Content -Raw -Path $cmdPath -ErrorAction SilentlyContinue
        $exe = Join-Path $Root ".venv\Scripts\$name.exe"
        if ($text -and $text.Contains($exe)) {
            Remove-Item -Force $cmdPath
            Write-Step "Removed $cmdPath"
        } else {
            Write-Host "Left $cmdPath (it does not point at this install)."
        }
    }
    if (Test-Path $bin) {
        $remaining = @(Get-ChildItem -Force $bin -ErrorAction SilentlyContinue)
        if ($remaining.Count -eq 0) {
            Remove-UserPath $bin
            Remove-Item -Force $bin -ErrorAction SilentlyContinue
        }
    }
}

function Invoke-Activate([string]$Root) {
    $activate = Join-Path $Root ".venv\Scripts\Activate.ps1"
    if (-not (Test-Path $activate)) { return }
    try {
        . $activate
        Write-Step "Activated $activate"
    } catch {
        Write-Host "Could not activate the virtualenv."
        Write-Host "If execution policy blocked Activate.ps1, run:"
        Write-Host "  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"
        Write-Host "Continuing with .venv\Scripts\python.exe."
    }
}

function Uninstall-IHAgent {
    $root = Resolve-InstallRoot -AllowClone:$false
    Remove-Shim $root
    $venv = Join-Path $root ".venv"
    if (Test-Path $venv) {
        Remove-Item -Recurse -Force $venv
        Write-Step "Removed $venv"
    } else {
        Write-Step "No virtualenv at $venv"
    }
    Write-Host "Left $(Join-Path $env:USERPROFILE '.hfagent') in place (config, sessions, memory)."
}

function Install-IHAgent {
    $root = Resolve-InstallRoot -AllowClone:$true
    $marker = Join-Path $root "ihagent\pyproject.toml"
    if (-not (Test-Path $marker)) { Fail "Missing $marker." }
    $python = Find-Python
    Write-Step "Using $($python.Exe) $($python.Prefix) (Python $($python.Version))"
    Ensure-Venv $python $root
    Invoke-Activate $root
    Install-Package $root
    Install-Playwright $root
    Write-UserEnv
    Install-Shim $root
    $cmd = Join-Path $env:USERPROFILE ".local\bin\ihagent.cmd"
    Write-Step "ihagent --version"
    & $cmd --version
    if ($LASTEXITCODE -ne 0) { Fail "ihagent --version failed." }
    Write-Host ""
    Write-Host "Edit $(Join-Path $env:USERPROFILE '.hfagent\.env') and set HF_API_KEY (Hugging Face) or OLLAMA_API_KEY (Ollama Cloud)."
    Write-Host "Then run:  ihagent"
    Write-Host "plain 'playwright' is not on PATH outside the venv. Use .venv\Scripts\python -m playwright."
}

if (-not $Help) {
    if ($Uninstall) {
        Uninstall-IHAgent
    } else {
        Install-IHAgent
    }
}
