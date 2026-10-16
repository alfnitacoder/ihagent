# Install IHAgent on Windows.
#
# From a clone of this repository, in PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallPython
#
# One line (clones or updates the repo, then installs):
#   irm https://raw.githubusercontent.com/alfnitacoder/ihagent/master/install.ps1 | iex
#
# Python 3.10 or newer. py -3.12 is preferred. Python 3.15 is accepted with a
# warning. -InstallPython (or IHAGENT_INSTALL_PYTHON=1) runs
# `py install --yes 3.12` non-interactively. Without that flag the installer
# uses whatever Python is already there.
#
# The installer never dot-sources Activate.ps1. It always calls
# .venv\Scripts\python.exe, so the virtualenv does not depend on execution policy.
#
# Playwright: bundled Chromium (no administrator prompt). Google Chrome is optional.
#
# IHAGENT_HOME overrides the clone directory used when this script is not
# already inside a checkout (default: ~\ihagent).
# IHAGENT_REPO_URL overrides the git remote (tests, forks).

$ErrorActionPreference = "Stop"
# PowerShell 7+: native stderr must not become a terminating error. Windows
# PowerShell 5.1 has no such variable; Invoke-HostedCommand sets
# ErrorActionPreference to Continue around every native call instead, because
# 2>&1 plus Stop turns git's stderr progress into a terminating error there.
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$RepoUrl = "https://github.com/alfnitacoder/ihagent.git"
$RepoBranch = "master"

function Get-RepoUrl {
    if ($env:IHAGENT_REPO_URL) { return $env:IHAGENT_REPO_URL.Trim() }
    return $RepoUrl
}

function Get-RepoBranch {
    if ($env:IHAGENT_REPO_BRANCH) { return $env:IHAGENT_REPO_BRANCH.Trim() }
    return $RepoBranch
}

$script:InstallPython = $env:IHAGENT_INSTALL_PYTHON -eq "1"
$script:FakeUserPath = $null

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message"
}

function Write-Warn([string]$Message) {
    Write-Host "warning: $Message"
}

function Fail([string]$Message) {
    Write-Host "error: $Message"
    # exit would close an interactive window when this file is piped to iex
    # (PSCommandPath is empty). -File sets ScriptPath, so exit keeps the
    # failure to one line. Tests set IHAGENT_INSTALL_LIBRARY and expect a throw.
    if ($env:IHAGENT_INSTALL_LIBRARY -eq "1" -or -not $script:ScriptPath) {
        throw $Message
    }
    exit 1
}

# -File sets PSCommandPath. irm | iex leaves it empty: MyCommand.Path then
# points at the caller, not at this installer, so it must not be used as the
# checkout. An empty ScriptPath means "update or clone ~\ihagent".
$script:ScriptPath = $null
if ($PSCommandPath) {
    $script:ScriptPath = $PSCommandPath
}

function Test-WindowsPlatform {
    if ($env:IHAGENT_INSTALL_FORCE_WINDOWS -eq "1") { return $true }
    if ($env:IHAGENT_INSTALL_FORCE_WINDOWS -eq "0") { return $false }
    $isWindowsVar = Get-Variable -Name IsWindows -ErrorAction SilentlyContinue
    if ($isWindowsVar) { return [bool]$isWindowsVar.Value }
    return $env:OS -eq "Windows_NT"
}

function Join-RepoPath {
    param([string]$Root, [string]$Relative)
    $acc = $Root
    foreach ($part in ($Relative -split '[\\/]')) {
        if ($part) { $acc = Join-Path $acc $part }
    }
    return $acc
}

function Format-PathChars([string]$Path) {
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($ch in $Path.ToCharArray()) {
        $code = [int]$ch
        if ($code -lt 32 -or $code -eq 127) {
            $parts.Add("<$code>")
        } else {
            $parts.Add([string]$ch)
        }
    }
    return ($parts -join "")
}

function Select-SingleResult {
    param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [System.Array]) {
        if ($Value.Count -eq 0) { return $null }
        return $Value[$Value.Count - 1]
    }
    return $Value
}

