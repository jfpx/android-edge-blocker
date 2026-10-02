"""Offline contracts: python -B -m unittest discover -s tests -p test_native_validation.py -v."""

import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, call, patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_validation", ROOT / "tools" / "native_validation.py")
nv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nv)


class AdbProtocolTests(unittest.TestCase):
    def setUp(self):
        self.process = self.enterContext(patch.object(
            nv.subprocess, "run", side_effect=AssertionError("Live subprocess forbidden")))

    @contextlib.contextmanager
    def server(self, chunks, stall=False, delay=.003):
        request, errors = bytearray(), []
        release = threading.Event()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(3)
            port = listener.getsockname()[1]

            def serve():
                try:
                    with listener.accept()[0] as peer:
                        peer.settimeout(3)
                        while len(request) < 16:
                            part = peer.recv(16 - len(request))
                            if not part:
                                break
                            request.extend(part)
                            if request == b"000chost:version":
                                break
                        for chunk in chunks:
                            peer.sendall(chunk)
                            if delay:
                                release.wait(delay)
                        if stall:
                            release.wait(3)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            connect = socket.create_connection

            def loopback_only(address, *args, **kwargs):
                self.assertEqual(address, ("127.0.0.1", port))
                return connect(address, *args, **kwargs)

            try:
                with patch.object(nv.socket, "create_connection", side_effect=loopback_only):
                    yield port
            finally:
                release.set()
                thread.join(4)
                self.assertFalse(thread.is_alive(), "Mock socket server did not stop")
                self.assertEqual(errors, [])
                self.assertEqual(bytes(request), b"000chost:version")
                self.process.assert_not_called()

    def test_local_protocol_is_decimal_and_allows_sdk_details(self):
        for protocol in (1, 41, 42, 65535):
            with self.subTest(protocol=protocol):
                text = (f"Android Debug Bridge version 1.0.{protocol}\r\n"
                        "Version 35.0.2-12147458\r\nInstalled as arbitrary-adb.exe\r\n")
                self.assertEqual(nv.parse_adb_protocol(text), protocol)

    def test_local_protocol_rejects_missing_invalid_and_ambiguous_versions(self):
        valid = "Android Debug Bridge version 1.0.41"
        for text in ("", "1.0.41", valid + "\n" + valid, valid.replace("41", "0"),
                     valid.replace("41", "65536"), valid.replace("41", "-1"),
                     valid.replace("41", "0x29"), valid.replace("41", "41.0"),
                     valid.replace("1.0.", "1.1."), valid + " trailing", " " + valid):
            with self.subTest(text=text), self.assertRaises(nv.ValidationError):
                nv.parse_adb_protocol(text)

    def test_fragmented_response_hex_0029_matches_decimal_41(self):
        with self.server([bytes([byte]) for byte in b"OKAY00040029"]) as port:
            self.assertIsNone(nv.check_adb_server(41, port=port, timeout=2))

    def test_hex_letters_are_case_insensitive(self):
        for version in (b"00af", b"00AF"):
            with self.subTest(version=version), self.server([b"OKAY0004", version]) as port:
                self.assertIsNone(nv.check_adb_server(175, port=port, timeout=2))

    def test_mismatch_never_confuses_decimal_with_hex(self):
        with self.server([b"OKAY00040041"]) as port:
            with self.assertRaisesRegex(nv.ValidationError, "protocol mismatch.*SDK decimal 41"):
                nv.check_adb_server(41, port=port)

    def test_status_length_and_nonhex_protocol_are_rejected(self):
        cases = [
            (b"FAIL0004oops", "status"), (b"NOPE00040029", "status"),
            (b"okay00040029", "status"), (b"OKAY00000029", "length"),
            (b"OKAY00030029", "length"), (b"OKAY00050029", "length"),
            (b"OKAYffff0029", "length"), (b"OKAYzzzz0029", "length"),
            (b"OKAY 0040029", "length"), (b"OKAY0004zz29", "protocol"),
            (b"OKAY0004+029", "protocol"), (b"OKAY0004\xff029", "protocol"),
        ]
        for response, reason in cases:
            with self.subTest(response=response), self.server([response]) as port:
                with self.assertRaisesRegex(nv.ValidationError, reason) as caught:
                    nv.check_adb_server(41, port=port)
                self.assertIn(f"127.0.0.1:{port}", str(caught.exception))
                self.assertIn("No server started/stopped", str(caught.exception))

    def test_every_truncated_response_prefix_is_rejected(self):
        response = b"OKAY00040029"
        for length in range(len(response)):
            with self.subTest(length=length), self.server([response[:length]]) as port:
                with self.assertRaisesRegex(nv.ValidationError, "Truncated"):
                    nv.check_adb_server(41, port=port)

    def test_timeout_at_each_response_stage_is_bounded(self):
        for prefix in (b"", b"OK", b"OKAY", b"OKAY0004", b"OKAY000400"):
            with self.subTest(prefix=prefix), self.server([prefix], stall=True) as port:
                start = time.monotonic()
                with self.assertRaisesRegex(nv.ValidationError, "timed out"):
                    nv.check_adb_server(41, port=port, timeout=.1)
                self.assertLess(time.monotonic() - start, 2)

    def test_slow_fragments_share_one_total_deadline(self):
        with self.server([bytes([byte]) for byte in b"OKAY00040029"], delay=.08) as port:
            start = time.monotonic()
            with self.assertRaisesRegex(nv.ValidationError, "timed out"):
                nv.check_adb_server(41, port=port, timeout=.3)
            self.assertLess(time.monotonic() - start, 2)

    def test_refused_ephemeral_loopback_connection_never_starts_adb(self):
        # Keep the port reserved without listening so no other process can claim it.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
            with self.assertRaisesRegex(nv.ValidationError, "unavailable/unsafe") as caught:
                nv.check_adb_server(41, port=port, timeout=2)
            self.assertIsInstance(caught.exception.__cause__, OSError)
        self.process.assert_not_called()


