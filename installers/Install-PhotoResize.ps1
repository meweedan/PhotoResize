# Installs PhotoResize.exe to Program Files and creates a Start Menu shortcut.
$ErrorActionPreference = "Stop"

$AppName   = "PhotoResize"
$ExeName   = "PhotoResize.exe"
$SourceExe = Join-Path $PSScriptRoot $ExeName
$TargetDir = Join-Path ${env:ProgramFiles} $AppName
$TargetExe = Join-Path $TargetDir $ExeName
$StartMenuDir = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"
$ShortcutPath = Join-Path $StartMenuDir "$AppName.lnk"

if (!(Test-Path $SourceExe)) {
  Write-Host "ERROR: $ExeName not found next to this installer." -ForegroundColor Red
  exit 1
}

New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

# Remove 'downloaded from internet' MOTW
Unblock-File -Path $SourceExe -ErrorAction SilentlyContinue

Copy-Item $SourceExe $TargetExe -Force

# Create Start Menu shortcut
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetExe
$Shortcut.WorkingDirectory = $TargetDir
$Shortcut.WindowStyle = 1
$Shortcut.Description = $AppName
$Shortcut.Save()

Write-Host "Installed to: $TargetExe"
Write-Host "Shortcut: $ShortcutPath"

Start-Process $TargetExe
