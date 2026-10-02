<#
.SYNOPSIS
Creates a private Windows test emulator, or stops only receipt-proven processes.
.DESCRIPTION
Requires Windows PowerShell 5.1+ and an official Android SDK. Install the official
Windows command-line tools ZIP from https://developer.android.com/studio.
For a fresh SDK with Java 17 on PATH (or JAVA_HOME pointing to it), these exact
PowerShell commands install the official Java-17-compatible command-line tools:

  $sdk = Join-Path $PWD 'build\android-sdk'
  New-Item -ItemType Directory -Force $sdk | Out-Null
  Invoke-WebRequest 'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip' -OutFile .\build\commandlinetools.zip
  Expand-Archive .\build\commandlinetools.zip "$sdk\cmdline-tools"
  Move-Item "$sdk\cmdline-tools\cmdline-tools" "$sdk\cmdline-tools\latest"
  & "$sdk\cmdline-tools\latest\bin\sdkmanager.bat" "--sdk_root=$sdk" --licenses
  & "$sdk\cmdline-tools\latest\bin\sdkmanager.bat" "--sdk_root=$sdk" 'platform-tools' 'emulator' 'system-images;android-34;default;x86_64'

Before Start, an external SDK adb server must already listen on 127.0.0.1:5037.
Coordinate with its owner: never use start-server against an existing server
(a mismatched client can kill/restart it). Only if no server exists, in a
separate PowerShell terminal with $sdk set to this same SDK, run:

  Remove-Item Env:ADB_SERVER_SOCKET,Env:ANDROID_ADB_SERVER_ADDRESS,Env:ANDROID_ADB_SERVER_PORT,Env:ADB_SERVER_PORT -ErrorAction SilentlyContinue
  & "$sdk\platform-tools\adb.exe" -L tcp:127.0.0.1:5037 server nodaemon

This foreground command binds or fails; it does not replace an existing server.
Leave it running, then in the original terminal:

  .\tools\emulator.ps1 Start -Sdk $sdk -Port 5580 -DryRun
  .\tools\emulator.ps1 Start -Sdk $sdk -Port 5580
  .\tools\emulator.ps1 Stop -Receipt '<receipt path printed by Start>'

Enable Windows Hypervisor Platform/virtualization if required by the official
emulator. Nothing is downloaded, licensed, or installed automatically.
Use -Image 'system-images;android-34;google_apis;x86_64' for Google APIs
    (install that package with sdkmanager first). RAM requested is 2048 MiB;
the SDK may raise its minimum: inspect emulator.stdout.log.

Fresh AVDs default to a PHYSICAL 1080x1920 display at density 420, matching the
native validation suite's fixed 1080x1920 profiles without wm size scaling.
Start accepts -Width / -Height (1..8192 pixels) and -Density (1..1000 dpi);
nondefault sizes are not compatible with those native suite profiles.
Only the newly created private AVD's config.ini is configured before launch;
existing AVDs and user data are never reused or reconfigured.

Receipt schemaVersion=1: status, serial, avd, port, sdk, repoRoot, runRoot,
avdHome, avdPath, scratch, receipt, image, pid, commandLine, processStartTime,
executablePath, and processes[]. Top-level identity is the launcher; processes[]
also records qemu children, each with pid, parentPid, processStartTime (UTC ISO
8601), commandLine (exact Win32_Process string), executablePath and role.
Consumers must require status=ready and verify live identities/port ownership;
a serial or a stale receipt alone is NOT ownership evidence. The launcher may
exit while a recorded qemu child remains alive. Receipts/logs/AVDs are retained.
avdConfig records the chosen physical display/skin config.ini keys in receipts
and DryRun plans. DryRun validates arguments without SDK execution or writes.
Start checks raw TCP host:version against the SDK's local adb version before
emulator startup and every adb probe. Missing, malformed, incompatible or timed
out servers are refused without issuing an adb server-management command.
Shared-server TOCTOU limitation: no handshake can lock the server between check
and use, or intercept later emulator-internal adb calls. Its owner must keep the
same compatible server running throughout; do not stop/replace it during a run.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('Start', 'Stop')][string]$Action,
    [string]$Sdk,
    [int]$Port,
    [ValidatePattern('^system-images;android-[0-9]+;(default|google_apis);x86_64$')]
    [string]$Image = 'system-images;android-34;default;x86_64',
    [ValidateRange(1, 8192)][int]$Width = 1080,
    [ValidateRange(1, 8192)][int]$Height = 1920,
    [ValidateRange(1, 1000)][int]$Density = 420,
    [string]$Receipt,
    [ValidateRange(10, 900)][int]$TimeoutSeconds = 240,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$privateRoot = Join-Path $repoRoot 'build\emulator'

