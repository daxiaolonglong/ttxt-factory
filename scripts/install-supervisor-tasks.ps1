# Install the unattended watchdog task (P31; trigger root-cause fix 2026-11-18).
# Root cause: the old `schtasks /sc daily /st 00:01 /ri 5 /du 23:59` repetition
# window only applied within the start boundary DAY; with the unified
# scheduling engine the task degraded to once-daily after day one (observed:
# supervisor offline 5.7h with nobody to bring it back). Now the task XML uses
# Repetition Interval=PT5M with no duration end, repeating forever.
# Limitation (measured, not hidden): without admin rights only an
# InteractiveToken task can be registered, so auto-recovery works only while
# the user is logged on. --WithLogon (logon trigger) needs an elevated shell.
# Keep this file ASCII-only: Windows PowerShell 5 misparses UTF-8 w/o BOM.
param(
    [switch]$WithLogon,
    [string]$TaskName = "ttxt-supervisor-watchdog"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sid = (New-Object System.Security.Principal.NTAccount(
    [System.Environment]::UserName)).Translate(
    [System.Security.Principal.SecurityIdentifier]).Value
$boundary = (Get-Date).Date.ToString("yyyy-MM-ddT00:01:00")
$desc = [System.Text.Encoding]::UTF8.GetString(
    [byte[]](0xE5,0xA4,0xB4,0xE6,0x9D,0xA1,0xE6,0x99,0xBA,0xE8,0x83,0xBD,0xE5,0xB7,0xA5,0xE5,0x8E,0x82,0x3A,0xE6,0xAF,0x8F,0x20,0x35,0x20,0xE5,0x88,0x86,0xE9,0x92,0x9F,0xE5,0xB9,0x82,0xE7,0xAD,0x89,0xE6,0x8B,0x89,0xE8,0xB5,0xB7,0x20,0x74,0x74,0x78,0x74,0x20,0x73,0x75,0x70,0x65,0x72,0x76,0x69,0x73,0x6F,0x72,0x28,0xE5,0x8F,0xAA,0xE6,0x89,0x98,0xE7,0xAE,0xA1,0x20,0x73,0x63,0x68,0x65,0x64,0x75,0x6C,0x65,0x20,0x2D,0x2D,0x64,0x61,0x65,0x6D,0x6F,0x6E,0x20,0xE4,0xB8,0x8E,0x20,0x77,0x6F,0x72,0x6B,0x65,0x72,0x29))

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$desc</Description>
    <URI>\$TaskName</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>$sid</UserId>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$boundary</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      <Repetition><Interval>PT5M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>$root\.venv\Scripts\pythonw.exe</Command>
      <Arguments>-X utf8 -m ttxt supervisor --ensure</Arguments>
      <WorkingDirectory>$root</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

$tmp = Join-Path $env:TEMP ($TaskName + ".xml")
[System.IO.File]::WriteAllText($tmp, $xml, [System.Text.Encoding]::Unicode)
schtasks /create /tn $TaskName /xml $tmp /f
if ($LASTEXITCODE -ne 0) { throw "task install failed exit=$LASTEXITCODE" }
Remove-Item $tmp -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "installed: $TaskName (idempotent --ensure every 5 min, repetition never ends)"
Write-Host "verify repetition : schtasks /query /tn `"$TaskName`" /xml   (must contain Repetition Interval=PT5M)"
Write-Host "run it right now  : schtasks /run /tn `"$TaskName`""

if ($WithLogon) {
    # requires elevated shell (non-admin measured: Access denied)
    schtasks /create /tn "ttxt-supervisor-logon" /tr "$root\.venv\Scripts\pythonw.exe -X utf8 -m ttxt supervisor --ensure" /sc onlogon /f
}
Write-Host ""
Write-Host "uninstall: scripts\uninstall-supervisor-tasks.cmd"