function ConvertTo-CleanPath {
    param($Value, [string]$Label = "path")
    $item = Select-SingleResult $Value
    if ($null -eq $item) { Fail "$Label is empty." }
    $text = "$item".Trim()
    if (-not $text) { Fail "$Label is empty." }
    $invalid = [System.IO.Path]::GetInvalidPathChars()
    $badIndex = $text.IndexOfAny($invalid)
    $hasBreak = $text.Contains("`n") -or $text.Contains("`r")
    if ($badIndex -ge 0 -or $hasBreak) {
        $visible = Format-PathChars $text
        Fail "$Label contains illegal characters and was not passed to Test-Path: $visible"
    }
    return $text
}

function Invoke-HostedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$ArgumentList
    )
    # Windows PowerShell 5.1: with ErrorActionPreference Stop, native stderr
    # redirected with 2>&1 becomes an ErrorRecord and then a terminating error.
    # Git writes fetch progress to stderr. Swallow it here, show it on the host,
    # and return only the exit code.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $hasNative = $false
    $nativePrev = $null
    if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
        $hasNative = $true
        $nativePrev = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }
    if (-not $ArgumentList) { $ArgumentList = @() }
    try {
        $captured = @(& $Exe @ArgumentList 2>&1)
        $code = $LASTEXITCODE
        foreach ($line in $captured) {
            if ($null -eq $line) { continue }
            $msg = "$line".TrimEnd()
            if ($msg) { Write-Host $msg }
        }
        if ($null -eq $code) { return 0 }
        return [int]$code
    } catch {
        Write-Host "$_"
        return 127
    } finally {
        $ErrorActionPreference = $previous
        if ($hasNative) {
            $PSNativeCommandUseErrorActionPreference = $nativePrev
        }
    }
}

function Invoke-Git {
    param([string[]]$ArgumentList)
    $prevPrompt = $env:GIT_TERMINAL_PROMPT
    $env:GIT_TERMINAL_PROMPT = "0"
    try {
        $gitArgs = @("-c", "advice.detachedHead=false", "-c", "color.ui=false") + @($ArgumentList)
        return (Invoke-HostedCommand -Exe "git" -ArgumentList $gitArgs)
    } finally {
        if ($null -eq $prevPrompt) {
            Remove-Item Env:GIT_TERMINAL_PROMPT -ErrorAction SilentlyContinue
        } else {
            $env:GIT_TERMINAL_PROMPT = $prevPrompt
        }
    }
}

function Get-PathSeparator {
    if (Test-WindowsPlatform) { return ";" }
    return ":"
}

function Get-ProcessPath {
    # Windows environment names are case-insensitive, so Path is PATH.
    # On Linux, $env:Path is a different variable from PATH, and command
    # lookup reads PATH. Read the one this process actually searches.
    if (Test-WindowsPlatform) {
        if ($null -eq $env:Path) { return "" }
        return [string]$env:Path
    }
    $value = [Environment]::GetEnvironmentVariable("PATH")
    if ($null -eq $value) { return "" }
    return [string]$value
}

function Set-ProcessPath([string]$Value) {
    if (Test-WindowsPlatform) {
        $env:Path = $Value
        return
    }
    [Environment]::SetEnvironmentVariable("PATH", $Value, "Process")
    $env:PATH = $Value
}