function Restore-ProcessEnvironment([hashtable]$OriginalEnvironment) {
    foreach ($name in $OriginalEnvironment.Keys) {
        if ($null -eq $OriginalEnvironment[$name]) {
            # Newer PowerShell/.NET bind null to an empty string, not deletion.
            if (Test-Path -LiteralPath "Env:$name") {
                Remove-Item -LiteralPath "Env:$name" -ErrorAction Stop
            }
        } else {
            [Environment]::SetEnvironmentVariable($name, $OriginalEnvironment[$name], 'Process')
        }
    }
}

function Assert-Port([int]$Value) {
    if ($Value -lt 5554 -or $Value -gt 5682 -or $Value % 2) {
        throw 'Specify an even -Port in 5554..5682 (its adjacent adb port must also be free).'
    }
}

function Assert-FreePorts([int]$Value) {
    $network = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties()
    $occupied = @($network.GetActiveTcpListeners()) +
        @($network.GetActiveTcpConnections() | ForEach-Object { $_.LocalEndPoint })
    if ($occupied | Where-Object { $_.Port -in @($Value, ($Value + 1)) }) {
        throw "Ports $Value/$($Value + 1) occupied; refusing to attach or reuse."
    }
    $sockets = @()
    try {
        foreach ($address in @([Net.IPAddress]::Any, [Net.IPAddress]::IPv6Any)) {
            if ($address.AddressFamily -eq 'InterNetworkV6' -and -not [Net.Sockets.Socket]::OSSupportsIPv6) { continue }
            foreach ($number in @($Value, ($Value + 1))) {
                $socket = [Net.Sockets.Socket]::new($address.AddressFamily, 'Stream', 'Tcp')
                $sockets += $socket
                $socket.ExclusiveAddressUse = $true
                if ($address.AddressFamily -eq 'InterNetworkV6') { $socket.DualMode = $false }
                $socket.Bind([Net.IPEndPoint]::new($address, $number))
            }
        }
    } finally {
        foreach ($socket in $sockets) { $socket.Dispose() }
    }
}

function Get-LiveProcess([int]$ProcessId) {
    Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId"
}

function Convert-Identity($Live, [string]$Role) {
    [pscustomobject]@{
        pid = [int]$Live.ProcessId
        parentPid = [int]$Live.ParentProcessId
        processStartTime = $Live.CreationDate.ToUniversalTime().ToString('o')
        commandLine = [string]$Live.CommandLine
        executablePath = [string]$Live.ExecutablePath
        role = $Role
    }
}

function Test-EmulatorIdentity($Identity, $Owner) {
    if ([string]::IsNullOrWhiteSpace($Identity.executablePath) -or
        [string]::IsNullOrWhiteSpace($Identity.commandLine)) {
        return $false
    }
    $launcher = Join-Path $Owner.sdk 'emulator\emulator.exe'
    $qemuRoot = Join-Path $Owner.sdk 'emulator\qemu\windows-x86_64'
    $isLauncher = $Identity.executablePath -ieq $launcher
    $isQemu = (Split-Path $Identity.executablePath -Parent) -ieq $qemuRoot -and
        (Split-Path $Identity.executablePath -Leaf) -match '^qemu-system-x86_64(-headless)?\.exe$'
    $avdToken = '(?:^|\s)"?-avd"?\s+"?' + [regex]::Escape($Owner.avd) + '"?(?=\s|$)'
    $portToken = '(?:^|\s)"?-port"?\s+"?' + $Owner.port + '"?(?=\s|$)'
    ($isLauncher -or $isQemu) -and
        $Identity.commandLine -cmatch $avdToken -and $Identity.commandLine -cmatch $portToken
}

function Assert-LiveIdentity($Identity, $Owner) {
    if (-not (Test-EmulatorIdentity $Identity $Owner)) { throw 'Receipt has an invalid emulator identity.' }
    $live = Get-LiveProcess $Identity.pid
    if ($null -eq $live) { return }
    $actual = Convert-Identity $live $Identity.role
    # PowerShell 7.5+ deserializes ISO JSON timestamps as DateTime automatically.
    if ([datetimeoffset]$actual.processStartTime -ne [datetimeoffset]$Identity.processStartTime -or
        $actual.commandLine -cne $Identity.commandLine -or
        $actual.executablePath -cne $Identity.executablePath -or
        $actual.parentPid -ne $Identity.parentPid) {
        throw "Ownership mismatch for PID $($Identity.pid); refusing to touch it."
    }
    $live
}

