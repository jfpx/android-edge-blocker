"""Offline emulator host contracts; run: python -B -m unittest discover -s tests -p test_emulator.py -v.

Only public dry-runs and invalid requests execute the script. Ownership/readiness
tests extract AST fragments into memory and replace OS/SDK boundaries with mocks.
Raw adb handshakes use owned ephemeral loopback sockets, never a real adb server.
All synthetic files live in a uniquely owned, ignored build directory.
"""

import base64
import contextlib
import json
import os
from pathlib import Path
import random
import shutil
import socket
import subprocess
import threading
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "emulator.ps1"
FIELDS = {
    "schemaVersion", "status", "serial", "avd", "port", "sdk", "repoRoot",
    "runRoot", "avdHome", "avdPath", "scratch", "receipt", "image", "pid",
    "commandLine", "processStartTime", "executablePath", "processes",
}
IDENTITY_FIELDS = {
    "pid", "parentPid", "processStartTime", "commandLine", "executablePath", "role",
}


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@contextlib.contextmanager
def free_pair():
    """Reserve only our own loopback sockets, never interrogate/stop port owners."""
    ports = list(range(5554, 5683, 2))
    random.SystemRandom().shuffle(ports)
    for port in ports:
        sockets = []
        try:
            for number in (port, port + 1):
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sockets.append(listener)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                listener.bind(("127.0.0.1", number))
                listener.listen(1)
        except OSError:
            for listener in sockets:
                listener.close()
            continue
        try:
            yield port, sockets
        finally:
            for listener in sockets:
                listener.close()
        return
    raise unittest.SkipTest("No unused loopback port pair in 5554..5683")


@contextlib.contextmanager
def adb_server(responses, delay=0, hold=False):
    """Serve raw frames on an OS-assigned port, recording exactly what was sent."""
    requests, errors = [], []
    done = threading.Event()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    port = listener.getsockname()[1]

    def serve():
        try:
            for chunks in responses:
                while not done.is_set():
                    try:
                        connection, _ = listener.accept()
                        break
                    except socket.timeout:
                        continue
                else:
                    return
                with connection:
                    connection.settimeout(5)
                    request = b""
                    while len(request) < 16:
                        data = connection.recv(16 - len(request))
                        if not data:
                            break
                        request += data
                    requests.append(request)
                    for chunk in chunks:
                        if done.wait(delay):
                            return
                        try:
                            connection.sendall(chunk)
                        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                            break  # Expected if the guard rejected a partial/slow frame.
                    if hold:
                        done.wait(10)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port, requests
    finally:
        done.set()
        thread.join(6)
        listener.close()
        if thread.is_alive():
            raise AssertionError("Owned mock adb server did not stop")
        if errors:
            raise AssertionError(errors)


PARSE = r"""
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
"""

RESTORE_ENVIRONMENT = PARSE + r"""
$definition = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Restore-ProcessEnvironment'
}, $false))
if ($definition.Count -ne 1) { throw 'Expected one environment restoration helper' }
. ([scriptblock]::Create($definition[0].Extent.Text))
$startTry = @($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.TryStatementAst] -and $_.Extent.Text -match 'Start-Process'
})
if ($startTry.Count -ne 1) { throw 'Expected one Start try/finally' }
$restoreCall = @($startTry[0].Finally.Statements | Where-Object {
    $_.Extent.Text -ceq 'Restore-ProcessEnvironment $originalEnvironment'
})
if ($restoreCall.Count -ne 1) { throw 'Start finally must call the restoration helper' }
# Execute only the helper call, never Start, cleanup, or emulator operations.
$restore = [scriptblock]::Create($restoreCall[0].Extent.Text)
"""

# The ownership harness imports only these functions, mocking stopping, probes
# and receipt writes. The separate adb harness also imports guarded probes.
MOCKS = PARSE + r"""
$safe = @('Assert-Port', 'Assert-FreePorts', 'Convert-Identity',
    'Test-EmulatorIdentity', 'Assert-LiveIdentity', 'Get-OwnedProcess', 'Update-Children', 'Test-OwnedPorts')
foreach ($name in $safe) {
    $definitions = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst]
    }, $false) | Where-Object { $_.Name -eq $name })
    if ($definitions.Count -ne 1) { throw "Expected one definition: $name" }
    . ([scriptblock]::Create($definitions[0].Extent.Text))
}
function Get-CimInstance { throw 'Unmocked CIM access forbidden' }
function Get-LiveProcess { throw 'Unmocked live-process access forbidden' }
function Get-NetTCPConnection { throw 'Unmocked network query forbidden' }
function Get-Process([int]$Id, [string]$ErrorAction) {
    Check ($ErrorAction -ceq 'Stop') 'Process acquisition must fail closed'
    $process = [pscustomobject]@{ Id = $Id; Handle = [intptr]123; HasExited = $false }
    $process | Add-Member ScriptMethod Dispose {}
    $process
}
function Start-Process { throw 'Process launch forbidden' }
function Stop-Process { throw 'Process stopping forbidden' }
$script:saves = 0
$script:stops = 0
$script:probes = 0
$launcher = $null
function Save-Receipt($Owner) { $script:saves++ }
function Stop-Owned($Owner, [switch]$PlanOnly) {
    $script:stops++
    if (-not $PlanOnly) { throw 'Real stopping forbidden' }
}
function Invoke-BootProbe($Owner, [int]$LimitMilliseconds) {
    $script:probes++
    return $true
}
function Check($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Must-Throw([scriptblock]$Body, [string]$Pattern) {
    $caught = $null
    try { & $Body | Out-Null } catch { $caught = $_.ToString() }
    Check ($null -ne $caught) "Expected rejection: $Pattern"
    Check ($caught -match $Pattern) "Unexpected rejection: $caught"
}
$repoRoot = Split-Path (Split-Path $scriptPath -Parent) -Parent
$privateRoot = Join-Path $repoRoot 'build\emulator'
$avd = 'edge_' + ('a' * 32)
$runRoot = Join-Path $privateRoot $avd
$sdk = Join-Path $fixture 'nonexistent SDK with spaces'
$exe = Join-Path $sdk 'emulator\emulator.exe'
$qemuExe = Join-Path $sdk 'emulator\qemu\windows-x86_64\qemu-system-x86_64.exe'
$script:live = [pscustomobject]@{
    ProcessId = 2000000001; ParentProcessId = 2000000000
    CreationDate = [datetime]::Parse('2026-01-02T03:04:05Z').ToUniversalTime()
    CommandLine = ('"{0}" -avd "{1}" -port 5580' -f $exe, $avd)
    ExecutablePath = $exe
}
$identity = Convert-Identity $script:live 'launcher'
$owner = [pscustomobject]@{
    schemaVersion = 1; status = 'starting'; serial = 'emulator-5580'
    avd = $avd; port = 5580; sdk = $sdk; repoRoot = $repoRoot; runRoot = $runRoot
    avdHome = Join-Path $runRoot 'avd'
    avdPath = Join-Path $runRoot "avd\$avd.avd"
    scratch = Join-Path $runRoot 'scratch'; receipt = Join-Path $runRoot 'owner.json'
    image = 'system-images;android-34;default;x86_64'
    pid = $identity.pid; commandLine = $identity.commandLine
    processStartTime = $identity.processStartTime; executablePath = $identity.executablePath
    processes = @($identity)
}
function Get-LiveProcess([int]$ProcessId) {
    if ($ProcessId -eq $script:live.ProcessId) { $script:live }
}
function Readiness-Block {
    $loops = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.WhileStatementAst] -and
        $node.Extent.Text -match 'Invoke-BootProbe'
    }, $false))
    Check ($loops.Count -eq 1) 'Expected one readiness loop'
    [scriptblock]::Create($loops[0].Extent.Text)
}
"""


STOP_BLOCK = r"""
$branches = @($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.IfStatementAst] -and
    $_.Clauses[0].Item1.Extent.Text -match '\$Action\s+-eq\s+''Stop'''
})
Check ($branches.Count -eq 1) 'Expected one Stop branch'
$blockText = $branches[0].Clauses[0].Item2.Extent.Text
$stopBody = [scriptblock]::Create($blockText.Substring(1, $blockText.Length - 2))
$DryRun = $true
$PSBoundParameters = @{}
"""

DISPLAY_FUNCTIONS = MOCKS + r"""
foreach ($name in @('Get-AvdDisplayConfig', 'Set-FreshAvdDisplayConfig')) {
    $definition = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst]
    }, $false) | Where-Object Name -eq $name)
    Check ($definition.Count -eq 1) "Expected one display function: $name"
    . ([scriptblock]::Create($definition[0].Extent.Text))
}
$repoRoot = $fixture
$privateRoot = Join-Path $repoRoot 'display-config'
$avd = 'edge_' + [guid]::NewGuid().ToString('N')
$runRoot = Join-Path $privateRoot $avd
$owner = [pscustomobject]@{
    avd = $avd; repoRoot = $repoRoot; runRoot = $runRoot
    avdHome = Join-Path $runRoot 'avd'
    avdPath = Join-Path $runRoot "avd\$avd.avd"
    status = 'planned'; pid = $null; processes = @()
    avdConfig = Get-AvdDisplayConfig
}
New-Item -ItemType Directory -Path $owner.avdPath | Out-Null
$configPath = Join-Path $owner.avdPath 'config.ini'
"""

ADB_FUNCTIONS = MOCKS + r"""
$names = @('New-AdbStartInfo', 'Convert-AdbProtocol', 'Read-AdbFrameBytes',
    'Assert-AdbServerProtocol', 'Assert-CompatibleAdbServer', 'Invoke-BootProbe')
foreach ($name in $names) {
    $definition = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst]
    }, $false) | Where-Object Name -eq $name)
    Check ($definition.Count -eq 1) "Expected one adb function: $name"
    . ([scriptblock]::Create($definition[0].Extent.Text))
}
# Never import the real process executor, even when exercising a real TCP guard.
function Invoke-AdbOutput { throw 'Unmocked adb process forbidden' }
"""

