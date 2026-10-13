<#
  Cutzo update. Double-click "Update Cutzo.bat" in the Cutzo folder to run it.
  Gets the newest Cutzo from GitHub, then runs setup again (which reinstalls and reopens it).
  Your episodes, settings and library folder are kept.
#>
param(
    [switch]$NoPause,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $AppDir

function Stop-Update([string]$Text) {
    Write-Host ""
    Write-Host "Update stopped: $Text" -ForegroundColor Red
    Write-Host "Copy everything in this window into Claude and ask what to do next."
    if (-not $NoPause) { [void](Read-Host "Press Enter to close") }
    exit 1
}

Write-Host "Cutzo update" -ForegroundColor White
if (-not (Test-Path -LiteralPath (Join-Path $AppDir ".git"))) {
    Stop-Update "This Cutzo folder wasn't downloaded with git, so it can't update itself. Ask Claude to set it up with git."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Stop-Update "git isn't installed. Ask Claude to help install it."
}

Write-Host ""
Write-Host "== Getting the newest Cutzo from GitHub" -ForegroundColor Cyan
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$env:GIT_TERMINAL_PROMPT = "0"   # never sit waiting at a username prompt
try {
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        # Sign in through the GitHub app already set up on this PC, for this command only.
        & git -c "credential.helper=" -c "credential.helper=!gh auth git-credential" pull --ff-only | Out-Host
    } else {
        & git pull --ff-only | Out-Host
    }
    $code = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previous
}
if ($code -ne 0) {
    Stop-Update "Couldn't get the update. If this folder has local changes, ask Claude to sort them out."
}

$setupArgs = @{}
if ($NoPause) { $setupArgs["NoPause"] = $true }
if ($NoOpen) { $setupArgs["NoOpen"] = $true }
& (Join-Path $PSScriptRoot "setup.ps1") @setupArgs
exit $LASTEXITCODE
