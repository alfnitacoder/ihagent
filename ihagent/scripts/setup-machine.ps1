# Install hfagent on Windows: Python venv, Ollama, and a local model.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup-machine.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup-machine.ps1 -Model qwen2.5-coder:7b
#   powershell -ExecutionPolicy Bypass -File scripts\setup-machine.ps1 -SkipOllama
#   powershell -ExecutionPolicy Bypass -File scripts\setup-machine.ps1 -SkipModel
#   powershell -ExecutionPolicy Bypass -File scripts\setup-machine.ps1 -Dev

[CmdletBinding()]
param(
    [string]$Model = $(if ($env:HFAGENT_SETUP_MODEL) { $env:HFAGENT_SETUP_MODEL } else { "qwen2.5-coder:7b" }),
    [switch]$SkipOllama,
    [switch]$SkipModel,
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message"
}

function Find-Python {
    $candidates = @(
        @{ File = "py"; Args = @("-3") },
        @{ File = "python"; Args = @() },
        @{ File = "python3"; Args = @() }
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.File -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $ver = & $c.File @($c.Args + @("-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}'); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"))
            if ($LASTEXITCODE -eq 0) {
                return @{ File = $c.File; Args = $c.Args; Version = ($ver | Select-Object -Last 1) }
            }
        } catch {
            continue
        }
    }
    throw "Python 3.10+ is required. Install from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'."
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Install-Ollama {
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Write-Step "Ollama already installed: $((Get-Command ollama).Source)"
        return
    }
    Write-Step "Installing Ollama"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
        Refresh-Path
    } else {
        throw "winget not found. Install Ollama from https://ollama.com/download/windows then re-run."
    }
    Refresh-Path
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        $guesses = @(
            "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
            "$env:ProgramFiles\Ollama\ollama.exe"
        )
        foreach ($g in $guesses) {
            if (Test-Path $g) {
                $env:Path = "$(Split-Path $g);$env:Path"
                break
            }
        }
    }
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        throw "Ollama installed but 'ollama' is not on PATH. Open a new PowerShell and re-run."
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
    throw "Ollama did not start. Open the Ollama app from the Start menu and re-run."
}

function Pull-Model {
    $installed = @(ollama list 2>$null | Select-Object -Skip 1 | ForEach-Object { ($_ -split "\s+")[0] })
    if ($installed -contains $Model) {
        Write-Step "Model already present: $Model"
        return
    }
    Write-Step "Pulling Ollama model $Model (this can take a while)"
    ollama pull $Model
    if ($LASTEXITCODE -ne 0) { throw "ollama pull failed for $Model" }
}

function Setup-Venv($Python) {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    Write-Step "Creating venv with $($Python.File) $($Python.Version) at $Root\.venv"
    if (-not (Test-Path $venvPython)) {
        & $Python.File @($Python.Args + @("-m", "venv", (Join-Path $Root ".venv")))
    }
    & $venvPython -m pip install --upgrade pip
    $spec = if ($Dev) { "$Root[dev]" } else { $Root }
    & (Join-Path $Root ".venv\Scripts\pip.exe") install -e $spec
}

if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    throw "Run this from a copy of the hfagent repo (missing pyproject.toml)."
}

$py = Find-Python
Write-Step "Using $($py.File) $($py.Version)"

if (-not $SkipOllama) {
    Install-Ollama
    Ensure-OllamaRunning
    if (-not $SkipModel) {
        Pull-Model
    }
} else {
    Write-Step "Skipping Ollama (-SkipOllama)"
}

Setup-Venv $py

$hfagent = Join-Path $Root ".venv\Scripts\hfagent.exe"
Write-Host ""
Write-Host "Setup complete."
Write-Host ""
Write-Host "Run the agent (offline, local model):"
Write-Host "  $hfagent --ollama $Model"
Write-Host ""
Write-Host "Classic line REPL:"
Write-Host "  $hfagent --console --ollama $Model"
Write-Host ""
Write-Host "Cloud instead of Ollama:"
Write-Host "  `$env:HF_TOKEN = 'hf_...'"
Write-Host "  $hfagent"
