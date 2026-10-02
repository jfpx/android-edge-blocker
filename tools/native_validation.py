"""Reproducible native validation; Windows, Python 3.11+ standard library only.

Example (paths are relative to the repository, not the caller's working directory):
  python tools\\native_validation.py --serial emulator-5580 --apk android-edge-blocker.apk
      --profile 2.3 --receipt build\\emulator\\edge_<id>\\owner.json

Requires tools\\emulator.ps1's ready schemaVersion=1 receipt, Android SDK platform-tools,
build-tools 34.0.0, platform android-34, Java 17, existing Gradle 8.10/AGP 8.5.0,
and Windows PowerShell 5.1+ (CIM/NetTCPIP). No pip dependencies are needed.
The runner builds its fixture from native-fixture (no prebuilt fixture input).
Evidence and fixture build outputs go to a unique ignored build\\native-validation run.
--output selects a non-existing child there (repository-relative or absolute), never
the root, an existing path, a '..' path, or a symlink/reparse component.
Child names use ASCII letters/digits, underscores, hyphens, spaces and dots;
start with a letter/digit/underscore/hyphen and never end with a space or dot.
Default output remains a fresh UUID child. SDK comes only from the validated receipt;
there is no --sdk override.
It installs ONLY into a receipt-proven disposable API34 emulator; both test packages
must initially be absent. It changes only their permissions/data and the owned
display's size/density/rotation, restoring display overrides and uninstalling its
packages in finally. Emulator lifetime belongs exclusively to tools\\emulator.ps1.

Audit: replaces device_final.py/verify_final.py and necessary run-edge-e2e/retry
behavior, including the four checks appended by write_handoff.py to the 20 core
checks. Excludes fixed hashes/paths, arbitrary exec, copied binaries, old failed
retry receipts, manual SDK signing, launcher code, and publish_feature.py (credential
export/git publication). No publication/history commands exist here.
Optional: --upgrade-from <supplied-old.apk> tests 2.0 (code 6) -> 2.1 (7), or
2.1 (7) -> 2.3 (9). SDK aapt/apksigner verify versions and identical signer sets;
the old UI sets auto-start false before install -r, then preferences are checked.
Without this option upgrade coverage is explicitly skipped. No private keys needed.
--smoke retains the same ownership/fresh-package preparation, source Gradle fixture
build, installs, version/hash verification, and initial permission-denied checks.
It tests immersive portrait stopped render/five delivered taps, Start/render/four
blocked edges plus delivered center/exact boundaries, then Stop/clean render/five
delivered taps/no overlay windows. Cleanup always runs, including on failure.
Smoke skips side toggles/persistence, landscape, visible bars, short displays,
notification Stop, permission revocation/recovery, and reboot/auto-start lifecycle.
It rejects --upgrade-from. A smoke success is NOT a full validation pass.
Requires an externally running compatible SDK adb server at 127.0.0.1:5037.
Every network adb use is preceded by a non-restarting raw host:version check.
For a missing server, in a separate terminal (SDK path in $sdk):
  & "$sdk\\platform-tools\\adb.exe" -L tcp:127.0.0.1:5037 server nodaemon
This binds or fails, never replaces a server. See emulator.ps1 help for environment
setup. Keep that server unchanged throughout: a shared handshake cannot eliminate
the check/use race or guard later emulator-internal adb operations.
Receipt-exclusive locking covers preparation through cleanup. Failed/uncertain
installs never authorize uninstall; retain the disposable emulator for inspection.
Gaps: physical/OEM/API23 behavior and human visual review are not covered.
Permission transitions use package-scoped appops rather than
Settings navigation. Unsupported UI/graphics formats fail, never count as passes.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "com.simple.edgeblocker"
FIXTURE = "com.example.touchprobe"
PROFILES = {"2.1": (7, 400, 400), "2.3": (9, 300, 600)}
UPGRADE_SOURCES = {"2.1": ("2.0", 6), "2.3": ("2.1", 7)}


class ValidationError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def root_path(value):
    path = Path(value)
    return (path if path.is_absolute() else ROOT / path).resolve()


def create_output(value=None):
    base = ROOT / "build" / "native-validation"
    supplied = Path(value) if value is not None else base / uuid.uuid4().hex
    require(supplied.is_absolute() or not (supplied.drive or supplied.root),
            "Output must be repository-relative or absolute")
    require(".." not in supplied.parts and
            not any(":" in part for part in supplied.parts if part != supplied.anchor),
            "Output cannot contain '..' or alternate data streams")
    path = supplied if supplied.is_absolute() else ROOT / supplied
    require(path.is_relative_to(base) and path != base,
            "Output must be a strict child of ROOT\\build\\native-validation")
    # These paths reach Gradle's Windows batch launcher as command arguments.
    require(all(re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_. -]*", part) and
                not part.endswith((" ", ".")) for part in path.relative_to(base).parts),
            "Unsafe output child name; use ASCII letters/digits, '_', '-', spaces and dots "
            "(no leading space/dot or trailing space/dot)")

    def check_directory(component):
        info = component.lstat()
        require(not stat.S_ISLNK(info.st_mode) and
                not (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT),
                "Output cannot traverse symlink/reparse components: " + str(component))
        require(stat.S_ISDIR(info.st_mode), "Output component is not a directory: " + str(component))

    # Inspect before resolving: resolve() would hide junctions and symlinks.
    components = [*reversed(path.parents), path]
    for component in components:
        try:
            check_directory(component)
        except FileNotFoundError:
            continue
        require(component != path, "Output already exists; choose a unique new child")
    for component in components[:-1]:
        try:
            check_directory(component)
        except FileNotFoundError:
            try:
                component.mkdir()
            except FileExistsError:
                pass
            check_directory(component)
    path.mkdir()  # Exclusive creation: never reuse another run's evidence.
    check_directory(path)
    return path


def coverage(args):
    smoke = getattr(args, "smoke", False)
    extended = ["side-toggles-and-persistence", "landscape", "visible-system-bars",
                "short-display", "landscape-scrolled-stop", "notification-stop",
                "permission-revocation-and-recovery", "reboot-auto-start"]
    planned = ["shared-prepare-ownership-fresh-packages-source-build-install-version-hash",
               "initial-permission-denied", "portrait-stopped-render-five-taps",
               "portrait-start-render-blocked-edges-center-exact-boundaries",
               "stop-render-five-taps-no-windows", "cleanup"]
    skipped = ["physical-OEM-API23", "human-visual-review"]
    if smoke:
        skipped += extended
    else:
        planned += extended
    if getattr(args, "upgrade_from", None):
        planned.append("upgrade")
    else:
        skipped.append("upgrade")
    return {"scope": "smoke" if smoke else "full",
            "coverage": {"planned": planned, "skipped": skipped}}


def adb_environment():
    return {key: value for key, value in os.environ.items()
            if key.upper() not in {"ADB_SERVER_SOCKET", "ANDROID_ADB_SERVER_ADDRESS",
                                   "ANDROID_ADB_SERVER_PORT", "ADB_SERVER_PORT"}}


def parse_adb_protocol(text):
    versions = re.findall(r"^Android Debug Bridge version 1\.0\.(\d+)\r?$", text, re.M)
    require(len(versions) == 1 and 0 < int(versions[0]) <= 65535,
            "Malformed local adb version; expected 1.0.<decimal protocol>")
    return int(versions[0])


def check_adb_server(protocol, port=5037, timeout=3):
    """Raw read-only request: never invoke adb's server restart logic."""
    deadline = time.monotonic() + timeout
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as client:
            def remaining():
                value = deadline - time.monotonic()
                require(value > 0, "adb host:version timed out")
                client.settimeout(value)

            def read(count):
                data = b""
                while len(data) < count:
                    remaining()
                    part = client.recv(count - len(data))
                    require(part, "Truncated adb host:version response")
                    data += part
                return data

            remaining()
            client.sendall(b"000chost:version")
            require(read(4) == b"OKAY", "Rejected adb host:version status")
            require(read(4) == b"0004", "Malformed adb host:version length")
            version = read(4)
            require(re.fullmatch(rb"[0-9a-fA-F]{4}", version),
                    "Malformed adb server protocol")
            require(int(version, 16) == protocol,
                    f"adb protocol mismatch: SDK decimal {protocol}, server hex {version!r}")
    except (OSError, ValidationError) as error:
        raise ValidationError(
            f"Refusing adb: external server at 127.0.0.1:{port} unavailable/unsafe: {error}. "
            "No server started/stopped; use the compatible SDK server described in --help."
        ) from error


