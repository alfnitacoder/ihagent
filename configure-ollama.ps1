# Run from the clone root (the folder that contains old_project_files).
#   powershell -ExecutionPolicy Bypass -File .\configure-ollama.ps1

param(
    [string]$Model = "qwen3:8b"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "old_project_files\scripts\configure-ollama.ps1"
if (-not (Test-Path $script)) {
    throw "Missing $script. Clone the full ihagent repo and run this from that folder."
}
& $script -Model $Model
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