class NativeValidationTests(unittest.TestCase):
    def setUp(self):
        self.scratch = ROOT / "build" / "native-validation" / ("host-tests-" + uuid.uuid4().hex)
        self.scratch.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.scratch)
        self.enterContext(patch.object(nv, "ROOT", self.scratch))
        self.process = self.enterContext(patch.object(
            nv.subprocess, "run", side_effect=AssertionError("Live subprocess forbidden")))
        self.server_check = self.enterContext(patch.object(
            nv, "check_adb_server", side_effect=AssertionError("Unmocked adb network check")))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def receipt(self, identity="a"):
        avd = "edge_" + identity * 32
        run = self.scratch / "build" / "emulator" / avd
        return {
            "schemaVersion": 1, "status": "ready", "serial": "emulator-5580",
            "port": 5580, "avd": avd, "repoRoot": str(self.scratch),
            "runRoot": str(run), "receipt": str(run / "owner.json"),
            "avdHome": str(run / "avd"), "avdPath": str(run / "avd" / (avd + ".avd")),
            "scratch": str(run / "scratch"), "sdk": str(self.scratch / "absent-sdk"),
            "image": "system-images;android-34;default;x86_64",
            "processes": [{"pid": 2000000001, "processStartTime": "2020-01-01T00:00:00Z"}],
        }

    def runner(self, identity="a", smoke=False):
        owner = self.receipt(identity)
        receipt = Path(owner["receipt"])
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(owner), encoding="utf-8")
        output = self.scratch / "evidence"
        output.mkdir(exist_ok=True)
        args = argparse.Namespace(serial=owner["serial"], receipt=str(receipt),
                                  apk="input.apk", profile="2.3", expected_sha256=None,
                                  upgrade_from=None, smoke=smoke)
        return nv.Runner(args, output)

    @staticmethod
    def raw(width, height, fmt, pixels, header=12):
        return struct.pack("<III", width, height, fmt) + (b"\0" * (header - 12)) + pixels

    @staticmethod
    def window(body, package="com.simple.edgeblocker"):
        return ("WINDOW MANAGER WINDOWS\n  Window #0 Window{abc u0 test}:\n"
                f"    package={package} ty=APPLICATION_OVERLAY\n    {body}\n")

    @staticmethod
    def state():
        return "count=3\nsize=1080x1920\nrotation=0\nimmersive=1\nready=1\nmarker_x=540\nmarker_y=810\n"

    def synthetic_screen(self, enabled=True, overrides=None):
        # Literal geometry is independent of the implementation's geometry oracle.
        w, h, top, bottom = 320, 720, 173, 347
        pixels = bytearray(b"\xff\xff\xff" * w * h)
        if enabled:
            for y in range(h):
                for x in range(w):
                    if y < top or y >= h - bottom or x < 50 or x >= w - 50:
                        offset = (y * w + x) * 3
                        pixels[offset:offset + 3] = b"\xf2\xf2\xf2"
        for y in range(253, 293):
            for x in range(140, 180):
                offset = (y * w + x) * 3
                pixels[offset:offset + 3] = b"\0\0\0"
        for (x, y), rgb in (overrides or {}).items():
            offset = (y * w + x) * 3
            pixels[offset:offset + 3] = bytes(rgb)
        return nv.Screen(self.raw(w, h, 3, pixels))

    def test_cli_requires_each_explicit_input(self):
        values = ["--serial", "emulator-5580", "--apk", "app.apk",
                  "--profile", "2.3", "--receipt", "owner.json"]
        for index in range(0, len(values), 2):
            with self.subTest(flag=values[index]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    nv.parser().parse_args(values[:index] + values[index + 2:])
                self.assertEqual(caught.exception.code, 2)
        self.assertEqual(nv.parser().parse_args(values).serial, "emulator-5580")

    def test_smoke_cli_defaults_exclusivity_output_and_receipt_only_sdk(self):
        values = ["--serial", "emulator-5580", "--apk", "app.apk",
                  "--profile", "2.3", "--receipt", "owner.json"]
        args = nv.parser().parse_args(values)
        self.assertFalse(args.smoke)
        self.assertIsNone(args.output)
        selected = nv.parser().parse_args(
            values + ["--smoke", "--output", r"build\native-validation\chosen"])
        self.assertTrue(selected.smoke)
        self.assertEqual(selected.output, r"build\native-validation\chosen")
        for extra in (["--smoke", "--upgrade-from", "old.apk"], ["--sdk", "other-sdk"]):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    nv.parser().parse_args(values + extra)
                self.assertEqual(error.exception.code, 2)
        help_text = " ".join(nv.parser().format_help().split())
        for text in ("--smoke", "--output", "sole SDK source", "NOT full lifecycle",
                     "symlink/reparse", "reboot/auto-start"):
            self.assertIn(text, help_text)
        self.process.assert_not_called()

    def test_output_defaults_remain_unique_uuid_children_and_accept_nested_paths(self):
        base = self.scratch / "build" / "native-validation"
        first, second = nv.create_output(), nv.create_output()
        for path in (first, second):
            self.assertEqual(path.parent, base)
            self.assertRegex(path.name, r"^[a-f0-9]{32}$")
            self.assertTrue(path.is_dir())
        self.assertNotEqual(first, second)
        relative = nv.create_output(r"build\native-validation\batch\run")
        absolute = nv.create_output(str(base / "absolute with spaces"))
        self.assertEqual(relative, base / "batch" / "run")
        self.assertTrue(absolute.is_dir())

    def test_output_rejects_root_escape_existing_files_and_directories_without_overwrite(self):
        base = self.scratch / "build" / "native-validation"
        base.mkdir(parents=True)
        existing = base / "existing"
        existing.mkdir()
        marker = existing / "results.json"
        marker.write_text("retain evidence")
        file = base / "file"
        file.write_text("retain file")
        values = [str(base), r"build\native-validation", str(self.scratch / "outside"),
                  r"build\native-validation-other\run",
                  r"build\native-validation\..\escape",
                  r"build\native-validation\nested\..\run", str(existing), str(file),
                  str(file / "child"), r"build\native-validation\stream:alternate",
                  r"\build\native-validation\root-relative", r"D:drive-relative"]
        values += [str(base / name) for name in
                   ("run&whoami", "run;id;", "run%PATH%", "run!PATH!", "run^name",
                    "run.", "run ", ".. ", "...", ".hidden")]
        for value in values:
            with self.subTest(value=value), self.assertRaises(nv.ValidationError):
                nv.create_output(value)
        self.assertEqual(marker.read_text(), "retain evidence")
        self.assertEqual(file.read_text(), "retain file")
        self.assertFalse((self.scratch / "outside").exists())

    def test_ui_remote_filename_is_uuid_not_custom_output_name(self):
        runner = self.runner(smoke=True)
        runner.out = nv.create_output(r"build\native-validation\smoke with spaces")
        with patch.object(runner, "shell", side_effect=["", "<hierarchy/>", ""]) as shell:
            self.assertEqual(runner.ui().tag, "hierarchy")
        remote = shell.call_args_list[0].args[2]
        self.assertRegex(remote, r"^/sdcard/native-validation-[a-f0-9]{32}\.xml$")
        self.assertEqual(shell.call_args_list, [
            call("uiautomator", "dump", remote, mutate=True, timeout=30),
            call("cat", remote), call("rm", remote, mutate=True)])
        self.assertEqual((runner.out / "ui-0.xml").read_text(), "<hierarchy/>")

    def test_output_rejects_symlink_and_reparse_components_before_resolving_or_writing(self):
        base = self.scratch / "build" / "native-validation"
        real_lstat = Path.lstat
        for component in (self.scratch, self.scratch / "build", base,
                          base / "linked", base / "linked" / "run"):
            for mode, attributes in ((nv.stat.S_IFLNK, 0),
                                     (nv.stat.S_IFDIR, nv.stat.FILE_ATTRIBUTE_REPARSE_POINT)):
                def lstat(path, *args, **kwargs):
                    if path == component:
                        return Mock(st_mode=mode, st_file_attributes=attributes)
                    return real_lstat(path, *args, **kwargs)

                with self.subTest(component=component, mode=mode), \
                        patch.object(Path, "lstat", autospec=True, side_effect=lstat), \
                        patch.object(Path, "mkdir") as mkdir, \
                        patch.object(Path, "resolve") as resolve:
                    with self.assertRaisesRegex(nv.ValidationError, "symlink/reparse"):
                        nv.create_output(str(base / "linked" / "run"))
                    mkdir.assert_not_called()
                    resolve.assert_not_called()

    def test_output_exclusive_creation_does_not_reuse_a_racing_run(self):
        base = self.scratch / "build" / "native-validation"
        base.mkdir(parents=True)
        destination = base / "race"
        real_mkdir = Path.mkdir

        def mkdir(path, *args, **kwargs):
            if path == destination:
                real_mkdir(path)
                (path / "results.json").write_text("other run")
            return real_mkdir(path, *args, **kwargs)

        with patch.object(Path, "mkdir", autospec=True, side_effect=mkdir):
            with self.assertRaises(FileExistsError):
                nv.create_output(str(destination))
        self.assertEqual((destination / "results.json").read_text(), "other run")

    def test_smoke_and_full_dispatch_share_prepare_and_portrait_checks_exactly_once(self):
        for smoke in (False, True):
            runner = self.runner(smoke=smoke)
            events = Mock()
            with self.subTest(smoke=smoke), contextlib.ExitStack() as stack:
                for name in ("prepare", "probe", "render", "five", "start", "taps",
                             "stop", "expect_windows", "run_full", "verify_installed"):
                    events.attach_mock(stack.enter_context(patch.object(runner, name)), name)
                runner.run()
            expected = [
                call.prepare(), call.probe(), call.render("portrait-off", False),
                call.five("baseline-five-taps", [1] * 5), call.start(), call.probe(),
                call.render("portrait-on"), call.five("portrait-touch", [0, 0, 0, 0, 1]),
                call.taps("portrait-boundaries", nv.boundary_points(runner.size, runner.geometry),
                          [0, 1, 1, 0] * 2)]
            if smoke:
                expected += [
                    call.stop("main-stop"), call.probe(), call.render("stopped", False),
                    call.five("stop-all-touches", [1] * 5),
                    call.expect_windows("smoke-final-no-windows", [])]
            else:
                expected += [call.run_full()]
            self.assertEqual(events.mock_calls, expected + [call.verify_installed()])
            evidence = json.loads((runner.out / "results.json").read_text())
            self.assertTrue(evidence["success"])
            self.assertEqual(evidence["scope"], "smoke" if smoke else "full")
            self.assertIn("upgrade", evidence["coverage"]["skipped"])
            for omitted in ("landscape", "side-toggles-and-persistence", "visible-system-bars",
                            "short-display", "landscape-scrolled-stop", "notification-stop",
                            "permission-revocation-and-recovery", "reboot-auto-start"):
                self.assertIn(omitted, evidence["coverage"]["skipped" if smoke else "planned"])
                self.assertNotIn(omitted, evidence["coverage"]["planned" if smoke else "skipped"])
        self.process.assert_not_called()

    def test_smoke_rejects_programmatic_upgrade_before_prepare(self):
        runner = self.runner(smoke=True)
        runner.args.upgrade_from = "old.apk"
        with patch.object(runner, "prepare") as prepare:
            with self.assertRaisesRegex(nv.ValidationError, "cannot be combined"):
                runner.run()
            prepare.assert_not_called()

    def test_smoke_main_cleans_up_and_persists_scope_on_success_or_any_stage_failure(self):
        # All device operations are mocked, but run(), main(), locking and cleanup
        # are real; failures cannot reach later smoke stages or report success.
        stages = ("prepare", "probe", "render", "five", "start", "probe", "render",
                  "five", "taps", "stop", "probe", "render", "five", "expect_windows",
                  "verify_installed")
        for fail_at in (None, *range(len(stages))):
            runner = self.runner(smoke=True)
            calls = []

            def operation(name):
                def invoke(*args, **kwargs):
                    calls.append(name)
                    if len(calls) - 1 == fail_at:
                        raise nv.ValidationError("stage failure")
                return invoke

            output = self.scratch / "build" / "native-validation" / ("smoke-" + str(fail_at))
            args = ["--serial", runner.args.serial, "--apk", "input.apk", "--profile", "2.3",
                    "--receipt", runner.args.receipt, "--smoke", "--output", str(output)]
            with self.subTest(fail_at=fail_at), contextlib.ExitStack() as stack:
                constructor = stack.enter_context(patch.object(nv, "Runner", return_value=runner))
                cleanup = stack.enter_context(patch.object(runner, "cleanup", wraps=runner.cleanup))
                full = stack.enter_context(patch.object(
                    runner, "run_full", side_effect=AssertionError("Smoke ran full scope")))
                for name in set(stages):
                    stack.enter_context(patch.object(runner, name, side_effect=operation(name)))
                runner.out = output
                if fail_at is None:
                    self.assertEqual(nv.main(args), 0)
                else:
                    with self.assertRaisesRegex(nv.ValidationError, "stage failure"):
                        nv.main(args)
                cleanup.assert_called_once_with()
                full.assert_not_called()
                self.assertEqual(constructor.call_args.args[1], output)
            self.assertEqual(calls, list(stages if fail_at is None else stages[:fail_at + 1]))
            evidence = json.loads((output / "results.json").read_text())
            self.assertEqual(evidence["success"], fail_at is None)
            self.assertEqual(evidence["scope"], "smoke")
            self.assertEqual(evidence["cleanup_errors"], [])
            if fail_at is not None:
                self.assertEqual(evidence["error"], "stage failure")
        self.process.assert_not_called()

    def test_smoke_cleanup_failure_cannot_report_success(self):
        runner = self.runner(smoke=True)
        runner.installed = [nv.PACKAGE, nv.FIXTURE]
        runner.display_original = {"size": "reset"}
        output = self.scratch / "build" / "native-validation" / "cleanup-failed"
        runner.out = output
        args = ["--serial", runner.args.serial, "--apk", "input.apk", "--profile", "2.3",
                "--receipt", runner.args.receipt, "--smoke", "--output", str(output)]
        with patch.object(nv, "Runner", return_value=runner), \
                patch.object(runner, "run", side_effect=lambda: runner.results.update(success=True)), \
                patch.object(runner, "adb", side_effect=[nv.ValidationError("uninstall failed"),
                                                        "Success"]) as adb, \
                patch.object(runner, "shell", return_value="") as shell:
            with self.assertRaisesRegex(nv.ValidationError, "Cleanup failed"):
                nv.main(args)
        self.assertEqual(adb.call_args_list, [
            call("uninstall", nv.FIXTURE, mutate=True, timeout=90),
            call("uninstall", nv.PACKAGE, mutate=True, timeout=90)])
        shell.assert_called_once_with("wm", "size", "reset", mutate=True)
        evidence = json.loads((output / "results.json").read_text())
        self.assertEqual(evidence["scope"], "smoke")
        self.assertFalse(evidence["success"])
        self.assertEqual(evidence["cleanup_errors"], ["uninstall failed"])

    @staticmethod
    def controlled_dumps(frames=(), bars=False):
        component = nv.FIXTURE + "/" + nv.FIXTURE + ".ProbeActivity"
        focus = "Window{abc u0 " + component + "}"
        activity = "ActivityRecord{def u0 " + nv.FIXTURE + "/.ProbeActivity t2}"
        displays = ("Display: mDisplayId=0\n  mCurrentFocus=" + focus +
                    "\n  mFocusedApp=" + activity + "\n")
        activities = "  topResumedActivity=" + activity + "\n"

        def window(number, title, package, kind, frame, flags):
            x1, y1, x2, y2 = frame
            return (f"  Window #{number} {title}:\n"
                    f"    mDisplayId=0 mSession=Session{{aaa}} package={package} appop=NONE\n"
                    f"    mAttrs={{ty={kind}\n      fl={flags}}}\n"
                    f"    Frames: frame=[{x1},{y1}][{x2},{y2}]\n"
                    "    mHasSurface=true isReadyForDisplay()=true\n"
                    "    isOnScreen=true\n    isVisible=true\n")

        windows = "WINDOW MANAGER WINDOWS (dumpsys window windows)\n"
        windows += window(0, focus, nv.FIXTURE, "BASE_APPLICATION",
                          (0, 0, 1080, 1920), "LAYOUT_IN_SCREEN")
        for i, frame in enumerate(frames, 1):
            windows += window(i, f"Window{{a{i} u0 {nv.PACKAGE}}}", nv.PACKAGE,
                              "APPLICATION_OVERLAY", frame, "NOT_FOCUSABLE NOT_TOUCH_MODAL")
        if bars:
            for i, title, kind, frame in (
                    (8, "StatusBar", "STATUS_BAR", (0, 0, 1080, 80)),
                    (9, "NavigationBar0", "NAVIGATION_BAR", (0, 1800, 1080, 1920))):
                windows += window(i, f"Window{{b{i} u0 {title}}}", "com.android.systemui",
                                  kind, frame, "NOT_FOCUSABLE")
        return windows, displays, activities

    def test_controlled_foreground_accepts_only_expected_overlay_and_bar_layouts(self):
        frames = [(0, 0, 1080, 300), (0, 1320, 1080, 1920),
                  (0, 300, 50, 1320), (1030, 300, 1080, 1320)]
        for expected in ([], frames, frames[:2] + frames[3:]):
            for bars in (False, True):
                with self.subTest(frames=expected, bars=bars):
                    result = nv.controlled_fixture_windows(
                        *self.controlled_dumps(expected, bars), (1080, 1920), expected, not bars)
                    self.assertEqual(len(result), 2 if bars else 0)

    def test_controlled_foreground_rejects_unknown_focus_activity_and_input_windows(self):
        windows, displays, activities = self.controlled_dumps()
        bad_dumps = [
            (windows, displays, ""),
            (windows, displays, activities * 2),
            (windows, displays, activities.replace(nv.FIXTURE, "unrelated.app")),
            (windows, displays.replace("mCurrentFocus=Window", "mCurrentFocus=null\nx=Window"), activities),
            (windows, displays.replace("mFocusedApp=", "missing="), activities),
            (windows, displays + "Display: mDisplayId=1\n", activities),
            (windows, displays.replace("u0", "u10"), activities),
            (windows.replace("isVisible=true", ""), displays, activities),
            (windows.replace("isOnScreen=true", "isOnScreen=false"), displays, activities),
            (windows.replace("mHasSurface=true", "mHasSurface=false"), displays, activities),
            (windows.replace("mDisplayId=0", "mDisplayId=1"), displays, activities),
            (windows.replace("fl=LAYOUT_IN_SCREEN", "fl=NOT_TOUCHABLE"), displays, activities),
            (windows.replace("frame=[0,0][1080,1920]", "frame=[0,0][1080,1800]"), displays, activities),
            (windows.replace("BASE_APPLICATION", "APPLICATION_ATTACHED_DIALOG"), displays, activities),
            (windows + windows.split("\n", 1)[1].replace("Window #0", "Window #1"),
             displays, activities),
        ]
        overlay, _, _ = self.controlled_dumps([(0, 0, 1080, 300)])
        extra = overlay[overlay.index("  Window #1"):]
        bad_dumps.extend([
            (overlay, displays, activities),
            (windows + extra.replace(nv.PACKAGE, "unrelated.app"), displays, activities),
            (windows + extra.replace(nv.PACKAGE, nv.FIXTURE).replace(
                "APPLICATION_OVERLAY", "APPLICATION"), displays, activities),
        ])
        for dumps in bad_dumps:
            with self.subTest(dumps=dumps), self.assertRaises(nv.ValidationError):
                nv.controlled_fixture_windows(*dumps, (1080, 1920), [], True)

    def test_api34_inline_visibility_and_inset_hidden_bars(self):
        windows, displays, activities = self.controlled_dumps(bars=True)
        windows = windows.replace("isOnScreen=true",
                                  "mForceSeamlesslyRotate=false seamlesslyRotate: pending=null    isOnScreen=true")
        fixture, bars = windows.split("  Window #8", 1)
        hidden = fixture + "  Window #8" + bars.replace("isVisible=true", "isVisible=false")
        insets = ("\nInsetsSource id=1 type=statusBars frame=[0,0][1080,80] visible=false\n"
                  "InsetsSource id=2 type=navigationBars frame=[0,1800][1080,1920] visible=false\n")
        self.assertEqual(nv.controlled_fixture_windows(
            hidden, displays + insets, activities, (1080, 1920), [], True), [])
        for bad_windows, bad_insets in (
                (hidden, ""),
                (hidden, insets.replace("visible=false", "visible=true")),
                (hidden, insets.replace("[1080,80]", "[1080,81]")),
                (hidden, insets + insets.replace("visible=false", "visible=true")),
                (hidden.replace("StatusBar}", "Unrelated}"), insets),
                (hidden.replace("isVisible=true", "isVisible=false"), insets),
                (hidden.replace("isOnScreen=true", "isOnScreen=true\nisOnScreen=false"), insets),
                (hidden.replace("isOnScreen=true", "prefixisOnScreen=true"), insets),
                (windows, insets)):
            with self.subTest(windows=bad_windows, insets=bad_insets), self.assertRaises(nv.ValidationError):
                nv.controlled_fixture_windows(
                    bad_windows, displays + bad_insets, activities, (1080, 1920), [], True)

    def test_controlled_overlays_and_system_bars_fail_closed(self):
        frames = [(0, 0, 1080, 300)]
        windows, displays, activities = self.controlled_dumps(frames, bars=True)
        for changed in (
                windows.replace("NOT_FOCUSABLE NOT_TOUCH_MODAL", "NOT_TOUCH_MODAL"),
                windows.replace("NOT_TOUCH_MODAL", "NOT_TOUCHABLE"),
                windows.replace("StatusBar}", "NotificationShade}"),
                windows.replace("[0,0][1080,80]", "[0,0][1080,900]"),
                windows.replace("isVisible=true", "isVisible=unknown"),
                windows.replace("frame=[0,0][1080,300]", "frame=[0,0][1080,301]")):
            with self.subTest(changed=changed), self.assertRaises(nv.ValidationError):
                nv.controlled_fixture_windows(changed, displays, activities, (1080, 1920), frames, False)
        with self.assertRaises(nv.ValidationError):
            nv.controlled_fixture_windows(windows, displays, activities, (1080, 1920), frames, True)

    def test_api34_bar_rotation_layouts_do_not_override_outer_input_attributes(self):
        windows, displays, activities = self.controlled_dumps(bars=True)
        for kind in ("STATUS_BAR", "NAVIGATION_BAR"):
            rotations = "\n      paramsForRotation:" + "".join(
                f"\n        ROTATION_{rotation}={{ty={kind}\n          fl=NOT_FOCUSABLE}}"
                for rotation in (0, 90, 180, 270))
            windows = windows.replace(f"mAttrs={{ty={kind}\n      fl=NOT_FOCUSABLE}}",
                                      f"mAttrs={{ty={kind}\n      fl=NOT_FOCUSABLE{rotations}}}")
        self.assertEqual(len(nv.controlled_fixture_windows(
            windows, displays, activities, (1080, 1920), [], False)), 2)
        for changed in (windows.replace("      fl=NOT_FOCUSABLE\n", "      fl=NOT_TOUCHABLE\n"),
                        windows.replace("ty=STATUS_BAR\n      fl", "ty=TOAST\n      fl")):
            with self.subTest(changed=changed), self.assertRaises(nv.ValidationError):
                nv.controlled_fixture_windows(changed, displays, activities, (1080, 1920), [], False)
        for attributes in ("", "mAttrs={ty=STATUS_BAR", "mAttrs={}\nmAttrs={}"):
            with self.subTest(attributes=attributes), self.assertRaises(nv.ValidationError):
                nv.window_attributes(attributes)

    def mock_fixture_shell(self, runner, dumps=None, state=None):
        windows, displays, activities = dumps or self.controlled_dumps()
        responses = {
            ("run-as", nv.FIXTURE, "cat", "files/state.txt"): state or self.state(),
            ("dumpsys", "activity", "activities"): activities,
            ("dumpsys", "window", "displays"): displays,
            ("dumpsys", "window", "windows"): windows,
        }
        return patch.object(runner, "shell", side_effect=lambda *args, **kw: responses[args])

    def test_stale_fixture_file_cannot_authorize_capture_or_blocked_touch_pass(self):
        runner = self.runner()
        windows, displays, activities = self.controlled_dumps()
        for method in (lambda: runner.capture("private"),
                       lambda: runner.taps("blocked", [(25, 500)], [0])):
            with self.mock_fixture_shell(
                    runner, (windows, displays, activities.replace(nv.FIXTURE, "unrelated.app"))), \
                    self.assertRaises(nv.ValidationError):
                method()
        self.process.assert_not_called()
        self.assertEqual(list(runner.out.iterdir()), [])

    def test_fixture_guard_rechecks_state_layout_and_excludes_system_bar_taps(self):
        runner = self.runner()
        for old, new in (("ready=1", "ready=0"), ("rotation=0", "rotation=1"),
                         ("immersive=1", "immersive=0"), ("marker_y=810", "marker_y=811"),
                         ("1080x1920", "1920x1080")):
            with self.subTest(old=old), self.mock_fixture_shell(
                    runner, state=self.state().replace(old, new)), self.assertRaises(nv.ValidationError):
                runner.fixture_guard()
        runner.immersive = False
        with self.mock_fixture_shell(runner, self.controlled_dumps(bars=True),
                                     self.state().replace("immersive=1", "immersive=0")):
            runner.fixture_guard((540, 810))
            for point in ((540, 20), (540, 1900), (-1, 810), (1080, 810)):
                with self.subTest(point=point), self.assertRaises(nv.ValidationError):
                    runner.fixture_guard(point)

    def test_fixture_guard_requires_live_expected_overlays_not_previous_samples(self):
        runner = self.runner()
        runner.running = True
        frames = [(0, 0, 1080, 300), (0, 1320, 1080, 1920),
                  (0, 300, 50, 1320), (1030, 300, 1080, 1320)]
        with self.mock_fixture_shell(runner, self.controlled_dumps(frames)):
            self.assertEqual(runner.fixture_guard()["count"], 3)
        for wrong in ([], frames[:-1], frames + [frames[0]]):
            with self.subTest(wrong=wrong), self.mock_fixture_shell(runner, self.controlled_dumps(wrong)):
                with self.assertRaises(nv.ValidationError):
                    runner.fixture_guard()

    def test_side_configuration_survives_probe_and_drives_expected_layout(self):
        runner = self.runner()
        node = nv.ET.fromstring('<node checked="false"/>')
        with patch.object(runner, "node", return_value=node):
            runner.checkbox("cbLeftEdge", False)
        self.assertEqual(runner.geometry, (0, 50, 300, 600))
        with patch.object(runner, "launch"), patch.object(nv, "wait_for"), \
                patch.object(runner, "ui", return_value=nv.ET.fromstring("<hierarchy/>")):
            runner.probe()
        self.assertEqual(runner.geometry, (0, 50, 300, 600))
        runner.running = True
        frames = [(0, 0, 1080, 300), (0, 1320, 1080, 1920), (1030, 300, 1080, 1320)]
        with self.mock_fixture_shell(runner, self.controlled_dumps(frames)):
            runner.fixture_guard()

    def test_each_tap_is_guarded_before_and_after_dispatch(self):
        runner = self.runner()
        events = Mock()
        with patch.object(runner, "fixture_guard", side_effect=[
                {"count": 3}, {"count": 3}, nv.ValidationError("dialog appeared")]) as guard, \
                patch.object(runner, "shell") as shell, patch.object(nv.time, "sleep"), \
                patch.object(runner, "check") as check:
            events.attach_mock(guard, "guard")
            events.attach_mock(shell, "shell")
            with self.assertRaisesRegex(nv.ValidationError, "dialog appeared"):
                runner.taps("blocked", [(25, 500), (1055, 500)], [0, 0])
            self.assertEqual(events.mock_calls, [
                call.guard((25, 500)), call.shell("input", "tap", 25, 500, mutate=True),
                call.guard(), call.guard((1055, 500))])
            check.assert_not_called()
        with patch.object(runner, "fixture_guard", side_effect=[
                {"count": 3}, nv.ValidationError("lost focus")]), \
                patch.object(runner, "shell"), patch.object(nv.time, "sleep"), \
                patch.object(runner, "check") as check:
            with self.assertRaisesRegex(nv.ValidationError, "lost focus"):
                runner.taps("blocked", [(25, 500)], [0])
            check.assert_not_called()

    def test_boundary_taps_keep_exact_points_and_retain_failed_input_diagnostics(self):
        runner = self.runner()
        points = nv.boundary_points(runner.size, runner.geometry)
        expected = [0, 1, 1, 0] * 2
        for passed in (True, False):
            count, states = 0, []
            for wanted in expected:
                states.append({"count": count})
                count += wanted if passed else 0
                states.append({"count": count})
            with patch.object(runner, "fixture_guard", side_effect=states), \
                    patch.object(runner, "shell", return_value="input diagnostic") as shell, \
                    patch.object(nv.time, "sleep"):
                if passed:
                    runner.taps("boundaries", points, expected)
                else:
                    with self.assertRaises(nv.ValidationError):
                        runner.taps("boundaries", points, expected)
            injections = [c.args[2:] for c in shell.call_args_list if c.args[:2] == ("input", "tap")]
            self.assertEqual(injections, points)
            self.assertEqual(runner.results["checks"][-1]["passed"], passed)
        self.assertEqual((runner.out / "boundaries-input.txt").read_text(), "input diagnostic")

    def test_failed_input_diagnostics_do_not_hide_touch_failure(self):
        runner = self.runner()
        with patch.object(runner, "fixture_guard", return_value={"count": 0}), \
                patch.object(runner, "shell", side_effect=["", nv.ValidationError("ownership lost")]), \
                patch.object(nv.time, "sleep"):
            with self.assertRaisesRegex(nv.ValidationError, "boundary-failed"):
                runner.taps("boundary-failed", [(50, 810)], [1])
        check = runner.results["checks"][-1]
        self.assertFalse(check["passed"])
        self.assertEqual(check["evidence"]["delivered"], [0])
        self.assertEqual(check["evidence"]["input_diagnostic_error"], "ownership lost")

    def test_stale_ownership_blocks_reads_captures_and_stability_samples(self):
        runner = self.runner()
        self.process.side_effect = None
        self.process.return_value = subprocess.CompletedProcess([], 0, b"not owned", b"")
        for action in (lambda: runner.adb("shell", "getprop"),
                       lambda: runner.capture("unsafe"),
                       lambda: runner.render("unsafe")):
            with patch.object(runner, "fixture_guard"), patch.object(runner, "expect_windows"):
                self.process.reset_mock()
                with self.assertRaisesRegex(nv.ValidationError, "Ownership"):
                    action()
                self.assertEqual(self.process.call_count, 1)
                self.assertEqual(self.process.call_args.args[0][0], "powershell.exe")
        self.assertFalse(list(runner.out.glob("*.screencap")))
        self.assertFalse(list(runner.out.glob("*.png")))
        self.server_check.assert_not_called()

    def test_every_capture_including_png_and_stability_revalidates_foreground(self):
        runner = self.runner()
        raw = self.raw(1, 1, 3, b"\xff\xff\xff")
        def command(argv, *args, **kwargs):
            if argv[-1] == "version":
                return "Android Debug Bridge version 1.0.41\n"
            return raw
        events = Mock()
        self.server_check.side_effect = None
        with patch.object(runner, "fixture_guard") as foreground, \
                patch.object(runner, "guard") as owner, \
                patch.object(runner, "command", side_effect=command) as execute, \
                patch.object(runner, "expect_windows"), patch.object(nv, "inspect_render"):
            events.attach_mock(foreground, "foreground")
            events.attach_mock(owner, "owner")
            events.attach_mock(execute, "execute")
            runner.render("controlled", False)
            self.assertEqual(foreground.call_count, 4)
            self.assertEqual(owner.call_count, 4)
            for i in range(0, len(events.mock_calls), 4):
                self.assertEqual(events.mock_calls[i:i + 2], [call.foreground(), call.owner()])
                self.assertEqual(events.mock_calls[i + 3].args[0][-2:],
                                 ["screencap", "-p"] if i == 12 else ["exec-out", "screencap"])

    def test_focus_loss_between_samples_or_before_png_blocks_next_capture(self):
        for capture in (True, False):
            runner = self.runner()
            raw = self.raw(1, 1, 3, b"\xff\xff\xff")
            self.runner_adb_path = runner.adb_path
            self.mock_adb_process(raw)
            self.process.reset_mock()
            with patch.object(runner, "fixture_guard", side_effect=[
                    {}, nv.ValidationError("lost foreground")]), patch.object(runner, "expect_windows"):
                with self.assertRaisesRegex(nv.ValidationError, "lost foreground"):
                    runner.capture("safe-first") if capture else runner.render("samples")
            captures = [c for c in self.process.call_args_list if "screencap" in c.args[0]]
            self.assertEqual(len(captures), 1)
            self.assertFalse(list(runner.out.glob("*.png")))

    def test_cli_profiles_and_exact_codes(self):
        self.assertEqual(nv.PROFILES, {"2.1": (7, 400, 400), "2.3": (9, 300, 600)})
        for profile in ("2.2", "2.0", "2.3.0", ""):
            with self.subTest(profile=profile), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    nv.parser().parse_args(["--serial", "emulator-5580", "--apk", "a.apk",
                                           "--receipt", "r.json", "--profile", profile])

    def test_installed_version_parses_supported_profiles(self):
        for name, code in (("2.1", 7), ("2.3", 9)):
            self.assertEqual(nv.parse_version(
                f"  versionCode={code} minSdk=23 targetSdk=34\n  versionName={name}\n"), (name, code))

    def test_installed_version_rejects_missing_malformed_ambiguous(self):
        for text in ("", "versionName=2.3", "versionCode=9",
                     "versionName=2.3\nversionCode=no",
                     "versionName=2.3\nversionCode=9.5",
                     "versionName=2.3\nversionCode=9oops",
                     "versionName=2.3\nversionName=2.1\nversionCode=9",
                     "versionName=2.3\nversionCode=9\nversionCode=7"):
            with self.subTest(text=text), self.assertRaises(nv.ValidationError):
                nv.parse_version(text)

    def test_apk_metadata_parses_exact_package_name_and_code(self):
        for name, code in (("2.1", 7), ("2.3", 9)):
            text = f"package: name='{nv.PACKAGE}' versionCode='{code}' versionName='{name}' platformBuildVersionName='14'"
            self.assertEqual(nv.parse_apk(text), (nv.PACKAGE, name, code))

    def test_apk_metadata_rejects_missing_malformed_ambiguous(self):
        valid = f"package: name='{nv.PACKAGE}' versionCode='9' versionName='2.3'"
        for text in ("", "package:", valid.replace("versionName='2.3'", ""),
                     valid.replace("'9'", "'9.5'"), valid + "\n" + valid):
            with self.subTest(text=text), self.assertRaises(nv.ValidationError):
                nv.parse_apk(text)

    def test_geometry_literal_portrait_landscape_and_short_contracts(self):
        cases = [
            ("2.1", 1080, 1920, (50, 50, 400, 400)),
            ("2.1", 1920, 1080, (50, 50, 400, 400)),
            ("2.1", 1280, 720, (50, 50, 260, 260)),
            ("2.3", 1080, 1920, (50, 50, 300, 600)),
            ("2.3", 1920, 1080, (50, 50, 270, 540)),
            ("2.3", 1280, 720, (50, 50, 173, 347)),
            ("2.3", 1080, 1200, (50, 50, 300, 600)),
            ("2.3", 1080, 1600, (50, 50, 300, 600)),
        ]
        for profile, w, h, expected in cases:
            with self.subTest(profile=profile, size=(w, h)):
                self.assertEqual(nv.expected_geometry(profile, w, h), expected)
                self.assertGreaterEqual(h - expected[2] - expected[3], min(400, max(200, h // 4)))

    def test_geometry_rejects_unknown_and_small_and_supports_side_toggles(self):
        for args in (("2.2", 1080, 1920), ("2.3", 100, 720), ("2.3", 320, 201)):
            with self.subTest(args=args), self.assertRaises(nv.ValidationError):
                nv.expected_geometry(*args)
        self.assertEqual(nv.expected_geometry("2.3", 1080, 1920, False, True), (0, 50, 300, 600))
        self.assertEqual(nv.expected_geometry("2.1", 1080, 1920, True, False), (50, 0, 400, 400))

    def test_frames_and_touch_boundaries_use_exact_edges(self):
        size, geometry = (320, 720), (50, 50, 173, 347)
        self.assertEqual(nv.expected_frames(size, geometry),
                         [(0, 0, 320, 173), (0, 173, 50, 373),
                          (0, 373, 320, 720), (270, 173, 320, 373)])
        self.assertEqual(nv.touch_points(size, geometry),
                         [(160, 86), (160, 547), (25, 273), (295, 273), (160, 273)])
        self.assertEqual(nv.boundary_points(size, geometry),
                         [(49, 273), (50, 273), (269, 273), (270, 273),
                          (160, 172), (160, 173), (160, 372), (160, 373)])

    def test_bounds_reject_malformed_empty_inverted(self):
        self.assertEqual(nv.parse_bounds("[-2,0][10,20]"), (-2, 0, 10, 20))
        for text in ("[0,0][0,1]", "[3,2][1,4]", "[0,0][1,0]", "[0,0,1,1]",
                     "[a,0][1,1]", "[0,0][1,1]junk", ""):
            with self.subTest(text=text), self.assertRaises(nv.ValidationError):
                nv.parse_bounds(text)

    def test_overlay_scope_requires_exact_package_and_type(self):
        self.assertEqual(nv.overlay_frames(self.window("frame=[0,0][320,173]")), [(0, 0, 320, 173)])
        self.assertEqual(nv.overlay_frames(self.window("mFrame=[0,0][320,173]")), [(0, 0, 320, 173)])
        for package in ("com.simple.edgeblocker.fake", "com.simple.edge", "other.com.simple.edgeblocker"):
            self.assertEqual(nv.overlay_frames(self.window("frame=[0,0][320,173]", package)), [])
        self.assertEqual(nv.overlay_frames(self.window("frame=[0,0][320,173]").replace(
            "APPLICATION_OVERLAY", "BASE_APPLICATION")), [])
        self.assertEqual(nv.overlay_frames("WINDOW MANAGER WINDOWS\n"), [])

    def test_overlay_rejects_malformed_missing_and_ambiguous_frames(self):
        for body in ("", "frame=garbage", "frame=[0,0][0,3]",
                     "frame=[0,0][320,173] mFrame=[0,0][320,173]",
                     "frame=[0,0][320,173] frame=garbage"):
            with self.subTest(body=body), self.assertRaises(nv.ValidationError):
                nv.overlay_frames(self.window(body))
        with self.assertRaises(nv.ValidationError):
            nv.overlay_frames("adb: device offline")

    def test_visible_bars_require_both_actual_visible_windows(self):
        status = self.window("frame=[0,0][320,24]\n isVisible=true", "com.android.systemui")
        status = status.replace("APPLICATION_OVERLAY", "STATUS_BAR")
        navigation = self.window("frame=[272,0][320,720]\n isVisible=true", "com.android.systemui")
        navigation = navigation.replace("APPLICATION_OVERLAY", "NAVIGATION_BAR")
        self.assertEqual(nv.visible_bar_frames(status + navigation),
                         [(0, 0, 320, 24), (272, 0, 320, 720)])
        for text in (status, navigation, status + navigation.replace("isVisible=true", "isVisible=false"),
                     status + status + navigation):
            with self.subTest(text=text), self.assertRaises(nv.ValidationError):
                nv.visible_bar_frames(text)

    def test_bar_exclusions_do_not_hide_app_owned_render_failures(self):
        bars = [(0, 0, 320, 24), (272, 0, 320, 720)]
        screen = self.synthetic_screen(overrides={(295, 273): (0, 0, 0)})
        nv.inspect_render(screen, (320, 720), (50, 50, 173, 347), system_bars=bars)
        with self.assertRaises(nv.ValidationError):
            nv.inspect_render(self.synthetic_screen(overrides={(25, 273): (0, 0, 0)}),
                              (320, 720), (50, 50, 173, 347), system_bars=bars)
        self.assertTrue(nv.inside((272, 273), bars))
        self.assertFalse(nv.inside((271, 273), bars))

    def test_notification_leaf_stop_is_not_mistaken_for_false(self):
        runner = self.runner()
        xml = nv.ET.fromstring("""<hierarchy>
          <node resource-id="com.android.systemui:id/expandableNotificationRow">
            <node resource-id="android:id/title" text="Unrelated app"/>
            <node text="STOP" bounds="[1,1][10,10]"/>
          </node>
          <node resource-id="com.android.systemui:id/expandableNotificationRow">
            <node resource-id="android:id/title" text="Edge Blocker Active"/>
            <node text="STOP" bounds="[100,200][200,300]"/>
          </node>
        </hierarchy>""")
        with patch.object(runner, "ui", return_value=xml), patch.object(runner, "shell") as shell, \
                patch.object(runner, "expect_windows") as frames, patch.object(runner, "probe"), \
                patch.object(runner, "five") as five:
            runner.notification_stop()
        shell.assert_any_call("input", "tap", 150, 250, mutate=True)
        self.assertFalse(any(call.args[:4] == ("input", "tap", 5, 5) for call in shell.call_args_list))
        frames.assert_called_once_with("notification-stop-windows", [])
        five.assert_called_once_with("notification-stop-touches", [1] * 5)

    def test_notification_collapsed_app_header_is_expanded_before_stop(self):
        runner = self.runner()
        collapsed = nv.ET.fromstring("""<hierarchy>
          <node resource-id="com.android.systemui:id/expandableNotificationRow">
            <node resource-id="android:id/app_name" text="Simple Edge Blocker"/>
            <node resource-id="android:id/expand_button" clickable="true" bounds="[10,20][30,40]"/>
          </node>
        </hierarchy>""")
        expanded = nv.ET.fromstring("""<hierarchy>
          <node resource-id="com.android.systemui:id/expandableNotificationRow">
            <node text="Edge Blocker Active"/>
            <node text="STOP" bounds="[100,200][200,300]"/>
          </node>
        </hierarchy>""")
        with patch.object(runner, "ui", side_effect=[collapsed, expanded]), \
                patch.object(runner, "shell") as shell, patch.object(nv.time, "sleep"), \
                patch.object(runner, "expect_windows"), patch.object(runner, "probe"), \
                patch.object(runner, "five"):
            runner.notification_stop()
        taps = [call.args for call in shell.call_args_list if call.args[:2] == ("input", "tap")]
        self.assertEqual(taps, [("input", "tap", 20, 30), ("input", "tap", 150, 250)])

    def test_fixture_state_parses_integer_schema(self):
        self.assertEqual(nv.parse_state(self.state()),
                         {"count": 3, "size": (1080, 1920), "rotation": 0, "immersive": 1,
                          "ready": 1, "marker_x": 540, "marker_y": 810})

    def test_fixture_state_rejects_malformed_duplicate_and_unexpected(self):
        state = self.state()
        for text in ("", state + "broken\n", state + "count=4\n", state + "extra=1\n",
                     state.replace("ready=1\n", ""), state.replace("count=3", "count=-1"),
                     state.replace("count=3", "count=3.0"), state.replace("ready=1", "ready=2"),
                     state.replace("rotation=0", "rotation=2"), state.replace("immersive=1", "immersive=2"),
                     state.replace("1080x1920", "0x1920"), state.replace("1080x1920", "1080,1920")):
            with self.subTest(text=text), self.assertRaises(nv.ValidationError):
                nv.parse_state(text)

    def test_screen_rgb_rgba_rgbx_and_both_headers(self):
        for fmt in (1, 2, 3):
            for header in (12, 16):
                with self.subTest(fmt=fmt, header=header):
                    pixel = b"\x12\x34\x56" + (b"\xab" if fmt != 3 else b"")
                    screen = nv.Screen(self.raw(2, 1, fmt, pixel * 2, header))
                    self.assertEqual(screen.size, (2, 1))
                    self.assertEqual(screen.pixel(1, 0), (18, 52, 86))

    def test_screen_rejects_bad_header_format_dimensions_and_payload(self):
        for raw in (b"", b"\0" * 11, self.raw(0, 1, 3, b""),
                    self.raw(8193, 1, 3, b""), self.raw(1, 1, 4, b"\0" * 4),
                    self.raw(2, 2, 3, b"\0" * 11), self.raw(2, 2, 3, b"\0" * 13),
                    self.raw(2, 2, 3, b"\0" * 17)):
            with self.subTest(length=len(raw)), self.assertRaises(nv.ValidationError):
                nv.Screen(raw)

    def test_screen_pixel_row_stride_and_bounds(self):
        screen = nv.Screen(self.raw(2, 2, 3, bytes(range(12))))
        self.assertEqual(screen.pixel(0, 1), (6, 7, 8))
        for xy in ((-1, 0), (0, -1), (2, 0), (0, 2)):
            with self.subTest(xy=xy), self.assertRaises(nv.ValidationError):
                screen.pixel(*xy)

    def test_synthetic_render_accepts_overlay_and_clean_absence(self):
        for enabled in (True, False):
            nv.inspect_render(self.synthetic_screen(enabled), (320, 720), (50, 50, 173, 347), enabled)

    def test_synthetic_render_rejects_gaps_double_alpha_and_lost_contrast(self):
        for xy, color in (((49, 273), (255, 255, 255)), ((100, 172), (255, 255, 255)),
                          ((25, 86), (230, 230, 230)), ((160, 273), (242, 242, 242)),
                          ((190, 273), (242, 242, 242))):
            with self.subTest(xy=xy), self.assertRaises(nv.ValidationError):
                nv.inspect_render(self.synthetic_screen(overrides={xy: color}),
                                  (320, 720), (50, 50, 173, 347))
        with self.assertRaises(nv.ValidationError):
            nv.inspect_render(self.synthetic_screen(), (720, 320), (50, 50, 173, 347))

    def test_receipt_accepts_matching_scoped_roots(self):
        owner = self.receipt()
        nv.validate_receipt(owner, Path(owner["receipt"]), owner["serial"])
        self.process.assert_not_called()

    def test_receipt_rejects_status_schema_serial_and_port(self):
        for key, value in (("status", "starting"), ("status", "stopped"), ("schemaVersion", 2),
                           ("serial", "emulator-5582"), ("port", 5581), ("port", "5580"),
                           ("port", 5552), ("port", 5684)):
            owner = self.receipt()
            owner[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(nv.ValidationError):
                nv.validate_receipt(owner, Path(owner["receipt"]), "emulator-5580")
        with self.assertRaises(nv.ValidationError):
            owner = self.receipt()
            nv.validate_receipt(owner, Path(owner["receipt"]), "physical-device")

    def test_receipt_rejects_missing_or_escaped_path_roots(self):
        for key in ("repoRoot", "runRoot", "receipt", "avdHome", "avdPath", "scratch"):
            for missing in (False, True):
                owner = self.receipt()
                receipt = Path(owner["receipt"])
                if missing:
                    del owner[key]
                else:
                    owner[key] = str(self.scratch / "outside-owned-run")
                with self.subTest(key=key, missing=missing), self.assertRaises(nv.ValidationError):
                    nv.validate_receipt(owner, receipt, "emulator-5580")
        owner = self.receipt()
        with self.assertRaises(nv.ValidationError):
            nv.validate_receipt(owner, self.scratch / "copied-owner.json", "emulator-5580")

    def test_receipt_rejects_non_disposable_avd_missing_identity_wrong_api(self):
        for key, value in (("avd", "personal-avd"), ("processes", []),
                           ("image", "system-images;android-33;default;x86_64")):
            owner = self.receipt()
            owner[key] = value
            with self.subTest(key=key), self.assertRaises(nv.ValidationError):
                nv.validate_receipt(owner, Path(owner["receipt"]), "emulator-5580")

    def test_stale_or_unconfirmed_ownership_blocks_all_adb_mutation(self):
        runner = self.runner()
        for result in (subprocess.CompletedProcess([], 1, b"", b"stale process identity"),
                       subprocess.CompletedProcess([], 0, b"not owned", b"")):
            self.process.reset_mock()
            self.process.side_effect = None
            self.process.return_value = result
            with self.subTest(result=result), self.assertRaises(nv.ValidationError):
                runner.adb("uninstall", nv.PACKAGE, mutate=True)
            self.assertEqual(self.process.call_count, 1)
            self.assertEqual(self.process.call_args.args[0][0], "powershell.exe")
            self.server_check.assert_not_called()

    def test_command_success_binary_and_text_are_logged(self):
        runner = self.runner()
        self.process.side_effect = None
        self.process.return_value = subprocess.CompletedProcess([], 0, b"\xffABC", b"")
        self.assertEqual(runner.command(["fake.exe", 123], timeout=7), "\ufffdABC")
        self.assertEqual(runner.command(["fake.exe"], binary=True), b"\xffABC")
        rows = [json.loads(line) for line in (runner.out / "commands.jsonl").read_text().splitlines()]
        self.assertEqual(rows[0]["command"], ["fake.exe", "123"])
        self.assertEqual(rows[1]["stdout"], "<binary>")
        self.assertEqual(self.process.call_args_list[0].kwargs,
                         {"cwd": self.scratch, "capture_output": True, "timeout": 7})

    def test_subprocess_failure_is_not_success_and_is_logged(self):
        runner = self.runner()
        self.process.side_effect = None
        self.process.return_value = subprocess.CompletedProcess([], 17, b"partial", b"failure")
        with self.assertRaises(nv.ValidationError):
            runner.command(["fake.exe"])
        row = json.loads((runner.out / "commands.jsonl").read_text())
        self.assertEqual((row["returncode"], row["stdout"], row["stderr"]), (17, "partial", "failure"))

    def test_console_line_endings_normalize_text_only_and_preserve_evidence(self):
        runner = self.runner()
        self.process.side_effect = None
        for newline in (b"\n", b"\r\n", b"\r\r\n"):
            raw = runner.owner["avd"].encode() + newline + b"OK" + newline
            self.process.return_value = subprocess.CompletedProcess([], 0, raw, b"")
            self.assertEqual(runner.command(["fake.exe"]).splitlines(), [runner.owner["avd"], "OK"])
            self.assertEqual(runner.command(["fake.exe"], binary=True), raw)
        rows = [json.loads(line) for line in (runner.out / "commands.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-2]["stdout"], raw.decode())

    def test_normalized_console_reply_retains_foreign_names_and_errors(self):
        runner = self.runner()
        self.process.side_effect = None
        replies = [
            runner.owner["avd"] + "\r\r\nOK\r\r\n",
            "edge_" + "b" * 32 + "\r\r\nOK\r\r\n",
            runner.owner["avd"] + "\r\r\nKO: console error\r\r\nOK\r\r\n",
            runner.owner["avd"] + "\r\r\n\r\r\nOK\r\r\n",
        ]
        for index, reply in enumerate(replies):
            self.process.return_value = subprocess.CompletedProcess([], 0, reply.encode(), b"")
            with patch.object(runner, "guard"), \
                    patch.object(runner, "adb", side_effect=lambda *a: runner.command(["fake.exe"])), \
                    patch.object(runner, "shell", side_effect=["1", "34", "10"]) as shell:
                with self.assertRaisesRegex(
                        nv.ValidationError, "primary user" if index == 0 else "Connected AVD"):
                    runner.prepare()
                self.assertEqual(shell.call_count, 3 if index == 0 else 2)
        self.process.return_value = subprocess.CompletedProcess(
            [], 1, replies[0].encode(), b"KO: console failure\r\r\n")
        with self.assertRaisesRegex(nv.ValidationError, "KO: console failure"):
            runner.command(["fake.exe"])

    def test_subprocess_timeout_and_launch_failure_propagate(self):
        runner = self.runner()
        for error in (subprocess.TimeoutExpired("fake.exe", 3), FileNotFoundError("missing executable")):
            self.process.side_effect = error
            with self.subTest(error=error), self.assertRaises(type(error)):
                runner.command(["fake.exe"], timeout=3)
            self.assertEqual(self.process.call_args.kwargs["timeout"], 3)

    def test_failed_checks_are_persisted_before_raising(self):
        runner = self.runner()
        with self.assertRaises(nv.ValidationError):
            runner.check("bad-geometry", False, {"actual": [1, 2]})
        result = json.loads((runner.out / "results.json").read_text())
        self.assertFalse(result["success"])
        self.assertEqual(result["checks"],
                         [{"name": "bad-geometry", "passed": False, "evidence": {"actual": [1, 2]}}])

    def test_waits_are_bounded_with_mock_clock_and_return_probe_value(self):
        now, sleeps = [0.0], []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        with patch.object(nv.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(nv.time, "sleep", side_effect=sleep):
            probe = Mock(return_value=False)
            with self.assertRaisesRegex(nv.ValidationError, "Readiness timeout"):
                nv.wait_for(probe, "absent", timeout=1, interval=.3)
            self.assertAlmostEqual(now[0], 1)
            self.assertEqual(probe.call_count, 5)
            self.assertTrue(all(0 < value <= .3 for value in sleeps))
            self.assertEqual(nv.wait_for(Mock(side_effect=[False, "ready"]), "ready"), "ready")

    def test_absence_window_cleanup_waits_for_real_absence(self):
        runner = self.runner()
        with patch.object(runner, "windows", side_effect=[
                self.window("frame=[0,0][320,173]"), "WINDOW MANAGER WINDOWS\n",
                "WINDOW MANAGER WINDOWS\n"]), patch.object(nv.time, "sleep"):
            runner.expect_windows("stopped-windows", [])
        self.assertTrue(runner.results["checks"][-1]["passed"])
        with patch.object(runner, "windows", return_value=self.window("frame=broken")):
            with self.assertRaises(nv.ValidationError):
                runner.expect_windows("malformed-is-not-absent", [])

    def test_cleanup_uninstalls_reverse_order_restores_display_and_persists(self):
        runner = self.runner()
        runner.installed = [nv.PACKAGE, nv.FIXTURE]
        runner.display_original = {"size": "reset", "density": "420", "rotation": "free"}
        runner.results["success"] = True
        with patch.object(runner, "adb", return_value="Success") as adb, \
                patch.object(runner, "shell") as shell:
            runner.cleanup()
        self.assertEqual([call.args for call in adb.call_args_list],
                         [("uninstall", nv.FIXTURE), ("uninstall", nv.PACKAGE)])
        self.assertTrue(all(call.kwargs["mutate"] for call in adb.call_args_list + shell.call_args_list))
        self.assertEqual([call.args for call in shell.call_args_list],
                         [("wm", "size", "reset"), ("wm", "density", "420"),
                          ("cmd", "window", "user-rotation", "free")])
        self.assertEqual(json.loads((runner.out / "results.json").read_text())["cleanup_errors"], [])

    def test_cleanup_failures_force_failure_and_attempt_remaining_cleanup(self):
        runner = self.runner()
        runner.installed = [nv.PACKAGE, nv.FIXTURE]
        runner.display_original = {"size": "reset", "density": "reset"}
        runner.results["success"] = True
        with patch.object(runner, "adb", side_effect=[nv.ValidationError("lost owner"), "Failure"]) as adb, \
                patch.object(runner, "shell", side_effect=[subprocess.TimeoutExpired("wm", 1), ""]) as shell:
            with self.assertRaises(nv.ValidationError):
                runner.cleanup()
        self.assertEqual((adb.call_count, shell.call_count), (2, 2))
        result = json.loads((runner.out / "results.json").read_text())
        self.assertFalse(result["success"])
        self.assertEqual(len(result["cleanup_errors"]), 3)

    def test_main_persists_run_failure_and_always_cleans_up(self):
        runner = self.runner()
        args = ["--serial", runner.args.serial, "--apk", "input.apk",
                "--profile", "2.3", "--receipt", runner.args.receipt]
        with patch.object(nv, "Runner", return_value=runner), \
                patch.object(runner, "run", side_effect=nv.ValidationError("test failure")), \
                patch.object(runner, "cleanup", wraps=runner.cleanup) as cleanup:
            with self.assertRaisesRegex(nv.ValidationError, "test failure"):
                nv.main(args)
        cleanup.assert_called_once()
        result = json.loads((runner.out / "results.json").read_text())
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "test failure")
        self.process.assert_not_called()

    def mock_adb_process(self, output=b"Success\n", returncode=0):
        def run(argv, **kwargs):
            if argv[0] == "powershell.exe":
                return subprocess.CompletedProcess(argv, 0, b"owned\n", b"")
            self.assertEqual(argv[0], str(self.runner_adb_path))
            if argv[-1] == "version":
                return subprocess.CompletedProcess(
                    argv, 0, b"Android Debug Bridge version 1.0.41\n", b"")
            return subprocess.CompletedProcess(argv, returncode, output, b"")
        self.process.side_effect = run
        self.server_check.side_effect = None

    def test_every_adb_network_call_checks_server_and_sanitizes_endpoint(self):
        runner = self.runner()
        self.runner_adb_path = runner.adb_path
        self.mock_adb_process(output=b"result")
        events = Mock()
        events.attach_mock(self.process, "process")
        events.attach_mock(self.server_check, "server")
        environment = {
            "PATH": "preserved-tool-path", "KEEP_ME": "unchanged",
            "ADB_SERVER_SOCKET": "tcp:unsafe:9999", "ANDROID_ADB_SERVER_ADDRESS": "unsafe",
            "ANDROID_ADB_SERVER_PORT": "9999", "ADB_SERVER_PORT": "9998",
            "AdB_sErVeR_sOcKeT": "tcp:another:9997",
        }
        commands = [
            (("shell", "getprop"), False, False), (("exec-out", "screencap"), False, True),
            (("wait-for-device",), False, False), (("emu", "avd", "name"), False, False),
            (("install", "supplied.apk"), True, False),
            (("uninstall", nv.PACKAGE), True, False), (("reboot",), True, False),
        ]
        with patch.dict(nv.os.environ, environment, clear=True), \
                patch.object(runner, "fixture_guard"):
            original = dict(nv.os.environ)
            for args, mutate, binary in commands:
                with self.subTest(args=args):
                    events.reset_mock()
                    result = runner.adb(*args, mutate=mutate, binary=binary, timeout=17)
                    self.assertEqual(result, b"result" if binary else "result")
                    calls = events.mock_calls
                    self.assertEqual([c[0] for c in calls],
                                     ["process", "process", "server", "process"])
                    self.assertEqual(calls[0].args[0][0], "powershell.exe")
                    endpoint = [str(runner.adb_path), "-H", "127.0.0.1", "-P", "5037"]
                    self.assertEqual(calls[-3].args[0], endpoint + ["version"])
                    self.assertEqual(calls[-2], call.server(41))
                    self.assertEqual(calls[-1].args[0], endpoint + ["-s", runner.args.serial, *args])
                    for invocation in (calls[-3], calls[-1]):
                        self.assertEqual(invocation.kwargs["env"],
                                         {"PATH": "preserved-tool-path", "KEEP_ME": "unchanged"})
                    self.assertEqual(calls[-1].kwargs["timeout"], 17)
            self.assertEqual(dict(nv.os.environ), original)

    def test_server_rejection_blocks_reads_and_mutations_after_local_version(self):
        runner = self.runner()
        self.runner_adb_path = runner.adb_path
        for mutate in (False, True):
            self.mock_adb_process()
            self.server_check.side_effect = nv.ValidationError("unsafe server")
            self.process.reset_mock()
            with self.subTest(mutate=mutate), self.assertRaisesRegex(nv.ValidationError, "unsafe server"):
                runner.adb("shell", "getprop", mutate=mutate)
            self.assertEqual(self.process.call_count, 2)
            self.assertEqual(self.process.call_args.args[0][-1], "version")

    def test_adb_environment_removes_case_variants_without_mutating_parent(self):
        original = {
            "adb_server_socket": "tcp:remote:1", "Android_Adb_Server_Address": "remote",
            "android_adb_server_port": "2", "aDb_SeRvEr_PoRt": "3",
            "PATH": "preserved", "ANDROID_HOME": "supplied sdk", "OTHER": "unchanged",
        }
        with patch.object(nv.os, "environ", original):
            self.assertEqual(nv.adb_environment(),
                             {"PATH": "preserved", "ANDROID_HOME": "supplied sdk", "OTHER": "unchanged"})
            self.assertEqual(len(original), 7)

    def test_bad_local_version_and_command_failure_never_contact_server(self):
        runner = self.runner()
        for code, output in ((0, b"malformed"), (1, b"Android Debug Bridge version 1.0.41\n")):
            self.process.side_effect = None
            self.process.return_value = subprocess.CompletedProcess([], code, output, b"")
            self.process.reset_mock()
            self.server_check.reset_mock()
            with self.subTest(code=code), patch.object(runner, "guard"), \
                    self.assertRaises(nv.ValidationError):
                runner.adb("shell", "getprop")
            self.assertEqual(self.process.call_count, 1)
            self.server_check.assert_not_called()

    def test_shell_forwards_mutation_and_timeout_through_adb_guard(self):
        runner = self.runner()
        with patch.object(runner, "adb", return_value="done") as adb:
            self.assertEqual(runner.shell("input", "tap", 1, 2, mutate=True, timeout=4), "done")
        adb.assert_called_once_with("shell", "input", "tap", "1", "2", mutate=True, timeout=4)

    def test_only_successful_installs_authorize_cleanup(self):
        runner = self.runner()
        for output in ("Success", "Success\r\n", "Performing Streamed Install\nSuccess\n"):
            runner.installed = []
            with self.subTest(output=output), patch.object(runner, "adb", return_value=output) as adb:
                runner.install_owned(nv.PACKAGE, Path("supplied input.apk"))
                self.assertEqual(runner.installed, [nv.PACKAGE])
                adb.assert_called_once_with("install", "-R", "supplied input.apk", mutate=True, timeout=120)
                adb.return_value = "Success"
                runner.cleanup()
                self.assertEqual(adb.call_args, call("uninstall", nv.PACKAGE, mutate=True, timeout=90))

    def test_failed_already_exists_install_never_uninstalls_preexisting_package(self):
        runner = self.runner()
        self.runner_adb_path = runner.adb_path
        for code in (0, 1):
            self.mock_adb_process(b"Failure [INSTALL_FAILED_ALREADY_EXISTS]\n", code)
            self.process.reset_mock()
            with self.subTest(returncode=code), self.assertRaises(nv.ValidationError):
                runner.install_owned(nv.PACKAGE, "supplied.apk")
            self.assertEqual(runner.installed, [])
            install = [c.args[0] for c in self.process.call_args_list if "install" in c.args[0]]
            self.assertEqual(len(install), 1)
            self.assertEqual(install[0][-3:], ["install", "-R", "supplied.apk"])
            runner.cleanup()
            self.assertFalse(any("uninstall" in c.args[0] for c in self.process.call_args_list))

    def test_nonzero_install_even_with_success_stdout_never_grants_authority(self):
        runner = self.runner()
        self.runner_adb_path = runner.adb_path
        self.mock_adb_process(b"Success\n", 17)
        with self.assertRaisesRegex(nv.ValidationError, "Command failed"):
            runner.install_owned(nv.PACKAGE, "supplied.apk")
        self.assertEqual(runner.installed, [])
        runner.cleanup()
        self.assertFalse(any("uninstall" in c.args[0] for c in self.process.call_args_list))

    def test_malformed_install_results_and_exceptions_never_authorize_cleanup(self):
        failures = ("", "success", "Success-ish", "Success\ntrailing",
                    "Failure [INSTALL_FAILED_ALREADY_EXISTS]\nSuccess", "Success\nFailure")
        errors = (subprocess.TimeoutExpired("adb", 120), FileNotFoundError("adb missing"),
                  nv.ValidationError("connection lost"))
        for outcome in (*failures, *errors):
            runner = self.runner()
            with self.subTest(outcome=outcome), patch.object(runner, "adb") as adb:
                if isinstance(outcome, Exception):
                    adb.side_effect = outcome
                else:
                    adb.return_value = outcome
                with self.assertRaises((nv.ValidationError, OSError, subprocess.SubprocessError)):
                    runner.install_owned(nv.PACKAGE, "supplied.apk")
                self.assertEqual(runner.installed, [])
                adb.reset_mock()
                runner.cleanup()
                adb.assert_not_called()

    def test_install_error_followed_by_success_must_not_authorize_uninstall(self):
        runner = self.runner()
        with patch.object(runner, "adb", return_value="Error: install failed\nSuccess\n") as adb:
            try:
                runner.install_owned(nv.PACKAGE, "supplied.apk")
            except nv.ValidationError:
                pass
            adb.reset_mock()
            adb.return_value = "Success"
            runner.cleanup()
            adb.assert_not_called()

    def test_replacement_requires_owned_install_before_any_adb_call(self):
        runner = self.runner()
        with patch.object(runner, "adb") as adb:
            with self.assertRaisesRegex(nv.ValidationError, "successfully owned"):
                runner.install_owned(nv.PACKAGE, "candidate.apk", replace=True)
        adb.assert_not_called()

    def test_uncertain_replacement_revokes_only_replaced_package_cleanup_authority(self):
        outcomes = ("Failure [INSTALL_FAILED_ALREADY_EXISTS]", "", "Success\ntrailing",
                    subprocess.TimeoutExpired("adb", 120), nv.ValidationError("nonzero exit"))
        for outcome in outcomes:
            runner = self.runner()
            runner.installed = [nv.PACKAGE, nv.FIXTURE]

            def install(*args, **kwargs):
                self.assertEqual(runner.installed, [nv.FIXTURE])
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            with self.subTest(outcome=outcome), patch.object(runner, "adb", side_effect=install) as adb:
                with self.assertRaises((nv.ValidationError, subprocess.TimeoutExpired)):
                    runner.install_owned(nv.PACKAGE, "candidate.apk", replace=True)
                adb.assert_called_once_with("install", "-r", "candidate.apk", mutate=True, timeout=120)
                adb.side_effect = None
                adb.return_value = "Success"
                adb.reset_mock()
                runner.cleanup()
                adb.assert_called_once_with("uninstall", nv.FIXTURE, mutate=True, timeout=90)
                self.assertEqual(runner.installed, [nv.FIXTURE])

    def test_successful_replacement_restores_single_cleanup_authority(self):
        runner = self.runner()
        runner.installed = [nv.PACKAGE, nv.FIXTURE]
        with patch.object(runner, "adb", return_value="Success") as adb:
            runner.install_owned(nv.PACKAGE, "candidate.apk", replace=True)
            self.assertEqual(runner.installed, [nv.FIXTURE, nv.PACKAGE])
            runner.cleanup()
        self.assertEqual(adb.call_args_list, [
            call("install", "-r", "candidate.apk", mutate=True, timeout=120),
            call("uninstall", nv.PACKAGE, mutate=True, timeout=90),
            call("uninstall", nv.FIXTURE, mutate=True, timeout=90)])

    @unittest.skipUnless(os.name == "nt", "Requires actual Windows msvcrt byte locking")
    def test_receipt_byte_lock_blocks_same_receipt_but_not_another(self):
        runner, other = self.runner(), self.runner("b")
        with nv.receipt_lock(runner.args.receipt, runner.args.serial):
            with self.assertRaisesRegex(nv.ValidationError, "already holds"):
                with nv.receipt_lock(runner.args.receipt, runner.args.serial):
                    self.fail("Same receipt acquired twice")
            with nv.receipt_lock(other.args.receipt, other.args.serial):
                pass
        with nv.receipt_lock(runner.args.receipt, runner.args.serial):
            pass
        lock = Path(runner.args.receipt).with_name("native-validation.lock")
        self.assertEqual(lock.read_bytes(), b"\0")
        self.process.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Requires actual Windows msvcrt byte locking")
    def test_receipt_lock_releases_after_context_exception(self):
        runner = self.runner()
        with self.assertRaisesRegex(RuntimeError, "body failed"):
            with nv.receipt_lock(runner.args.receipt, runner.args.serial):
                raise RuntimeError("body failed")
        with nv.receipt_lock(runner.args.receipt, runner.args.serial):
            pass

    @unittest.skipUnless(os.name == "nt", "Requires actual Windows msvcrt byte locking")
    def test_invalid_receipt_cannot_create_cleanup_lock(self):
        runner = self.runner()
        owner = json.loads(Path(runner.args.receipt).read_text())
        owner["status"] = "stopped"
        Path(runner.args.receipt).write_text(json.dumps(owner), encoding="utf-8")
        with self.assertRaises(nv.ValidationError):
            with nv.receipt_lock(runner.args.receipt, runner.args.serial):
                self.fail("Invalid receipt was accepted")
        self.assertFalse(Path(runner.args.receipt).with_name("native-validation.lock").exists())

    @unittest.skipUnless(os.name == "nt", "Requires actual Windows msvcrt byte locking")
    def test_receipt_lock_contends_in_separate_python_process(self):
        runner, other = self.runner(), self.runner("b")
        script = """
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("native_validation", sys.argv[1])
nv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nv)
nv.ROOT = pathlib.Path(sys.argv[2])
try:
    with nv.receipt_lock(sys.argv[3], sys.argv[4]):
        print("acquired")
except nv.ValidationError as error:
    print(error)
    sys.exit(23)
"""

        def child(receipt):
            process = subprocess.Popen(
                [sys.executable, "-B", "-c", script, str(ROOT / "tools" / "native_validation.py"),
                 str(self.scratch), receipt, runner.args.serial],
                cwd=self.scratch, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                stdout, stderr = process.communicate(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
            self.assertEqual(stderr, b"")
            return process.returncode, stdout.decode().strip()

        with nv.receipt_lock(runner.args.receipt, runner.args.serial):
            code, output = child(runner.args.receipt)
            self.assertEqual(code, 23)
            self.assertIn("already holds", output)
            self.assertEqual(child(other.args.receipt), (0, "acquired"))
        self.assertEqual(child(runner.args.receipt), (0, "acquired"))
        self.process.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Requires actual Windows msvcrt byte locking")
    def test_main_holds_lock_through_cleanup_and_releases_on_all_exit_paths(self):
        for run_fails, cleanup_fails in ((False, False), (True, False), (False, True), (True, True)):
            runner = self.runner()
            events = []

            def assert_locked():
                with self.assertRaisesRegex(nv.ValidationError, "already holds"):
                    with nv.receipt_lock(runner.args.receipt, runner.args.serial):
                        self.fail("Receipt released prematurely")

            def run():
                assert_locked()
                events.append("run")
                if run_fails:
                    raise nv.ValidationError("run failed")
                runner.results["success"] = True

            args = ["--serial", runner.args.serial, "--apk", "input.apk", "--profile", "2.3",
                    "--receipt", runner.args.receipt]
            cleanup_method = nv.Runner.cleanup

            def cleanup_bound():
                assert_locked()
                events.append("cleanup")
                if cleanup_fails:
                    runner.installed = [nv.PACKAGE]
                with patch.object(runner, "adb", side_effect=nv.ValidationError("cleanup failed")):
                    cleanup_method(runner)

            with self.subTest(run_fails=run_fails, cleanup_fails=cleanup_fails), \
                    patch.object(nv, "Runner", return_value=runner), \
                    patch.object(runner, "run", side_effect=run), \
                    patch.object(runner, "cleanup", side_effect=cleanup_bound):
                if run_fails or cleanup_fails:
                    with self.assertRaisesRegex(nv.ValidationError,
                                                "Cleanup failed" if cleanup_fails else "run failed"):
                        nv.main(args)
                else:
                    self.assertEqual(nv.main(args), 0)
            self.assertEqual(events, ["run", "cleanup"])
            with nv.receipt_lock(runner.args.receipt, runner.args.serial):
                pass
            result = json.loads((runner.out / "results.json").read_text())
            self.assertEqual(result["success"], not (run_fails or cleanup_fails))
            if run_fails:
                self.assertEqual(result["error"], "run failed")
            self.assertEqual(len(result["cleanup_errors"]), int(cleanup_fails))

    @unittest.skipUnless(os.name == "nt", "Requires actual Windows msvcrt byte locking")
    def test_main_constructor_failure_releases_lock_and_persists_failure(self):
        runner = self.runner()
        args = ["--serial", runner.args.serial, "--apk", "input.apk", "--profile", "2.3",
                "--receipt", runner.args.receipt]
        with patch.object(nv, "Runner", side_effect=nv.ValidationError("construction failed")):
            with self.assertRaisesRegex(nv.ValidationError, "construction failed"):
                nv.main(args)
        with nv.receipt_lock(runner.args.receipt, runner.args.serial):
            pass
        files = list((self.scratch / "build" / "native-validation").glob("*\\results.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text()),
                         {"success": False, "error": "construction failed",
                          **nv.coverage(runner.args)})
        self.process.assert_not_called()

    def test_upgrade_cli_is_optional_and_uses_exact_current_source_profiles(self):
        args = ["--serial", "emulator-5580", "--apk", "candidate.apk",
                "--profile", "2.3", "--receipt", "owner.json"]
        self.assertIsNone(nv.parser().parse_args(args).upgrade_from)
        self.assertEqual(nv.parser().parse_args(args + ["--upgrade-from", "supplied old.apk"]).upgrade_from,
                         "supplied old.apk")
        self.assertEqual(nv.UPGRADE_SOURCES, {"2.1": ("2.0", 6), "2.3": ("2.1", 7)})
        runner = self.runner()
        self.assertIsNone(runner.prepare_upgrade(Path("candidate.apk")))
        runner.save()
        self.assertEqual(json.loads((runner.out / "results.json").read_text())["upgrade"],
                         {"status": "skipped", "reason": "--upgrade-from not supplied"})
        self.process.assert_not_called()

    @staticmethod
    def signer(digest="ab" * 32, number=1):
        return f"Signer #{number} certificate SHA-256 digest: {digest}\n"

    def test_signer_parser_requires_valid_hex_and_sequential_unambiguous_signers(self):
        self.assertEqual(nv.parse_signers("Verifies\n" + self.signer("AB" * 32) +
                                         self.signer("01" * 32, 2)),
                         ["01" * 32, "ab" * 32])
        valid = self.signer()
        for text in ("", "DOES NOT VERIFY", self.signer("g" * 64), self.signer("a" * 63),
                     self.signer("a" * 65), self.signer("a" * 32 + " " + "b" * 31),
                     self.signer(number=0), self.signer(number=2), valid + valid,
                     valid + self.signer(number=3),
                     valid.replace("SHA-256", "SHA-1")):
            with self.subTest(text=text), self.assertRaises(nv.ValidationError):
                nv.parse_signers(text)

    def test_signer_parser_must_not_silently_drop_malformed_additional_signer(self):
        # A valid first signer must not hide a malformed second signer from set comparison.
        with self.assertRaises(nv.ValidationError):
            nv.parse_signers(self.signer() + self.signer("g" * 64, 2))

    def upgrade_inputs(self, runner):
        runner.args.upgrade_from = "explicit source with spaces.apk"
        source = self.scratch / runner.args.upgrade_from
        source.write_bytes(b"supplied source bytes, not an SDK key or prebuilt fixture")
        candidate = runner.out / "candidate.apk"
        candidate.write_bytes(b"supplied candidate bytes")
        return source, candidate

    def test_upgrade_copies_supplied_source_and_verifies_same_signers_without_keys(self):
        for profile, old_name, old_code in (("2.1", "2.0", 6), ("2.3", "2.1", 7)):
            runner = self.runner()
            runner.args.profile = profile
            source, candidate = self.upgrade_inputs(runner)
            metadata = f"package: name='{nv.PACKAGE}' versionCode='{old_code}' versionName='{old_name}'"
            with self.subTest(profile=profile), patch.object(
                    runner, "command", side_effect=[metadata, self.signer(), self.signer("AB" * 32)]) as command:
                old = runner.prepare_upgrade(candidate)
            self.assertEqual(old, runner.out / "upgrade-from.apk")
            self.assertEqual(old.read_bytes(), source.read_bytes())
            source.write_bytes(b"changed caller input")
            self.assertNotEqual(old.read_bytes(), source.read_bytes())
            self.assertEqual(command.call_args_list, [
                call([runner.aapt, "dump", "badging", old]),
                call([runner.apksigner, "verify", "--verbose", "--print-certs", old]),
                call([runner.apksigner, "verify", "--verbose", "--print-certs", candidate])])
            result = runner.results["upgrade"]
            self.assertEqual(result["status"], "verified-inputs")
            self.assertEqual(result["from"], (old_name, old_code))
            self.assertEqual(result["to"], profile)
            self.assertEqual(result["signer_sha256"], ["ab" * 32])
            self.assertEqual(result["source_sha256"], nv.hashlib.sha256(old.read_bytes()).hexdigest())
            self.process.assert_not_called()

    def test_upgrade_rejects_wrong_package_name_code_and_malformed_metadata_before_signing(self):
        for profile, valid_name, valid_code in (("2.1", "2.0", 6), ("2.3", "2.1", 7)):
            invalid = [
                (nv.PACKAGE + ".fake", valid_name, valid_code),
                (nv.PACKAGE, valid_name, valid_code + 1),
                (nv.PACKAGE, valid_name + ".0", valid_code),
                (nv.PACKAGE, profile, nv.PROFILES[profile][0]),
            ]
            texts = [f"package: name='{p}' versionCode='{c}' versionName='{n}'" for p, n, c in invalid]
            for metadata in ["", *texts]:
                runner = self.runner()
                runner.args.profile = profile
                _, candidate = self.upgrade_inputs(runner)
                with self.subTest(profile=profile, metadata=metadata), \
                        patch.object(runner, "command", return_value=metadata) as command:
                    with self.assertRaises(nv.ValidationError):
                        runner.prepare_upgrade(candidate)
                self.assertEqual(command.call_count, 1)
                self.assertNotIn("upgrade", runner.results)
                self.assertEqual(runner.installed, [])

    def test_upgrade_rejects_missing_source_without_commands(self):
        runner = self.runner()
        runner.args.upgrade_from = "absent supplied.apk"
        with self.assertRaises(FileNotFoundError):
            runner.prepare_upgrade(Path("candidate.apk"))
        self.process.assert_not_called()

    def test_upgrade_requires_identical_complete_signer_sets(self):
        metadata = f"package: name='{nv.PACKAGE}' versionCode='7' versionName='2.1'"
        for source_certs, candidate_certs in (
                (self.signer(), self.signer("cd" * 32)),
                (self.signer() + self.signer("cd" * 32, 2), self.signer()),
                (self.signer("g" * 64), self.signer()),
                (self.signer(), "DOES NOT VERIFY"),
                ("", self.signer())):
            runner = self.runner()
            _, candidate = self.upgrade_inputs(runner)
            with self.subTest(source=source_certs, candidate=candidate_certs), \
                    patch.object(runner, "command", side_effect=[metadata, source_certs, candidate_certs]):
                with self.assertRaises(nv.ValidationError):
                    runner.prepare_upgrade(candidate)
            self.assertNotIn("upgrade", runner.results)
            self.assertEqual(runner.installed, [])

    def test_upgrade_accepts_same_multiple_signers_in_different_order(self):
        runner = self.runner()
        _, candidate = self.upgrade_inputs(runner)
        metadata = f"package: name='{nv.PACKAGE}' versionCode='7' versionName='2.1'"
        with patch.object(runner, "command", side_effect=[
                metadata, self.signer() + self.signer("cd" * 32, 2),
                self.signer("CD" * 32) + self.signer("AB" * 32, 2)]):
            runner.prepare_upgrade(candidate)
        self.assertEqual(runner.results["upgrade"]["signer_sha256"], ["ab" * 32, "cd" * 32])

    def test_nonzero_apksigner_verification_rejects_even_valid_certificate_output(self):
        for failed_index in (1, 2):
            runner = self.runner()
            _, candidate = self.upgrade_inputs(runner)
            metadata = f"package: name='{nv.PACKAGE}' versionCode='7' versionName='2.1'"
            results = [subprocess.CompletedProcess([], 0, metadata.encode(), b"")]
            results += [subprocess.CompletedProcess([], int(i == failed_index),
                                                   self.signer().encode(), b"verification error")
                        for i in (1, 2)]
            self.process.side_effect = results
            self.process.reset_mock()
            with self.subTest(failed_index=failed_index), self.assertRaisesRegex(
                    nv.ValidationError, "Command failed"):
                runner.prepare_upgrade(candidate)
            self.assertEqual(self.process.call_count, failed_index + 1)
            self.assertNotIn("upgrade", runner.results)
            self.assertEqual(runner.installed, [])

    def test_upgrade_verifies_false_persistence_before_and_after_replacement_then_sides_true(self):
        for profile, old_name, old_code in (("2.1", "2.0", 6), ("2.3", "2.1", 7)):
            runner = self.runner()
            runner.args.profile = profile
            runner.installed = [nv.PACKAGE, nv.FIXTURE]
            runner.results["upgrade"] = {"status": "verified-inputs"}
            events = Mock()
            nodes = [nv.ET.Element("node", checked=value) for value in ("false", "false", "true", "true")]
            with self.subTest(profile=profile), contextlib.ExitStack() as stack:
                for name, options in (
                        ("shell", {"return_value": f"versionName={old_name}\nversionCode={old_code}\n"}),
                        ("main", {}), ("checkbox", {}), ("node", {"side_effect": nodes}),
                        ("adb", {"return_value": "Success"}), ("verify_installed", {})):
                    events.attach_mock(stack.enter_context(patch.object(runner, name, **options)), name)
                runner.upgrade(Path("candidate.apk"))
            self.assertEqual(events.mock_calls, [
                call.shell("dumpsys", "package", nv.PACKAGE), call.main(),
                call.checkbox("cbAutoStart", False),
                call.shell("input", "keyevent", "KEYCODE_HOME", mutate=True),
                call.shell("am", "force-stop", nv.PACKAGE, mutate=True), call.main(),
                call.node("cbAutoStart"),
                call.shell("input", "keyevent", "KEYCODE_HOME", mutate=True),
                call.shell("am", "force-stop", nv.PACKAGE, mutate=True),
                call.adb("install", "-r", "candidate.apk", mutate=True, timeout=120),
                call.verify_installed(), call.main(), call.node("cbAutoStart"),
                call.node("cbLeftEdge"), call.node("cbRightEdge")])
            result = json.loads((runner.out / "results.json").read_text())
            self.assertEqual(result["upgrade"]["status"], "passed")
            self.assertEqual([c["name"] for c in result["checks"]], [
                "upgrade-source-installed", "upgrade-source-auto-start-false",
                "upgrade-preserved-cbAutoStart", "upgrade-preserved-cbLeftEdge",
                "upgrade-preserved-cbRightEdge"])
            self.assertTrue(all(c["passed"] for c in result["checks"]))
            self.assertEqual(runner.installed.count(nv.PACKAGE), 1)

    def test_upgrade_failed_persistence_or_new_defaults_never_pass(self):
        for index in range(4):
            runner = self.runner()
            runner.installed = [nv.PACKAGE]
            runner.results["upgrade"] = {"status": "verified-inputs"}
            values = ["false", "false", "true", "true"]
            values[index] = "true" if index < 2 else "false"
            nodes = [nv.ET.Element("node", checked=value) for value in values]
            with self.subTest(index=index), \
                    patch.object(runner, "shell", return_value="versionName=2.1\nversionCode=7\n"), \
                    patch.object(runner, "main"), patch.object(runner, "checkbox"), \
                    patch.object(runner, "node", side_effect=nodes), \
                    patch.object(runner, "adb", return_value="Success") as adb, \
                    patch.object(runner, "verify_installed"):
                with self.assertRaises(nv.ValidationError):
                    runner.upgrade(Path("candidate.apk"))
                self.assertEqual(adb.call_count, 0 if index == 0 else 1)
            result = json.loads((runner.out / "results.json").read_text())
            self.assertFalse(result["checks"][-1]["passed"])
            self.assertNotEqual(result["upgrade"]["status"], "passed")

    def test_upgrade_rejects_wrong_installed_source_before_ui_or_replacement(self):
        runner = self.runner()
        runner.installed = [nv.PACKAGE]
        runner.results["upgrade"] = {"status": "verified-inputs"}
        with patch.object(runner, "shell", return_value="versionName=2.1\nversionCode=6\n"), \
                patch.object(runner, "main") as main, patch.object(runner, "adb") as adb:
            with self.assertRaisesRegex(nv.ValidationError, "upgrade-source-installed"):
                runner.upgrade(Path("candidate.apk"))
        main.assert_not_called()
        adb.assert_not_called()
        self.assertEqual(runner.installed, [nv.PACKAGE])

    def test_upgrade_ui_cannot_replace_unowned_package(self):
        runner = self.runner()
        runner.results["upgrade"] = {"status": "verified-inputs"}
        with patch.object(runner, "shell", return_value="versionName=2.1\nversionCode=7\n"), \
                patch.object(runner, "main"), patch.object(runner, "checkbox"), \
                patch.object(runner, "node", return_value=nv.ET.Element("node", checked="false")), \
                patch.object(runner, "adb") as adb, patch.object(runner, "verify_installed") as verify:
            with self.assertRaisesRegex(nv.ValidationError, "successfully owned"):
                runner.upgrade(Path("candidate.apk"))
        adb.assert_not_called()
        verify.assert_not_called()
        self.assertNotEqual(runner.results["upgrade"]["status"], "passed")

    @contextlib.contextmanager
    def prepared_host(self, profile="2.3", upgrade=False, candidate_metadata=None, packages=None):
        runner = self.runner()
        runner.args.profile = profile
        runner.args.apk = "explicit candidate with spaces.apk"
        candidate_bytes = b"caller supplied candidate, never executed"
        (self.scratch / runner.args.apk).write_bytes(candidate_bytes)
        if upgrade:
            self.upgrade_inputs(runner)
        candidate = runner.out / "candidate.apk"
        fixture = runner.out / "fixture" / "outputs" / "apk" / "debug" / "native-fixture-debug.apk"
        metadata = candidate_metadata or (
            f"package: name='{nv.PACKAGE}' versionCode='{nv.PROFILES[profile][0]}' versionName='{profile}'")
        old_name, old_code = nv.UPGRADE_SOURCES[profile]
        events = Mock()

        def command(argv, **kwargs):
            if argv == [runner.aapt, "dump", "badging", candidate]:
                return metadata
            if argv == [runner.aapt, "dump", "badging", runner.out / "upgrade-from.apk"]:
                return f"package: name='{nv.PACKAGE}' versionCode='{old_code}' versionName='{old_name}'"
            if argv == [runner.aapt, "dump", "badging", fixture]:
                return f"package: name='{nv.FIXTURE}' versionCode='1' versionName='1.0'"
            if argv in ([runner.apksigner, "verify", "--verbose", "--print-certs", candidate],
                        [runner.apksigner, "verify", "--verbose", "--print-certs", runner.out / "upgrade-from.apk"]):
                return self.signer()
            expected_build = [
                self.scratch / "gradlew.bat", "--no-daemon", "--console=plain",
                ":native-fixture:assembleDebug",
                "-PnativeValidationOutput=" + str((runner.out / "fixture").relative_to(self.scratch))]
            self.assertEqual(argv, expected_build, "Unexpected host command")
            self.assertEqual(kwargs["timeout"], 600)
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_bytes(b"mock fixture bytes, no build")
            return ""

        package_outputs = iter(packages or ("", ""))

        def shell(*args, **kwargs):
            responses = {
                ("getprop", "ro.kernel.qemu"): "1",
                ("getprop", "ro.build.version.sdk"): "34",
                ("am", "get-current-user"): "0",
                ("pm", "list", "users"): "Users:\nUserInfo{0:Owner:13}",
            }
            if args == ("pm", "list", "packages", "-u"):
                return next(package_outputs)
            if args in responses:
                return responses[args]
            self.assertIn(args, (
                ("pm", "grant", nv.PACKAGE, "android.permission.POST_NOTIFICATIONS"),
                ("appops", "set", nv.PACKAGE, "SYSTEM_ALERT_WINDOW", "deny"),
                ("appops", "set", nv.PACKAGE, "SYSTEM_ALERT_WINDOW", "allow")))
            self.assertTrue(kwargs["mutate"])
            return ""

        def adb(*args, **kwargs):
            if args == ("emu", "avd", "name"):
                return runner.owner["avd"] + "\nOK\n"
            self.assertEqual(args[0], "install")
            self.assertNotIn("-r", args)
            return "Success"

        with contextlib.ExitStack() as stack:
            for name, options in (
                    ("guard", {}), ("command", {"side_effect": command}), ("shell", {"side_effect": shell}),
                    ("adb", {"side_effect": adb}), ("set_display", {}), ("upgrade", {}),
                    ("verify_installed", {}), ("main", {}), ("click", {}), ("expect_windows", {}),
                    ("node", {"return_value": nv.ET.Element("node", text="未授予 已停止")})):
                events.attach_mock(stack.enter_context(patch.object(runner, name, **options)), name)
            yield runner, events, candidate, fixture, candidate_bytes
        self.process.assert_not_called()

    def test_prepare_selects_optional_upgrade_and_exact_supplied_candidate_for_both_profiles(self):
        for profile in ("2.1", "2.3"):
            for upgrade in (False, True):
                with self.subTest(profile=profile, upgrade=upgrade), \
                        self.prepared_host(profile, upgrade) as (runner, events, candidate, fixture, data):
                    runner.prepare()
                    self.assertEqual(candidate.read_bytes(), data)
                    self.assertEqual(runner.results["apk_sha256"], nv.hashlib.sha256(data).hexdigest())
                    first = runner.out / "upgrade-from.apk" if upgrade else candidate
                    self.assertEqual(events.adb.call_args_list, [
                        call("emu", "avd", "name"),
                        call("install", "-R", str(first), mutate=True, timeout=120),
                        call("install", "-R", str(fixture), mutate=True, timeout=120)])
                    self.assertEqual(runner.installed, [nv.PACKAGE, nv.FIXTURE])
                    if upgrade:
                        events.upgrade.assert_called_once_with(candidate)
                        events.verify_installed.assert_not_called()
                        self.assertEqual(first.read_bytes(),
                                         (self.scratch / runner.args.upgrade_from).read_bytes())
                    else:
                        events.upgrade.assert_not_called()
                        events.verify_installed.assert_called_once_with()
                        self.assertEqual(runner.results["upgrade"]["status"], "skipped")

    def test_smoke_keeps_real_prepare_build_fresh_installs_and_receipt_sdk(self):
        for profile in ("2.1", "2.3"):
            with self.subTest(profile=profile), \
                    self.prepared_host(profile) as (runner, events, candidate, fixture, data), \
                    patch.dict(os.environ, ANDROID_HOME="wrong-sdk", ANDROID_SDK_ROOT="other-sdk"):
                runner.args.smoke = True
                runner.prepare()
                events.guard.assert_called_once_with()
                self.assertEqual(runner.sdk, Path(runner.owner["sdk"]))
                builds = [invocation for invocation in events.command.call_args_list
                          if invocation.args[0][0] == self.scratch / "gradlew.bat"]
                self.assertEqual(len(builds), 1)
                for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
                    self.assertEqual(builds[0].kwargs["env"][key], runner.owner["sdk"])
                self.assertEqual(events.adb.call_args_list, [
                    call("emu", "avd", "name"),
                    call("install", "-R", str(candidate), mutate=True, timeout=120),
                    call("install", "-R", str(fixture), mutate=True, timeout=120)])
                package_checks = [invocation for invocation in events.shell.call_args_list
                                  if invocation.args == ("pm", "list", "packages", "-u")]
                self.assertEqual(len(package_checks), 2)
                events.verify_installed.assert_called_once_with()
                events.upgrade.assert_not_called()
                self.assertEqual(runner.results["apk_sha256"], nv.hashlib.sha256(data).hexdigest())
                self.assertEqual(runner.results["fixture_sha256"],
                                 nv.hashlib.sha256(fixture.read_bytes()).hexdigest())
                self.assertEqual(runner.results["upgrade"]["status"], "skipped")
                events.expect_windows.assert_called_once_with("permission-denied-no-windows", [])

    def test_prepare_rejects_candidate_profile_mismatch_before_build_or_install(self):
        for profile, name, code in (("2.1", "2.1", 6), ("2.1", "2.3", 9),
                                    ("2.3", "2.3", 7), ("2.3", "2.1", 7)):
            metadata = f"package: name='{nv.PACKAGE}' versionCode='{code}' versionName='{name}'"
            with self.subTest(profile=profile, name=name, code=code), \
                    self.prepared_host(profile, candidate_metadata=metadata) as (runner, events, *_):
                with self.assertRaisesRegex(nv.ValidationError, "does not match profile"):
                    runner.prepare()
                self.assertEqual(events.command.call_count, 1)
                events.adb.assert_called_once_with("emu", "avd", "name")
                events.upgrade.assert_not_called()
                self.assertEqual(runner.installed, [])

    def test_prepare_rechecks_package_absence_after_mock_build_without_replacing_others(self):
        for package in (nv.PACKAGE, nv.FIXTURE):
            for appeared_during_build in (False, True):
                listing = "package:" + package
                listings = ("", listing) if appeared_during_build else (listing,)
                with self.subTest(package=package, during_build=appeared_during_build), \
                        self.prepared_host(upgrade=True, packages=listings) as (runner, events, *_):
                    with self.assertRaisesRegex(nv.ValidationError, "already installed|appeared during build"):
                        runner.prepare()
                    package_checks = [
                        index for index, invocation in enumerate(events.mock_calls)
                        if invocation == call.shell("pm", "list", "packages", "-u")]
                    builds = [
                        index for index, invocation in enumerate(events.mock_calls)
                        if invocation[0] == "command"
                        and invocation.args[0][0] == self.scratch / "gradlew.bat"]
                    self.assertEqual(len(package_checks), 2 if appeared_during_build else 1)
                    if appeared_during_build:
                        self.assertEqual(len(builds), 1)
                        self.assertLess(package_checks[0], builds[0])
                        self.assertLess(builds[0], package_checks[1])
                    else:
                        self.assertEqual(builds, [])
                    events.adb.assert_called_once_with("emu", "avd", "name")
                    self.assertEqual(runner.installed, [])
                    events.adb.reset_mock()
                    runner.cleanup()
                    events.adb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