NATIVE_OWNER_CHECK = MOCKS + r"""
$checkerPath = Join-Path (Split-Path $scriptPath -Parent) 'native_owner_check.ps1'
$checkerAst = [Management.Automation.Language.Parser]::ParseFile(
    $checkerPath, [ref]$tokens, [ref]$parseErrors)
Check ($parseErrors.Count -eq 0) 'Checker parse failure'
$statements = @($checkerAst.EndBlock.Statements)
$starts = @($statements | Where-Object {
    $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $_.Name -ceq 'Get-VerifiedNativeProcess'
})
Check ($starts.Count -eq 1) 'Expected one native identity helper'
$index = [array]::IndexOf($statements, $starts[0])
$checkBody = [scriptblock]::Create(
    ($statements[$index..($statements.Count - 1)] | ForEach-Object { $_.Extent.Text }) -join "`n")
# Extract only the checker body: never execute emulator Stop or acquire real processes.
Check ($checkBody.ToString() -notmatch '::GetProcessById|::new|Start-Process|Stop-Process') 'Unsafe boundary'
function Reset-NativeCheck {
    $script:nativeEvents = [Collections.Generic.List[string]]::new()
    $script:nativeHandles = [Collections.Generic.List[object]]::new()
    $script:nativeReads = @{}
    $script:nativeLive = @($script:live)
    $script:nativeAcquire = {}
    $script:nativeHandle = {}
    $script:nativeRecheck = {}
    $script:nativeListeners = {}
    $script:listenerOwner = $identity.pid
    $script:listenerPorts = @(5580, 5581)
}
function Assert-NativeRetained {
    foreach ($handle in $script:nativeHandles) {
        Check ($handle.Held -and -not $handle.Disposed) 'Handle not retained through validation'
    }
}
function Assert-NativeDisposed {
    foreach ($handle in $script:nativeHandles) {
        Check $handle.Disposed 'Leaked process object'
    }
}
function Get-CimInstance($ClassName, $Filter) {
    Check ($ClassName -ceq 'Win32_Process') 'Unexpected CIM class'
    Check ($Filter -match '^ProcessId = (\d+)$') 'Unexpected CIM filter'
    $processId = [int]$Matches[1]
    if (-not $script:nativeReads.ContainsKey($processId)) { $script:nativeReads[$processId] = 0 }
    $script:nativeReads[$processId]++
    $script:nativeEvents.Add("identity:$processId")
    if ($script:nativeReads[$processId] -eq 2) {
        Assert-NativeRetained
        & $script:nativeRecheck $processId
    }
    $script:nativeLive | Where-Object ProcessId -eq $processId
}
function Get-Process([int]$Id, [string]$ErrorAction) {
    Check ($ErrorAction -ceq 'Stop') 'Process acquisition must fail closed'
    $script:nativeEvents.Add("get:$Id")
    & $script:nativeAcquire $Id
    $handle = [pscustomobject]@{
        Id = $Id; Held = $false; Disposed = $false; HasExited = $false; HandleValue = [intptr]123
    }
    $handle | Add-Member ScriptProperty Handle {
        Check (-not $this.Disposed) 'Acquiring disposed process'
        $script:nativeEvents.Add("handle:$($this.Id)")
        & $script:nativeHandle $this
        $this.Held = $true
        $this.HandleValue
    }
    $handle | Add-Member ScriptMethod Dispose {
        Check (-not $this.Disposed) 'Process disposed twice'
        $this.Disposed = $true
        $script:nativeEvents.Add("dispose:$($this.Id)")
    }
    $script:nativeHandles.Add($handle)
    $handle
}
function Get-NetTCPConnection($State) {
    Check ($State -ceq 'Listen') 'Expected listener query'
    Assert-NativeRetained
    $script:nativeEvents.Add('listeners')
    & $script:nativeListeners
    foreach ($port in $script:listenerPorts) {
        $listener = [pscustomobject]@{ LocalPort = $port }
        $listener | Add-Member ScriptProperty OwningProcess {
            Assert-NativeRetained
            $script:nativeEvents.Add('validate')
            $script:listenerOwner
        }
        $listener
    }
}
Reset-NativeCheck
"""

EMULATOR_HANDLES = NATIVE_OWNER_CHECK + r"""
function Get-LiveProcess([int]$ProcessId) {
    Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId"
}
$checkBody = { Test-OwnedPorts $owner }
"""

STOP_HANDLES = EMULATOR_HANDLES + r"""
foreach ($name in @('Get-OwnedProcess', 'Stop-Owned')) {
    $definition = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst]
    }, $false) | Where-Object Name -eq $name)
    Check ($definition.Count -eq 1) "Expected one stop function: $name"
    Check ($definition[0].Extent.Text -notmatch '::GetProcessById|::new|Stop-Process') 'Unsafe stop boundary'
    . ([scriptblock]::Create($definition[0].Extent.Text))
}
$getIdentity = (Get-Item Function:Get-CimInstance).ScriptBlock
$getHandle = (Get-Item Function:Get-Process).ScriptBlock
$script:killHook = {}
$script:queryHook = {}
$script:waitResult = $true
$script:children = @()
$script:rootId = $identity.pid
$script:survivors = @()
function Get-CimInstance($ClassName, $Filter) {
    if ($Filter -eq "Name = 'emulator.exe' OR Name LIKE 'qemu-system-x86_64%.exe'") {
        Assert-NativeRetained
        $script:survivors
    } elseif ($Filter -match '^ParentProcessId = (\d+)$') {
        $parentId = [int]$Matches[1]
        Assert-NativeRetained
        $parentHandle = @($script:nativeHandles | Where-Object Id -eq $parentId)
        Check ($parentHandle.Count -eq 1 -and $parentHandle[0].HasExited) 'Child snapshot before parent exit'
        $script:nativeEvents.Add("children:$parentId")
        & $script:queryHook $parentId
        $script:children | Where-Object ParentProcessId -eq $parentId
    } else {
        & $getIdentity $ClassName $Filter
    }
}
function Get-Process([int]$Id, [string]$ErrorAction) {
    $handle = & $getHandle -Id $Id -ErrorAction $ErrorAction
    $handle | Add-Member ScriptMethod Kill {
        Assert-NativeRetained
        $script:nativeEvents.Add("kill:$($this.Id)")
        & $script:killHook $this
    }
    $handle | Add-Member ScriptMethod WaitForExit {
        param($Milliseconds)
        Check ($Milliseconds -eq 10000) 'Unbounded termination wait'
        Assert-NativeRetained
        $script:nativeEvents.Add("wait:$($this.Id)")
        if ($script:waitResult) {
            $this.HasExited = $true
            $script:nativeLive = @($script:nativeLive | Where-Object ProcessId -ne $this.Id)
        }
        $script:waitResult
    }
    $handle
}
function New-Child([int]$ProcessId, [int]$ParentId) {
    [pscustomobject]@{
        ProcessId = $ProcessId; ParentProcessId = $ParentId
        CreationDate = $script:live.CreationDate.AddSeconds(1)
        CommandLine = ('"{0}" -avd "{1}" -port 5580' -f $qemuExe, $avd)
        ExecutablePath = $qemuExe
    }
}
function Start-FailureBlock {
    $startTry = @($ast.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.TryStatementAst] -and $_.Extent.Text -match 'Start-Process'
    })[0]
    $body = $startTry.CatchClauses[0].Body.Extent.Text
    [scriptblock]::Create($body.Substring(1, $body.Length - 2))
}
"""