function Test-SamePath([string]$Left, [string]$Right) {
    $a = $Left.TrimEnd('\', '/')
    $b = $Right.TrimEnd('\', '/')
    if (Test-WindowsPlatform) { return $a -ieq $b }
    return $a -eq $b
}

function Get-PersistedUserPath {
    if ($env:IHAGENT_INSTALL_FAKE_USERPATH -eq "1" -or -not (Test-WindowsPlatform)) {
        if ($null -eq $script:FakeUserPath) { return "" }
        return [string]$script:FakeUserPath
    }
    try {
        $value = [Environment]::GetEnvironmentVariable("Path", "User")
    } catch {
        $value = $null
    }
    if ($null -eq $value) { return "" }
    return [string]$value
}

function Set-PersistedUserPath([string]$Value) {
    if ($env:IHAGENT_INSTALL_FAKE_USERPATH -eq "1" -or -not (Test-WindowsPlatform)) {
        $script:FakeUserPath = $Value
        return
    }
    [Environment]::SetEnvironmentVariable("Path", $Value, "User")
}

function Show-Help {
    @"
Install IHAgent on Windows.

  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallPython

From the network (clones or updates ~\ihagent, unless IHAGENT_HOME is set,
or the ihagent shim already points at a checkout):

  irm https://raw.githubusercontent.com/alfnitacoder/ihagent/master/install.ps1 | iex

  `$env:IHAGENT_INSTALL_PYTHON = "1"
  irm https://raw.githubusercontent.com/alfnitacoder/ihagent/master/install.ps1 | iex

Python 3.10 or newer. py -3.12 is preferred. Newer Pythons (including 3.15)
warn and still run. -InstallPython runs: py install --yes 3.12
Without that flag the installer uses the Python you already have.

The installer does not activate the virtualenv and does not run Activate.ps1.
Execution policy does not have to allow scripts for the venv. If a saved
install.ps1 will not start, use -ExecutionPolicy Bypass as shown above.

Playwright installs bundled Chromium, which does not need administrator rights.
"@ | Write-Host
}

$Uninstall = $false
$Help = $false
if ($env:IHAGENT_UNINSTALL -eq "1") { $Uninstall = $true }
foreach ($arg in $args) {
    switch ($arg) {
        "-Uninstall" { $Uninstall = $true }
        "--uninstall" { $Uninstall = $true }
        "-InstallPython" { $script:InstallPython = $true }
        "--install-python" { $script:InstallPython = $true }
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
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $hasNative = $false
    $nativePrev = $null
    if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
        $hasNative = $true
        $nativePrev = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }
    if (-not $Prefix) { $Prefix = @() }
    $found = $null
    try {
        $raw = @(& $Exe @Prefix -c $code 2>&1)
        $exit = $LASTEXITCODE
        if ($exit -ne 0) { return $null }
        foreach ($line in $raw) {
            if ($null -eq $line) { continue }
            $trim = "$line".Trim()
            if ($trim -match '^\d+\.\d+$') { $found = $trim }
        }
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $previous
        if ($hasNative) {
            $PSNativeCommandUseErrorActionPreference = $nativePrev
        }
    }
    return $found
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

function Install-PreferredPython {
    Write-Step "Installing Python 3.12 (py install --yes 3.12)"
    $code = Invoke-HostedCommand -Exe "py" -ArgumentList @("install", "--yes", "3.12")
    if ($code -ne 0) {
        Write-Warn "py install --yes 3.12 failed (exit $code). Continuing with the Python already installed."
        return $false
    }
    return $true
}

function Find-Python {
    $py = $null
    if (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
    $preferredSpecs = @("-3.12", "-3.13", "-3.11", "-3.10")
    if ($py) {
        foreach ($spec in $preferredSpecs) {
            $ver = Get-PythonVersion -Exe $py -Prefix @($spec)
            if ($ver -and (Test-VersionInRange $ver)) {
                return @{ Exe = $py; Prefix = @($spec); Version = $ver }
            }
        }
        if ($script:InstallPython) {
            $installed = Install-PreferredPython
            if ($installed) {
                $ver = Get-PythonVersion -Exe $py -Prefix @("-3.12")
                if ($ver -and (Test-VersionInRange $ver)) {
                    return @{ Exe = $py; Prefix = @("-3.12"); Version = $ver }
                }
                Write-Warn "py install --yes 3.12 finished, but py -3.12 still does not run."
            }
        } else {
            Write-Host "Python 3.12 is not installed for the py launcher."
            Write-Host "Using the Python already on this machine. To install 3.12 non-interactively, re-run with -InstallPython"
            Write-Host "(that runs: py install --yes 3.12)."
        }
    } elseif ($script:InstallPython) {
        Write-Warn "-InstallPython needs the Python install manager command py. It was not found on PATH."
    }

    $newer = $null
    if ($py) {
        $ver = Get-PythonVersion -Exe $py -Prefix @("-3")
        if ($ver -and (Test-VersionInRange $ver)) {
            return @{ Exe = $py; Prefix = @("-3"); Version = $ver }
        }
        if ($ver -and (Test-VersionTooNew $ver)) {
            $newer = @{ Exe = $py; Prefix = @("-3"); Version = $ver }
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
        Write-Warn "Python $($newer.Version) is newer than 3.13. Continuing with it. Python 3.12 is preferred."
        return $newer
    }
    if ($py -and -not $script:InstallPython) {
        Fail "Python 3.10 or newer is required. Re-run with -InstallPython to run: py install --yes 3.12"
    }
    Fail "Python 3.10 or newer is required. Install Python 3.12 (https://www.python.org/downloads/) and re-run. If the Python install manager is on PATH, re-run with -InstallPython."
}

function Get-ShimDir {
    if (-not $env:USERPROFILE) {
        if ($env:HOME) { $env:USERPROFILE = $env:HOME }
        else { Fail "USERPROFILE is not set." }
    }
    return (Join-RepoPath $env:USERPROFILE ".local\bin")
}

function Get-RootFromShimText([string]$Text) {
    if (-not $Text) { return $null }
    $win = [regex]::Match($Text, '([A-Za-z]:\\[^"\r\n]+)\\\.venv\\Scripts\\ihagent\.exe')
    if ($win.Success) { return $win.Groups[1].Value.Trim() }
    $unix = [regex]::Match($Text, 'exec\s+"([^"]+)/\.venv/bin/ihagent"')
    if ($unix.Success) { return $unix.Groups[1].Value.Trim() }
    return $null
}

function Get-RootFromShim {
    $bin = Get-ShimDir
    $candidates = @(
        (Join-Path $bin "ihagent.cmd"),
        (Join-Path $bin "ihagent")
    )
    foreach ($cmd in $candidates) {
        if (-not (Test-Path -LiteralPath $cmd)) { continue }
        $text = Get-Content -Raw -LiteralPath $cmd -ErrorAction SilentlyContinue
        $root = Get-RootFromShimText $text
        if ($root) { return $root }
    }
    return $null
}

function Resolve-InstallRoot {
    param([bool]$AllowClone)
    if ($script:ScriptPath) {
        $scriptFile = ConvertTo-CleanPath $script:ScriptPath -Label "Script path"
        if (Test-Path -LiteralPath $scriptFile) {
            $scriptDir = Split-Path -Parent $scriptFile
            $marker = Join-RepoPath $scriptDir "ihagent\pyproject.toml"
            if (Test-Path -LiteralPath $marker) {
                $resolved = (Resolve-Path -LiteralPath $scriptDir).Path
                return (ConvertTo-CleanPath $resolved -Label "Install root")
            }
        }
    }
    $dest = $null
    $fromShim = Get-RootFromShim
    if ($fromShim) {
        $dest = ConvertTo-CleanPath $fromShim -Label "Path from ihagent shim"
    }
    if (-not $dest) {
        if ($env:IHAGENT_HOME) {
            $dest = $env:IHAGENT_HOME.Trim()
        } else {
            if (-not $env:USERPROFILE) {
                if ($env:HOME) { $env:USERPROFILE = $env:HOME }
                else { Fail "USERPROFILE is not set." }
            }
            $dest = Join-Path $env:USERPROFILE "ihagent"
        }
    }
    $dest = ConvertTo-CleanPath $dest -Label "Install root"
    $marker = Join-RepoPath $dest "ihagent\pyproject.toml"
    $gitDir = Join-Path $dest ".git"
    if ((Test-Path -LiteralPath $marker) -and (Test-Path -LiteralPath $gitDir)) {
        if ($AllowClone) {
            Write-Step "Updating $dest"
            if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
                Write-Warn "git was not found. Installing the copy already in $dest."
            } else {
                $pullExit = Invoke-Git -ArgumentList @("-C", $dest, "pull", "--ff-only", "-q")
                if ($pullExit -ne 0) {
                    Write-Warn "git pull failed. Installing the copy already in $dest."
                }
            }
        }
    } elseif (Test-Path -LiteralPath $marker) {
        Write-Step "Using existing checkout at $dest"
    } elseif (Test-Path -LiteralPath $dest) {
        Fail "$dest exists but is not an IHAgent checkout. Set IHAGENT_HOME, or move that directory aside."
    } elseif ($AllowClone) {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Fail "git is required to clone IHAgent. Install Git for Windows and re-run."
        }
        $parent = Split-Path -Parent $dest
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        $cloneUrl = Get-RepoUrl
        $cloneBranch = Get-RepoBranch
        Write-Step "Cloning $cloneUrl into $dest"
        $cloneExit = Invoke-Git -ArgumentList @("clone", "--branch", $cloneBranch, "-q", $cloneUrl, $dest)
        if ($cloneExit -ne 0) { Fail "git clone failed." }
    }
    if (Test-Path -LiteralPath $dest) {
        $dest = (Resolve-Path -LiteralPath $dest).Path
    }
    return (ConvertTo-CleanPath $dest -Label "Install root")
}

function Get-VenvPython([string]$Root) {
    if (Test-WindowsPlatform) {
        return (Join-RepoPath $Root ".venv\Scripts\python.exe")
    }
    return (Join-RepoPath $Root ".venv/bin/python")
}

function Get-VenvExe([string]$Root, [string]$Name) {
    if (Test-WindowsPlatform) {
        return (Join-RepoPath $Root ".venv\Scripts\$Name.exe")
    }
    return (Join-RepoPath $Root ".venv/bin/$Name")
}

function Ensure-Venv($Python, [string]$Root) {
    $venvPython = Get-VenvPython $Root
    if (Test-Path -LiteralPath $venvPython) {
        $have = Get-PythonVersion -Exe $venvPython -Prefix @()
        if ($have -and $have -ne $Python.Version) {
            Write-Step "Replacing .venv (Python $have) with Python $($Python.Version)"
            Remove-Item -Recurse -Force (Join-RepoPath $Root ".venv")
        }
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $shown = "$($Python.Exe) $($Python.Prefix -join ' ')".Trim()
        Write-Step "Creating .venv with $shown (Python $($Python.Version))"
        $pyArgs = @()
        if ($Python.Prefix) { $pyArgs += @($Python.Prefix) }
        $pyArgs += @("-m", "venv", (Join-RepoPath $Root ".venv"))
        $code = Invoke-HostedCommand -Exe $Python.Exe -ArgumentList $pyArgs
        if ($code -ne 0) { Fail "Could not create the virtualenv." }
    } else {
        Write-Step "Using existing .venv (Python $($Python.Version))"
    }
}

function Install-Package([string]$Root) {
    $venvPython = Get-VenvPython $Root
    Write-Step "Installing ihagent[browser]"
    Push-Location $Root
    try {
        $code = Invoke-HostedCommand -Exe $venvPython -ArgumentList @("-m", "pip", "install", "--upgrade", "pip")
        if ($code -ne 0) { Fail "pip upgrade failed." }
        # Double quotes: cmd.exe does not treat single quotes as quotes. PowerShell
        # would also treat unquoted [browser] as a wildcard.
        $code = Invoke-HostedCommand -Exe $venvPython -ArgumentList @("-m", "pip", "install", "-e", "ihagent[browser]")
        if ($code -ne 0) { Fail "pip install failed." }
    } finally {
        Pop-Location
    }
}

function Install-Playwright([string]$Root) {
    $venvPython = Get-VenvPython $Root
    # chromium is the Playwright-managed browser. `playwright install chrome`
    # tries to install Google Chrome and, on Windows, fails without administrator
    # rights ("re-running as Administrator may help").
    Write-Step "Installing Playwright browser (chromium)"
    $code = Invoke-HostedCommand -Exe $venvPython -ArgumentList @("-m", "playwright", "install", "chromium")
    if ($code -ne 0) {
        Fail "playwright install chromium failed. This does not need administrator rights. playwright install chrome is optional and may require an administrator prompt on Windows."
    }
}

function Write-UserEnv {
    if (-not $env:USERPROFILE) {
        if ($env:HOME) { $env:USERPROFILE = $env:HOME }
        else { Fail "USERPROFILE is not set." }
    }
    $dir = Join-RepoPath $env:USERPROFILE ".hfagent"
    $dest = Join-RepoPath $dir ".env"
    if (Test-Path -LiteralPath $dest) {
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
    $userPath = Get-PersistedUserPath
    $sep = Get-PathSeparator
    foreach ($entry in ($userPath -split [regex]::Escape($sep))) {
        if (-not $entry) { continue }
        if (Test-SamePath $entry $Dir) { return $false }
    }
    if ($userPath) { $updated = "$Dir$sep$userPath" } else { $updated = $Dir }
    Set-PersistedUserPath $updated
    return $true
}

function Remove-UserPath([string]$Dir) {
    $userPath = Get-PersistedUserPath
    if (-not $userPath) { return }
    $sep = Get-PathSeparator
    $kept = @()
    $removed = $false
    foreach ($entry in ($userPath -split [regex]::Escape($sep))) {
        if (-not $entry) { continue }
        if (Test-SamePath $entry $Dir) { $removed = $true; continue }
        $kept += $entry
    }
    if ($removed) {
        Set-PersistedUserPath ($kept -join $sep)
        Write-Step "Removed $Dir from your user PATH"
    }
}

function Add-SessionPath([string]$Dir) {
    $sep = Get-PathSeparator
    $current = Get-ProcessPath
    $found = $false
    foreach ($entry in ($current -split [regex]::Escape($sep))) {
        if (-not $entry) { continue }
        if (Test-SamePath $entry $Dir) { $found = $true; break }
    }
    if (-not $found) {
        if ($current) { Set-ProcessPath "$Dir$sep$current" } else { Set-ProcessPath $Dir }
    }
    return (-not $found)
}

function Install-Shim([string]$Root) {
    $bin = Get-ShimDir
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    foreach ($name in @("ihagent", "hfagent")) {
        $exe = Get-VenvExe $Root $name
        if (-not (Test-Path -LiteralPath $exe)) { Fail "Expected $exe after install." }
        if (Test-WindowsPlatform) {
            $cmdPath = Join-Path $bin "$name.cmd"
            $body = "@echo off`r`n`"$exe`" %*`r`n"
            Set-Content -LiteralPath $cmdPath -Value $body -Encoding Ascii
        } else {
            $cmdPath = Join-Path $bin $name
            $body = "#!/bin/sh`nexec `"$exe`" " + '"$@"' + "`n"
            $utf8 = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($cmdPath, $body, $utf8)
            $chmodExit = Invoke-HostedCommand -Exe "chmod" -ArgumentList @("+x", $cmdPath)
            if ($chmodExit -ne 0) { Fail "chmod +x $cmdPath failed." }
        }
        Write-Step "Wrote $cmdPath"
    }
    $addedUser = Select-SingleResult (Add-UserPath $bin)
    $addedSession = Select-SingleResult (Add-SessionPath $bin)
    if ($addedUser) {
        Write-Host "Added $bin to your user PATH."
    } else {
        Write-Host "$bin is already on your user PATH."
    }
    if ($addedSession) {
        Write-Host "Added $bin to this session's PATH. ihagent works in this window."
    } else {
        Write-Host "This session's PATH already includes $bin."
    }
    Write-Host "Open a new terminal so other windows can find ihagent:"
    Write-Host "  Windows Terminal: open a new tab, or"
    Write-Host "  Start menu: open Windows PowerShell or Command Prompt."
}