function Save-Receipt($Owner) {
    $json = $Owner | ConvertTo-Json -Depth 8
    $staging = $Owner.receipt + '.new'
    [IO.File]::WriteAllText($staging, $json, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $staging -Destination $Owner.receipt -Force
}

function Get-AvdDisplayConfig(
    [ValidateRange(1, 8192)][int]$Width = 1080,
    [ValidateRange(1, 8192)][int]$Height = 1920,
    [ValidateRange(1, 1000)][int]$Density = 420
) {
    # Official avd/info.c accepts WxH as a directory-free skin; skin.path wins over skin.name.
    [ordered]@{
        'hw.lcd.width' = "$Width"
        'hw.lcd.height' = "$Height"
        'hw.lcd.density' = "$Density"
        'skin.name' = "${Width}x${Height}"
        'skin.path' = "${Width}x${Height}"
    }
}

function Set-FreshAvdDisplayConfig($Owner) {
    $expectedRun = Join-Path $privateRoot $Owner.avd
    if ($Owner.avd -cnotmatch '^edge_[a-f0-9]{32}$' -or
        $Owner.repoRoot -ine $repoRoot -or $Owner.runRoot -ine $expectedRun -or
        $Owner.avdHome -ine (Join-Path $expectedRun 'avd') -or
        $Owner.avdPath -ine (Join-Path $Owner.avdHome ($Owner.avd + '.avd')) -or
        $Owner.status -cne 'planned' -or $null -ne $Owner.pid -or @($Owner.processes).Count) {
        throw 'Display configuration requires a fresh, unstarted private AVD.'
    }
    $path = Join-Path $Owner.avdPath 'config.ini'
    $item = Get-Item -LiteralPath $path -Force
    if ($item.PSIsContainer) { throw 'Expected an AVD config.ini file.' }
    for ($ancestorPath = $path; $ancestorPath; $ancestorPath = Split-Path $ancestorPath -Parent) {
        $ancestor = Get-Item -LiteralPath $ancestorPath -Force
        if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Refusing AVD configuration through a reparse point.'
        }
    }
    $lines = [Collections.Generic.List[string]]::new()
    $seen = @{}
    foreach ($line in [IO.File]::ReadAllLines($path)) {
        if ($line -match '^\s*([^#;=\s]+)\s*=' -and $Owner.avdConfig.Contains($Matches[1])) {
            $key = $Matches[1]
            if (-not $seen.ContainsKey($key)) {
                $lines.Add("$key=$($Owner.avdConfig[$key])")
                $seen[$key] = $true
            }
        } else {
            $lines.Add($line)
        }
    }
    foreach ($key in $Owner.avdConfig.Keys) {
        if (-not $seen.ContainsKey($key)) { $lines.Add("$key=$($Owner.avdConfig[$key])") }
    }
    # Replace the file rather than write through a possible hard link.
    $staging = $path + '.' + [guid]::NewGuid().ToString('N') + '.new'
    $stream = [IO.File]::Open($staging, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($lines -join "`r`n") + "`r`n")
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Dispose()
        [IO.File]::Replace($staging, $path, [Management.Automation.Language.NullString]::Value)
    } finally {
        $stream.Dispose()
        if ([IO.File]::Exists($staging)) { [IO.File]::Delete($staging) }
    }
}

function Update-Children($Owner, [Diagnostics.Process]$LauncherHandle = $null) {
    # A retained Start-Process handle also proves an exited launcher's PID cannot be reused.
    foreach ($parent in @($Owner.processes)) {
        $process = $null
        try {
            if ($null -ne $LauncherHandle -and $parent.role -eq 'launcher' -and
                $LauncherHandle.Id -eq $parent.pid) {
                $nativeHandle = $LauncherHandle.Handle
                if ($null -eq $nativeHandle -or $nativeHandle -eq [intptr]::Zero) {
                    throw 'Cannot acquire emulator launcher handle.'
                }
                if ($null -eq (Assert-LiveIdentity $parent $Owner) -and -not $LauncherHandle.HasExited) {
                    throw 'Process changed during ownership validation.'
                }
            } else {
                $process = Get-OwnedProcess $parent $Owner
                if ($null -eq $process) { continue }
            }
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($parent.pid)")
            foreach ($child in $children) {
                if ($child.ProcessId -in @($Owner.processes | ForEach-Object { $_.pid })) { continue }
                $identity = Convert-Identity $child 'qemu'
                if ($identity.parentPid -eq $parent.pid -and (Test-EmulatorIdentity $identity $Owner) -and
                    [datetimeoffset]$identity.processStartTime -ge [datetimeoffset]$parent.processStartTime) {
                    $Owner.processes = @($Owner.processes) + @($identity)
                    Save-Receipt $Owner
                }
            }
        } finally {
            if ($null -ne $process) { $process.Dispose() }
        }
    }
}