class EmulatorContracts:
    shell_name = None

    @classmethod
    def setUpClass(cls):
        if os.name != "nt":
            raise unittest.SkipTest("Windows PowerShell host tests")
        cls.shell = shutil.which(cls.shell_name)
        if not cls.shell:
            raise unittest.SkipTest(f"{cls.shell_name} is not installed")
        if not SCRIPT.is_file():
            raise AssertionError(f"Missing implementation: {SCRIPT}")
        cls.fixture = ROOT / "build" / ("emulator tests " + uuid.uuid4().hex)
        cls.fixture.mkdir(parents=True)
        cls.addClassCleanup(shutil.rmtree, cls.fixture)
        cls.sdk = cls.fixture / "nonexistent SDK with spaces"

    def tearDown(self):
        # Isolate display fixtures, including deferred NTFS directory timestamp updates.
        display_fixture = self.fixture / "display-config"
        if display_fixture.exists():
            shutil.rmtree(display_fixture)

    def ps(self, body, env=None, success=True):
        child_env = os.environ.copy()
        for name in list(child_env):
            if name.upper().startswith("ANDROID_"):
                del child_env[name]
        child_env.update({"TEMP": str(self.fixture), "TMP": str(self.fixture)})
        child_env.update(env or {})
        code = (
            "$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n"
            "$ProgressPreference = 'SilentlyContinue'\n"
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
            f"$scriptPath = {quote(SCRIPT)}\n$fixture = {quote(self.fixture)}\n"
            "try {\n" + body +
            "\n} catch { [Console]::Error.WriteLine($_.ToString()); exit 1 }\n"
        )
        # Stream AST harnesses in memory without Windows' command-line length limit.
        entry = (
            "[Console]::InputEncoding = [Text.UTF8Encoding]::new($false); "
            "& ([scriptblock]::Create([Console]::In.ReadToEnd()))"
        )
        encoded = base64.b64encode(entry.encode("utf-16le")).decode("ascii")
        result = subprocess.run(
            [self.shell, "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            cwd=self.fixture, env=child_env, capture_output=True, input=code,
            encoding="utf-8", errors="replace", timeout=45,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def json_ps(self, body, **kwargs):
        return json.loads(self.ps(body, **kwargs).stdout)

    def start(self, port, extra="", env=None):
        return self.json_ps(
            f"& $scriptPath Start -Port {port} -DryRun {extra}", env=env,
        )

    def assert_plan(self, plan, port, sdk, status="planned", root=ROOT):
        self.assertTrue(FIELDS <= plan.keys(), FIELDS - plan.keys())
        self.assertEqual(plan["schemaVersion"], 1)
        self.assertEqual(plan["status"], status)
        self.assertEqual(plan["port"], port)
        self.assertEqual(plan["serial"], f"emulator-{port}")
        self.assertRegex(plan["avd"], r"^edge_[a-f0-9]{32}$")
        self.assertEqual(Path(plan["sdk"]), sdk)
        self.assertEqual(Path(plan["repoRoot"]), root)
        run = root / "build" / "emulator" / plan["avd"]
        for field, path in {
            "runRoot": run, "avdHome": run / "avd",
            "avdPath": run / "avd" / (plan["avd"] + ".avd"),
            "scratch": run / "scratch", "receipt": run / "owner.json",
            "executablePath": sdk / "emulator" / "emulator.exe",
        }.items():
            self.assertEqual(Path(plan[field]), path, field)
        if status == "planned":
            for field in ("pid", "commandLine", "processStartTime"):
                self.assertIsNone(plan[field], field)
            self.assertEqual(plan["processes"], [])
            for field in ("runRoot", "avdHome", "avdPath", "scratch", "receipt"):
                self.assertFalse(Path(plan[field]).exists(), field)

    def test_ast_parse(self):
        result = self.json_ps(PARSE + r"""
[pscustomobject]@{
    errors = @($parseErrors).Count
    parameters = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
} | ConvertTo-Json
""")
        self.assertEqual(result["errors"], 0)
        self.assertTrue(
            {"Action", "Sdk", "Port", "Image", "Width", "Height", "Density",
             "TimeoutSeconds", "Receipt", "DryRun"}
            <= set(result["parameters"])
        )

    def test_display_defaults_and_custom_dryrun(self):
        with free_pair() as (port, sockets):
            for listener in sockets:
                listener.close()
            for extra, width, height, density in (
                ("", 1080, 1920, 420),
                ("-Width 720 -Height 1280 -Density 320", 720, 1280, 320),
                ("-Width 1920", 1920, 1920, 420),
            ):
                with self.subTest(extra=extra):
                    plan = self.start(port, f"-Sdk {quote(self.sdk)} {extra}")
                    self.assert_plan(plan, port, self.sdk)
                    self.assertEqual(plan["avdConfig"], {
                        "hw.lcd.width": str(width), "hw.lcd.height": str(height),
                        "hw.lcd.density": str(density),
                        "skin.name": f"{width}x{height}", "skin.path": f"{width}x{height}",
                    })
                    self.assertNotIn("--force", plan["createArguments"])
                    self.assertNotIn("-wipe-data", plan["launchArguments"])

    def test_display_invalid_arguments_fail_before_side_effects(self):
        self.ps(MOCKS + r"""
function New-Item { throw 'Unexpected filesystem write' }
function Assert-FreePorts { throw 'Unexpected port query' }
foreach ($name in @('Width', 'Height', 'Density')) {
    $maximum = if ($name -eq 'Density') { 1000 } else { 8192 }
    foreach ($value in @('0', '-1', "$($maximum + 1)", '2147483648', 'invalid')) {
        $arguments = @{ $name = $value }
        Must-Throw { & $scriptPath Start -Sdk $sdk -Port 5580 -DryRun @arguments } "parameter '$name'"
    }
}
""")

    def test_display_config_replaces_duplicates_and_preserves_other_lines(self):
        result = self.json_ps(DISPLAY_FUNCTIONS + r"""
$original = @(
    '# preserve comments and whitespace', '; hw.lcd.width=999', ''
    'AvdId=edge_test', 'image.sysdir.1=system-images\android-34\default\x86_64\'
    '  hw.lcd.width = 320', 'hw.lcd.width=480', 'hw.lcd.height=640'
    'hw.lcd.density = 160', 'skin.name=old', 'skin.name=duplicate'
    'skin.path=C:\machine-specific\skin', 'skin.path=another'
    'hw.lcd.width.extra=untouched', 'hw.ramSize=2048'
    'disk.dataPartition.size=6G', '  custom.key = preserve = value  ', 'avd.ini.displayname=测试'
)
[IO.File]::WriteAllLines($configPath, $original, [Text.UTF8Encoding]::new($false))
$sentinel = Join-Path $owner.avdPath 'userdata-qemu.img'
[IO.File]::WriteAllText($sentinel, 'untouched user data')
Set-FreshAvdDisplayConfig $owner
$first = [IO.File]::ReadAllText($configPath)
Set-FreshAvdDisplayConfig $owner
Check ($first -ceq [IO.File]::ReadAllText($configPath)) 'Config rewrite is not idempotent'
Check ([IO.File]::ReadAllText($sentinel) -ceq 'untouched user data') 'User data was modified'
Check (@(Get-ChildItem -LiteralPath $owner.avdPath -Filter '*.new').Count -eq 0) 'Staging file leaked'
[pscustomobject]@{ original = $original; lines = [IO.File]::ReadAllLines($configPath) } | ConvertTo-Json
""")
        targets = {
            "hw.lcd.width": "1080", "hw.lcd.height": "1920", "hw.lcd.density": "420",
            "skin.name": "1080x1920", "skin.path": "1080x1920",
        }
        for key, value in targets.items():
            entries = [line for line in result["lines"] if line.split("=")[0].strip() == key]
            self.assertEqual(entries, [f"{key}={value}"])
        untouched = lambda lines: [
            line for line in lines if line.split("=")[0].strip() not in targets
        ]
        self.assertEqual(untouched(result["lines"]), untouched(result["original"]))

    def test_display_config_appends_missing_keys_and_custom_values(self):
        self.ps(DISPLAY_FUNCTIONS + r"""
$owner.avdConfig = Get-AvdDisplayConfig -Width 720 -Height 1280 -Density 320
foreach ($original in @('', "# comment`nhw.ramSize=2048")) {
    [IO.File]::WriteAllText($configPath, $original)
    Set-FreshAvdDisplayConfig $owner
    $lines = [IO.File]::ReadAllLines($configPath)
    foreach ($key in $owner.avdConfig.Keys) {
        Check (@($lines | Where-Object { $_ -ceq "$key=$($owner.avdConfig[$key])" }).Count -eq 1) "Missing/duplicate $key"
    }
    if ($original) {
        Check ($lines[0] -ceq '# comment' -and $lines[1] -ceq 'hw.ramSize=2048') 'Other keys lost'
    }
}
""")

    def test_display_config_rejects_unowned_started_and_missing_avds(self):
        self.ps(DISPLAY_FUNCTIONS + r"""
Must-Throw { Set-FreshAvdDisplayConfig $owner } 'does not exist|cannot find'
[IO.File]::WriteAllText($configPath, 'hw.lcd.width=320')
foreach ($field in @('avd', 'repoRoot', 'runRoot', 'avdHome', 'avdPath', 'status', 'pid', 'processes')) {
    $original = $owner.$field
    $owner.$field = switch ($field) {
        'pid' { 123 }
        'processes' { @($identity) }
        default { 'not-owned-or-already-started' }
    }
    Must-Throw { Set-FreshAvdDisplayConfig $owner } 'fresh, unstarted private AVD'
    $owner.$field = $original
    Check ([IO.File]::ReadAllText($configPath) -ceq 'hw.lcd.width=320') 'Rejected config modified'
}
""")

    def test_display_config_rejects_reparse_ancestors(self):
        self.ps(DISPLAY_FUNCTIONS + r"""
$target = Join-Path $runRoot 'external'
New-Item -ItemType Directory -Path $target | Out-Null
$targetConfig = Join-Path $target 'config.ini'
[IO.File]::WriteAllText($targetConfig, 'hw.lcd.width=320')
Remove-Item -LiteralPath $owner.avdPath
$junction = New-Item -ItemType Junction -Path $owner.avdPath -Target $target
try {
    Must-Throw { Set-FreshAvdDisplayConfig $owner } 'reparse point'
    Check ([IO.File]::ReadAllText($targetConfig) -ceq 'hw.lcd.width=320') 'Reparse target modified'
} finally {
    [IO.Directory]::Delete($junction.FullName)
}
""")

    def test_display_config_replaces_file_without_writing_through_hardlinks(self):
        self.ps(DISPLAY_FUNCTIONS + r"""
$originalPath = Join-Path $runRoot 'original.ini'
[IO.File]::WriteAllText($originalPath, 'hw.lcd.width=320')
New-Item -ItemType HardLink -Path $configPath -Target $originalPath | Out-Null
Set-FreshAvdDisplayConfig $owner
Check ([IO.File]::ReadAllText($originalPath) -ceq 'hw.lcd.width=320') 'Hardlink target modified'
Check ([IO.File]::ReadAllText($configPath) -match 'hw.lcd.width=1080') 'Owned config not replaced'
""")

    def test_display_config_occurs_only_after_creation_before_launch(self):
        self.ps(MOCKS + r"""
$calls = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq 'Set-FreshAvdDisplayConfig'
}, $true))
Check ($calls.Count -eq 1) 'Configuration must only run once on Start'
$startTry = @($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.TryStatementAst] -and $_.Extent.Text -match 'Start-Process'
})[0]
$statements = @($startTry.Body.Statements)
$index = 0
while ($statements[$index].Extent.Text -cne 'Set-FreshAvdDisplayConfig $owner') { $index++ }
Check ($statements[$index - 1].Extent.Text -match 'avdmanager failed') 'Configuration before creation success'
Check ($statements[$index + 1].Extent.Text -ceq 'Assert-FreePorts $Port') 'Configuration must precede launch guards'
Check ($startTry.Extent.Text -match 'New-Item -ItemType Directory -Path \$runRoot \|') 'Fresh exclusive run directory required'
Check ($startTry.Extent.Text -notmatch 'New-Item[^\r\n]*-Force') 'Must not reuse existing run directory'
""")

    def test_adb_protocol_parses_decimal_not_sdk_release(self):
        self.ps(ADB_FUNCTIONS + r"""
Check ((Convert-AdbProtocol "Android Debug Bridge version 1.0.41`r`nVersion 36.0.0-123`r`n") -eq 41) 'Wrong decimal protocol'
Check ((Convert-AdbProtocol "Android Debug Bridge version 1.0.42`n") -eq 42) 'Hardcoded protocol'
foreach ($text in @('', 'Version 41.0.0', 'Android Debug Bridge version 1.0.0029',
    'Android Debug Bridge version 1.0.41 extra', 'Android Debug Bridge version 1.0.41.0',
    'Android Debug Bridge version 1.1.41', 'Android Debug Bridge version 1.0.0',
    'Android Debug Bridge version 1.0.65536', 'Android Debug Bridge version 1.0.9999999999999',
    "Android Debug Bridge version 1.0.41`nAndroid Debug Bridge version 1.0.42")) {
    if ($text -eq 'Android Debug Bridge version 1.0.0029') {
        Check ((Convert-AdbProtocol $text) -eq 29) 'Local decimal interpreted as hexadecimal'
    } else {
        Must-Throw { Convert-AdbProtocol $text } 'Malformed local adb version'
    }
}
""")

    def test_adb_raw_matching_fragmented_response(self):
        with adb_server([[bytes([byte]) for byte in b"OKAY00040029"]], delay=0.005) as (port, requests):
            self.ps(ADB_FUNCTIONS + f"""
Assert-AdbServerProtocol -Protocol (Convert-AdbProtocol 'Android Debug Bridge version 1.0.41') -ServerPort {port}
""")
        self.assertEqual(requests, [b"000chost:version"])

    def test_adb_raw_rejects_mismatch_malformed_and_truncated(self):
        cases = (
            (b"OKAY00040028", "protocol mismatch"),
            (b"OKAY00040041", "protocol mismatch"),
            (b"FAIL0004nope", "status"),
            (b"okay00040029", "status"),
            (b"OKAYzzzz0029", "length"),
            (b"OKAYffff0029", "length"),
            (b"OKAY00030029", "length"),
            (b"OKAY0004002g", "hexadecimal"),
            (b"OKAY0004\xff029", "hexadecimal"),
            (b"OK", "truncated"),
            (b"OKAY000400", "truncated"),
            (b"", "truncated"),
        )
        for response, expected in cases:
            with self.subTest(response=response):
                with adb_server([[response]]) as (port, requests):
                    result = self.ps(ADB_FUNCTIONS + f"""
Assert-AdbServerProtocol -Protocol 41 -ServerPort {port}
""", success=False)
                self.assertIn(expected, result.stderr)
                self.assertIn("Refusing adb/emulator use", result.stderr)
                self.assertEqual(requests, [b"000chost:version"])

    def test_adb_raw_timeout_and_slow_trickle_have_total_deadline(self):
        for chunks, delay in (([], 0), ([bytes([b]) for b in b"OKAY00040029"], 0.09)):
            with self.subTest(chunks=chunks):
                with adb_server([chunks], delay=delay, hold=True) as (port, requests):
                    self.ps(ADB_FUNCTIONS + f"""
$clock = [Diagnostics.Stopwatch]::StartNew()
Must-Throw {{ Assert-AdbServerProtocol -Protocol 41 -ServerPort {port} -LimitMilliseconds 300 }} 'timed out'
Check ($clock.ElapsedMilliseconds -lt 2000) 'Raw handshake exceeded total deadline'
""")
                self.assertEqual(requests, [b"000chost:version"])

    def test_adb_raw_missing_server_is_refused(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            reserved.bind(("127.0.0.1", 0))  # Reserved, deliberately not listening.
            port = reserved.getsockname()[1]
            result = self.ps(ADB_FUNCTIONS + f"""
Assert-AdbServerProtocol -Protocol 41 -ServerPort {port} -LimitMilliseconds 300
""", success=False)
        self.assertIn(f"127.0.0.1:{port}", result.stderr)
        self.assertIn("No server was started/stopped", result.stderr)

    def test_adb_client_endpoint_is_pinned_despite_environment(self):
        self.ps(ADB_FUNCTIONS + r"""
$names = @('ADB_SERVER_SOCKET', 'ANDROID_ADB_SERVER_ADDRESS', 'ANDROID_ADB_SERVER_PORT', 'ADB_SERVER_PORT')
foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, 'foreign-endpoint', 'Process') }
$info = New-AdbStartInfo $sdk '-s emulator-5580 shell getprop sys.boot_completed'
Check ($info.FileName -ceq (Join-Path $sdk 'platform-tools\adb.exe')) 'Wrong adb executable'
Check ($info.Arguments -ceq '-H 127.0.0.1 -P 5037 -s emulator-5580 shell getprop sys.boot_completed') 'Unpinned adb command'
foreach ($name in $names) {
    Check (-not $info.EnvironmentVariables.ContainsKey($name)) "Inherited redirect: $name"
    Check ([Environment]::GetEnvironmentVariable($name, 'Process') -ceq 'foreign-endpoint') 'Parent environment changed'
}
""")

    def test_every_boot_probe_checks_raw_server_before_client(self):
        with adb_server([[b"OKAY00040029"], [b"OKAY00040028"]]) as (port, requests):
            self.ps(ADB_FUNCTIONS + f"""
$rawGuard = (Get-Item Function:Assert-AdbServerProtocol).ScriptBlock
function Assert-AdbServerProtocol($Protocol, $LimitMilliseconds) {{
    & $rawGuard -Protocol $Protocol -LimitMilliseconds $LimitMilliseconds -ServerPort {port}
}}
""" + r"""
$script:versions = 0
$script:clients = 0
function Invoke-AdbOutput($Info, $LimitMilliseconds, [switch]$AllowFailure) {
    Check ($LimitMilliseconds -gt 0) 'Invalid client deadline'
    if ($Info.Arguments -ceq '-H 127.0.0.1 -P 5037 version') {
        $script:versions++
        return "Android Debug Bridge version 1.0.41`nVersion 36.0.0"
    }
    Check ($Info.Arguments -ceq '-H 127.0.0.1 -P 5037 -s emulator-5580 shell getprop sys.boot_completed') 'Unexpected command'
    Check $AllowFailure 'Transient boot failure must remain retryable'
    $script:clients++
    '1'
}
Check (Invoke-BootProbe $owner 3000) 'Matching server did not allow probe'
Must-Throw { Invoke-BootProbe $owner 3000 } 'protocol mismatch'
Check ($script:versions -eq 2 -and $script:clients -eq 1) 'Guard cached/bypassed on second probe'
""")
        self.assertEqual(requests, [b"000chost:version"] * 2)

    def test_local_version_failure_prevents_raw_guard_and_probe(self):
        self.ps(ADB_FUNCTIONS + r"""
function Assert-AdbServerProtocol { throw 'Raw socket must not be reached' }
foreach ($mode in @('malformed', 'failed', 'timeout')) {
    $script:mode = $mode
    function Invoke-AdbOutput($Info, $LimitMilliseconds) {
        Check ($Info.Arguments -ceq '-H 127.0.0.1 -P 5037 version') 'Network adb client executed'
        if ($script:mode -eq 'malformed') { return 'Version 36.0.0' }
        throw "local version $script:mode"
    }
    Must-Throw { Invoke-BootProbe $owner 3000 } 'Malformed local adb version|local version failed|local version timeout'
}
""")

    def test_environment_restoration_removes_originally_absent_names(self):
        result = self.json_ps(RESTORE_ENVIRONMENT + r"""
$names = @('ANDROID_USER_HOME', 'ANDROID_AVD_HOME', 'ANDROID_EMULATOR_HOME')
$originalEnvironment = @{}
foreach ($name in $names) {
    if (Test-Path -LiteralPath "Env:$name") { Remove-Item -LiteralPath "Env:$name" }
    $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, 'private-emulator-home', 'Process')
}
. $restore
$afterFirst = [Environment]::GetEnvironmentVariables('Process')
. $restore
$afterSecond = [Environment]::GetEnvironmentVariables('Process')
@($names | ForEach-Object {
    [pscustomobject]@{
        name = $_
        savedNull = ($null -eq $originalEnvironment[$_])
        presentFirst = $afterFirst.Contains($_)
        presentSecond = $afterSecond.Contains($_)
    }
}) | ConvertTo-Json
""")
        for entry in result:
            self.assertTrue(entry["savedNull"], entry)
            self.assertFalse(entry["presentFirst"], entry)
            self.assertFalse(entry["presentSecond"], entry)

    def test_environment_restoration_preserves_nonempty_values(self):
        result = self.json_ps(RESTORE_ENVIRONMENT + r"""
$name = 'ANDROID_USER_HOME'
$value = 'C:\Original Home\MiXeD;=value '
[Environment]::SetEnvironmentVariable($name, $value, 'Process')
$originalEnvironment = @{ $name = [Environment]::GetEnvironmentVariable($name, 'Process') }
[Environment]::SetEnvironmentVariable($name, 'private-emulator-home', 'Process')
. $restore
[pscustomobject]@{
    present = [Environment]::GetEnvironmentVariables('Process').Contains($name)
    value = [Environment]::GetEnvironmentVariable($name, 'Process')
} | ConvertTo-Json
""")
        self.assertTrue(result["present"])
        self.assertEqual(result["value"], "C:\\Original Home\\MiXeD;=value ")

    def test_environment_restoration_preserves_host_empty_semantics(self):
        result = self.json_ps(RESTORE_ENVIRONMENT + r"""
$name = 'ANDROID_USER_HOME'
# PS5/.NET Framework removes empty values; modern PS/.NET retains them.
[Environment]::SetEnvironmentVariable($name, '', 'Process')
$before = [Environment]::GetEnvironmentVariables('Process')
$originalEnvironment = @{ $name = [Environment]::GetEnvironmentVariable($name, 'Process') }
[Environment]::SetEnvironmentVariable($name, 'private-emulator-home', 'Process')
. $restore
$after = [Environment]::GetEnvironmentVariables('Process')
[pscustomobject]@{
    presentBefore = $before.Contains($name)
    valueBefore = $before[$name]
    presentAfter = $after.Contains($name)
    valueAfter = $after[$name]
} | ConvertTo-Json
""")
        self.assertEqual(result["valueBefore"], "" if result["presentBefore"] else None)
        self.assertEqual(result["presentAfter"], result["presentBefore"])
        self.assertEqual(result["valueAfter"], result["valueBefore"])

    def test_emulator_start_is_guarded_and_environment_pinned(self):
        self.ps(MOCKS + r"""
$preflight = @($ast.EndBlock.Statements | Where-Object { $_.Extent.Text -ceq 'Assert-CompatibleAdbServer $Sdk' })
Check ($preflight.Count -eq 1) 'Missing preflight before any AVD writes'
$environmentStatement = @($ast.EndBlock.Statements | Where-Object { $_.Extent.Text -match '^\$environment = ' })[0]
. ([scriptblock]::Create($environmentStatement.Extent.Text))
Check ($environment.ADB_SERVER_SOCKET -ceq 'tcp:127.0.0.1:5037') 'Emulator socket not pinned'
Check ($environment.ANDROID_ADB_SERVER_ADDRESS -ceq '127.0.0.1') 'Emulator host not pinned'
Check ($environment.ANDROID_ADB_SERVER_PORT -ceq '5037' -and $environment.ADB_SERVER_PORT -ceq '5037') 'Emulator port not pinned'
$startTry = @($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.TryStatementAst] -and $_.Extent.Text -match 'Start-Process'
})[0]
$statements = @($startTry.Body.Statements)
$launchIndex = 0
while ($statements[$launchIndex].Extent.Text -notmatch '^\$launcher = Start-Process') { $launchIndex++ }
Check ($statements[$launchIndex - 2].Extent.Text -ceq 'Assert-CompatibleAdbServer $Sdk') 'No immediate prelaunch guard'
function Assert-CompatibleAdbServer { throw 'Unsafe server' }
$prefix = ($statements[($launchIndex - 2)..$launchIndex] | ForEach-Object { $_.Extent.Text }) -join "`n"
Must-Throw { & ([scriptblock]::Create($prefix)) } 'Unsafe server'
""")

    def test_dryrun_roots_spaces_uniqueness_and_no_mutation(self):
        isolated_root = self.fixture / "dryrun repository"
        isolated_script = isolated_root / "tools" / SCRIPT.name
        isolated_script.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, isolated_script)
        with free_pair() as (port, sockets):
            for listener in sockets:
                listener.close()
            result = self.json_ps(f"$scriptPath = {quote(isolated_script)}\n" + r"""
function Snapshot($Path) {
    if (Test-Path -LiteralPath $Path) {
        @(Get-ChildItem -LiteralPath $Path -Recurse -Force | ForEach-Object {
            '{0}|{1}|{2}' -f $_.FullName, $_.LastWriteTimeUtc.Ticks,
                $(if ($_ -is [IO.FileInfo]) { $_.Length } else { 'directory' })
        } | Sort-Object) -join "`n"
    } else { '<absent>' }
}
function Environment-Snapshot {
    (Get-ChildItem Env: | Sort-Object Name | ForEach-Object {
        $_.Name + '=' + $_.Value
    }) -join "`n"
}
function Start-Process { throw 'DryRun attempted process launch' }
function New-Item { throw 'DryRun attempted filesystem write' }
function Set-Content { throw 'DryRun attempted filesystem write' }
function Out-File { throw 'DryRun attempted filesystem write' }
function Move-Item { throw 'DryRun attempted filesystem write' }
$private = Join-Path (Split-Path (Split-Path $scriptPath -Parent) -Parent) 'build\emulator'
$beforeFiles = Snapshot $private
$beforeFixture = Snapshot $fixture
$beforeEnv = Environment-Snapshot
""" + f"""
$first = & $scriptPath Start -Sdk {quote(self.sdk)} -Port {port} -DryRun | ConvertFrom-Json
$second = & $scriptPath Start -Sdk {quote(self.sdk)} -Port {port} -DryRun | ConvertFrom-Json
""" + r"""
$afterFiles = Snapshot $private
$afterFixture = Snapshot $fixture
[pscustomobject]@{
    first = $first; second = $second
    filesUnchanged = ($beforeFiles -ceq $afterFiles)
    fixtureUnchanged = ($beforeFixture -ceq $afterFixture)
    filesDiff = @(Compare-Object ($beforeFiles -split "`n") ($afterFiles -split "`n"))
    fixtureDiff = @(Compare-Object ($beforeFixture -split "`n") ($afterFixture -split "`n"))
    envUnchanged = ($beforeEnv -ceq (Environment-Snapshot))
} | ConvertTo-Json -Depth 10
""", env={
                "ANDROID_HOME": str(self.fixture / "untouched home"),
                "ANDROID_SDK_ROOT": str(self.fixture / "untouched sdk"),
                "ANDROID_AVD_HOME": str(self.fixture / "untouched avd"),
                "ANDROID_USER_HOME": str(self.fixture / "untouched user"),
                "ANDROID_EMULATOR_HOME": str(self.fixture / "untouched emulator"),
            })
        self.assertFalse(self.sdk.exists())
        for key in ("filesUnchanged", "fixtureUnchanged", "envUnchanged"):
            self.assertTrue(result[key], f"{key}: {result.get(key.replace('Unchanged', 'Diff'))}")
        for plan in (result["first"], result["second"]):
            self.assert_plan(plan, port, self.sdk, root=isolated_root)
            self.assertEqual(plan["image"], "system-images;android-34;default;x86_64")
        self.assertNotEqual(result["first"]["avd"], result["second"]["avd"])
        self.assertNotEqual(result["first"]["receipt"], result["second"]["receipt"])

    def test_relative_sdk_uses_powershell_location(self):
        location = self.fixture / "PS location"
        location.mkdir(exist_ok=True)
        with free_pair() as (port, sockets):
            for listener in sockets:
                listener.close()
            plan = self.json_ps(
                f"Set-Location -LiteralPath {quote(location)}\n"
                "function Start-Process { throw 'SDK launch forbidden' }\n"
                f"& $scriptPath Start -Sdk '.\\relative SDK' -Port {port} -DryRun"
            )
        self.assert_plan(plan, port, location / "relative SDK")

    def test_sdk_fallback_precedence_and_custom_image(self):
        home = self.fixture / "home SDK missing"
        fallback = self.fixture / "fallback SDK missing"
        image = "system-images;android-34;google_apis;x86_64"
        cases = [
            ({}, f"-Sdk {quote(self.sdk)}", self.sdk),
            ({"ANDROID_HOME": str(home), "ANDROID_SDK_ROOT": str(fallback)}, "", home),
            ({"ANDROID_SDK_ROOT": str(fallback)}, "", fallback),
            ({"ANDROID_HOME": "", "ANDROID_SDK_ROOT": str(fallback)}, "", fallback),
            ({"ANDROID_HOME": str(home), "ANDROID_SDK_ROOT": str(fallback)},
             f"-Sdk {quote(self.sdk)}", self.sdk),
        ]
        with free_pair() as (port, sockets):
            for listener in sockets:
                listener.close()
            for env, extra, expected in cases:
                with self.subTest(env=env, extra=extra):
                    plan = self.start(
                        port, extra + f" -Image {quote(image)} -TimeoutSeconds 240", env,
                    )
                    self.assert_plan(plan, port, expected)
                    self.assertEqual(plan["image"], image)

    def test_start_rejects_missing_sdk(self):
        result = self.ps("& $scriptPath Start -Port 5580 -DryRun", success=False)
        self.assertRegex(result.stderr, r"(?i)(sdk|ANDROID_HOME)")

    def test_port_is_explicit_even_and_in_range(self):
        for argument in ("", "-Port 5553", "-Port 5555", "-Port 5683",
                         "-Port 5552", "-Port 5684", "-Port 0", "-Port -2"):
            with self.subTest(argument=argument):
                result = self.ps(
                    f"& $scriptPath Start -Sdk {quote(self.sdk)} {argument} -DryRun",
                    success=False,
                )
                self.assertRegex(result.stderr, r"even.*5554.*5682")
        self.ps(MOCKS + "Assert-Port 5554; Assert-Port 5682")

    def test_busy_console_or_adb_port_refuses_reuse(self):
        for busy_index in (0, 1):
            with self.subTest(busy_index=busy_index), free_pair() as (port, sockets):
                sockets[1 - busy_index].close()
                result = self.ps(
                    f"& $scriptPath Start -Sdk {quote(self.sdk)} -Port {port} -DryRun",
                    success=False,
                )
                self.assertRegex(result.stderr, r"(?i)(occupied|refusing|in use|access)")
                self.assertEqual(sockets[busy_index].getsockname()[1], port + busy_index)

    def test_invalid_image_timeout_and_start_receipt(self):
        for argument in (
            "-Image 'system-images;android-34;default;arm64-v8a'",
            "-TimeoutSeconds 9", "-TimeoutSeconds 901",
            f"-Receipt {quote(self.fixture / 'not-owned.json')}",
        ):
            with self.subTest(argument=argument):
                self.ps(
                    f"& $scriptPath Start -Sdk {quote(self.sdk)} -Port 5580 -DryRun {argument}",
                    success=False,
                )

    def test_stop_missing_nonexistent_and_malformed_receipts(self):
        self.ps("& $scriptPath Stop -DryRun", success=False)
        missing = self.fixture / "nonexistent owner.json"
        self.ps(f"& $scriptPath Stop -Receipt {quote(missing)} -DryRun", success=False)
        for override in (
            f"-Sdk {quote(self.sdk)}", "-Port 5580",
            "-Image 'system-images;android-34;default;x86_64'",
            "-Width 1080", "-Height 1920", "-Density 420",
        ):
            with self.subTest(override=override):
                result = self.ps(
                    f"& $scriptPath Stop -Receipt {quote(missing)} -DryRun {override}",
                    success=False,
                )
                self.assertIn("only from -Receipt", result.stderr)
        for contents in ("not json", "{}", '{"schemaVersion": 999, "port": 5580}'):
            receipt = self.fixture / ("invalid-" + uuid.uuid4().hex + ".json")
            receipt.write_text(contents, encoding="utf-8")
            try:
                with self.subTest(contents=contents):
                    self.ps(
                        f"& $scriptPath Stop -Receipt {quote(receipt)} -DryRun",
                        success=False,
                    )
                    self.assertEqual(receipt.read_text(encoding="utf-8"), contents)
            finally:
                receipt.unlink()

    def test_identity_timestamp_json_roundtrip_normalizes_datetimeoffset(self):
        self.ps(MOCKS + r"""
$script:live.CreationDate = $script:live.CreationDate.AddTicks(1234567)
$identity = Convert-Identity $script:live 'launcher'
$owner.processStartTime = $identity.processStartTime
$owner.processes = @($identity)
$roundtrip = $owner | ConvertTo-Json -Depth 8 | ConvertFrom-Json
Check ($identity.processStartTime -is [string]) 'Actual identity must retain ISO text'
Check ($identity.processStartTime -match '\.1234567Z$') 'Expected exact UTC timestamp'
if ($PSVersionTable.PSVersion -ge [version]'7.5') {
    Check ($roundtrip.processes[0].processStartTime -is [datetime]) 'Expected automatic DateTime'
}
foreach ($stamp in @(
    $identity.processStartTime,
    $roundtrip.processes[0].processStartTime,
    ([datetimeoffset]$identity.processStartTime).UtcDateTime,
    ([datetimeoffset]$identity.processStartTime).ToOffset([timespan]::FromHours(2))
)) {
    $roundtrip.processes[0].processStartTime = $stamp
    Check ($null -ne (Assert-LiveIdentity $roundtrip.processes[0] $roundtrip)) 'Same instant rejected'
}
$roundtrip.processes[0].processStartTime = ([datetimeoffset]$identity.processStartTime).AddTicks(1)
Must-Throw { Assert-LiveIdentity $roundtrip.processes[0] $roundtrip } 'Ownership mismatch'
""")

    def test_native_owner_checker_normalizes_timestamps_and_rechecks_identity(self):
        self.ps(NATIVE_OWNER_CHECK + r"""
$script:live.CreationDate = $script:live.CreationDate.AddTicks(1234567)
$identity = Convert-Identity $script:live 'launcher'
$owner.processes = @($identity)
$owner = $owner | ConvertTo-Json -Depth 8 | ConvertFrom-Json
Check ((& $checkBody) -ceq 'owned') 'JSON timestamp rejected'
Assert-NativeDisposed
foreach ($stamp in @(
    $identity.processStartTime,
    ([datetimeoffset]$identity.processStartTime).UtcDateTime,
    ([datetimeoffset]$identity.processStartTime).ToOffset([timespan]::FromHours(2))
)) {
    Reset-NativeCheck
    $owner.processes[0].processStartTime = $stamp
    Check ((& $checkBody) -ceq 'owned') 'Equivalent instant rejected'
    Assert-NativeDisposed
}
$owner.processes[0].processStartTime = ([datetimeoffset]$identity.processStartTime).AddTicks(1)
Must-Throw { & $checkBody } 'identity changed'
$owner.processes[0].processStartTime = 'not a timestamp'
Must-Throw { & $checkBody } 'DateTimeOffset'
$owner.processes[0].processStartTime = $identity.processStartTime
foreach ($field in @('CommandLine', 'ExecutablePath', 'ParentProcessId')) {
    $original = $script:live.$field
    try {
        if ($field -eq 'ParentProcessId') { $script:live.$field = $original - 1 }
        else { $script:live.$field = $original + '-changed' }
        Must-Throw { & $checkBody } 'identity changed'
    } finally { $script:live.$field = $original }
}
Reset-NativeCheck
$script:listenerOwner++
Must-Throw { & $checkBody } 'ports must belong'
Assert-NativeDisposed
""")

    def test_native_owner_checker_retains_all_handles_until_listener_validation(self):
        self.ps(NATIVE_OWNER_CHECK + r"""
$child = $script:live.PSObject.Copy()
$child.ProcessId++
$child.ParentProcessId = $identity.pid
$child.ExecutablePath = $qemuExe
$script:nativeLive += @($child)
$owner.processes += @(Convert-Identity $child 'qemu')
$script:listenerOwner = $child.ProcessId
Check ((& $checkBody) -ceq 'owned') 'Proven child listeners rejected'
Assert-NativeDisposed
$first = $identity.pid
$second = $child.ProcessId
$expected = @("identity:$first", "get:$first", "handle:$first", "identity:$first",
    "identity:$second", "get:$second", "handle:$second", "identity:$second",
    'listeners', 'validate', 'validate', "dispose:$first", "dispose:$second")
Check (($script:nativeEvents -join ',') -ceq ($expected -join ',')) (
    'Unexpected acquisition/revalidation/disposal order: ' + ($script:nativeEvents -join ','))
""")

    def test_native_owner_checker_rejects_identity_races_after_acquisition(self):
        self.ps(NATIVE_OWNER_CHECK + r"""
foreach ($stage in @('acquire', 'handle', 'recheck')) {
    foreach ($field in @('CreationDate', 'CommandLine', 'ExecutablePath', 'ParentProcessId',
        'ProcessId', 'missing', 'exited')) {
        Reset-NativeCheck
        $script:nativeLive = @($script:live.PSObject.Copy())
        $mutation = {
            switch ($field) {
                CreationDate { $script:nativeLive[0].CreationDate = $script:live.CreationDate.AddTicks(1) }
                CommandLine { $script:nativeLive[0].CommandLine += ' changed' }
                ExecutablePath { $script:nativeLive[0].ExecutablePath += '.changed' }
                ParentProcessId { $script:nativeLive[0].ParentProcessId-- }
                ProcessId { $script:nativeLive[0].ProcessId++ }
                missing { $script:nativeLive = @() }
                exited {
                    # The Process object only exists after Get-Process returns.
                    if ($script:nativeHandles.Count) { $script:nativeHandles[0].HasExited = $true }
                    else { $script:nativeLive = @() }
                }
            }
        }
        switch ($stage) {
            acquire { $script:nativeAcquire = $mutation }
            handle { $script:nativeHandle = $mutation }
            recheck { $script:nativeRecheck = $mutation }
        }
        Must-Throw { & $checkBody } 'identity changed'
        Check ($script:nativeHandles.Count -eq 1) "No retained process for $stage/$field"
        Assert-NativeDisposed
        Check (-not $script:nativeEvents.Contains('listeners')) "Race reached listeners: $stage/$field"
    }
}
""")

    def test_native_owner_checker_disposes_handles_on_acquisition_and_listener_failures(self):
        self.ps(NATIVE_OWNER_CHECK + r"""
$child = $script:live.PSObject.Copy()
$child.ProcessId++
$child.ParentProcessId = $identity.pid
$childIdentity = Convert-Identity $child 'qemu'
foreach ($failure in @('get', 'handle', 'null-handle', 'zero-handle', 'identity',
    'listeners', 'foreign', 'missing-port', 'no-ports')) {
    Reset-NativeCheck
    $script:nativeLive += @($child.PSObject.Copy())
    $owner.processes = @($identity, $childIdentity)
    $pattern = 'synthetic failure'
    switch ($failure) {
        get {
            $script:nativeAcquire = { param($ProcessId)
                if ($ProcessId -eq $child.ProcessId) { throw 'synthetic failure' }
            }
        }
        handle {
            $script:nativeHandle = { param($Handle)
                if ($Handle.Id -eq $child.ProcessId) { throw 'synthetic failure' }
            }
            $pattern = 'Cannot acquire emulator process handle'
        }
        null-handle {
            $script:nativeHandle = { param($Handle)
                if ($Handle.Id -eq $child.ProcessId) { $Handle.HandleValue = $null }
            }
            $pattern = 'Cannot acquire emulator process handle'
        }
        zero-handle {
            $script:nativeHandle = { param($Handle)
                if ($Handle.Id -eq $child.ProcessId) { $Handle.HandleValue = [intptr]::Zero }
            }
            $pattern = 'Cannot acquire emulator process handle'
        }
        identity {
            $script:nativeRecheck = { param($ProcessId)
                if ($ProcessId -eq $child.ProcessId) { $script:nativeLive[1].CommandLine += ' changed' }
            }
            $pattern = 'identity changed'
        }
        listeners { $script:nativeListeners = { throw 'synthetic failure' } }
        foreign { $script:listenerOwner = 2000000099; $pattern = 'ports must belong' }
        missing-port { $script:listenerPorts = @(5580); $pattern = 'ports must belong' }
        no-ports { $script:listenerPorts = @(); $pattern = 'ports must belong' }
    }
    Must-Throw { & $checkBody } $pattern
    $expectedHandles = 2
    if ($failure -eq 'get') { $expectedHandles = 1 }
    Check ($script:nativeHandles.Count -eq $expectedHandles) "Wrong retained count: $failure"
    Assert-NativeDisposed
    if ($failure -in @('get', 'handle', 'null-handle', 'zero-handle', 'identity')) {
        Check (-not $script:nativeEvents.Contains('listeners')) "Failed acquisition reached ports: $failure"
    }
}
""")

    def test_native_owner_checker_preserves_exited_launcher_and_live_child_checks(self):
        self.ps(NATIVE_OWNER_CHECK + r"""
$child = $script:live.PSObject.Copy()
$child.ProcessId++
$child.ParentProcessId = $identity.pid
$script:nativeLive = @($child)
$owner.processes += @(Convert-Identity $child 'qemu')
$script:listenerOwner = $child.ProcessId
Check ((& $checkBody) -ceq 'owned') 'Exited launcher prevented proven child validation'
Check ($script:nativeHandles.Count -eq 1) 'Acquired absent launcher'
Assert-NativeDisposed
Reset-NativeCheck
$script:nativeLive = @()
Must-Throw { & $checkBody } 'ports must belong'
Check ($script:nativeHandles.Count -eq 0) 'Acquired absent processes'
""")

    def test_identity_rejects_reused_pid_or_changed_identity(self):
        self.ps(MOCKS + r"""
Check ($null -ne (Assert-LiveIdentity $identity $owner)) 'Matching identity rejected'
foreach ($field in @('CreationDate', 'CommandLine', 'ExecutablePath', 'ParentProcessId')) {
    $original = $script:live.$field
    try {
        switch ($field) {
            CreationDate { $script:live.$field = $original.AddSeconds(1) }
            CommandLine { $script:live.$field = $original + ' -different' }
            ExecutablePath { $script:live.$field = Join-Path $fixture 'other\emulator.exe' }
            ParentProcessId { $script:live.$field = $original - 1 }
        }
        Must-Throw { Assert-LiveIdentity $identity $owner } 'Ownership mismatch'
    } finally { $script:live.$field = $original }
}
$script:live.ProcessId = 2000000002
Check ($null -eq (Assert-LiveIdentity $identity $owner)) 'Exited launcher treated as live'
""")

    def test_emulator_identity_requires_exact_avd_port_and_sdk_path(self):
        self.ps(MOCKS + r"""
foreach ($path in @($exe, $qemuExe, ($qemuExe -replace '\.exe$', '-headless.exe'))) {
    $candidate = $identity | ConvertTo-Json | ConvertFrom-Json
    $candidate.executablePath = $path
    Check (Test-EmulatorIdentity $candidate $owner) "Valid executable rejected: $path"
}
foreach ($line in @(
    ($identity.commandLine -replace '5580', '55800'),
    ($identity.commandLine -replace '5580', '5582'),
    ($identity.commandLine -replace $avd, ($avd + 'x')),
    ($identity.commandLine -replace '-avd', '--avd'),
    ($identity.commandLine -replace '-port', '--port')
)) {
    $candidate = $identity | ConvertTo-Json | ConvertFrom-Json
    $candidate.commandLine = $line
    Check (-not (Test-EmulatorIdentity $candidate $owner)) "Accepted wrong tokens: $line"
}
foreach ($path in @(
    $null, '', ' ',
    (Join-Path $fixture 'foreign SDK\emulator\emulator.exe'),
    (Join-Path $sdk 'emulator\qemu\wrong\qemu-system-x86_64.exe'),
    (Join-Path $sdk 'emulator\qemu\windows-x86_64\unrelated.exe')
)) {
    $candidate = $identity | ConvertTo-Json | ConvertFrom-Json
    $candidate.executablePath = $path
    Check (-not (Test-EmulatorIdentity $candidate $owner)) "Accepted foreign path: $path"
}
""")

    def test_qemu_children_tracked_only_through_verified_live_parent(self):
        result = self.json_ps(MOCKS + r"""
$script:children = @()
foreach ($offset in 1..5) {
    $child = [pscustomobject]@{
        ProcessId = 2000000001 + $offset; ParentProcessId = $identity.pid
        CreationDate = $script:live.CreationDate.AddSeconds($offset)
        CommandLine = ('"{0}" -avd "{1}" -port 5580' -f $qemuExe, $avd)
        ExecutablePath = $qemuExe
    }
    switch ($offset) {
        2 { $child.ExecutablePath = $qemuExe -replace '\.exe$', '-headless.exe' }
        3 { $child.ExecutablePath = Join-Path $fixture 'unrelated.exe' }
        4 { $child.CommandLine = $child.CommandLine -replace '5580', '5582' }
        5 { $child.CreationDate = $script:live.CreationDate.AddSeconds(-1) }
    }
    $script:children += $child
}
$script:queries = 0
function Get-CimInstance($ClassName, $Filter) {
    Check ($ClassName -eq 'Win32_Process') 'Unexpected CIM class'
    Check ($Filter -eq "ParentProcessId = $($identity.pid)") 'Unverified parent queried'
    $script:queries++
    $script:children
}
Update-Children $owner
Check ($owner.processes.Count -eq 3) 'Expected launcher and two verified qemu children'
Check ($script:saves -eq 2) 'Discovered children not persisted'
Update-Children $owner
Check ($owner.processes.Count -eq 3 -and $script:saves -eq 2) 'Duplicate children recorded'
$before = $script:queries
$script:live.CreationDate = $script:live.CreationDate.AddSeconds(10)
Must-Throw { Update-Children $owner } 'Ownership mismatch'
Check ($script:queries -eq $before) 'Queried children of reused PID'
$script:live.ProcessId = 2000000099
Update-Children $owner
Check ($script:queries -eq $before) 'Queried children of exited parent'
$owner.processes | ConvertTo-Json -Depth 8
""")
        self.assertEqual(len(result), 3)
        for index, identity in enumerate(result):
            self.assertEqual(set(identity), IDENTITY_FIELDS)
            self.assertEqual(identity["role"], "launcher" if index == 0 else "qemu")
            self.assertEqual(identity["pid"], 2000000001 + index)
            self.assertRegex(identity["processStartTime"], r"^2026-01-02T03:04:0[5-7]")
            self.assertIn("-port 5580", identity["commandLine"])
        self.assertEqual(result[1]["parentPid"], result[0]["pid"])
        self.assertEqual(result[2]["parentPid"], result[0]["pid"])

    def test_ready_json_contract_with_mocked_boot_and_ports(self):
        result = self.json_ps(MOCKS + r"""
function Get-CimInstance { @() }
function Get-NetTCPConnection {
    [pscustomobject]@{ LocalPort = 5580; OwningProcess = $identity.pid }
    [pscustomobject]@{ LocalPort = 5581; OwningProcess = $identity.pid }
}
$clock = [Diagnostics.Stopwatch]::StartNew()
$TimeoutSeconds = 10
$json = & (Readiness-Block)
Check ($script:probes -eq 1 -and $script:saves -eq 1) 'Ready state not probed/persisted'
$json
""")
        self.assert_plan(result, 5580, self.sdk, status="ready")
        self.assertEqual(result["image"], "system-images;android-34;default;x86_64")
        self.assertEqual(len(result["processes"]), 1)
        identity = result["processes"][0]
        self.assertEqual(set(identity), IDENTITY_FIELDS)
        for field in ("pid", "commandLine", "processStartTime", "executablePath"):
            self.assertEqual(result[field], identity[field])
        self.assertEqual(identity["role"], "launcher")

    def test_unknown_port_owner_prevents_any_adb_probe(self):
        self.ps(MOCKS + r"""
function Get-CimInstance { @() }
foreach ($unknownPort in @(5580, 5581)) {
    $script:unknownPort = $unknownPort
    function Get-NetTCPConnection {
        foreach ($number in @(5580, 5581)) {
            [pscustomobject]@{
                LocalPort = $number
                OwningProcess = $(if ($number -eq $script:unknownPort) { 2000000099 } else { $identity.pid })
            }
        }
    }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $TimeoutSeconds = 10
    Must-Throw { & (Readiness-Block) } 'unproven process'
    Check ($script:probes -eq 0) 'adb invoked on an unproven port owner'
    Check ($script:saves -eq 0) 'Unknown process promoted to ready'
}
""")

    def test_port_ownership_accepts_recorded_qemu_after_launcher_exits(self):
        self.ps(MOCKS + r"""
$script:live = [pscustomobject]@{
    ProcessId = 2000000002; ParentProcessId = $identity.pid
    CreationDate = $script:live.CreationDate.AddSeconds(1)
    CommandLine = ('"{0}" -avd "{1}" -port 5580' -f $qemuExe, $avd)
    ExecutablePath = $qemuExe
}
$owner.processes = @($identity, (Convert-Identity $script:live 'qemu'))
$script:ports = @(5580, 5581)
function Get-NetTCPConnection {
    foreach ($number in $script:ports) {
        [pscustomobject]@{ LocalPort = $number; OwningProcess = $script:live.ProcessId }
    }
}
Check (Test-OwnedPorts $owner) 'Recorded live qemu child was not accepted'
$script:ports = @(5580)
Check (-not (Test-OwnedPorts $owner)) 'Single port considered ready'
$script:ports = @(5581)
Check (-not (Test-OwnedPorts $owner)) 'adb port alone considered ready'
$script:live.ProcessId = 2000000099
Must-Throw { Test-OwnedPorts $owner } 'exited'
""")

    def test_port_ownership_without_listeners_is_not_ready(self):
        self.ps(MOCKS + r"""
function Get-NetTCPConnection { @() }
Check (-not (Test-OwnedPorts $owner)) 'No listeners considered ready'
Check ($script:probes -eq 0) 'adb invoked before listeners exist'
""")

    def test_emulator_ports_retain_handles_through_listener_validation(self):
        self.ps(EMULATOR_HANDLES + r"""
$child = $script:live.PSObject.Copy()
$child.ProcessId++
$child.ParentProcessId = $identity.pid
$child.ExecutablePath = $qemuExe
$script:nativeLive += @($child)
$owner.processes += @(Convert-Identity $child 'qemu')
$script:listenerOwner = $child.ProcessId
Check (Test-OwnedPorts $owner) 'Proven child listeners rejected'
Assert-NativeDisposed
$first = $identity.pid
$second = $child.ProcessId
$expected = @("identity:$first", "get:$first", "handle:$first", "identity:$first",
    "identity:$second", "get:$second", "handle:$second", "identity:$second",
    'listeners', 'validate', 'validate', "dispose:$first", "dispose:$second")
Check (($script:nativeEvents -join ',') -ceq ($expected -join ',')) 'Ports checked without pinned identities'
""")

    def test_emulator_ports_reject_acquisition_races_and_dispose(self):
        self.ps(EMULATOR_HANDLES + r"""
foreach ($stage in @('acquire', 'handle', 'recheck')) {
    foreach ($field in @('CreationDate', 'CommandLine', 'ExecutablePath', 'ParentProcessId', 'missing', 'exited')) {
        Reset-NativeCheck
        $script:nativeLive = @($script:live.PSObject.Copy())
        $mutation = {
            switch ($field) {
                CreationDate { $script:nativeLive[0].CreationDate = $script:live.CreationDate.AddTicks(1) }
                CommandLine { $script:nativeLive[0].CommandLine += ' changed' }
                ExecutablePath { $script:nativeLive[0].ExecutablePath += '.changed' }
                ParentProcessId { $script:nativeLive[0].ParentProcessId-- }
                missing { $script:nativeLive = @() }
                exited {
                    if ($script:nativeHandles.Count) { $script:nativeHandles[0].HasExited = $true }
                    else { $script:nativeLive = @() }
                }
            }
        }
        switch ($stage) {
            acquire { $script:nativeAcquire = $mutation }
            handle { $script:nativeHandle = $mutation }
            recheck { $script:nativeRecheck = $mutation }
        }
        Must-Throw { Test-OwnedPorts $owner } 'Ownership mismatch|identity changed'
        Assert-NativeDisposed
        Check (-not $script:nativeEvents.Contains('listeners')) "Race reached ports: $stage/$field"
    }
}
""")

    def test_emulator_ports_release_all_handles_on_failures(self):
        self.ps(EMULATOR_HANDLES + r"""
$child = $script:live.PSObject.Copy()
$child.ProcessId++
$child.ParentProcessId = $identity.pid
$owner.processes += @(Convert-Identity $child 'qemu')
foreach ($failure in @('get', 'handle', 'zero', 'null', 'listeners', 'foreign')) {
    Reset-NativeCheck
    $script:nativeLive += @($child)
    switch ($failure) {
        get { $script:nativeAcquire = { param($Id) if ($Id -eq $child.ProcessId) { throw 'synthetic failure' } } }
        handle { $script:nativeHandle = { param($Handle) if ($Handle.Id -eq $child.ProcessId) { throw 'synthetic failure' } } }
        zero { $script:nativeHandle = { param($Handle) $Handle.HandleValue = [intptr]::Zero } }
        null { $script:nativeHandle = { param($Handle) $Handle.HandleValue = $null } }
        listeners { $script:nativeListeners = { throw 'synthetic failure' } }
        foreign { $script:listenerOwner = 2000000099 }
    }
    Must-Throw { Test-OwnedPorts $owner } 'synthetic failure|Cannot acquire|unproven'
    Check ($script:nativeHandles.Count -gt 0) 'Did not exercise disposal'
    Assert-NativeDisposed
}
""")

    def test_stop_catches_children_spawned_during_parent_termination(self):
        self.ps(STOP_HANDLES + r"""
$script:killHook = {
    param($Handle)
    if ($Handle.Id -lt $script:rootId + 2) {
        $child = New-Child ($Handle.Id + 1) $Handle.Id
        $script:children += @($child)
        $script:nativeLive += @($child)
    }
}
Stop-Owned $owner
Check ($owner.processes.Count -eq 3) 'Late child or grandchild missed'
Check ($script:saves -eq 2) 'Late descendants not persisted'
foreach ($id in @($identity.pid, ($identity.pid + 1), ($identity.pid + 2))) {
    $events = $script:nativeEvents
    Check ($events.IndexOf("kill:$id") -lt $events.IndexOf("wait:$id")) 'No termination wait'
    Check ($events.IndexOf("wait:$id") -lt $events.IndexOf("children:$id")) 'Snapshot preceded parent death'
    Check ($events.IndexOf("children:$id") -lt $events.IndexOf("dispose:$id")) 'Parent PID reusable during snapshot'
}
Assert-NativeDisposed
""")

    def test_child_discovery_pins_parent_before_accepting_ancestry(self):
        self.ps(EMULATOR_HANDLES + r"""
$parent = $script:live.PSObject.Copy()
$parent.ProcessId++
$parent.ParentProcessId = $identity.pid
$parent.ExecutablePath = $qemuExe
$parentIdentity = Convert-Identity $parent 'qemu'
$child = $parent.PSObject.Copy()
$child.ProcessId++
$child.ParentProcessId = $parent.ProcessId
$script:parentId = $parent.ProcessId
$script:discoveryChild = $child
$readIdentity = (Get-Item Function:Get-CimInstance).ScriptBlock
foreach ($mode in @('success', 'reused', 'query-failed')) {
    Reset-NativeCheck
    $script:nativeLive = @($parent.PSObject.Copy())
    $owner.processes = @($identity, $parentIdentity)
    $script:queries = 0
    function Get-CimInstance($ClassName, $Filter) {
        if ($Filter -match '^ParentProcessId = (\d+)$') {
            Check ([int]$Matches[1] -eq $script:parentId) 'Unproven parent queried'
            Assert-NativeRetained
            $script:queries++
            if ($mode -eq 'query-failed') { throw 'synthetic failure' }
            $script:discoveryChild
        } else { & $readIdentity $ClassName $Filter }
    }
    if ($mode -eq 'reused') {
        $script:nativeAcquire = { $script:nativeLive[0].CreationDate = $script:live.CreationDate.AddSeconds(10) }
        Must-Throw { Update-Children $owner } 'Ownership mismatch'
        Check ($script:queries -eq 0) 'Queried children of reused parent'
    } elseif ($mode -eq 'query-failed') {
        Must-Throw { Update-Children $owner } 'synthetic failure'
    } else {
        Update-Children $owner
        Check ($owner.processes.Count -eq 3) 'Proven descendant missed'
    }
    Assert-NativeDisposed
}
""")

    def test_stop_final_discovery_rejects_foreign_children(self):
        self.ps(STOP_HANDLES + r"""
$script:killHook = {
    param($Handle)
    if ($Handle.Id -ne $script:rootId) { return }
    foreach ($offset in 1..5) {
        $child = New-Child ($script:rootId + $offset) $script:rootId
        switch ($offset) {
            1 { $child.ExecutablePath = Join-Path $fixture 'foreign SDK\qemu-system-x86_64.exe' }
            2 { $child.CommandLine = $child.CommandLine -replace $avd, ($avd + 'x') }
            3 { $child.CommandLine = $child.CommandLine -replace '5580', '5582' }
            4 { $child.CreationDate = $script:live.CreationDate.AddTicks(-1) }
        }
        $script:children += @($child)
        $script:nativeLive += @($child)
    }
}
$script:queryHook = {
    param($ParentId)
    # A stale/incorrect query result must not authorize an unrelated parent.
    New-Child 2000000099 ($ParentId - 1)
}
Stop-Owned $owner
Check ($owner.processes.Count -eq 2 -and $owner.processes[1].pid -eq $identity.pid + 5) 'Foreign child adopted'
Check ($script:nativeHandles.Count -eq 2) 'Foreign child acquired'
Assert-NativeDisposed
""")

    def test_stop_uncertainty_never_persists_stopped(self):
        self.ps(STOP_HANDLES + STOP_BLOCK + r"""
$valid = $owner | ConvertTo-Json -Depth 8
function Get-Content { $valid }
$Receipt = $owner.receipt
$DryRun = $false
foreach ($failure in @('missing', 'reused', 'get', 'handle', 'recheck', 'kill', 'wait', 'query', 'child-reused', 'save', 'orphan')) {
    Reset-NativeCheck
    $script:nativeLive = @($script:live.PSObject.Copy())
    $script:killHook = {}
    $script:queryHook = {}
    $script:waitResult = $true
    $script:children = @()
    $script:survivors = @()
    $script:savedStatuses = @()
    function Save-Receipt($Owner) {
        if ($failure -eq 'save') { throw 'synthetic failure' }
        $script:savedStatuses += $Owner.status
    }
    switch ($failure) {
        missing { $script:nativeLive = @() }
        reused { $script:nativeLive[0].CreationDate = $script:live.CreationDate.AddSeconds(10) }
        get { $script:nativeAcquire = { throw 'synthetic failure' } }
        handle { $script:nativeHandle = { param($Handle) $Handle.HandleValue = [intptr]::Zero } }
        recheck { $script:nativeRecheck = { $script:nativeLive = @() } }
        kill { $script:killHook = { throw 'synthetic failure' } }
        wait { $script:waitResult = $false }
        query { $script:queryHook = { throw 'synthetic failure' } }
        orphan {
            # An unobserved intermediate child exited, leaving a matching grandchild.
            $script:survivors = @(New-Child ($script:rootId + 2) ($script:rootId + 1))
        }
        default {
            $script:killHook = {
                $child = New-Child ($script:rootId + 1) $script:rootId
                $script:children = @($child)
                $script:nativeLive += @($child.PSObject.Copy())
            }
            if ($failure -eq 'child-reused') {
                $script:nativeAcquire = { param($Id)
                    if ($Id -ne $script:rootId) { $script:nativeLive[0].CreationDate = $script:live.CreationDate.AddSeconds(10) }
                }
            }
        }
    }
    Must-Throw { & $stopBody } 'uncertain|Ownership mismatch|Cannot acquire|changed|synthetic failure|did not stop'
    Check ('stopped' -notin $script:savedStatuses) "False stopped receipt: $failure"
    Assert-NativeDisposed
    if ($failure -in @('missing', 'reused', 'get', 'handle', 'recheck')) {
        Check (-not ($script:nativeEvents | Where-Object { $_ -match '^(kill|children):' })) 'Unproven parent used'
    }
}
""")

    def test_stop_validates_entire_recorded_set_and_plan_is_read_only(self):
        self.ps(STOP_HANDLES + r"""
$child = New-Child ($identity.pid + 1) $identity.pid
$owner.processes += @(Convert-Identity $child 'qemu')
$script:nativeLive += @($child)
Stop-Owned $owner -PlanOnly
Assert-NativeDisposed
Check (-not ($script:nativeEvents | Where-Object { $_ -match '^(kill|children):' })) 'Plan terminated/discovered children'
Reset-NativeCheck
$script:nativeLive += @($child)
$script:nativeRecheck = { param($Id)
    if ($Id -eq $child.ProcessId) { $child.CommandLine += ' changed' }
}
Must-Throw { Stop-Owned $owner } 'Ownership mismatch'
Assert-NativeDisposed
Check (-not ($script:nativeEvents | Where-Object { $_ -match '^kill:' })) 'Stopped before verifying full set'
""")

    def test_stop_branch_persists_stopped_only_after_final_child_cleanup(self):
        self.ps(STOP_HANDLES + STOP_BLOCK + r"""
$valid = $owner | ConvertTo-Json -Depth 8
function Get-Content { $valid }
$Receipt = $owner.receipt
$DryRun = $false
$script:savedStatuses = @()
function Save-Receipt($Owner) {
    $script:savedStatuses += $Owner.status
    if ($Owner.status -eq 'stopped') {
        Check ($script:nativeHandles.Count -eq 2) 'Stopped before late child discovery'
        foreach ($handle in $script:nativeHandles) {
            Check $handle.HasExited 'Stopped receipt while child still alive'
            Check ($script:nativeEvents.Contains("children:$($handle.Id)")) 'Skipped final descendant snapshot'
        }
        Assert-NativeDisposed
    }
}
$script:killHook = {
    param($Handle)
    if ($Handle.Id -eq $script:rootId) {
        $child = New-Child ($script:rootId + 1) $script:rootId
        $script:children = @($child)
        $script:nativeLive += @($child)
    }
}
$result = & $stopBody | ConvertFrom-Json
Check ($result.status -eq 'stopped' -and $result.processes.Count -eq 2) 'Incorrect Stop receipt'
Check (($script:savedStatuses -join ',') -ceq 'starting,stopped') 'Final receipt not persisted in order'
""")

    def test_stop_missing_launcher_cleans_proven_child_but_remains_uncertain(self):
        self.ps(STOP_HANDLES + r"""
$child = New-Child ($script:rootId + 1) $script:rootId
$owner.processes += @(Convert-Identity $child 'qemu')
$script:nativeLive = @($child)
Must-Throw { Stop-Owned $owner } 'Cleanup uncertain'
Check ($script:nativeHandles.Count -eq 1 -and $script:nativeHandles[0].HasExited) 'Proven child not cleaned'
Check (-not $script:nativeEvents.Contains("children:$script:rootId")) 'Queried unpinned missing launcher'
Assert-NativeDisposed
""")

    def test_start_failure_keeps_launcher_through_final_discovery(self):
        self.ps(STOP_HANDLES + r"""
$failureBody = Start-FailureBlock
foreach ($captured in @($true, $false)) {
    foreach ($exited in @($true, $false)) {
        Reset-NativeCheck
        $owner.processes = @()
        if ($captured) { $owner.processes = @($identity) }
        $script:nativeLive = @($script:live)
        $script:children = @()
        $launcher = Get-Process -Id $identity.pid -ErrorAction Stop
        $null = $launcher.Handle
        $launchedAt = [datetimeoffset]$identity.processStartTime
        $script:killHook = {
            param($Handle)
            if ($Handle.Id -eq $script:rootId) {
                $child = New-Child ($script:rootId + 1) $script:rootId
                $script:children = @($child)
                $script:nativeLive += @($child)
            }
        }
        if ($exited) {
            & $script:killHook $launcher
            $launcher.HasExited = $true
            $script:nativeLive = @($script:nativeLive | Where-Object ProcessId -ne $launcher.Id)
        }
        Must-Throw {
            try { throw 'synthetic start failure' } catch { & $failureBody }
        } 'synthetic start failure.*Logs/receipt'
        Check ($owner.status -eq 'failed') 'Failure promoted to stopped'
        Check ($script:nativeHandles.Count -eq 2) 'Missed late child or reacquired borrowed launcher'
        Check $script:nativeHandles[1].HasExited 'Failed Start left late qemu running'
        Check $script:nativeHandles[1].Disposed 'Child handle leaked'
        Check (-not $launcher.Disposed -and $launcher.Held) 'Borrowed launcher released before caller finally'
        $launcher.Dispose()
        Assert-NativeDisposed
    }
}
""")

    def test_stop_receipt_roots_and_launcher_consistency_fail_closed(self):
        self.ps(MOCKS + STOP_BLOCK + r"""
$script:receiptJson = $owner | ConvertTo-Json -Depth 8
function Get-Content { $script:receiptJson }
$Receipt = $owner.receipt
$DryRun = $true
$PSBoundParameters = @{}
$owner.status = 'ready'
$valid = $owner | ConvertTo-Json -Depth 8
$script:receiptJson = $valid
# This block is in memory: Get-Content and Stop-Owned are both mocks.
& $stopBody | Out-Null
Check ($script:stops -eq 1) 'Valid synthetic receipt did not reach PlanOnly'
foreach ($field in @(
    'schemaVersion', 'avd', 'port', 'repoRoot', 'runRoot', 'receipt',
    'avdHome', 'avdPath', 'scratch', 'serial', 'processes',
    'pid', 'commandLine', 'processStartTime', 'executablePath', 'role'
)) {
    $bad = $valid | ConvertFrom-Json
    switch ($field) {
        schemaVersion { $bad.schemaVersion = 999 }
        avd { $bad.avd = 'shared_avd' }
        port { $bad.port = 5555 }
        processes { $bad.processes = @() }
        pid { $bad.pid++ }
        role { $bad.processes[0].role = 'qemu' }
        default { $bad.$field = [string]$bad.$field + '-wrong' }
    }
    $script:receiptJson = $bad | ConvertTo-Json -Depth 8
    Must-Throw { & $stopBody } 'receipt|launcher|even'
    Check ($script:stops -eq 1 -and $script:saves -eq 0) "Invalid $field reached stopping"
}
$script:receiptJson = $valid
$Receipt = Join-Path $fixture 'copied-owner.json'
Must-Throw { & $stopBody } 'receipt'
Check ($script:stops -eq 1) 'Copied receipt authorized stopping'
Check ($script:stops -eq 1 -and $script:probes -eq 0) 'Rejected Stop had side effects'
""")

    def test_stop_bomless_utf8_and_relative_receipt_use_powershell_location(self):
        result = self.json_ps(MOCKS + STOP_BLOCK + r"""
$location = Join-Path $fixture 'receipt location'
$privateRoot = Join-Path $location 'private'
$owner.runRoot = Join-Path $privateRoot $avd
$owner.avdHome = Join-Path $owner.runRoot 'avd'
$owner.avdPath = Join-Path $owner.avdHome "$avd.avd"
$owner.scratch = Join-Path $owner.runRoot 'scratch'
$owner.receipt = Join-Path $owner.runRoot 'owner.json'
$owner.sdk = Join-Path $fixture 'SDK 中文 café'
$script:live.ExecutablePath = Join-Path $owner.sdk 'emulator\emulator.exe'
$script:live.CommandLine = ('"{0}" -avd "{1}" -port 5580 -label "测试 été"' -f
    $script:live.ExecutablePath, $avd)
$owner.processes = @((Convert-Identity $script:live 'launcher'))
$owner.executablePath = $script:live.ExecutablePath
$owner.commandLine = $script:live.CommandLine
New-Item -ItemType Directory -Path $owner.runRoot -Force | Out-Null
$json = $owner | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText($owner.receipt, $json, [Text.UTF8Encoding]::new($false))
Set-Location -LiteralPath $location
$Receipt = ".\private\$avd\owner.json"
function Get-LiveProcess { throw 'Stop validation must not query real processes' }
function Stop-Owned($Owner, [switch]$PlanOnly) {
    Check $PlanOnly 'Expected PlanOnly'
    Check ($Owner.sdk -ceq (Join-Path $fixture 'SDK 中文 café')) 'SDK text corrupted'
    Check ($Owner.commandLine -ceq $script:live.CommandLine) 'Command line corrupted'
    Check ($Owner.processes[0].commandLine -ceq $script:live.CommandLine) 'Child text corrupted'
    $script:stops++
}
$result = & $stopBody | ConvertFrom-Json
Check ($script:stops -eq 1 -and $script:saves -eq 0 -and $script:probes -eq 0) 'Unexpected side effects'
Check ([IO.File]::ReadAllText($result.receipt, [Text.Encoding]::UTF8) -ceq $json) 'Receipt modified'
$result | ConvertTo-Json -Depth 8
""")
        receipt = Path(result["receipt"])
        raw = receipt.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn("SDK 中文 café", raw.decode("utf-8"))
        self.assertIn("测试 été", result["commandLine"])
        persisted = json.loads(raw.decode("utf-8"))
        for field in ("sdk", "commandLine", "executablePath"):
            self.assertEqual(persisted[field], result[field])
        self.assertEqual(
            persisted["processes"][0]["commandLine"],
            result["processes"][0]["commandLine"],
        )

    def test_stop_process_set_rejects_pid_role_ancestry_and_timestamp_tampering(self):
        self.ps(MOCKS + STOP_BLOCK + r"""
$child = $identity | ConvertTo-Json | ConvertFrom-Json
$child.pid++
$child.parentPid = $identity.pid
$child.role = 'qemu'
$child.executablePath = $qemuExe
$child.commandLine = ('"{0}" -avd "{1}" -port 5580' -f $qemuExe, $avd)
$owner.processes = @($identity, $child)
$valid = $owner | ConvertTo-Json -Depth 8
$script:receiptJson = $valid
function Get-Content { $script:receiptJson }
function Get-LiveProcess { throw 'Stop validation must not query real processes' }
$Receipt = $owner.receipt
& $stopBody | Out-Null
Check ($script:stops -eq 1) 'Child with equal launcher timestamp rejected'
$laterOwner = $valid | ConvertFrom-Json
$laterOwner.processes[1].processStartTime =
    ([datetimeoffset]$identity.processStartTime).AddTicks(1).ToString('o')
$script:receiptJson = $laterOwner | ConvertTo-Json -Depth 8
& $stopBody | Out-Null
Check ($script:stops -eq 2) 'Child newer than launcher rejected'
foreach ($case in @('duplicate', 'zero', 'negative', 'unknown-role', 'second-launcher',
    'foreign-parent', 'self-parent', 'forward-parent', 'older-child')) {
    $bad = $valid | ConvertFrom-Json
    switch ($case) {
        duplicate { $bad.processes[1].pid = $bad.pid }
        zero { $bad.processes[1].pid = 0 }
        negative { $bad.processes[1].pid = -1 }
        unknown-role { $bad.processes[1].role = 'unrelated' }
        second-launcher { $bad.processes[1].role = 'launcher' }
        foreign-parent { $bad.processes[1].parentPid = 2000000099 }
        self-parent { $bad.processes[1].parentPid = $bad.processes[1].pid }
        forward-parent {
            $later = $child | ConvertTo-Json | ConvertFrom-Json
            $later.pid++
            $bad.processes[1].parentPid = $later.pid
            $bad.processes += @($later)
        }
        older-child {
            $bad.processes[1].processStartTime =
                ([datetimeoffset]$identity.processStartTime).AddTicks(-1).ToString('o')
        }
    }
    $script:receiptJson = $bad | ConvertTo-Json -Depth 8
    Must-Throw { & $stopBody } 'process set|descended'
    Check ($script:stops -eq 2 -and $script:saves -eq 0 -and $script:probes -eq 0) "Tampering reached stop: $case"
}
""")


class WindowsPowerShellTests(EmulatorContracts, unittest.TestCase):
    shell_name = "powershell.exe"


class PowerShellCoreTests(EmulatorContracts, unittest.TestCase):
    shell_name = "pwsh"


if __name__ == "__main__":
    unittest.main()
