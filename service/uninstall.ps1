# Windows-Dienst „c4bridge“ wieder entfernen (Programm, Daten und Einstellungen bleiben). Fragt selbst nach Admin.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$me = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$me).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    return
}
try {
    if (Get-Service c4bridge -ErrorAction SilentlyContinue) {
        Stop-Service c4bridge -ErrorAction SilentlyContinue
        & (Join-Path $here 'c4bridge-dienst.exe') uninstall
        Write-Host 'Dienst c4bridge entfernt. Von Hand starten: c4bridge\run.bat'
    } else { Write-Host 'Dienst c4bridge ist nicht eingerichtet.' }
} catch { Write-Host "FEHLER: $($_.Exception.Message)" }
Read-Host 'Enter zum Schließen' | Out-Null
