# Harness for install.ps1. Run with:
#   pwsh -NoProfile -File tests/install.Tests.ps1
#   pwsh -NoProfile -File tests/install.Tests.ps1 -SkipE2E
#
# Dot-sources the installer (IHAGENT_INSTALL_LIBRARY=1) and also simulates
# `irm install.ps1 | iex`, where $PSCommandPath is empty.

param([switch]$SkipE2E)

$ErrorActionPreference = "Stop"
$Pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue)
if (-not $Pwsh) {
    $candidate = Join-Path $env:HOME ".local/pwsh/pwsh"
    if (Test-Path -LiteralPath $candidate) { $Pwsh = $candidate }
    else { throw "pwsh is not on PATH" }
} else {
    $Pwsh = $Pwsh.Source
}

$Installer = (Resolve-Path (Join-Path $PSScriptRoot "..\install.ps1")).Path
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:OriginalPath = [Environment]::GetEnvironmentVariable("PATH")
$script:Failures = New-Object System.Collections.Generic.List[string]
$script:Passed = 0

function It([string]$Name, [scriptblock]$Test) {
    Write-Host "  it $Name"
    try {
        & $Test
        $script:Passed++
        Write-Host "    ok"
    } catch {
        $msg = $_.Exception.Message
        Write-Host "    FAIL: $msg"
        $script:Failures.Add("${Name}: $msg")
    }
}

function Assert-Equal($Actual, $Expected, [string]$Label) {
    if ($Actual -ne $Expected) {
        throw "$Label expected [$Expected] got [$Actual]"
    }
}

function Assert-True($Condition, [string]$Label) {
    if (-not $Condition) { throw $Label }
}

function Set-TestPath([string]$Value) {
    [Environment]::SetEnvironmentVariable("PATH", $Value, "Process")
    $env:PATH = $Value
}

function New-TempDir {
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ("ihagent-install-" + [guid]::NewGuid().ToString("n"))
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    return $dir
}

# Dot-source at script scope. Doing this inside a function would hide every
# installer function from the tests when that function returned.
$env:IHAGENT_INSTALL_LIBRARY = "1"
. $Installer
$script:ScriptPath = $null
$script:InstallPython = $false
$script:FakeUserPath = $null

function New-GitCheckoutBehindOrigin {
    param([string]$UserRoot)
    $remote = Join-Path $UserRoot "remote.git"
    $seed = Join-Path $UserRoot "seed"
    & git init --bare $remote | Out-Null
    & git --git-dir $remote symbolic-ref HEAD refs/heads/master
    New-Item -ItemType Directory -Force -Path $seed | Out-Null
    Push-Location $seed
    try {
        & git init -b master | Out-Null
        & git config user.email "ihagent-test@example.com"
        & git config user.name "IHAgent Test"
        New-Item -ItemType Directory -Force -Path (Join-Path $seed "ihagent") | Out-Null
        Set-Content -LiteralPath (Join-Path $seed "ihagent/pyproject.toml") -Value "[project]`nname = `"hfagent`"`n" -Encoding ascii
        & git add ihagent/pyproject.toml
        & git commit -m "init" | Out-Null
        & git remote add origin $remote
        & git push -u origin master | Out-Null
    } finally {
        Pop-Location
    }
    $checkout = Join-Path $UserRoot "ihagent"
    & git clone $remote $checkout | Out-Null
    Push-Location $seed
    try {
        Add-Content -LiteralPath (Join-Path $seed "ihagent/pyproject.toml") -Value "# updated" -Encoding ascii
        & git add ihagent/pyproject.toml
        & git commit -m "update" | Out-Null
        & git push origin master | Out-Null
    } finally {
        Pop-Location
    }
    return $checkout
}

function New-NoisyGit {
    param([string]$Dir, [string]$Log)
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $stub = Join-Path $Dir "git"
    $body = @"
#!/bin/bash
printf '%s\n' "hit `$*" >> "$Log"
echo "STDOUT-LEAK From https://github.com/alfnitacoder/ihagent"
echo "STDERR-LEAK remote: Enumerating objects: 3, done." >&2
exec /usr/bin/git "`$@"
"@
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($stub, ($body -replace "`r`n", "`n"), $utf8)
    & /bin/chmod -- a+x $stub
    return $stub
}

