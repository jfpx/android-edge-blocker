"""Offline contracts: python -B -m unittest discover -s tests -p test_build_helper.py -v."""

import os
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh")
WRAPPER = r"""@echo off
setlocal
>> "%~dp0wrapper-call.txt" echo %*
>> "%~dp0wrapper-call.txt" echo %CD%
>> "%~dp0wrapper-call.txt" echo %ANDROID_HOME%
>> "%~dp0wrapper-call.txt" echo %ANDROID_SDK_ROOT%
if defined FAKE_GRADLE_FAIL exit /b 23
set "variant=debug"
set "apk=app-debug.apk"
if "%~1"==":app:assembleRelease" (
    set "variant=release"
    set "apk=app-release-unsigned.apk"
) else (
    if not "%~1"==":app:assembleDebug" exit /b 24
)
mkdir "%~dp0app\build\outputs\apk\%variant%" 2>nul
> "%~dp0app\build\outputs\apk\%variant%\%apk%" echo dummy-%variant%
exit /b 0
"""


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows and pwsh required")
class BuildHelperTests(unittest.TestCase):
    def setUp(self):
        self.scratch = ROOT.joinpath("build", "test-build-helper-" + uuid.uuid4().hex)
        self.scratch.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.scratch)
        self.project = self.scratch.joinpath("copied project")
        self.cwd = self.scratch.joinpath("unrelated cwd")
        self.sdk = self.scratch.joinpath("explicit sdk")
        self.home = self.scratch.joinpath("home sdk")
        self.fallback = self.scratch.joinpath("fallback sdk")
        for directory in (self.project, self.cwd, self.sdk, self.home, self.fallback):
            directory.mkdir()
        self.script = self.project.joinpath("build-apk.ps1")
        shutil.copyfile(ROOT.joinpath("build-apk.ps1"), self.script)
        self.wrapper = self.project.joinpath("gradlew.bat")
        self.wrapper.write_text(WRAPPER, encoding="ascii")
        self.trace = self.project.joinpath("wrapper-call.txt")
        self.cwd.joinpath("gradlew.bat").write_text("@exit /b 91\n", encoding="ascii")
        for variant, name in (("debug", "app-debug.apk"),
                              ("release", "app-release-unsigned.apk")):
            decoy = self.cwd.joinpath("app", "build", "outputs", "apk", variant, name)
            decoy.parent.mkdir(parents=True)
            decoy.write_bytes(b"wrong-cwd-apk")
        self.env = os.environ.copy()
        self.env.update(ANDROID_HOME=str(self.home), ANDROID_SDK_ROOT=str(self.fallback),
                        TEMP=str(self.scratch), TMP=str(self.scratch))
        self.env.pop("FAKE_GRADLE_FAIL", None)

    def invoke(self, *arguments):
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(self.script), *arguments],
            cwd=self.cwd, env=self.env, capture_output=True, encoding="utf-8",
            errors="replace", timeout=30,
        )

    def assert_build(self, result, variant, sdk):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = self.trace.read_text().splitlines()
        self.assertEqual(lines[0], ":app:assemble" + variant.capitalize())
        self.assertEqual(Path(lines[1]), self.project)
        self.assertEqual([Path(value) for value in lines[2:]], [sdk, sdk])
        output = self.project.joinpath("build", "distributions",
                                       f"SimpleEdgeBlocker-{variant}.apk")
        self.assertEqual(output.read_bytes(), f"dummy-{variant}\r\n".encode())
        apks = list(self.project.rglob("SimpleEdgeBlocker*.apk"))
        self.assertEqual(apks, [output])
        self.assertNotIn("v1.0", result.stdout + result.stderr)
        self.assertFalse(self.cwd.joinpath("build", "distributions").exists())

    def assert_rejected(self, *arguments):
        result = self.invoke(*arguments)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.trace.exists(), result.stdout + result.stderr)
        self.assertFalse(self.project.joinpath("build", "distributions").exists())

    def test_debug_explicit_sdk_overrides_both_environment_paths(self):
        self.assert_build(self.invoke("-Debug", "-AndroidSdkPath", str(self.sdk)),
                          "debug", self.sdk)

    def test_release_task_and_unsigned_source(self):
        self.assert_build(self.invoke("-Release"), "release", self.home)

    def test_default_debug_and_android_home_precedes_sdk_root(self):
        self.assert_build(self.invoke(), "debug", self.home)

    def test_sdk_root_fallback_without_android_home(self):
        self.env.pop("ANDROID_HOME")
        self.assert_build(self.invoke("-Debug"), "debug", self.fallback)

    def test_sdk_root_fallback_with_empty_android_home(self):
        self.env["ANDROID_HOME"] = ""
        self.assert_build(self.invoke("-Debug"), "debug", self.fallback)

    def test_bad_explicit_sdk_does_not_fall_back(self):
        sdk_file = self.scratch.joinpath("not a directory")
        sdk_file.write_text("not an SDK", encoding="ascii")
        for sdk in (self.scratch.joinpath("missing sdk"), sdk_file):
            with self.subTest(sdk=sdk.name):
                self.assert_rejected("-AndroidSdkPath", str(sdk))

    def test_missing_sdk_rejected(self):
        self.env.pop("ANDROID_HOME")
        self.env.pop("ANDROID_SDK_ROOT")
        self.assert_rejected()

    def test_missing_repository_wrapper_rejected(self):
        self.wrapper.unlink()
        self.assert_rejected("-Debug")

    def test_gradle_failure_returns_nonzero_without_publishing(self):
        self.env["FAKE_GRADLE_FAIL"] = "1"
        stale = self.project.joinpath("app", "build", "outputs", "apk",
                                      "debug", "app-debug.apk")
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale-apk-must-not-be-published")
        result = self.invoke("-Debug")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.trace.read_text().splitlines()[0], ":app:assembleDebug")
        self.assertFalse(self.project.joinpath("build", "distributions").exists())
        self.assertEqual(stale.read_bytes(), b"stale-apk-must-not-be-published")

    def test_debug_and_release_rejected_before_wrapper(self):
        self.assert_rejected("-Debug", "-Release")


if __name__ == "__main__":
    unittest.main()
