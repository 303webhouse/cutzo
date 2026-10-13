<#
  Cutzo setup. Double-click "Setup Cutzo.bat" in the Cutzo folder to run it.
  Safe to run again at any time: it repairs or updates whatever is already there.

  What it does:
    1. Makes Cutzo's own Python folder (.venv) using uv, and installs Cutzo into it.
    2. Picks the library folder (F:\Cutzo when the F: drive exists) and saves settings.json.
    3. Adds a Cutzo shortcut to the desktop and the Start menu.
    4. Adds an on-demand task, "Cutzo Engine", so Cutzo keeps running when Claude closes.
    5. Connects Cutzo to the Claude desktop app (after backing up Claude's settings file).
    6. Opens Cutzo.
  Nothing outside the Cutzo folder, the library folder, the two shortcuts, that task and
  Claude's settings file is changed. uv is installed first only if it is missing.
#>
param(
    [switch]$NoPause,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $AppDir

function Step([string]$Text) { Write-Host ""; Write-Host "== $Text" -ForegroundColor Cyan }
function Good([string]$Text) { Write-Host "   $Text" -ForegroundColor Green }
function Note([string]$Text) { Write-Host "   $Text" -ForegroundColor Yellow }

function Stop-Setup([string]$Text) {
    Write-Host ""
    Write-Host "Setup stopped: $Text" -ForegroundColor Red
    Write-Host "Copy everything in this window into Claude and ask what to do next."
    if (-not $NoPause) { [void](Read-Host "Press Enter to close") }
    exit 1
}

function Invoke-Tool([string]$Exe, [string[]]$Arguments) {
    # Runs a program, shows its messages in this window, and returns its exit code.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments | Out-Host
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
            (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
            (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"),
            (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\uv.exe"))) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Get-Port {
    $settingsFile = Join-Path $AppDir "settings.json"
    if (Test-Path -LiteralPath $settingsFile) {
        try {
            $port = (Get-Content -LiteralPath $settingsFile -Raw | ConvertFrom-Json).port
            if ($port) { return [int]$port }
        } catch { }
    }
    return 8765
}

function Test-VenvHealthy([string]$VenvDir, [string]$PythonHome) {
    # A usable .venv has python.exe and pythonw.exe, runs Python 3.12, and is built on the
    # Python inside the Cutzo folder. A Python kept in AppData can be invisible to Windows
    # outside the app that installed it (the Claude app and the Microsoft Store Python both
    # keep private copies of AppData), and then the desktop shortcut can't start Cutzo.
    if (-not (Test-Path -LiteralPath (Join-Path $VenvDir "Scripts\python.exe"))) { return $false }
    if (-not (Test-Path -LiteralPath (Join-Path $VenvDir "Scripts\pythonw.exe"))) { return $false }
    $cfg = Join-Path $VenvDir "pyvenv.cfg"
    if (-not (Test-Path -LiteralPath $cfg)) { return $false }
    $text = Get-Content -LiteralPath $cfg -Raw
    if (-not $text.ToLower().Contains($PythonHome.ToLower())) { return $false }
    if ($text -notmatch "(?m)^\s*(version|version_info)\s*=\s*3\.12\.") { return $false }
    return $true
}

try {

Write-Host "Cutzo setup" -ForegroundColor White
Write-Host "Cutzo folder: $AppDir"

# --- 0. Stop a running copy of Cutzo so it can be updated -------------------------
try {
    $port = Get-Port
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/shutdown" -Body "{}" `
        -ContentType "application/json" -Headers @{ "X-Cutzo-Source" = "app" } -TimeoutSec 3 | Out-Null
    Note "Closed the copy of Cutzo that was running."
    Start-Sleep -Seconds 2
} catch { }

# --- 1. Python folder and Cutzo itself -------------------------------------------
Step "Getting Cutzo's own Python ready"
$Uv = Find-Uv
if (-not $Uv) {
    Note "uv (a small tool that manages Python) isn't installed. Installing it now..."
    try {
        Invoke-Expression (Invoke-RestMethod "https://astral.sh/uv/install.ps1")
    } catch {
        Stop-Setup "Couldn't install uv. Check the internet connection and run Setup Cutzo again."
    }
    $Uv = Find-Uv
    if (-not $Uv) { Stop-Setup "uv was installed but can't be found. Restart the computer and run Setup Cutzo again." }
}
Good "Using uv at $Uv"

$Venv = Join-Path $AppDir ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$VenvPyw = Join-Path $Venv "Scripts\pythonw.exe"
# Cutzo's own copy of Python lives in the Cutzo folder, where every program can see it.
$PythonHome = Join-Path $AppDir ".python"
$env:UV_PYTHON_INSTALL_DIR = $PythonHome
if (-not (Test-VenvHealthy $Venv $PythonHome)) {
    if (Test-Path -LiteralPath $Venv) { Note "Cutzo's Python folder needs rebuilding; doing that now." }
    $code = Invoke-Tool $Uv @("venv", "--clear", "--python", "3.12", "--python-preference", "only-managed", $Venv)
    if ($code -ne 0) {
        Stop-Setup "Couldn't make Cutzo's Python folder (.venv). If the Claude desktop app is open, quit it completely and run Setup Cutzo again."
    }
    Good "Made Cutzo's Python folder."
} else {
    Good "Cutzo's Python folder is already there."
}

Step "Installing Cutzo (about a minute the first time)"
$code = Invoke-Tool $Uv @("pip", "install", "--python", $VenvPy, "--editable", $AppDir)
if ($code -ne 0) { Stop-Setup "Installing Cutzo's parts failed. Check the internet connection and run Setup Cutzo again." }
if (-not (Test-Path -LiteralPath $VenvPyw)) { Stop-Setup "pythonw.exe is missing from $Venv. Delete the .venv folder and run Setup Cutzo again." }
Good "Installed."

# --- 2. Library folder ------------------------------------------------------------
Step "Choosing where Cutzo keeps its work"
$SettingsFile = Join-Path $AppDir "settings.json"
$Library = $null
if (Test-Path -LiteralPath $SettingsFile) {
    try { $Library = (Get-Content -LiteralPath $SettingsFile -Raw | ConvertFrom-Json).library_folder } catch { }
}
if ($Library) {
    Good "Keeping your settings. Library folder: $Library"
} else {
    if (Test-Path -LiteralPath "F:\") {
        $Library = "F:\Cutzo"
    } else {
        $Library = Join-Path $env:USERPROFILE "Cutzo"
        Note "There's no F: drive, so Cutzo will use $Library. You can change library_folder in settings.json."
    }
    $json = [ordered]@{ library_folder = $Library; port = 8765 } | ConvertTo-Json
    [System.IO.File]::WriteAllText($SettingsFile, $json, (New-Object System.Text.UTF8Encoding($false)))
    Good "Library folder: $Library"
}
New-Item -ItemType Directory -Force -Path $Library | Out-Null

# --- 3. Shortcuts -----------------------------------------------------------------
Step "Adding Cutzo to the desktop and the Start menu"
$Icon = Join-Path $AppDir "cutzo\web\cutzo.ico"
$Shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    if (-not $folder) { continue }
    $link = $Shell.CreateShortcut((Join-Path $folder "Cutzo.lnk"))
    $link.TargetPath = $VenvPyw
    $link.Arguments = "-m cutzo"
    $link.WorkingDirectory = $AppDir
    $link.IconLocation = "$Icon,0"
    $link.Description = "Open Cutzo"
    $link.Save()
    Good "Shortcut: $(Join-Path $folder 'Cutzo.lnk')"
}

# --- 3b. Background task ----------------------------------------------------------
# Cutzo's engine is started through this task so it keeps running when whatever started
# it (the shortcut, or Claude's connector) closes. It runs only on demand, as you.
Step "Letting Cutzo keep running in the background"
try {
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $VenvPyw -Argument "-m cutzo.engine" -WorkingDirectory $AppDir
    $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName "Cutzo Engine" -Action $action -Settings $taskSettings -Principal $principal `
        -Description "Starts Cutzo's engine when Cutzo or Claude needs it." -Force | Out-Null
    Good "Added the task Cutzo Engine (starts only when Cutzo or Claude needs it)."
} catch {
    Note "Couldn't add the background task ($($_.Exception.Message)). Cutzo still works, but may stop when Claude closes."
}

# --- 4. Claude connector ----------------------------------------------------------
Step "Connecting Cutzo to the Claude desktop app"
$code = Invoke-Tool $VenvPy @("-m", "cutzo.connector_install")
if ($code -ne 0) { Note "The connector wasn't added. Cutzo still works on its own; ask Claude to help connect it." }

# --- 5. Check and open ------------------------------------------------------------
Step "Checking Cutzo"
$code = Invoke-Tool $VenvPy @("-c", "import cutzo, cutzo.engine, cutzo.mcp_server; print('   Cutzo ' + cutzo.__version__ + ' is ready.')")
if ($code -ne 0) { Stop-Setup "Cutzo is installed but doesn't start." }

if (-not $NoOpen) {
    Start-Process -FilePath $VenvPyw -ArgumentList @("-m", "cutzo") -WorkingDirectory $AppDir
    Good "Opening Cutzo in your browser."
}

Write-Host ""
Write-Host "All set." -ForegroundColor Green
Write-Host " - Next time, double-click Cutzo on your desktop."
Write-Host " - Quit the Claude desktop app completely and open it again, so it picks up the Cutzo connector."
Write-Host "   Then ask Claude: List my Cutzo episodes."
if (-not $NoPause) { [void](Read-Host "Press Enter to close") }
exit 0

} catch {
    Stop-Setup $_.Exception.Message
}