Write-Host "describe install.ps1"

It "does not activate the venv and installs chromium" {
    $src = Get-Content -Raw -LiteralPath $Installer
    Assert-True ($src -notmatch 'Invoke-Activate') "Invoke-Activate is still defined"
    Assert-True ($src -notmatch '(?m)^\s*\.\s+\$activate') "Activate.ps1 is still dot-sourced"
    Assert-True ($src -match 'playwright", "install", "chromium"') "playwright install is not chromium"
    Assert-True ($src -notmatch 'playwright", "install", "chrome"') "playwright install chrome is still the default"
    Assert-True ($src -match 'Windows Terminal') "missing new-terminal instructions"
    Assert-True ($src -match 'Start menu') "missing Start menu instructions"
    Assert-True ($src -match 'py install --yes 3.12') "missing non-interactive py install"
    Assert-True (-not (Get-Command Invoke-Activate -ErrorAction SilentlyContinue)) "Invoke-Activate loaded"
}

It "coerces a leaked pipeline to the last path and rejects illegal characters" {
    $clean = ConvertTo-CleanPath @(
        "STDOUT-LEAK From https://github.com/alfnitacoder/ihagent",
        "Already up to date.",
        "/tmp/ihagent-clean"
    ) -Label "Install root"
    Assert-Equal $clean "/tmp/ihagent-clean" "coerced path"
    $failed = $false
    try {
        ConvertTo-CleanPath "C:\Users\ageorge\ihagent`nFrom https://github.com/alfnitacoder/ihagent" -Label "Install root" | Out-Null
    } catch {
        $failed = $true
        $msg = $_.Exception.Message
        Assert-True ($msg -match "illegal characters") "message was: $msg"
        Assert-True ($msg -match "was not passed to Test-Path") "message was: $msg"
        Assert-True ($msg -notmatch "Test-Path :") "Test-Path error leaked: $msg"
    }
    Assert-True $failed "illegal path was accepted"
}

It "reads the install root out of a Windows shim and a Unix shim" {
    $win = "@echo off`r`n`"C:\Users\ageorge\ihagent\.venv\Scripts\ihagent.exe`" %*`r`n"
    Assert-Equal (Get-RootFromShimText $win) "C:\Users\ageorge\ihagent" "windows shim"
    $unix = "#!/bin/sh`n" + 'exec "/tmp/home/ihagent/.venv/bin/ihagent" "$@"' + "`n"
    Assert-Equal (Get-RootFromShimText $unix) "/tmp/home/ihagent" "unix shim"
}

It "uses Python 3.15 when py -3.12 is missing and does not install it" {
    $dir = New-TempDir
    $log = Join-Path $dir "py.log"
    $mark = Join-Path $dir "installed"
    $py = @'
#!/bin/bash
printf '%s\n' "$*" >> "$PY_STUB_LOG"
if [[ "$1" == "install" ]]; then
  touch "$PY_STUB_MARK"
  exit 0
fi
if [[ "$1" == "-3.12" && -f "$PY_STUB_MARK" ]]; then
  echo "3.12"
  exit 0
fi
if [[ "$1" == "-3.12" || "$1" == "-3.13" || "$1" == "-3.11" || "$1" == "-3.10" ]]; then
  echo "No runtime installed. Try running \"py install ${1#-}\"" >&2
  exit 1
fi
if [[ "$1" == "-3" || -z "$1" ]]; then
  echo "3.15"
  exit 0
fi
echo "unexpected: $*" >&2
exit 1
'@
    $pyPath = Join-Path $dir "py"
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($pyPath, ($py -replace "`r`n", "`n"), $utf8)
    & /bin/chmod -- a+x $pyPath
    $nope = "#!/bin/bash`necho 'not a runtime' >&2`nexit 1`n"
    foreach ($name in @("python", "python3")) {
        $path = Join-Path $dir $name
        [System.IO.File]::WriteAllText($path, $nope, $utf8)
        & /bin/chmod -- a+x $path
    }
    $saved = [Environment]::GetEnvironmentVariable("PATH")
    Set-TestPath ($dir + [IO.Path]::PathSeparator + $saved)
    $env:PY_STUB_LOG = $log
    $env:PY_STUB_MARK = $mark
    $previousNative = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $true
    $script:InstallPython = $false
    try {
        $found = Find-Python
        Assert-Equal $found.Version "3.15" "version"
        Assert-Equal $found.Exe "py" "exe"
        $logged = Get-Content -Raw -LiteralPath $log
        Assert-True ($logged -notmatch '(?m)^install ') "py install ran without -InstallPython: $logged"
    } finally {
        Set-TestPath $saved
        $PSNativeCommandUseErrorActionPreference = $previousNative
        Remove-Item Env:PY_STUB_LOG -ErrorAction SilentlyContinue
        Remove-Item Env:PY_STUB_MARK -ErrorAction SilentlyContinue
    }
}

