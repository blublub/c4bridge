# c4bridge als Windows-Dienst einrichten (WinSW 2.12). Rechtsklick → „Mit PowerShell ausführen“ reicht: das Skript
# fragt selbst nach Administratorrechten (UAC). Kein Passwort nötig – der Dienst läuft als SYSTEM.
# Danach darf DEIN Benutzer den Dienst ohne Admin starten/stoppen (sc start/stop c4bridge, Admin › Neustart).
param([switch]$Ja,    # -Ja: laufende Bridge ohne Rückfrage beenden
      [string]$Profil = $env:USERPROFILE,   # vom ersten Durchlauf (ohne Admin) übergeben
      [string]$Sid = '')
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $here 'c4bridge-dienst.exe'
$xml = Join-Path $here 'c4bridge-dienst.xml'
$hash = '05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da'   # WinSW-x64.exe v2.12.0 (GitHub)

$me = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$me).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # neu starten mit Admin-Rechten; Benutzerordner und SID des aufrufenden Benutzers mitgeben
    $args_ = "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -Profil `"$env:USERPROFILE`" -Sid $($me.User.Value)"
    if ($Ja) { $args_ += ' -Ja' }
    Start-Process powershell -Verb RunAs -ArgumentList $args_
    return
}
# (zweiter Durchlauf, mit Admin-Rechten – oder gleich als Admin gestartet)
if (-not $Sid) { $Sid = $me.User.Value }

function Pause-Ende($msg) { Write-Host "`n$msg"; Read-Host 'Enter zum Schließen' | Out-Null }

try {
    if ((Get-FileHash $exe -Algorithm SHA256).Hash.ToLower() -ne $hash) { throw "c4bridge-dienst.exe hat nicht die erwartete Prüfsumme – abgebrochen." }
    if (-not (Test-Path "$Profil\.config\c4bridge\config.env")) { throw "Keine config.env unter $Profil\.config\c4bridge – abgebrochen." }
    if (Get-Service c4bridge -ErrorAction SilentlyContinue) { throw "Dienst c4bridge gibt es schon (erst uninstall.ps1)." }

    # Konfiguration aus der Vorlage (Benutzerordner einsetzen)
    (Get-Content (Join-Path $here 'c4bridge-dienst.vorlage.xml') -Raw -Encoding UTF8).Replace('{PROFIL}', $Profil) |
        Set-Content $xml -Encoding UTF8

    # Läuft die Bridge noch von Hand (Port 8080)? Dann vorher beenden – sonst ist der Port belegt.
    $busy = Get-NetTCPConnection -LocalPort 8080, 8443, 8090 -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        $pids = $busy.OwningProcess | Sort-Object -Unique
        $procs = $pids | ForEach-Object { Get-CimInstance Win32_Process -Filter "ProcessId=$_" }
        Write-Host 'Diese Prozesse belegen die Ports der Bridge:'
        $procs | ForEach-Object { $c = ($_.CommandLine -replace '\s+', ' '); Write-Host ("  PID {0}  {1}" -f $_.ProcessId, $c.Substring(0, [Math]::Min(100, $c.Length))) }
        if (-not $Ja -and (Read-Host 'Beenden und Dienst einrichten? (j/n)') -ne 'j') { throw 'Abgebrochen – nichts geändert.' }
        $pids | ForEach-Object { & taskkill /F /T /PID $_ | Out-Null }
        # Erkennungsdienste der alten Bridge (whisper-server / stt_gpu.py) mit beenden
        Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'whisper-server.exe' -or $_.CommandLine -like '*stt_gpu.py*' } |
            ForEach-Object { & taskkill /F /T /PID $_.ProcessId | Out-Null }
        Start-Sleep 2
    }

    & $exe install
    if ($LASTEXITCODE -ne 0) { throw "WinSW install fehlgeschlagen ($LASTEXITCODE)" }

    # Deinem Benutzer Start/Stopp/Abfrage erlauben (ohne Admin): ACE für die SID ergänzen
    $sd = ((& sc.exe sdshow c4bridge) | Where-Object { $_ -match '^D:' }) -join ''
    $ace = "(A;;CCLCSWRPWPDTLOCRRC;;;$Sid)"
    if ($sd -and $sd -notmatch [regex]::Escape($Sid)) {
        $new = $sd -replace '^D:', "D:$ace"
        & sc.exe sdset c4bridge $new | Out-Null
    }

    Start-Service c4bridge
    Write-Host 'Dienst gestartet, warte auf die Bridge …'
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep 1
        try { Invoke-RestMethod http://127.0.0.1:8080/api/voice/status -TimeoutSec 2 | Out-Null; $ok = $true; break } catch {}
    }
    if ($ok) { Pause-Ende "Fertig: Dienst „c4bridge“ läuft (http://<PC>:8080, https://<PC>:8443). Logs: $here\logs" }
    else { Pause-Ende "Dienst eingerichtet, aber die Bridge antwortet nach 60 s noch nicht – siehe $here\logs\c4bridge-dienst.err.log" }
} catch {
    Pause-Ende "FEHLER: $($_.Exception.Message)"
}