function Get-OwnedProcess($Identity, $Owner) {
    if ($null -eq (Assert-LiveIdentity $Identity $Owner)) { return }
    $process = Get-Process -Id $Identity.pid -ErrorAction Stop
    try {
        $nativeHandle = $process.Handle
        if ($null -eq $nativeHandle -or $nativeHandle -eq [intptr]::Zero) {
            throw 'Cannot acquire emulator process handle.'
        }
        $live = Assert-LiveIdentity $Identity $Owner
        if ($null -eq $live -or $process.Id -ne $Identity.pid -or $process.HasExited) {
            throw 'Process changed during ownership validation.'
        }
        # Keep this verified PID pinned even if it exits before final child discovery.
        $process
    } catch {
        $process.Dispose()
        throw
    }
}

function Stop-Owned($Owner, [switch]$PlanOnly, $LauncherHandle = $null, $LaunchedAt = $null) {
    $handles = @()
    $parents = @{}
    $uncertain = $false
    try {
        if ($null -ne $LauncherHandle) {
            $nativeHandle = $LauncherHandle.Handle
            if ($null -eq $nativeHandle -or $nativeHandle -eq [intptr]::Zero) {
                throw 'Cannot acquire emulator launcher handle.'
            }
            $parent = @($Owner.processes | Where-Object { $_.role -eq 'launcher' })
            if ($parent.Count) {
                if ($parent.Count -ne 1 -or $parent[0].pid -ne $LauncherHandle.Id) {
                    throw 'Retained launcher identity mismatch.'
                }
                $live = Assert-LiveIdentity $parent[0] $Owner
                if ($null -eq $live -and -not $LauncherHandle.HasExited) {
                    throw 'Process changed during ownership validation.'
                }
                $parents[$LauncherHandle.Id] = $parent[0]
            } else {
                if ($null -eq $LaunchedAt) { throw 'Missing launcher creation bound for cleanup.' }
                $parents[$LauncherHandle.Id] = [pscustomobject]@{
                    pid = $LauncherHandle.Id; processStartTime = $LaunchedAt
                }
            }
            $handles += $LauncherHandle
        }
        # Verify the entire set before stopping anything; keep handles to prevent PID reuse races.
        foreach ($identity in @($Owner.processes)) {
            if ($parents.ContainsKey([int]$identity.pid)) { continue }
            $process = Get-OwnedProcess $identity $Owner
            if ($null -eq $process) { $uncertain = $true; continue }
            $handles += $process
            $parents[[int]$identity.pid] = $identity
        }
        if ($PlanOnly) { return }
        # Terminate each parent before its final snapshot; retain it until all descendants are checked.
        for ($index = 0; $index -lt $handles.Count; $index++) {
            $process = $handles[$index]
            if (-not $process.HasExited) {
                $process.Kill()
                if (-not $process.WaitForExit(10000)) { throw "PID $($process.Id) did not stop." }
            }
            $parent = $parents[[int]$process.Id]
            foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($process.Id)")) {
                $identity = Convert-Identity $child 'qemu'
                if ($identity.parentPid -ne $process.Id -or
                    -not (Test-EmulatorIdentity $identity $Owner) -or
                    [datetimeoffset]$identity.processStartTime -lt [datetimeoffset]$parent.processStartTime) {
                    continue
                }
                if ($parents.ContainsKey([int]$identity.pid)) { continue }
                if ($identity.pid -in @($Owner.processes | ForEach-Object { $_.pid })) {
                    throw 'Recorded child changed during cleanup.'
                }
                $Owner.processes = @($Owner.processes) + @($identity)
                Save-Receipt $Owner
                $childProcess = Get-OwnedProcess $identity $Owner
                if ($null -eq $childProcess) { $uncertain = $true; continue }
                $handles += $childProcess
                $parents[[int]$identity.pid] = $identity
            }
        }
        if ($uncertain) { throw 'Cleanup uncertain: an exited process had no retained handle for final child discovery.' }
        # An intermediate child may have exited before discovery. Never adopt an orphan by arguments alone.
        foreach ($live in @(Get-CimInstance Win32_Process -Filter "Name = 'emulator.exe' OR Name LIKE 'qemu-system-x86_64%.exe'")) {
            if (Test-EmulatorIdentity (Convert-Identity $live 'qemu') $Owner) {
                throw 'Cleanup uncertain: a matching emulator process remains without proven ancestry.'
            }
        }
    } finally {
        foreach ($process in $handles) {
            if ($process -ne $LauncherHandle) { $process.Dispose() }
        }
    }
}