It "runs py install --yes 3.12 only when -InstallPython is set" {
    $dir = New-TempDir
    $log = Join-Path $dir "py.log"
    $mark = Join-Path $dir "installed"
    $py = @'
#!/bin/bash
printf '%s\n' "$*" >> "$PY_STUB_LOG"
if [[ "$1" == "install" ]]; then
  touch "$PY_STUB_MARK"
  exit 0
fi
if [[ "$1" == "-3.12" && -f "$PY_STUB_MARK" ]]; then
  echo "3.12"
  exit 0
fi
if [[ "$1" == "-3.12" || "$1" == "-3.13" || "$1" == "-3.11" || "$1" == "-3.10" ]]; then
  echo "No runtime installed. Try running \"py install ${1#-}\"" >&2
  exit 1
fi
if [[ "$1" == "-3" ]]; then
  echo "3.15"
  exit 0
fi
exit 1
'@
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $pyPath = Join-Path $dir "py"
    [System.IO.File]::WriteAllText($pyPath, ($py -replace "`r`n", "`n"), $utf8)
    & /bin/chmod -- a+x $pyPath
    $nope = "#!/bin/bash`necho 'not a runtime' >&2`nexit 1`n"
    foreach ($name in @("python", "python3")) {
        $path = Join-Path $dir $name
        [System.IO.File]::WriteAllText($path, $nope, $utf8)
        & /bin/chmod -- a+x $path
    }
    $saved = [Environment]::GetEnvironmentVariable("PATH")
    Set-TestPath ($dir + [IO.Path]::PathSeparator + $saved)
    $env:PY_STUB_LOG = $log
    $env:PY_STUB_MARK = $mark
    $script:InstallPython = $true
    try {
        $found = Find-Python
        Assert-Equal $found.Version "3.12" "version"
        Assert-Equal (@($found.Prefix) -join " ") "-3.12" "prefix"
        $logged = Get-Content -Raw -LiteralPath $log
        Assert-True ($logged -match '(?m)^install --yes 3\.12$') "missing py install --yes 3.12 in: $logged"
    } finally {
        Set-TestPath $saved
        $script:InstallPython = $false
        Remove-Item Env:PY_STUB_LOG -ErrorAction SilentlyContinue
        Remove-Item Env:PY_STUB_MARK -ErrorAction SilentlyContinue
    }
}

