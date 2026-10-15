# Run from the clone root (the folder that contains ihagent).
#   powershell -ExecutionPolicy Bypass -File .\configure-ollama.ps1

param(
    [string]$Model = "qwen3:8b"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "ihagent\scripts\configure-ollama.ps1"
if (-not (Test-Path $script)) {
    throw "Missing $script. Clone the full ihagent repo and run this from that folder."
}
& $script -Model $Model
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