function Test-OwnedPorts($Owner) {
    $handles = @()
    try {
        $liveIds = @()
        foreach ($identity in @($Owner.processes)) {
            if ($null -eq (Assert-LiveIdentity $identity $Owner)) { continue }
            $process = Get-Process -Id $identity.pid -ErrorAction Stop
            $handles += $process
            $nativeHandle = $process.Handle
            if ($null -eq $nativeHandle -or $nativeHandle -eq [intptr]::Zero) {
                throw 'Cannot acquire emulator process handle.'
            }
            $live = Assert-LiveIdentity $identity $Owner
            if ($null -eq $live -or $process.Id -ne $identity.pid -or $process.HasExited) {
                throw 'Emulator identity changed during validation.'
            }
            $liveIds += $identity.pid
        }
        if (-not $liveIds.Count) { throw 'Owned emulator exited before readiness.' }
        $listeners = @(Get-NetTCPConnection -State Listen |
            Where-Object { $_.LocalPort -in @($Owner.port, ($Owner.port + 1)) })
        if ($listeners | Where-Object { $_.OwningProcess -notin $liveIds }) {
            throw 'Emulator port belongs to an unproven process; refusing adb.'
        }
        @($listeners | ForEach-Object { $_.LocalPort } | Select-Object -Unique).Count -eq 2
    } finally {
        foreach ($process in $handles) { $process.Dispose() }
    }
}

function New-AdbStartInfo([string]$SdkPath, [string]$Arguments) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = Join-Path $SdkPath 'platform-tools\adb.exe'
    $info.Arguments = "-H 127.0.0.1 -P 5037 $Arguments"
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($name in @('ADB_SERVER_SOCKET', 'ANDROID_ADB_SERVER_ADDRESS',
        'ANDROID_ADB_SERVER_PORT', 'ADB_SERVER_PORT')) {
        $info.EnvironmentVariables.Remove($name)
    }
    $info
}

function Invoke-AdbOutput(
    [Diagnostics.ProcessStartInfo]$Info, [int]$LimitMilliseconds, [switch]$AllowFailure
) {
    if ($LimitMilliseconds -le 0) { throw 'adb client deadline expired.' }
    $probe = [Diagnostics.Process]::new()
    $started = $false
    $probe.StartInfo = $Info
    try {
        $started = $probe.Start()
        $output = $probe.StandardOutput.ReadToEndAsync()
        $errors = $probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit($LimitMilliseconds)) {
            $probe.Kill() # This handle is our adb client, never the shared adb server.
            $null = $probe.WaitForExit(1000)
            if ($AllowFailure) { return '' }
            throw 'adb client timed out.'
        }
        if ($probe.ExitCode -ne 0) {
            if ($AllowFailure) { return '' }
            throw "adb client failed (exit $($probe.ExitCode))."
        }
        $output.GetAwaiter().GetResult()
    } finally {
        if ($started -and -not $probe.HasExited) { $probe.Kill() }
        $probe.Dispose()
    }
}

function Convert-AdbProtocol([string]$VersionText) {
    $versions = [regex]::Matches($VersionText, '(?m)^Android Debug Bridge version 1\.0\.([0-9]+)\r?$')
    [uint32]$protocol = 0
    if ($versions.Count -ne 1 -or
        -not [uint32]::TryParse($versions[0].Groups[1].Value, [ref]$protocol) -or
        $protocol -eq 0 -or $protocol -gt 65535) {
        throw 'Malformed local adb version; expected Android Debug Bridge version 1.0.<decimal protocol>.'
    }
    $protocol
}

