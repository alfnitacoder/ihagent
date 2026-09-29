# Configure IHAgent on Windows to use a local Ollama model.
# Does not install or require the Hugging Face CLI.
#
# From the clone:
#   powershell -ExecutionPolicy Bypass -File old_project_files\scripts\configure-ollama.ps1
# From old_project_files:
#   powershell -ExecutionPolicy Bypass -File .\scripts\configure-ollama.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\configure-ollama.ps1 -Model qwen3:8b

[CmdletBinding()]
param(
    [string]$Model = "qwen3:8b"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message"
}

function Test-Python([string]$File, [string[]]$Args) {
    try {
        $ver = & $File @($Args + @("-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}'); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"))
        if ($LASTEXITCODE -eq 0) {
            return @{ File = $File; Args = $Args; Version = ($ver | Select-Object -Last 1) }
        }
    } catch { }
    return $null
}

function Find-Python {
    $candidates = @(
        @{ File = "py"; Args = @("-3") },
        @{ File = "python"; Args = @() },
        @{ File = "python3"; Args = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.File -ErrorAction SilentlyContinue)) { continue }
        $found = Test-Python $c.File $c.Args
        if ($found) { return $found }
    }
    $roots = @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:ProgramFiles\Python",
        ${env:ProgramFiles(x86)}
    )
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $exes = Get-ChildItem -Path $root -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending
        foreach ($exe in $exes) {
            $found = Test-Python $exe.FullName @()
            if ($found) {
                $env:Path = "$(Split-Path $exe.FullName);$env:Path"
                return $found
            }
        }
    }
    return $null
}

function Install-Python {
    $found = Find-Python
    if ($found) { return $found }
    Write-Step "Installing Python 3.12"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python 3.10+ is required. Install from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'."
    }
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $found = Find-Python
    if (-not $found) {
        throw "Python installed, but this window cannot see it yet. Close PowerShell, open a new window, and run this script again."
    }
    return $found
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Find-Ollama {
    if (Get-Command ollama -ErrorAction SilentlyContinue) { return }
    $guesses = @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "$env:ProgramFiles\Ollama\ollama.exe"
    )
    foreach ($g in $guesses) {
        if (Test-Path $g) {
            $env:Path = "$(Split-Path $g);$env:Path"
            return
        }
    }
}

function Install-Ollama {
    Find-Ollama
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Write-Step "Ollama already installed: $((Get-Command ollama).Source)"
        return
    }
    Write-Step "Installing Ollama"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget not found. Install Ollama from https://ollama.com/download/windows then re-run this script."
    }
    winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
    Refresh-Path
    Find-Ollama
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        throw "Ollama installed but 'ollama' is not on PATH. Open a new PowerShell and re-run this script."
    }
}

function Ensure-OllamaRunning {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
        return
    } catch { }

    Write-Step "Starting Ollama"
    $app = "$env:LOCALAPPDATA\Programs\Ollama\Ollama.exe"
    if (Test-Path $app) {
        Start-Process $app | Out-Null
    } else {
        Start-Process ollama -ArgumentList "serve" | Out-Null
    }
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 1 | Out-Null
            return
        } catch { }
    }
    throw "Ollama did not start. Open the Ollama app from the Start menu and re-run this script."
}

function Ensure-Model {
    $installed = @(ollama list 2>$null | Select-Object -Skip 1 | ForEach-Object { ($_ -split "\s+")[0] } | Where-Object { $_ })
    if ($installed -contains $Model) {
        Write-Step "Model already present: $Model"
        return
    }
    $partial = @($installed | Where-Object { $_ -like "$Model*" -or $_ -like "*$Model*" })
    if ($partial.Count -eq 1) {
        $script:Model = $partial[0]
        Write-Step "Using installed model: $Model"
        return
    }
    Write-Step "Pulling Ollama model $Model (this can take a while)"
    ollama pull $Model
    if ($LASTEXITCODE -ne 0) { throw "ollama pull failed for $Model" }
}

function Setup-Venv($Python) {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    Write-Step "Creating venv with $($Python.File) $($Python.Version)"
    if (-not (Test-Path $venvPython)) {
        & $Python.File @($Python.Args + @("-m", "venv", (Join-Path $Root ".venv")))
        if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
    }
    & $venvPython -m pip install --upgrade pip
    & (Join-Path $Root ".venv\Scripts\pip.exe") install -e $Root
    if ($LASTEXITCODE -ne 0) { throw "pip install failed." }
}

function Write-Utf8([string]$Path, [string]$Content) {
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Content, $utf8)
}

if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    throw "This script must live in the IHAgent project (missing pyproject.toml)."
}

$py = Install-Python
Write-Step "Using $($py.File) $($py.Version)"
Install-Ollama
Ensure-OllamaRunning
Ensure-Model
Setup-Venv $py

$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    Copy-Item $envFile "$envFile.bak" -Force
    Write-Step "Backed up existing .env to .env.bak"
}
Write-Utf8 $envFile @"
# Local Ollama. No Hugging Face token required.
HF_BASE_URL=http://127.0.0.1:11434/v1
HFAGENT_MODEL=$Model
HFAGENT_APPROVAL=auto
HFAGENT_MAX_STEPS=40
"@

$launcher = Join-Path $Root "start-ihagent.cmd"
Write-Utf8 $launcher @"
@echo off
cd /d "%~dp0"
".venv\Scripts\ihagent.exe" %*
"@

$exe = Join-Path $Root ".venv\Scripts\ihagent.exe"
Write-Host ""
Write-Host "IHAgent is configured for Ollama model $Model."
Write-Host ""
Write-Host "Start it:"
Write-Host "  $exe"
Write-Host ""
Write-Host "Or double-click / run:"
Write-Host "  $launcher"