It "returns one clean path when git updates an existing checkout" {
    $userRoot = New-TempDir
    $checkout = New-GitCheckoutBehindOrigin $userRoot
    $log = Join-Path $userRoot "git-stub.log"
    $stubDir = Join-Path $userRoot "bin"
    New-NoisyGit -Dir $stubDir -Log $log | Out-Null
    $savedPath = [Environment]::GetEnvironmentVariable("PATH")
    $savedProfile = $env:USERPROFILE
    $savedHomeVar = $env:IHAGENT_HOME
    Set-TestPath ($stubDir + [IO.Path]::PathSeparator + $savedPath)
    $env:USERPROFILE = $userRoot
    Remove-Item Env:IHAGENT_HOME -ErrorAction SilentlyContinue
    $script:ScriptPath = $null
    $previous = $ErrorActionPreference
    $previousNative = $PSNativeCommandUseErrorActionPreference
    $ErrorActionPreference = "Stop"
    $PSNativeCommandUseErrorActionPreference = $true
    try {
        $result = Resolve-InstallRoot -AllowClone $true
        Assert-True (-not ($result -is [System.Array])) "returned an array: $($result -join ' | ')"
        Assert-True ($result -is [string]) "type $($result.GetType().FullName)"
        $expected = (Resolve-Path -LiteralPath $checkout).Path
        Assert-Equal $result $expected "install root"
        Assert-True ($result -notmatch "LEAK|`n|`r") "path contained git output: $result"
        $body = Get-Content -Raw -LiteralPath (Join-Path $checkout "ihagent/pyproject.toml")
        Assert-True ($body -match "updated") "git pull did not fast-forward"
        $hits = Get-Content -Raw -LiteralPath $log
        Assert-True ($hits -match "pull") "noisy git was not used: $hits"
    } finally {
        Set-TestPath $savedPath
        $env:USERPROFILE = $savedProfile
        if ($null -eq $savedHomeVar) { Remove-Item Env:IHAGENT_HOME -ErrorAction SilentlyContinue }
        else { $env:IHAGENT_HOME = $savedHomeVar }
        $ErrorActionPreference = $previous
        $PSNativeCommandUseErrorActionPreference = $previousNative
    }
}

It "clones into USERPROFILE\ihagent without leaking git output" {
    $userRoot = New-TempDir
    $remote = Join-Path $userRoot "remote.git"
    $seed = Join-Path $userRoot "seed"
    & git init --bare $remote | Out-Null
    & git --git-dir $remote symbolic-ref HEAD refs/heads/master
    New-Item -ItemType Directory -Force -Path $seed | Out-Null
    Push-Location $seed
    try {
        & git init -b master | Out-Null
        & git config user.email "ihagent-test@example.com"
        & git config user.name "IHAgent Test"
        New-Item -ItemType Directory -Force -Path (Join-Path $seed "ihagent") | Out-Null
        Set-Content -LiteralPath (Join-Path $seed "ihagent/pyproject.toml") -Value "name=hfagent" -Encoding ascii
        & git add ihagent/pyproject.toml
        & git commit -m "init" | Out-Null
        & git remote add origin $remote
        & git push -u origin master | Out-Null
    } finally {
        Pop-Location
    }
    $user = Join-Path $userRoot "user"
    New-Item -ItemType Directory -Force -Path $user | Out-Null
    $savedProfile = $env:USERPROFILE
    $env:USERPROFILE = $user
    $env:IHAGENT_REPO_URL = $remote
    $env:IHAGENT_REPO_BRANCH = "master"
    Remove-Item Env:IHAGENT_HOME -ErrorAction SilentlyContinue
    $script:ScriptPath = $null
    try {
        $result = Resolve-InstallRoot -AllowClone $true
        Assert-True ($result -is [string]) "type $($result.GetType().FullName) value=$result"
        $expected = (Resolve-Path -LiteralPath (Join-Path $user "ihagent")).Path
        Assert-Equal $result $expected "cloned root"
        Assert-True (Test-Path -LiteralPath (Join-Path $expected "ihagent/pyproject.toml")) "missing marker"
    } finally {
        $env:USERPROFILE = $savedProfile
        Remove-Item Env:IHAGENT_REPO_URL -ErrorAction SilentlyContinue
        Remove-Item Env:IHAGENT_REPO_BRANCH -ErrorAction SilentlyContinue
    }
}