function Read-AdbFrameBytes($Stream, [int]$Count, $Clock, [int]$LimitMilliseconds) {
    $buffer = [byte[]]::new($Count)
    $offset = 0
    while ($offset -lt $Count) {
        $remaining = $LimitMilliseconds - [int]$Clock.ElapsedMilliseconds
        if ($remaining -le 0) { throw 'Raw adb handshake timed out.' }
        $Stream.ReadTimeout = $remaining
        try {
            $read = $Stream.Read($buffer, $offset, $Count - $offset)
        } catch [IO.IOException] {
            if ($_.Exception.InnerException -is [Net.Sockets.SocketException] -and
                $_.Exception.InnerException.SocketErrorCode -eq [Net.Sockets.SocketError]::TimedOut) {
                throw 'Raw adb handshake timed out.'
            }
            throw
        }
        if ($read -eq 0) { throw 'Malformed/truncated adb host:version response.' }
        $offset += $read
    }
    [Text.Encoding]::ASCII.GetString($buffer)
}

function Assert-AdbServerProtocol(
    [uint32]$Protocol,
    [ValidateRange(1, 60000)][int]$LimitMilliseconds = 3000,
    [ValidateRange(1, 65535)][int]$ServerPort = 5037
) {
    # Only the raw protocol helper accepts a test port; launcher callers always use 5037.
    $client = [Net.Sockets.TcpClient]::new([Net.Sockets.AddressFamily]::InterNetwork)
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $pending = $null
    try {
        $pending = $client.BeginConnect([Net.IPAddress]::Loopback, $ServerPort, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne($LimitMilliseconds)) {
            throw 'Raw adb connection timed out.'
        }
        $client.EndConnect($pending)
        $stream = $client.GetStream()
        $remaining = $LimitMilliseconds - [int]$clock.ElapsedMilliseconds
        if ($remaining -le 0) { throw 'Raw adb handshake timed out.' }
        $stream.WriteTimeout = $remaining
        $request = [Text.Encoding]::ASCII.GetBytes('000chost:version')
        $stream.Write($request, 0, $request.Length)
        $status = Read-AdbFrameBytes $stream 4 $clock $LimitMilliseconds
        if ($status -cne 'OKAY') { throw "Malformed/rejected adb host:version status: $status" }
        $length = Read-AdbFrameBytes $stream 4 $clock $LimitMilliseconds
        if ($length -cne '0004') { throw 'Malformed adb host:version length; expected 0004.' }
        $version = Read-AdbFrameBytes $stream 4 $clock $LimitMilliseconds
        if ($version -cnotmatch '\A[0-9a-fA-F]{4}\z') { throw 'Malformed adb host:version hexadecimal protocol.' }
        $actual = [Convert]::ToUInt32($version, 16)
        if ($actual -ne $Protocol) {
            throw "adb protocol mismatch: SDK decimal $Protocol, server hex $version (decimal $actual)."
        }
    } catch {
        throw "Refusing adb/emulator use: existing external server at 127.0.0.1:$ServerPort unavailable or unsafe. $($_.Exception.Message) No server was started/stopped; see inline help."
    } finally {
        $client.Dispose()
        if ($null -ne $pending) { $pending.AsyncWaitHandle.Close() }
    }
}

function Assert-CompatibleAdbServer([string]$SdkPath, [int]$LimitMilliseconds = 3000) {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    # `version` is local-only: unlike devices/shell it never contacts or starts a server.
    $version = Invoke-AdbOutput (New-AdbStartInfo $SdkPath 'version') $LimitMilliseconds
    $protocol = Convert-AdbProtocol $version
    $remaining = $LimitMilliseconds - [int]$clock.ElapsedMilliseconds
    if ($remaining -le 0) { throw 'adb compatibility check timed out before raw handshake.' }
    Assert-AdbServerProtocol -Protocol $protocol -LimitMilliseconds $remaining
}

function Invoke-BootProbe($Owner, [int]$LimitMilliseconds) {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $info = New-AdbStartInfo $Owner.sdk "-s $($Owner.serial) shell getprop sys.boot_completed"
    Assert-CompatibleAdbServer $Owner.sdk $LimitMilliseconds
    $remaining = $LimitMilliseconds - [int]$clock.ElapsedMilliseconds
    (Invoke-AdbOutput $info $remaining -AllowFailure).Trim() -eq '1'
}

