# Read-only bridge to the launcher's identity validation. Never starts/stops a process.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Receipt,
    [Parameter(Mandatory)][string]$Serial
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$owner = (& (Join-Path $PSScriptRoot 'emulator.ps1') Stop -Receipt $Receipt -DryRun) |
    ConvertFrom-Json
if ($owner.status -cne 'ready' -or $owner.serial -cne $Serial) {
    throw 'Native validation requires a ready receipt for the explicit serial.'
}
function Get-VerifiedNativeProcess($Identity) {
    $live = Get-CimInstance Win32_Process -Filter "ProcessId = $($Identity.pid)"
    if ($null -eq $live) { return }
    if ([int]$live.ProcessId -ne $Identity.pid -or
        [datetimeoffset]$live.CreationDate.ToUniversalTime() -ne [datetimeoffset]$Identity.processStartTime -or
        [string]$live.CommandLine -cne $Identity.commandLine -or
        [string]$live.ExecutablePath -cne $Identity.executablePath -or
        [int]$live.ParentProcessId -ne $Identity.parentPid) {
        throw 'Emulator identity changed during validation.'
    }
    $live
}

$handles = @()
try {
    $liveIds = @()
    foreach ($identity in @($owner.processes)) {
        if ($null -eq (Get-VerifiedNativeProcess $identity)) { continue }
        $process = Get-Process -Id $identity.pid -ErrorAction Stop
        $handles += $process
        # Get-Process is lazy: pin the PID before rechecking identity and enumerating ports.
        $nativeHandle = $process.Handle
        if ($null -eq $nativeHandle -or $nativeHandle -eq [intptr]::Zero) {
            throw 'Cannot acquire emulator process handle.'
        }
        $live = Get-VerifiedNativeProcess $identity
        if ($null -eq $live -or $process.Id -ne $identity.pid -or $process.HasExited) {
            throw 'Emulator identity changed during validation.'
        }
        $liveIds += [int]$live.ProcessId
    }
    $listeners = @(Get-NetTCPConnection -State Listen |
        Where-Object { $_.LocalPort -in @($owner.port, ($owner.port + 1)) })
    if (-not $liveIds.Count -or
        @($listeners | ForEach-Object { $_.LocalPort } | Select-Object -Unique).Count -ne 2 -or
        @($listeners | Where-Object { $_.OwningProcess -notin $liveIds }).Count) {
        throw 'Both emulator ports must belong to live receipt-proven processes.'
    }
} finally {
    foreach ($process in $handles) { $process.Dispose() }
}
Write-Output 'owned'