It "simulates irm | iex with an empty PSCommandPath" {
    $userRoot = New-TempDir
    $checkout = New-GitCheckoutBehindOrigin $userRoot
    $log = Join-Path $userRoot "git-stub.log"
    $stubDir = Join-Path $userRoot "bin"
    New-NoisyGit -Dir $stubDir -Log $log | Out-Null
    $runner = Join-Path $userRoot "iex-runner.ps1"
    $runnerBody = @'
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$env:IHAGENT_INSTALL_LIBRARY = "1"
Remove-Item Env:IHAGENT_HOME -ErrorAction SilentlyContinue
$env:USERPROFILE = $env:IHAGENT_TEST_HOME
$joined = $env:IHAGENT_TEST_GITBIN + [IO.Path]::PathSeparator + $env:IHAGENT_TEST_ORIGINAL_PATH
[Environment]::SetEnvironmentVariable("PATH", $joined, "Process")
$env:PATH = $joined
$code = Get-Content -Raw -LiteralPath $env:IHAGENT_TEST_INSTALLER
Invoke-Expression $code
$PSNativeCommandUseErrorActionPreference = $true
$ErrorActionPreference = "Stop"
if ($script:ScriptPath) { throw "PSCommandPath leaked into ScriptPath: $($script:ScriptPath)" }
$result = Resolve-InstallRoot -AllowClone $true
if ($result -is [System.Array]) { throw "returned $($result.Count) objects" }
if ($result -isnot [string]) { throw "type $($result.GetType().FullName)" }
$expected = (Resolve-Path -LiteralPath (Join-Path $env:USERPROFILE "ihagent")).Path
if ($result -ne $expected) { throw "got [$result] expected [$expected]" }
if ($result -match "LEAK") { throw "git output leaked into [$result]" }
$marker = Get-Content -Raw -LiteralPath (Join-Path $expected "ihagent/pyproject.toml")
if ($marker -notmatch "updated") { throw "pull did not update the checkout" }
Write-Host "IEX_ROOT=$result"
'@
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($runner, ($runnerBody -replace "`r`n", "`n"), $utf8)
    $env:IHAGENT_TEST_HOME = $userRoot
    $env:IHAGENT_TEST_GITBIN = $stubDir
    $env:IHAGENT_TEST_INSTALLER = $Installer
    $env:IHAGENT_TEST_ORIGINAL_PATH = $script:OriginalPath
    & $Pwsh -NoProfile -File $runner
    if ($LASTEXITCODE -ne 0) { throw "iex runner failed with exit $LASTEXITCODE" }
}

It "adds the shim directory to the user PATH and this session" {
    $userRoot = New-TempDir
    $root = Join-Path $userRoot "ihagent"
    $venvBin = Join-Path $root ".venv/bin"
    New-Item -ItemType Directory -Force -Path $venvBin | Out-Null
    $utf8 = New-Object System.Text.UTF8Encoding $false
    foreach ($name in @("ihagent", "hfagent")) {
        $exe = Join-Path $venvBin $name
        [System.IO.File]::WriteAllText($exe, "#!/bin/sh`necho 0.4.0`n", $utf8)
        & /bin/chmod -- a+x $exe
    }
    $savedPath = [Environment]::GetEnvironmentVariable("PATH")
    $savedProfile = $env:USERPROFILE
    $env:USERPROFILE = $userRoot
    $script:FakeUserPath = ""
    Remove-Item Env:IHAGENT_INSTALL_FORCE_WINDOWS -ErrorAction SilentlyContinue
    try {
        Install-Shim $root
        $bin = Join-Path $userRoot ".local/bin"
        $sessionPath = Get-ProcessPath
        Assert-True ($sessionPath.StartsWith($bin)) "session PATH is $sessionPath"
        Assert-True ($script:FakeUserPath.StartsWith($bin)) "user PATH is $($script:FakeUserPath)"
        $again = Add-SessionPath $bin
        Assert-True (-not $again) "session PATH was added twice"
        $shim = Get-RootFromShim
        $expected = (Resolve-Path -LiteralPath $root).Path
        Assert-Equal $shim $expected "shim root"
    } finally {
        Set-TestPath $savedPath
        $env:USERPROFILE = $savedProfile
    }
}