if ($Action -eq 'Stop') {
    if (-not $Receipt) { throw 'Stop requires -Receipt; no serial/PID-only stopping is supported.' }
    if ($PSBoundParameters.ContainsKey('Sdk') -or $PSBoundParameters.ContainsKey('Port') -or
        $PSBoundParameters.ContainsKey('Image') -or $PSBoundParameters.ContainsKey('Width') -or
        $PSBoundParameters.ContainsKey('Height') -or $PSBoundParameters.ContainsKey('Density')) {
        throw 'Stop takes its SDK, AVD and port only from -Receipt; display options are Start-only.'
    }
    $receiptPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Receipt)
    $owner = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Port $owner.port
    $expectedRun = Join-Path $privateRoot $owner.avd
    if ($owner.schemaVersion -ne 1 -or $owner.avd -cnotmatch '^edge_[a-f0-9]{32}$' -or
        $owner.repoRoot -ine $repoRoot -or $owner.runRoot -ine $expectedRun -or
        $owner.receipt -ine (Join-Path $expectedRun 'owner.json') -or
        $receiptPath -ine $owner.receipt -or
        $owner.avdHome -ine (Join-Path $expectedRun 'avd') -or
        $owner.avdPath -ine (Join-Path $owner.avdHome ($owner.avd + '.avd')) -or
        $owner.scratch -ine (Join-Path $expectedRun 'scratch') -or
        $owner.serial -cne "emulator-$($owner.port)" -or -not @($owner.processes).Count) {
        throw 'Invalid receipt schema or private repository roots.'
    }
    $first = $owner.processes[0]
    if ($owner.pid -ne $first.pid -or $owner.commandLine -cne $first.commandLine -or
        $owner.processStartTime -cne $first.processStartTime -or
        $owner.executablePath -cne $first.executablePath -or $first.role -cne 'launcher') {
        throw 'Receipt launcher identity is inconsistent.'
    }
    $seen = @{}
    foreach ($identity in @($owner.processes)) {
        if ($identity.pid -le 0 -or $seen.ContainsKey([int]$identity.pid) -or
            $identity.role -notin @('launcher', 'qemu')) { throw 'Invalid receipt process set.' }
        if ($identity -ne $first -and ($identity.role -ne 'qemu' -or
            -not $seen.ContainsKey([int]$identity.parentPid) -or
            [datetimeoffset]$identity.processStartTime -lt
                [datetimeoffset]$seen[[int]$identity.parentPid].processStartTime)) {
            throw 'Receipt child is not descended from its launcher.'
        }
        $seen[[int]$identity.pid] = $identity
    }
    if ($DryRun) {
        Stop-Owned $owner -PlanOnly
        $owner | ConvertTo-Json -Depth 8
        return
    }
    Stop-Owned $owner
    $owner.status = 'stopped'
    Save-Receipt $owner
    $owner | ConvertTo-Json -Depth 8
    return
}