function Remove-Shim([string]$Root) {
    $bin = Get-ShimDir
    foreach ($name in @("ihagent", "hfagent")) {
        $cmdPath = if (Test-WindowsPlatform) { Join-Path $bin "$name.cmd" } else { Join-Path $bin $name }
        if (-not (Test-Path -LiteralPath $cmdPath)) { continue }
        $text = Get-Content -Raw -LiteralPath $cmdPath -ErrorAction SilentlyContinue
        $exe = Get-VenvExe $Root $name
        if ($text -and $text.Contains($exe)) {
            Remove-Item -Force -LiteralPath $cmdPath
            Write-Step "Removed $cmdPath"
        } else {
            Write-Host "Left $cmdPath (it does not point at this install)."
        }
    }
    if (Test-Path -LiteralPath $bin) {
        $remaining = @(Get-ChildItem -Force -LiteralPath $bin -ErrorAction SilentlyContinue)
        if ($remaining.Count -eq 0) {
            Remove-UserPath $bin
            Remove-Item -Force -LiteralPath $bin -ErrorAction SilentlyContinue
        }
    }
}

function Uninstall-IHAgent {
    $root = ConvertTo-CleanPath (Resolve-InstallRoot -AllowClone:$false) -Label "Install root"
    Remove-Shim $root
    $venv = Join-RepoPath $root ".venv"
    if (Test-Path -LiteralPath $venv) {
        Remove-Item -Recurse -Force -LiteralPath $venv
        Write-Step "Removed $venv"
    } else {
        Write-Step "No virtualenv at $venv"
    }
    $kept = Join-RepoPath $env:USERPROFILE ".hfagent"
    Write-Host "Left $kept in place (config, sessions, memory)."
}