It "asks Playwright to install chromium" {
    $userRoot = New-TempDir
    $root = Join-Path $userRoot "proj"
    $venvBin = Join-Path $root ".venv/bin"
    New-Item -ItemType Directory -Force -Path $venvBin | Out-Null
    $log = Join-Path $userRoot "py.log"
    $stub = @'
#!/bin/bash
printf '%s\n' "$*" >> "$FAKE_PY_LOG"
exit 0
'@
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $python = Join-Path $venvBin "python"
    [System.IO.File]::WriteAllText($python, ($stub -replace "`r`n", "`n"), $utf8)
    & /bin/chmod -- a+x $python
    $env:FAKE_PY_LOG = $log
    try {
        Install-Playwright $root
        $logged = (Get-Content -Raw -LiteralPath $log).Trim()
        Assert-Equal $logged "-m playwright install chromium" "playwright args"
    } finally {
        Remove-Item Env:FAKE_PY_LOG -ErrorAction SilentlyContinue
    }
}

if (-not $SkipE2E) {
    It "runs the installer end to end on Linux via iex" {
        $userRoot = New-TempDir
        $checkout = Join-Path $userRoot "ihagent"
        & git clone --local $RepoRoot $checkout
        if ($LASTEXITCODE -ne 0) { throw "git clone of the repo failed" }
        $browsers = Join-Path $userRoot "ms-playwright"
        $runner = Join-Path $userRoot "e2e.ps1"
        $runnerBody = @'
$ErrorActionPreference = "Stop"
Remove-Item Env:IHAGENT_INSTALL_LIBRARY -ErrorAction SilentlyContinue
Remove-Item Env:IHAGENT_INSTALL_FAKE_USERPATH -ErrorAction SilentlyContinue
Remove-Item Env:IHAGENT_INSTALL_FORCE_WINDOWS -ErrorAction SilentlyContinue
Remove-Item Env:IHAGENT_INSTALL_PYTHON -ErrorAction SilentlyContinue
Remove-Item Env:IHAGENT_HOME -ErrorAction SilentlyContinue
Remove-Item Env:IHAGENT_REPO_URL -ErrorAction SilentlyContinue
$env:USERPROFILE = $env:IHAGENT_TEST_HOME
[Environment]::SetEnvironmentVariable("PATH", $env:IHAGENT_TEST_ORIGINAL_PATH, "Process")
$env:PATH = $env:IHAGENT_TEST_ORIGINAL_PATH
$env:PLAYWRIGHT_BROWSERS_PATH = $env:IHAGENT_TEST_BROWSERS
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$code = Get-Content -Raw -LiteralPath $env:IHAGENT_TEST_INSTALLER
Invoke-Expression $code
if ($script:ScriptPath) { throw "iex set ScriptPath to $($script:ScriptPath)" }
$bin = Join-Path $env:USERPROFILE ".local/bin"
$sep = [IO.Path]::PathSeparator
$processPath = [Environment]::GetEnvironmentVariable("PATH")
$found = $false
foreach ($entry in ($processPath -split [regex]::Escape([string]$sep))) {
    if ($entry -eq $bin) { $found = $true }
}
if (-not $found) { throw "session PATH missing $bin : $processPath" }
$version = & (Join-Path $bin "ihagent") --version
if ($LASTEXITCODE -ne 0) { throw "ihagent --version failed" }
if ("$version" -notmatch "0\.4\.0") { throw "version [$version]" }
Write-Host "E2E_OK=$version"
'@
        $utf8 = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText($runner, ($runnerBody -replace "`r`n", "`n"), $utf8)
        $env:IHAGENT_TEST_HOME = $userRoot
        $env:IHAGENT_TEST_INSTALLER = $Installer
        $env:IHAGENT_TEST_ORIGINAL_PATH = $script:OriginalPath
        $env:IHAGENT_TEST_BROWSERS = $browsers
        & $Pwsh -NoProfile -File $runner
        if ($LASTEXITCODE -ne 0) { throw "end-to-end installer failed with exit $LASTEXITCODE" }
    }
}

Write-Host ""
Write-Host "$script:Passed passed, $($script:Failures.Count) failed"
if ($script:Failures.Count -gt 0) {
    foreach ($item in $script:Failures) { Write-Host " - $item" }
    exit 1
}
exit 0