@contextmanager
def receipt_lock(path, serial):
    path = root_path(path)
    validate_receipt(json.loads(path.read_text(encoding="utf-8-sig")), path, serial)
    # Keep the inode/file permanently: unlinking locks permits concurrent owners.
    with path.with_name("native-validation.lock").open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        import msvcrt
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise ValidationError("Validation already holds this receipt lock") from error
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def parse_signers(text):
    lines = [line for line in text.splitlines() if "certificate SHA-256 digest:" in line]
    matches = [re.fullmatch(r"Signer #(\d+) certificate SHA-256 digest: ([a-fA-F0-9]{64})",
                            line) for line in lines]
    require(matches and all(matches), "Malformed APK signer SHA-256 certificates")
    signers = [match.groups() for match in matches]
    require(signers and [int(n) for n, _ in signers] == list(range(1, len(signers) + 1)),
            "Missing/ambiguous APK signer SHA-256 certificates")
    return sorted(digest.lower() for _, digest in signers)


def expected_geometry(profile, width, height, left=True, right=True):
    """Independent specification oracle; never inferred from app pixels/logs."""
    require(profile in PROFILES, "Unknown profile")
    require(width >= 101 and height >= 202, "Display too small for native touch protocol")
    _, top, bottom = PROFILES[profile]
    minimum = min(400, max(200, height // 4))
    blocked = min(top + bottom, height - minimum)
    top = blocked * top // (top + bottom)
    bottom = blocked - top
    return (50 if left else 0, 50 if right else 0, top, bottom)


def expected_frames(size, geometry):
    w, h = size
    left, right, top, bottom = geometry
    frames = [(0, 0, w, top), (0, h - bottom, w, h)]
    if left:
        frames.append((0, top, left, h - bottom))
    if right:
        frames.append((w - right, top, w, h - bottom))
    return sorted(frames)


def parse_version(text):
    names = re.findall(r"^\s*versionName=(\S+)\s*$", text, re.M)
    codes = re.findall(r"^\s*versionCode=(\S+)", text, re.M)
    require(len(names) == len(codes) == 1, "Missing/ambiguous installed package version")
    require(re.fullmatch(r"\d+", codes[0]), "Invalid installed version code")
    return names[0], int(codes[0])


def parse_apk(text):
    matches = re.findall(
        r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'",
        text, re.M)
    require(len(matches) == 1, "Missing/ambiguous APK metadata")
    package, code, name = matches[0]
    return package, name, int(code)


def parse_bounds(text):
    match = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", text)
    require(match is not None, "Malformed bounds")
    bounds = tuple(map(int, match.groups()))
    require(bounds[0] < bounds[2] and bounds[1] < bounds[3], "Empty/inverted bounds")
    return bounds


def overlay_frames(text):
    require("WINDOW MANAGER WINDOWS" in text, "Not a window dump")
    frames = []
    for block in re.split(r"(?m)^\s*Window #\d+ ", text)[1:]:
        if not re.search(r"\bpackage=" + re.escape(PACKAGE) + r"\s", block):
            continue
        if not re.search(r"\bty=APPLICATION_OVERLAY\b", block):
            continue
        found = re.findall(r"(?:\bframe=|\bmFrame=)(\S+)", block)
        require(len(found) == 1, "Overlay lacks a unique physical frame")
        frames.append(parse_bounds(found[0]))
    return sorted(frames)


def visible_bar_frames(text):
    require("WINDOW MANAGER WINDOWS" in text, "Not a window dump")
    bars = {}
    for block in re.split(r"(?m)^\s*Window #\d+ ", text)[1:]:
        kind = re.search(r"\bty=(STATUS_BAR|NAVIGATION_BAR)\b", block)
        if not kind or not re.search(r"(?m)^\s*isVisible=true\s*$", block):
            continue
        found = re.findall(r"\bframe=(\S+)", block)
        require(len(found) == 1, "System bar lacks a unique physical frame")
        require(kind[1] not in bars, "Ambiguous system bars")
        bars[kind[1]] = parse_bounds(found[0])
    require(set(bars) == {"STATUS_BAR", "NAVIGATION_BAR"}, "Both system bars must actually be visible")
    return list(bars.values())


def inside(point, rectangles):
    x, y = point
    return any(x1 <= x < x2 and y1 <= y < y2 for x1, y1, x2, y2 in rectangles)


def window_attributes(block):
    starts = list(re.finditer(r"(?m)^\s*mAttrs=\{", block))
    require(len(starts) == 1, "Missing/ambiguous window attributes")
    depth, outer = 1, []
    # API34 bars include nested paramsForRotation; only the outer flags govern input.
    for char in block[starts[0].end():]:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return "".join(outer)
        elif depth == 1:
            outer.append(char)
    raise ValidationError("Unterminated window attributes")


def controlled_fixture_windows(windows, displays, activities, size, frames, immersive):
    """Fail closed on API34 focus, transient windows, and unexpected input surfaces."""
    component = re.escape(FIXTURE) + r"/(?:\.ProbeActivity|" + re.escape(FIXTURE) + r"\.ProbeActivity)"
    resumed = re.findall(r"(?m)^\s*topResumedActivity=(.*)$", activities)
    require(len(resumed) == 1 and re.fullmatch(
        r"ActivityRecord\{[0-9a-f]+ u0 " + component + r" t\d+\}", resumed[0]),
        "Controlled fixture is not the unique resumed activity")
    require(re.findall(r"(?m)^\s*Display: mDisplayId=(\d+)\b", displays) == ["0"],
            "Requires only the controlled default display")
    focus = re.findall(r"(?m)^\s*mCurrentFocus=(.*)$", displays)
    require(len(focus) == 1 and re.fullmatch(
        r"Window\{[0-9a-f]+ u0 " + component + r"\}", focus[0]),
        "Controlled fixture does not own window focus")
    focused_app = re.findall(r"(?m)^\s*mFocusedApp=(.*)$", displays)
    require(len(focused_app) == 1 and re.fullmatch(
        r"ActivityRecord\{[0-9a-f]+ u0 " + component + r" t\d+\}", focused_app[0]),
        "Controlled fixture does not own app focus")
    require("WINDOW MANAGER WINDOWS" in windows, "Not a window dump")
    blocks = re.split(r"(?m)^\s*Window #\d+ ", windows)[1:]
    require(blocks, "Missing input windows")
    actual, fixture_count, bars, hidden_bars = [], 0, {}, set()
    w, h = size
    for block in blocks:
        visible = re.findall(r"(?m)^\s*isVisible=(true|false)\s*$", block)
        on_screen = re.findall(r"(?m)(?<!\S)isOnScreen=(true|false)[ \t]*$", block)
        require(len(visible) == len(on_screen) == 1, "Unknown window visibility")
        if visible == on_screen == ["false"]:
            continue
        inset_hidden = immersive and visible == ["false"] and on_screen == ["true"]
        require((visible == on_screen == ["true"] or inset_hidden) and
                re.findall(r"\bmHasSurface=(true|false)\b", block) == ["true"],
                "Window visibility is transitioning")
        require(re.findall(r"\bmDisplayId=(\d+)\b", block) == ["0"],
                "Unexpected input display")
        attributes = window_attributes(block)
        packages = re.findall(r"\bpackage=(\S+)", block)
        kinds = re.findall(r"\bty=(\w+)\b", attributes)
        found = re.findall(r"(?:\bframe=|\bmFrame=)(\S+)", block)
        flags = re.findall(r"(?m)^\s*fl=([^\r\n}]*)", attributes)
        require(len(packages) == len(kinds) == len(found) == len(flags) == 1,
                "Unknown input window layout")
        frame, flags = parse_bounds(found[0]), set(flags[0].split())
        title = block.splitlines()[0]
        if packages == [FIXTURE] and kinds == ["BASE_APPLICATION"]:
            require(not inset_hidden and title == focus[0] + ":" and frame == (0, 0, w, h) and
                    not flags.intersection({"NOT_FOCUSABLE", "NOT_TOUCHABLE"}),
                    "Unexpected fixture input window")
            fixture_count += 1
        elif packages == [PACKAGE] and kinds == ["APPLICATION_OVERLAY"]:
            require(not inset_hidden and "NOT_FOCUSABLE" in flags and "NOT_TOUCHABLE" not in flags,
                    "Unexpected overlay input flags")
            actual.append(frame)
        elif packages == ["com.android.systemui"] and kinds[0] in ("STATUS_BAR", "NAVIGATION_BAR"):
            kind = kinds[0]
            title_pattern = r"Window\{[0-9a-f]+ u0 " + (
                "StatusBar" if kind == "STATUS_BAR" else "NavigationBar0") + r"\}:"
            x1, y1, x2, y2 = frame
            require((not immersive or inset_hidden) and kind not in bars and kind not in hidden_bars and
                    re.fullmatch(title_pattern, title) and
                    "NOT_FOCUSABLE" in flags and 0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h and
                    ((x1 == 0 and x2 == w and (y1 == 0 or y2 == h) and y2 - y1 <= 150) or
                     (y1 == 0 and y2 == h and (x1 == 0 or x2 == w) and x2 - x1 <= 150)),
                    "Unexpected system bar input window")
            if inset_hidden:
                # API34 isOnScreen ignores client inset visibility. Require the
                # matching display inset to prove this known bar is hidden.
                inset_type = "statusBars" if kind == "STATUS_BAR" else "navigationBars"
                sources = re.findall(r"\bInsetsSource id=\S+ type=" + inset_type +
                                     r" frame=(\S+) visible=(true|false)\b", displays)
                require(sources and all(parse_bounds(bounds) == frame and visible == "false"
                                        for bounds, visible in sources),
                        "Hidden system bar lacks matching invisible display insets")
                hidden_bars.add(kind)
            else:
                bars[kind] = frame
        else:
            raise ValidationError("Unrelated visible/input window; refusing capture or tap")
    require(fixture_count == 1 and sorted(actual) == sorted(frames),
            "Controlled fixture/overlay layout differs from expected")
    require(immersive or set(bars) == {"STATUS_BAR", "NAVIGATION_BAR"},
            "Expected visible system bars missing")
    return list(bars.values())


def parse_state(text):
    pairs = [line.split("=", 1) for line in text.splitlines() if line]
    require(all(len(pair) == 2 for pair in pairs), "Malformed fixture state")
    values = dict(pairs)
    require(len(values) == len(pairs), "Duplicate fixture state keys")
    required = {"count", "size", "rotation", "immersive", "ready", "marker_x", "marker_y"}
    require(set(values) == required, "Unexpected fixture state schema")
    require(re.fullmatch(r"[1-9]\d*x[1-9]\d*", values["size"]), "Invalid fixture size")
    result = {"size": tuple(map(int, values.pop("size").split("x")))}
    for key, value in values.items():
        require(re.fullmatch(r"\d+", value), "Invalid fixture integer: " + key)
        result[key] = int(value)
    require(all(result[k] in (0, 1) for k in ("rotation", "immersive", "ready")),
            "Invalid fixture flags")
    return result


def validate_receipt(owner, path, serial):
    require(re.fullmatch(r"emulator-\d+", serial), "An explicit emulator serial is required")
    avd = owner.get("avd", "")
    require(re.fullmatch(r"edge_[a-f0-9]{32}", avd), "Not a disposable launcher AVD")
    run = ROOT / "build" / "emulator" / avd
    require(owner.get("schemaVersion") == 1 and owner.get("status") == "ready",
            "Receipt must be schemaVersion=1, status=ready")
    require(owner.get("serial") == serial and
            serial == f"emulator-{owner.get('port')}", "Receipt serial mismatch")
    port = owner.get("port")
    require(type(port) is int and 5554 <= port <= 5682 and port % 2 == 0, "Invalid port")
    for key, expected in (
            ("repoRoot", ROOT), ("runRoot", run), ("receipt", run / "owner.json"),
            ("avdHome", run / "avd"), ("avdPath", run / "avd" / (avd + ".avd")),
            ("scratch", run / "scratch")):
        require(key in owner and root_path(owner[key]) == expected.resolve(),
                "Invalid receipt root: " + key)
    require(path.resolve() == (run / "owner.json").resolve(), "Receipt path mismatch")
    require(owner.get("processes"), "Receipt has no process identities")
    require(owner.get("image") in (
        "system-images;android-34;default;x86_64",
        "system-images;android-34;google_apis;x86_64"), "Native protocol requires API34")


class Screen:
    """Decode adb exec-out screencap's raw RGB(A), with strict size/header checks."""
    def __init__(self, data):
        require(len(data) >= 12, "Truncated screencap")
        self.width, self.height, fmt = struct.unpack_from("<III", data)
        require(0 < self.width <= 8192 and 0 < self.height <= 8192, "Invalid screencap size")
        require(fmt in (1, 2, 3), "Unsupported screencap pixel format")
        self.stride = 3 if fmt == 3 else 4
        count = self.width * self.height * self.stride
        offset = len(data) - count
        require(offset in (12, 16), "Truncated/padded/unsupported screencap")
        self.data = data[offset:]
        self.size = self.width, self.height

    def pixel(self, x, y):
        require(0 <= x < self.width and 0 <= y < self.height, "Pixel outside screenshot")
        offset = (y * self.width + x) * self.stride
        return tuple(self.data[offset:offset + 3])


def inspect_render(screen, size, geometry, enabled=True, system_bars=()):
    require(screen.size == size, f"Screen {screen.size}, expected {size}")
    w, h = size
    left, right, top, bottom = geometry
    center = top + (h - top - bottom) // 2
    gray, white = (242, 242, 242), (255, 255, 255)
    # The full scan lines prove exact edges, including absence of an inset or gap.
    for x in range(w):
        expected = gray if enabled and (x < left or x >= w - right) else white
        if not (w // 2 - 20 <= x < w // 2 + 20) and not inside((x, center), system_bars):
            require(screen.pixel(x, center) == expected, f"Horizontal pixel mismatch at {x},{center}")
    for y in range(h):
        expected = gray if enabled and (y < top or y >= h - bottom) else white
        if not inside((100, y), system_bars):
            require(screen.pixel(100, y) == expected, f"Vertical pixel mismatch at 100,{y}")
    for x, y in ((25, top // 2), (w - 25, top // 2),
                 (25, h - bottom // 2), (w - 25, h - bottom // 2)):
        if not inside((x, y), system_bars):
            require(screen.pixel(x, y) == (gray if enabled else white),
                    "Corner alpha doubled or unexpected strip color")
    require(not inside((w // 2, center), system_bars), "System bars obscure the expected safe center")
    require(screen.pixel(w // 2, center) == (0, 0, 0), "Center contrast marker obscured/missing")
    require(screen.pixel(w // 2 + 30, center) == white, "Center background obscured")


def touch_points(size, geometry):
    w, h = size
    _, _, top, bottom = geometry
    center = top + (h - top - bottom) // 2
    return [(w // 2, top // 2), (w // 2, h - bottom // 2),
            (25, center), (w - 25, center), (w // 2, center)]


def boundary_points(size, geometry):
    w, h = size
    left, right, top, bottom = geometry
    y = top + (h - top - bottom) // 2
    return [(left - 1, y), (left, y), (w - right - 1, y), (w - right, y),
            (w // 2, top - 1), (w // 2, top),
            (w // 2, h - bottom - 1), (w // 2, h - bottom)]


def wait_for(probe, label, timeout=25, interval=.3):
    deadline = time.monotonic() + timeout
    while True:
        value = probe()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise ValidationError("Readiness timeout: " + label)
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


class Runner:
    def __init__(self, args, output):
        self.args, self.out = args, output
        self.owner = json.loads(root_path(args.receipt).read_text(encoding="utf-8-sig"))
        validate_receipt(self.owner, root_path(args.receipt), args.serial)
        self.sdk = Path(self.owner["sdk"])
        self.adb_path = self.sdk / "platform-tools" / "adb.exe"
        self.aapt = self.sdk / "build-tools" / "34.0.0" / "aapt.exe"
        self.apksigner = self.aapt.with_name("apksigner.bat")
        self.results = {"profile": args.profile, "serial": args.serial, "success": False,
                        "checks": [], **coverage(args)}
        self.sequence = 0
        self.installed = []
        self.display_original = {}
        self.size, self.rotation = (1080, 1920), 0
        self.running = False
        self.immersive = True
        self.sides = {"cbLeftEdge": True, "cbRightEdge": True}
        self.geometry = expected_geometry(args.profile, *self.size)

    def save(self):
        (self.out / "results.json").write_text(json.dumps(self.results, indent=2), encoding="utf-8")

    def check(self, name, condition, evidence):
        self.results["checks"].append({"name": name, "passed": bool(condition), "evidence": evidence})
        self.save()
        require(condition, name + ": " + str(evidence))
        print("PASS", name, flush=True)

    def command(self, argv, timeout=60, binary=False, env=None):
        options = {"env": env} if env is not None else {}
        result = subprocess.run(list(map(str, argv)), cwd=ROOT, capture_output=True,
                                timeout=timeout, **options)
        self.sequence += 1
        stderr = result.stderr.decode("utf-8", errors="replace")
        stdout = result.stdout.decode("utf-8", errors="replace") if not binary else "<binary>"
        with (self.out / "commands.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"command": list(map(str, argv)), "returncode": result.returncode,
                                  "stdout": stdout, "stderr": stderr}) + "\n")
        require(result.returncode == 0, f"Command failed ({result.returncode}): {argv}\n{stdout}\n{stderr}")
        # Windows emulator console replies can use CRCRLF. Keep raw evidence and
        # binary payloads intact; collapse only CRs immediately preceding LF.
        return result.stdout if binary else re.sub(r"\r+\n", "\n", stdout)

    def guard(self):
        result = self.command(["powershell.exe", "-NoProfile", "-NonInteractive", "-File",
                               ROOT / "tools" / "native_owner_check.ps1",
                               "-Receipt", root_path(self.args.receipt), "-Serial", self.args.serial])
        require(result.strip() == "owned", "Ownership check did not confirm emulator")

    def adb(self, *args, mutate=False, timeout=60, binary=False):
        if args[:2] == ("exec-out", "screencap"):
            self.fixture_guard()
        # Reads can disclose personal content too, or target a reused emulator port.
        self.guard()
        environment = adb_environment()
        endpoint = [self.adb_path, "-H", "127.0.0.1", "-P", "5037"]
        protocol = parse_adb_protocol(self.command([*endpoint, "version"], env=environment))
        check_adb_server(protocol)
        return self.command([*endpoint, "-s", self.args.serial, *args],
                            timeout, binary, env=environment)

    def shell(self, *args, mutate=False, timeout=60):
        return self.adb("shell", *map(str, args), mutate=mutate, timeout=timeout)

    def windows(self):
        return self.shell("dumpsys", "window", "windows")

    def frames(self):
        return overlay_frames(self.windows())

    def expect_windows(self, name, frames):
        wait_for(lambda: self.frames() == sorted(frames), name)
        self.check(name, self.frames() == sorted(frames), frames)

    def ui(self):
        remote = "/sdcard/native-validation-" + uuid.uuid4().hex + ".xml"
        self.shell("uiautomator", "dump", remote, mutate=True, timeout=30)
        raw = self.shell("cat", remote)
        self.shell("rm", remote, mutate=True)
        (self.out / f"ui-{self.sequence}.xml").write_text(raw, encoding="utf-8")
        root = ET.fromstring(raw)
        require(not any(n.get("resource-id") in ("android:id/aerr_close", "android:id/aerr_wait")
                        for n in root.iter("node")), "App/System UI crash or ANR dialog")
        return root

    def launch(self, component, *extras):
        text = self.shell("am", "start", "-W", "-f", "0x14000000", "-n", component,
                          *extras, mutate=True)
        require("Error:" not in text and "Status: ok" in text, "Activity launch failed: " + text)

    def main(self):
        self.launch(PACKAGE + "/.MainActivity")
        wait_for(lambda: any(n.get("package") == PACKAGE for n in self.ui().iter("node")), "main UI")

    def node(self, resource):
        """Find a control using only safe-center scrolling; never tap through an overlay."""
        w, h = self.size
        _, _, top, bottom = self.geometry if self.running else (0, 0, 80, 140)
        low, high = top + 20, h - bottom - 20
        for attempt in range(16):
            root = self.ui()
            nodes = [n for n in root.iter("node") if n.get("resource-id") == PACKAGE + ":id/" + resource]
            require(len(nodes) <= 1, "Ambiguous control: " + resource)
            direction = 1 if attempt < 8 else -1
            if nodes:
                n = nodes[0]
                x1, y1, x2, y2 = parse_bounds(n.get("bounds", ""))
                y = (y1 + y2) // 2
                if low <= y <= high and x1 < w // 2 < x2:
                    return n
                direction = 1 if y > high else -1
            start, end = (high, low) if direction == 1 else (low, high)
            self.shell("input", "swipe", w // 2, start, w // 2, end, 450, mutate=True)
        raise ValidationError("Control not reachable in safe center: " + resource)

    def click(self, resource):
        n = self.node(resource)
        require(n.get("enabled") == "true", "Disabled control: " + resource)
        x1, y1, x2, y2 = parse_bounds(n.get("bounds", ""))
        self.shell("input", "tap", (x1 + x2) // 2, (y1 + y2) // 2, mutate=True)

    def checkbox(self, resource, desired):
        n = self.node(resource)
        require(n.get("checked") in ("true", "false"), "Not a checkbox")
        if (n.get("checked") == "true") != desired:
            self.click(resource)
        require((self.node(resource).get("checked") == "true") == desired, "Checkbox did not change")
        if resource in self.sides:
            self.sides[resource] = desired
            self.geometry = expected_geometry(self.args.profile, *self.size, *self.sides.values())

    def start(self):
        self.main()
        self.click("btnStart")
        self.running = True
        self.expect_windows("service-running", expected_frames(self.size, self.geometry))

    def stop(self, name):
        self.main()
        self.click("btnStop")
        self.expect_windows(name + "-windows", [])
        self.running = False

    def state(self):
        return parse_state(self.shell("run-as", FIXTURE, "cat", "files/state.txt"))

    def fixture_guard(self, point=None):
        state = self.state()
        w, h = self.size
        _, _, top, bottom = self.geometry
        require(state["ready"] == 1 and state["size"] == self.size and
                state["rotation"] == self.rotation and state["immersive"] == int(self.immersive) and
                state["marker_x"] == w // 2 and state["marker_y"] == top + (h - top - bottom) // 2,
                "Fixture state does not match expected layout")
        activities = self.shell("dumpsys", "activity", "activities")
        displays = self.shell("dumpsys", "window", "displays")
        bars = controlled_fixture_windows(
            self.windows(), displays, activities, self.size,
            expected_frames(self.size, self.geometry) if self.running else [], self.immersive)
        if point is not None:
            require(0 <= point[0] < w and 0 <= point[1] < h and not inside(point, bars),
                    "Tap outside controlled fixture input area")
        return state

    def probe(self, rotation=0, immersive=True, size=None):
        self.rotation = rotation
        self.immersive = immersive
        self.size = size or ((1080, 1920) if rotation == 0 else (1920, 1080))
        self.geometry = expected_geometry(self.args.profile, *self.size, *self.sides.values())
        w, h = self.size
        _, _, top, bottom = self.geometry
        center = top + (h - top - bottom) // 2
        self.launch(FIXTURE + "/.ProbeActivity", "--ei", "rotation", str(rotation),
                    "--ez", "immersive", str(immersive).lower(),
                    "--ei", "marker_x", str(w // 2), "--ei", "marker_y", str(center))
        wait_for(lambda: "state.txt" in self.shell("run-as", FIXTURE, "ls", "files").splitlines(),
                 "fixture state file")
        def ready():
            state = self.state()
            return (state["ready"] == 1 and state["size"] == self.size
                    and state["rotation"] == rotation and state["immersive"] == int(immersive)
                    and state["marker_x"] == w // 2 and state["marker_y"] == center)
        wait_for(ready, "fixture layout")
        # Dismiss ONLY Android's immersive coachmark; no unrelated apps/settings.
        root = self.ui()
        coaches = [n for n in root.iter("node")
                   if n.get("resource-id") in ("android:id/ok", "com.android.systemui:id/ok")
                   and n.get("package") == "com.android.systemui"]
        if coaches:
            require(len(coaches) == 1, "Ambiguous immersive coachmark")
            x1, y1, x2, y2 = parse_bounds(coaches[0].get("bounds", ""))
            self.shell("input", "tap", (x1+x2)//2, (y1+y2)//2, mutate=True)

    def capture(self, name):
        raw = self.adb("exec-out", "screencap", binary=True)
        screen = Screen(raw)
        (self.out / (name + ".screencap")).write_bytes(raw)
        (self.out / (name + ".png")).write_bytes(
            self.adb("exec-out", "screencap", "-p", binary=True))
        return screen

    def visible_bars(self, rotation):
        self.probe(rotation, False)
        name = ("portrait" if rotation == 0 else "landscape") + "-visible-bars"
        text = self.windows()
        (self.out / (name + "-windows.txt")).write_text(text, encoding="utf-8")
        bars = visible_bar_frames(text)
        w, h = self.size
        require(all(0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h
                    and (x2 - x1 <= 150 or y2 - y1 <= 150)
                    for x1, y1, x2, y2 in bars), "Unexpected system-owned bar area")
        self.expect_windows(name + "-frames", expected_frames(self.size, self.geometry))
        screen = self.capture(name)
        inspect_render(screen, self.size, self.geometry, system_bars=bars)
        self.check(name + "-pixels", True, {"system_owned_exclusions": bars})
        points = touch_points(self.size, self.geometry)
        expected = [0, 0, 0, 0, 1]
        selected = [(p, e) for p, e in zip(points, expected) if not inside(p, bars)]
        require(len(selected) >= 3 and selected[-1][1] == 1, "Insufficient fixture-owned touch points")
        self.taps(name + "-touches", [p for p, _ in selected], [e for _, e in selected])

    def render(self, name, enabled=True):
        self.expect_windows(name + "-frames",
                            expected_frames(self.size, self.geometry) if enabled else [])
        # Two identical samples avoid mistaking a rotation animation for a steady frame.
        previous = self.adb("exec-out", "screencap", binary=True)
        def stable():
            nonlocal previous
            current = self.adb("exec-out", "screencap", binary=True)
            same = current == previous
            previous = current
            return same
        wait_for(stable, name + " stable pixels")
        screen = self.capture(name)
        inspect_render(screen, self.size, self.geometry, enabled)
        self.check(name + "-pixels-corners-contrast", True, {"size": self.size, "geometry": self.geometry})

    def taps(self, name, points, expected):
        delivered = []
        for x, y in points:
            old = self.fixture_guard((x, y))["count"]
            self.shell("input", "tap", x, y, mutate=True)
            # input is synchronous; allow the activity a bounded dispatch/drain interval.
            time.sleep(.3)
            delivered.append(self.fixture_guard()["count"] - old)
        evidence = {"points": points, "delivered": delivered, "expected": expected}
        if delivered != expected:
            try:
                (self.out / (name + "-input.txt")).write_text(
                    self.shell("dumpsys", "input"), encoding="utf-8")
            except (OSError, subprocess.SubprocessError, ValidationError) as error:
                evidence["input_diagnostic_error"] = str(error)
        self.check(name, delivered == expected, evidence)

    def five(self, name, expected):
        self.taps(name, touch_points(self.size, self.geometry), expected)

    def set_display(self, size, density=None):
        for key in ("size", "density"):
            if key not in self.display_original:
                text = self.shell("wm", key)
                match = re.search(r"Override " + key + r":\s*(\S+)", text)
                require("Physical " + key + ":" in text, "Cannot read display " + key)
                self.display_original[key] = match[1] if match else "reset"
        self.shell("wm", "size", size, mutate=True)
        if density is not None:
            self.shell("wm", "density", density, mutate=True)

    def verify_installed(self):
        actual = parse_version(self.shell("dumpsys", "package", PACKAGE))
        self.check("installed-profile", actual == (self.args.profile, PROFILES[self.args.profile][0]), actual)
        paths = self.shell("pm", "path", PACKAGE).splitlines()
        require(len(paths) == 1 and paths[0].startswith("package:"), "Expected one installed base APK")
        remote = paths[0][len("package:"):]
        installed = self.adb("exec-out", "cat", remote, binary=True)
        self.check("installed-apk-bytes", hashlib.sha256(installed).hexdigest() ==
                   self.results["apk_sha256"], "Installed APK matches explicit input")

    def install_owned(self, package, path, replace=False):
        if replace:
            require(package in self.installed, "Upgrade requires a successfully owned install")
            # An uncertain replacement must not grant cleanup authority either.
            self.installed.remove(package)
        result = self.adb("install", "-r" if replace else "-R", str(path),
                          mutate=True, timeout=120)
        require(result.strip().splitlines()[-1:] == ["Success"] and
                not re.search(r"\b(error|failure|failed)\b", result, re.I),
                "Installation did not unambiguously report success; package retained")
        self.installed.append(package)

    def prepare_upgrade(self, candidate):
        source = getattr(self.args, "upgrade_from", None)
        if not source:
            self.results["upgrade"] = {"status": "skipped", "reason": "--upgrade-from not supplied"}
            return None
        old = self.out / "upgrade-from.apk"
        old.write_bytes(root_path(source).read_bytes())
        expected = UPGRADE_SOURCES[self.args.profile]
        actual = parse_apk(self.command([self.aapt, "dump", "badging", old]))
        require(actual == (PACKAGE, *expected), "Unsupported upgrade source version: " + str(actual))
        certificates = []
        for apk in (old, candidate):
            certificates.append(parse_signers(self.command(
                [self.apksigner, "verify", "--verbose", "--print-certs", apk])))
        require(certificates[0] == certificates[1], "Upgrade APK signer mismatch")
        self.results["upgrade"] = {
            "status": "verified-inputs", "from": expected, "to": self.args.profile,
            "source_sha256": hashlib.sha256(old.read_bytes()).hexdigest(),
            "signer_sha256": certificates[0]}
        return old

    def upgrade(self, candidate):
        expected = UPGRADE_SOURCES[self.args.profile]
        self.check("upgrade-source-installed",
                   parse_version(self.shell("dumpsys", "package", PACKAGE)) == expected, expected)
        self.main()
        self.checkbox("cbAutoStart", False)
        # Backgrounding flushes SharedPreferences.apply before force-stop.
        self.shell("input", "keyevent", "KEYCODE_HOME", mutate=True)
        self.shell("am", "force-stop", PACKAGE, mutate=True)
        self.main()
        self.check("upgrade-source-auto-start-false",
                   self.node("cbAutoStart").get("checked") == "false", "Persisted old UI setting")
        self.shell("input", "keyevent", "KEYCODE_HOME", mutate=True)
        self.shell("am", "force-stop", PACKAGE, mutate=True)
        self.install_owned(PACKAGE, candidate, replace=True)
        self.verify_installed()
        self.main()
        for resource, expected_value in (("cbAutoStart", "false"), ("cbLeftEdge", "true"),
                                         ("cbRightEdge", "true")):
            actual = self.node(resource).get("checked")
            self.check("upgrade-preserved-" + resource, actual == expected_value, actual)
        self.results["upgrade"]["status"] = "passed"
        self.save()

    def prepare(self):
        self.guard()
        require(self.shell("getprop", "ro.kernel.qemu").strip() == "1", "Not an emulator")
        require(self.shell("getprop", "ro.build.version.sdk").strip() == "34", "Requires API34")
        avd = self.adb("emu", "avd", "name").splitlines()
        require(avd == [self.owner["avd"], "OK"], "Connected AVD differs from receipt")
        require(self.shell("am", "get-current-user").strip() == "0", "Requires disposable primary user")
        users = re.findall(r"UserInfo\{(\d+):", self.shell("pm", "list", "users"))
        require(users == ["0"], "Requires a fresh single-user emulator")
        packages = set(self.shell("pm", "list", "packages", "-u").splitlines())
        require(all("package:" + p not in packages for p in (PACKAGE, FIXTURE)),
                "Test packages already installed; use a fresh launcher emulator (no data resets)")
        data = root_path(self.args.apk).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        apk = self.out / "candidate.apk"
        apk.write_bytes(data)
        self.results["apk_sha256"] = digest
        if self.args.expected_sha256:
            require(digest == self.args.expected_sha256.lower(), "User-specified APK SHA256 mismatch")
        metadata = parse_apk(self.command([self.aapt, "dump", "badging", apk]))
        require(metadata == (PACKAGE, self.args.profile, PROFILES[self.args.profile][0]),
                "APK package/version does not match profile: " + str(metadata))
        old = self.prepare_upgrade(apk)
        fixture_output = self.out / "fixture"
        environment = dict(os.environ, ANDROID_HOME=str(self.sdk), ANDROID_SDK_ROOT=str(self.sdk))
        self.command([ROOT / "gradlew.bat", "--no-daemon", "--console=plain",
                      ":native-fixture:assembleDebug",
                      "-PnativeValidationOutput=" + str(fixture_output.relative_to(ROOT))],
                     timeout=600, env=environment)
        fixture = fixture_output / "outputs" / "apk" / "debug" / "native-fixture-debug.apk"
        require(parse_apk(self.command([self.aapt, "dump", "badging", fixture])) ==
                (FIXTURE, "1.0", 1), "Unexpected fixture APK")
        self.results["fixture_sha256"] = hashlib.sha256(fixture.read_bytes()).hexdigest()
        # Building can take minutes; recheck no other actor installed either package.
        packages = set(self.shell("pm", "list", "packages", "-u").splitlines())
        require(all("package:" + p not in packages for p in (PACKAGE, FIXTURE)),
                "Test package appeared during build; refusing to replace it")
        for package, path in ((PACKAGE, old or apk), (FIXTURE, fixture)):
            self.install_owned(package, path)
        self.set_display("1080x1920", 420)
        if old:
            self.upgrade(apk)
        else:
            self.verify_installed()
        self.shell("pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS", mutate=True)
        self.shell("appops", "set", PACKAGE, "SYSTEM_ALERT_WINDOW", "deny", mutate=True)
        self.main()
        status = self.node("tvStatus").get("text", "")
        self.check("permission-denied-visible", "未授予" in status and "已停止" in status, status)
        self.click("btnStart")
        self.expect_windows("permission-denied-no-windows", [])
        self.main()
        self.shell("appops", "set", PACKAGE, "SYSTEM_ALERT_WINDOW", "allow", mutate=True)

    def notification_stop(self):
        self.shell("cmd", "statusbar", "expand-notifications", mutate=True)
        def stop_node():
            root = self.ui()
            # Scope Stop to the notification containing this app's exact title.
            for parent in root.iter("node"):
                descendants = list(parent.iter("node"))
                if parent.get("resource-id") != "com.android.systemui:id/expandableNotificationRow":
                    continue
                if not any(n.get("text") == "Edge Blocker Active" or
                           (n.get("resource-id") == "android:id/app_name" and
                            n.get("text") == "Simple Edge Blocker") for n in descendants):
                    continue
                stops = [n for n in descendants if n.get("text", "").casefold() == "stop"]
                if len(stops) == 1:
                    return (stops[0],)
                expand = [n for n in descendants if n.get("resource-id") ==
                          "android:id/expand_button" and n.get("clickable") == "true"]
                if len(expand) == 1:
                    x1, y1, x2, y2 = parse_bounds(expand[0].get("bounds", ""))
                    self.shell("input", "tap", (x1+x2)//2, (y1+y2)//2, mutate=True)
            return None
        node = wait_for(stop_node, "notification Stop action", timeout=40)[0]
        x1, y1, x2, y2 = parse_bounds(node.get("bounds", ""))
        self.shell("input", "tap", (x1+x2)//2, (y1+y2)//2, mutate=True)
        self.shell("cmd", "statusbar", "collapse", mutate=True)
        self.running = False
        self.expect_windows("notification-stop-windows", [])
        self.probe()
        self.five("notification-stop-touches", [1] * 5)

    def run(self):
        require(not (getattr(self.args, "smoke", False) and self.args.upgrade_from),
                "--smoke cannot be combined with --upgrade-from")
        self.prepare()
        self.probe()
        self.render("portrait-off", False)
        self.five("baseline-five-taps", [1] * 5)
        self.start()
        self.probe()
        self.render("portrait-on")
        self.five("portrait-touch", [0, 0, 0, 0, 1])
        self.taps("portrait-boundaries", boundary_points(self.size, self.geometry), [0, 1, 1, 0] * 2)
        if getattr(self.args, "smoke", False):
            self.stop("main-stop")
            self.probe()
            self.render("stopped", False)
            self.five("stop-all-touches", [1] * 5)
            self.expect_windows("smoke-final-no-windows", [])
        else:
            self.run_full()
        self.verify_installed()
        self.results["success"] = True
        self.save()

    def run_full(self):
        for side in ("Left", "Right"):
            self.main()
            self.checkbox("cb" + side + "Edge", False)
            expected = [0, 0, int(side == "Left"), int(side == "Right"), 1]
            self.probe()
            off_geometry = expected_geometry(self.args.profile, *self.size,
                                             side != "Left", side != "Right")
            self.expect_windows(side.lower() + "-off-live-frames", expected_frames(self.size, off_geometry))
            self.five(side.lower() + "-off-live", expected)
            self.shell("am", "force-stop", PACKAGE, mutate=True)
            self.running = False
            self.main()
            values = [self.node("cb" + s + "Edge").get("checked") == "true" for s in ("Left", "Right")]
            self.check(side.lower() + "-off-persisted", values ==
                       [side != "Left", side != "Right"], values)
            self.geometry = off_geometry
            self.start()
            self.probe()
            self.five(side.lower() + "-off-restarted", expected)
            self.main()
            self.checkbox("cb" + side + "Edge", True)
        self.probe(1)
        self.render("landscape-on")
        self.five("landscape-touch", [0, 0, 0, 0, 1])
        self.taps("landscape-boundaries", boundary_points(self.size, self.geometry), [0, 1, 1, 0] * 2)
        self.visible_bars(0)
        self.visible_bars(1)
        # MainActivity has no orientation lock; lock only this disposable display for
        # the landscape safe-center scrolling test, then return to free rotation.
        original_rotation = self.shell("cmd", "window", "user-rotation").strip()
        require(re.fullmatch(r"(free|lock [0-3])", original_rotation), "Unknown display rotation mode")
        self.display_original["rotation"] = original_rotation
        self.shell("cmd", "window", "user-rotation", "lock", 1, mutate=True)
        self.stop("landscape-ui-stop-scrolled")
        self.probe(1)
        self.five("landscape-ui-stop-scrolled", [1] * 5)
        self.shell("cmd", "window", "user-rotation", "free", mutate=True)
        self.probe()
        self.start()
        # Explicit short-display safety geometry: 2.1=260/260, 2.3=173/347.
        self.set_display("720x1280")
        self.probe(1, size=(1280, 720))
        self.render("short-screen-safety")
        self.five("short-screen-touch", [0, 0, 0, 0, 1])
        self.set_display("1080x1920")
        self.probe()
        self.stop("main-stop")
        self.probe()
        self.render("stopped", False)
        self.five("stop-all-touches", [1] * 5)
        self.start()
        self.notification_stop()
        self.start()
        self.shell("appops", "set", PACKAGE, "SYSTEM_ALERT_WINDOW", "deny", mutate=True)
        self.expect_windows("permission-revoked-cleanup", [])
        self.running = False
        self.main()
        status = self.node("tvStatus").get("text", "")
        self.check("permission-revoked-error", "已停止" in status and "未授予" in status
                   and "悬浮窗权限已撤销" in status, status)
        self.probe()
        self.five("permission-revoked-touches", [1] * 5)
        self.shell("appops", "set", PACKAGE, "SYSTEM_ALERT_WINDOW", "allow", mutate=True)
        self.start()
        self.main()
        self.checkbox("cbAutoStart", True)
        self.shell("input", "keyevent", "KEYCODE_HOME", mutate=True)
        self.shell("appops", "write-settings", mutate=True)
        self.shell("sync", mutate=True)
        before = self.shell("cat", "/proc/sys/kernel/random/boot_id").strip()
        self.adb("reboot", mutate=True)
        self.adb("wait-for-device", timeout=150)
        wait_for(lambda: self.shell("getprop", "sys.boot_completed").strip() == "1", "reboot", timeout=150)
        self.guard()
        after = self.shell("cat", "/proc/sys/kernel/random/boot_id").strip()
        self.check("real-reboot", bool(before) and before != after, {"before": before, "after": after})
        self.shell("input", "keyevent", "KEYCODE_WAKEUP", mutate=True)
        self.shell("wm", "dismiss-keyguard", mutate=True)
        self.running = True
        self.probe()
        # Never press Start before this assertion: it must be BootReceiver's service.
        self.render("boot-restored")
        self.five("boot-restored-clean", [0, 0, 0, 0, 1])

    def cleanup(self):
        errors = []
        for package in reversed(self.installed):
            try:
                text = self.adb("uninstall", package, mutate=True, timeout=90)
                require(text.strip() == "Success", "Uninstall failed: " + text)
            except (OSError, subprocess.SubprocessError, ValidationError) as error:
                errors.append(str(error))
        for key, value in self.display_original.items():
            try:
                if key == "rotation":
                    self.shell("cmd", "window", "user-rotation", *value.split(), mutate=True)
                else:
                    self.shell("wm", key, value, mutate=True)
            except (OSError, subprocess.SubprocessError, ValidationError) as error:
                errors.append(str(error))
        self.results["cleanup_errors"] = errors
        if errors:
            self.results["success"] = False
        self.save()
        require(not errors, "Cleanup failed; retain emulator receipt: " + "; ".join(errors))


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--serial", required=True)
    result.add_argument("--apk", required=True)
    result.add_argument("--profile", required=True, choices=PROFILES)
    result.add_argument("--receipt", required=True, help="Ready ownership receipt; sole SDK source")
    result.add_argument("--expected-sha256", type=lambda x: x.lower())
    scope = result.add_mutually_exclusive_group()
    scope.add_argument("--smoke", action="store_true",
                       help="Focused real portrait Start/Stop render/touch/boundary checks; "
                            "same preparation and cleanup, NOT full lifecycle coverage")
    scope.add_argument("--upgrade-from", metavar="APK",
                        help="Supplied same-signer 2.0/code6 APK for 2.1, or 2.1/code7 for 2.3")
    result.add_argument("--output", metavar="DIRECTORY",
                        help="New unique child of ROOT\\build\\native-validation; "
                             "repository-relative or absolute, no '..'/symlink/reparse components; "
                             "default: UUID child")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    require(os.name == "nt", "Live ownership validation requires Windows")
    if args.expected_sha256:
        require(re.fullmatch(r"[a-f0-9]{64}", args.expected_sha256), "Invalid expected SHA256")
    output = create_output(args.output)
    print("Evidence:", output, flush=True)
    runner = None
    with receipt_lock(args.receipt, args.serial):
        try:
            runner = Runner(args, output)
            runner.run()
        except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError, ValidationError) as error:
            if runner:
                runner.results["error"] = str(error)
                runner.results["success"] = False
                runner.save()
            else:
                (output / "results.json").write_text(json.dumps(
                    {"success": False, "error": str(error), **coverage(args)}),
                                                    encoding="utf-8")
            raise
        finally:
            if runner:
                runner.cleanup()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError, ValidationError) as exc:
        print("FAIL:", exc, file=sys.stderr)
        sys.exit(1)