function Install-IHAgent {
    $root = ConvertTo-CleanPath (Resolve-InstallRoot -AllowClone:$true) -Label "Install root"
    $marker = Join-RepoPath $root "ihagent\pyproject.toml"
    if (-not (Test-Path -LiteralPath $marker)) { Fail "Missing $marker." }
    $python = Select-SingleResult (Find-Python)
    if (-not $python -or -not $python.Exe) {
        Fail "Python lookup did not return an interpreter."
    }
    $prefixText = (@($python.Prefix) -join " ").Trim()
    $using = "Using $($python.Exe) $prefixText (Python $($python.Version))"
    Write-Step $using.Trim()
    Ensure-Venv $python $root
    Install-Package $root
    Install-Playwright $root
    Write-UserEnv
    Install-Shim $root
    $cmd = if (Test-WindowsPlatform) {
        Join-Path (Get-ShimDir) "ihagent.cmd"
    } else {
        Join-Path (Get-ShimDir) "ihagent"
    }
    Write-Step "ihagent --version"
    $versionExit = Invoke-HostedCommand -Exe $cmd -ArgumentList @("--version")
    if ($versionExit -ne 0) { Fail "ihagent --version failed." }
    $envFile = Join-RepoPath $env:USERPROFILE ".hfagent\.env"
    $venvPython = Get-VenvPython $root
    Write-Host ""
    Write-Host "Edit $envFile and set HF_API_KEY (Hugging Face) or OLLAMA_API_KEY (Ollama Cloud)."
    Write-Host "Then run:  ihagent"
    Write-Host "The installer does not activate the virtualenv. Call the venv Python directly:"
    Write-Host "  $venvPython -m playwright"
}

# Dot-sourcing (tests) and IHAGENT_INSTALL_LIBRARY=1 define the functions and
# return. iex and -File run the install. irm | iex leaves PSCommandPath empty;
# that path is handled in Resolve-InstallRoot.
if ($env:IHAGENT_INSTALL_LIBRARY -eq "1" -or $MyInvocation.InvocationName -eq ".") {
    # library mode
} elseif (-not $Help) {
    if ($Uninstall) {
        Uninstall-IHAgent
    } else {
        Install-IHAgent
    }
}