if ($Receipt) { throw 'Start generates its own unique receipt; -Receipt is only for Stop.' }
Assert-Port $Port
if (-not $Sdk) { $Sdk = $env:ANDROID_HOME }
if (-not $Sdk) { $Sdk = $env:ANDROID_SDK_ROOT }
if (-not $Sdk) { throw 'Specify -Sdk or set ANDROID_HOME / ANDROID_SDK_ROOT.' }
$Sdk = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Sdk).TrimEnd('\')
if ($Sdk -match '["%!\r\n]') { throw 'SDK path contains characters unsafe for avdmanager.bat invocation.' }
$avd = 'edge_' + [guid]::NewGuid().ToString('N')
$runRoot = Join-Path $privateRoot $avd
$owner = [pscustomobject][ordered]@{
    schemaVersion = 1; status = 'planned'; serial = "emulator-$Port"; avd = $avd
    port = $Port; sdk = $Sdk; repoRoot = $repoRoot; runRoot = $runRoot
    avdHome = Join-Path $runRoot 'avd'
    avdPath = Join-Path $runRoot "avd\$avd.avd"
    scratch = Join-Path $runRoot 'scratch'
    receipt = Join-Path $runRoot 'owner.json'
    image = $Image; pid = $null; commandLine = $null; processStartTime = $null
    avdConfig = Get-AvdDisplayConfig -Width $Width -Height $Height -Density $Density
    executablePath = Join-Path $Sdk 'emulator\emulator.exe'
    processes = @()
    avdmanager = Join-Path $Sdk 'cmdline-tools\latest\bin\avdmanager.bat'
    createArguments = @('create', 'avd', '--name', $avd, '--package', $Image)
    launchArguments = @('-avd', $avd, '-port', "$Port", '-cores', '2', '-memory', '2048',
        '-gpu', 'swiftshader_indirect', '-no-snapshot', '-no-window', '-no-audio', '-no-boot-anim', '-no-metrics')
}
Assert-FreePorts $Port
if ($DryRun) { $owner | ConvertTo-Json -Depth 8; return }
foreach ($required in @($owner.executablePath, $owner.avdmanager,
    (Join-Path $Sdk 'platform-tools\adb.exe'),
    (Join-Path $Sdk (($Image.Replace(';', '\')) + '\package.xml')))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing SDK component: $required. See Get-Help .\tools\emulator.ps1 -Full for install commands."
    }
}

Assert-CompatibleAdbServer $Sdk
$environment = @{
    ANDROID_HOME = $Sdk; ANDROID_SDK_ROOT = $Sdk; ANDROID_AVD_HOME = $owner.avdHome
    ANDROID_USER_HOME = Join-Path $runRoot 'android-home'
    ANDROID_EMULATOR_HOME = Join-Path $runRoot 'emulator-home'
    TEMP = $owner.scratch; TMP = $owner.scratch
    ADB_SERVER_SOCKET = 'tcp:127.0.0.1:5037'
    ANDROID_ADB_SERVER_ADDRESS = '127.0.0.1'; ANDROID_ADB_SERVER_PORT = '5037'
    ADB_SERVER_PORT = '5037'
}
$originalEnvironment = @{}
$launcher = $null
$launchedAt = $null
try {
    foreach ($name in $environment.Keys) {
        $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process')
    }
    New-Item -ItemType Directory -Path $runRoot | Out-Null
    foreach ($directory in @($owner.avdHome, $owner.scratch, $env:ANDROID_USER_HOME, $env:ANDROID_EMULATOR_HOME)) {
        New-Item -ItemType Directory -Path $directory | Out-Null
    }
    Save-Receipt $owner
    'no' | & $owner.avdmanager @($owner.createArguments) 2>&1 |
        Out-File (Join-Path $runRoot 'avdmanager.log') -Encoding utf8
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $owner.avdPath 'config.ini'))) {
        throw "avdmanager failed (exit $LASTEXITCODE); see $runRoot\avdmanager.log"
    }
    Set-FreshAvdDisplayConfig $owner
    Assert-FreePorts $Port
    # The emulator can launch its own adb client; recheck after potentially slow AVD creation.
    Assert-CompatibleAdbServer $Sdk
    $launchedAt = [datetimeoffset]::UtcNow
    $launcher = Start-Process -FilePath $owner.executablePath -ArgumentList $owner.launchArguments -PassThru `
        -RedirectStandardOutput (Join-Path $runRoot 'emulator.stdout.log') `
        -RedirectStandardError (Join-Path $runRoot 'emulator.stderr.log')
    $null = $launcher.Handle
    $live = Get-LiveProcess $launcher.Id
    if ($null -eq $live) { throw 'Emulator launcher exited before identity capture.' }
    $identity = Convert-Identity $live 'launcher'
    if (-not (Test-EmulatorIdentity $identity $owner)) { throw 'Unexpected emulator launch command.' }
    $owner.pid = $identity.pid
    $owner.commandLine = $identity.commandLine
    $owner.processStartTime = $identity.processStartTime
    $owner.executablePath = $identity.executablePath
    $owner.processes = @($identity)
    $owner.status = 'starting'
    Save-Receipt $owner
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        Update-Children $owner $launcher
        $remaining = [int](1000 * $TimeoutSeconds - $clock.Elapsed.TotalMilliseconds)
        if ($remaining -le 0) { break }
        if ((Test-OwnedPorts $owner) -and (Invoke-BootProbe $owner ([Math]::Min(5000, $remaining)))) {
            Update-Children $owner $launcher
            if (-not (Test-OwnedPorts $owner)) { throw 'Emulator port ownership changed during readiness.' }
            $owner.status = 'ready'
            Save-Receipt $owner
            $owner | ConvertTo-Json -Depth 8
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Emulator boot timed out after $TimeoutSeconds seconds."
} catch {
    $failure = $_
    try {
        $owner.status = 'failed'
        Save-Receipt $owner
    } catch {
        Write-Warning "Could not complete failure receipt: $_"
    }
    try {
        Stop-Owned $owner -LauncherHandle $launcher -LaunchedAt $launchedAt
        Save-Receipt $owner
    } catch {
        Write-Warning "Cleanup could not safely complete: $_. Retained receipt: $($owner.receipt)"
    }
    throw "$failure Logs/receipt: $runRoot"
} finally {
    Restore-ProcessEnvironment $originalEnvironment
    if ($null -ne $launcher) { $launcher.Dispose() }
}
