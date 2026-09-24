"""Bounded, offline contract tests for the privacy gate (stdlib only)."""

import sys

sys.dont_write_bytecode = True

import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import unittest
import uuid
import zipfile
import zlib


PROJECT = Path(__file__).resolve().parents[1]
IMPLEMENTATION = PROJECT / "tools" / "privacy_gate.py"
_GATE = None


def load_gate():
    global _GATE
    if _GATE is None:
        if not IMPLEMENTATION.is_file():
            raise unittest.SkipTest("Privacy gate implementation is not available")
        module_name = "_privacy_gate_contract_tests"
        spec = importlib.util.spec_from_file_location(module_name, IMPLEMENTATION)
        if spec is None or spec.loader is None:
            raise AssertionError("Cannot load privacy gate implementation")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise AssertionError("Privacy gate import failed") from None
        _GATE = module
    return _GATE


def classic_token(kind="p"):
    return "".join(("gh", kind, "_", "A1b2C3" * 6))


def fine_token():
    return "".join(("git", "hub", "_pat", "_", "A" * 22, "_", "b" * 59))


def windows_home():
    return "\\".join(("C:", "Us" + "ers", "FixturePerson", "Documents", "note.txt"))


def checkout_path(segment):
    return "\\".join(("D:", segment, "fixture-project", "src", "Main.kt"))


def unix_home():
    return "/".join(("", "ho" + "me", "fixtureperson", "notes.txt"))


def credential_url():
    return "".join(("https", "://", "fixture-user", ":", "NotReal928", "@", "example.test", "/repo"))


def assignment(key="password", value="NotReal928Secret"):
    return "".join((key, ' = "', value, '"'))


def copilot_location():
    return "\\".join(("C:", "Us" + "ers", "FixturePerson", "." + "copilot", "session-" + "state", "fixture.json"))


def identifier_assignment(key):
    return json.dumps({key: "-".join(("12345678", "1234", "4abc", "8def", "123456789abc"))})


def escaped_unicode(text):
    units = text.encode("utf-16-be")
    return "".join("\\u%04x" % struct.unpack_from(">H", units, i)[0] for i in range(0, len(units), 2))


def archive(entries, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as bundle:
        for name, payload in entries:
            bundle.writestr(name, payload)
    return output.getvalue()


def encrypted_archive():
    data = bytearray(archive([("asset.txt", b"ordinary asset")]))
    # ZIP encryption is advertised independently in the local and central headers.
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    for offset in (local + 6, central + 8):
        struct.pack_into("<H", data, offset, struct.unpack_from("<H", data, offset)[0] | 1)
    return bytes(data)


def bad_crc_archive():
    data = bytearray(archive([("asset.txt", b"ordinary asset")]))
    name_length, extra_length = struct.unpack_from("<HH", data, 26)
    data[30 + name_length + extra_length] ^= 1
    return bytes(data)


def uleb128(value):
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


def mutf8(text):
    result = bytearray()
    units = text.encode("utf-16-be", errors="surrogatepass")
    for offset in range(0, len(units), 2):
        unit = struct.unpack_from(">H", units, offset)[0]
        if 0 < unit <= 0x7F:
            result.append(unit)
        elif unit <= 0x7FF:
            result.extend((0xC0 | (unit >> 6), 0x80 | (unit & 0x3F)))
        else:
            result.extend((0xE0 | (unit >> 12), 0x80 | ((unit >> 6) & 0x3F), 0x80 | (unit & 0x3F)))
    return bytes(result)


def seal_dex(data):
    struct.pack_into("<I", data, 32, len(data))
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 0xFFFFFFFF)
    return bytes(data)


def dex_file(strings):
    strings = sorted(set(strings), key=lambda text: text.encode("utf-16-be", errors="surrogatepass"))
    ids_offset = 112
    data_offset = ids_offset + 4 * len(strings)
    payload = bytearray()
    offsets = []
    for text in strings:
        offsets.append(data_offset + len(payload))
        units = len(text.encode("utf-16-le", errors="surrogatepass")) // 2
        payload.extend(uleb128(units) + mutf8(text) + b"\0")
    payload.extend(b"\0" * (-len(payload) % 4))
    map_offset = data_offset + len(payload)
    map_items = [(0, 1, 0)]
    if strings:
        map_items.extend(((1, len(strings), ids_offset), (0x2002, len(strings), data_offset)))
    map_items.append((0x1000, 1, map_offset))
    payload.extend(struct.pack("<I", len(map_items)))
    for kind, count, offset in map_items:
        payload.extend(struct.pack("<HHII", kind, 0, count, offset))
    result = bytearray(data_offset)
    result[:8] = b"dex\n035\0"
    struct.pack_into("<II", result, 36, 112, 0x12345678)
    struct.pack_into("<I", result, 52, map_offset)
    struct.pack_into("<II", result, 56, len(strings), ids_offset if strings else 0)
    struct.pack_into("<II", result, 104, len(payload), data_offset)
    for index, offset in enumerate(offsets):
        struct.pack_into("<I", result, ids_offset + 4 * index, offset)
    result.extend(payload)
    return seal_dex(result)


def length8(length):
    if length > 0x7FFF:
        raise ValueError("Fixture string too large")
    return bytes((length,)) if length < 0x80 else bytes((0x80 | (length >> 8), length & 0xFF))


def length16(length):
    if length < 0x8000:
        return struct.pack("<H", length)
    return struct.pack("<HH", 0x8000 | (length >> 16), length & 0xFFFF)


def string_pool(strings, utf8=True):
    payload = bytearray()
    offsets = []
    for text in strings:
        offsets.append(len(payload))
        units = len(text.encode("utf-16-le")) // 2
        if utf8:
            encoded = text.encode("utf-8")
            payload.extend(length8(units) + length8(len(encoded)) + encoded + b"\0")
        else:
            payload.extend(length16(units) + text.encode("utf-16-le") + b"\0\0")
    start = 28 + 4 * len(strings)
    payload.extend(b"\0" * (-(start + len(payload)) % 4))
    header = struct.pack("<HHI5I", 1, 28, start + len(payload), len(strings), 0, 0x100 if utf8 else 0, start, 0)
    return header + b"".join(struct.pack("<I", offset) for offset in offsets) + payload


def android_file(strings, utf8=True, xml=False):
    pool = string_pool(strings, utf8)
    if xml:
        return struct.pack("<HHI", 3, 8, 8 + len(pool)) + pool
    return struct.pack("<HHII", 2, 12, 12 + len(pool), 0) + pool


@contextlib.contextmanager
def scratch_directory():
    path = PROJECT / (".privacy-gate-tests-" + uuid.uuid4().hex)
    try:
        path.mkdir()
    except OSError:
        raise AssertionError("Cannot create isolated fixture directory") from None
    try:
        yield path
    finally:
        try:
            def remove_readonly(function, filename, error_info):
                if not isinstance(error_info[1], PermissionError):
                    raise error_info[1]
                os.chmod(filename, stat.S_IREAD | stat.S_IWRITE)
                function(filename)

            shutil.rmtree(path, onerror=remove_readonly)
        except OSError:
            raise AssertionError("Cannot remove isolated fixture directory") from None


def subprocess_environment():
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CEILING_DIRECTORIES": str(PROJECT),
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


class GateCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = load_gate()

    def scan(self, payload=b"", name="fixture.bin", **limits):
        scanner = self.gate.Scanner(limits=self.gate.Limits(**limits))
        self.scan_into(scanner, payload, name)
        self.check_result_shape(scanner)
        return scanner

    def scan_into(self, scanner, payload, name=None):
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                if name is None:
                    scanner.scan_bytes(payload)
                else:
                    scanner.scan_bytes(payload, name=name)
        except Exception:
            raise self.failureException("Scanner raised instead of returning a safe result") from None

    def check_result_shape(self, scanner):
        self.assertTrue(isinstance(scanner.findings, set), "Findings must be a set")
        self.assertTrue(isinstance(scanner.errors, set), "Errors must be a set")
        self.assertEqual(scanner.clean, not scanner.findings and not scanner.errors)
        for finding in scanner.findings | scanner.errors:
            self.assertTrue(isinstance(finding, self.gate.Finding), "Unexpected finding type")
            self.assertTrue(isinstance(finding.rule, str), "Rule must be a string")
            self.assertTrue(
                isinstance(finding.reference, str)
                and re.fullmatch(r"file@[0-9a-fA-F]+", finding.reference) is not None,
                "Reference must be an opaque file hash",
            )
            self.assertTrue(
                finding.line is None or (isinstance(finding.line, int) and finding.line >= 0),
                "Line must be absent or a nonnegative integer",
            )
        self.assertTrue(all(item.rule.startswith("ERROR_") for item in scanner.errors), "Errors need error rule IDs")

    def assert_clean(self, scanner):
        self.assertTrue(scanner.clean, "Clean fixture was rejected")

    def assert_rule(self, scanner, rule, allow_errors=False):
        self.assertTrue(any(finding.rule == rule for finding in scanner.findings), "Expected rule ID missing: " + rule)
        self.assertFalse(scanner.clean, "A finding must make the result unclean")
        if not allow_errors:
            self.assertFalse(bool(scanner.errors), "Valid fixture generated a scan error")

    def assert_error(self, scanner):
        self.assertTrue(bool(scanner.errors), "Malformed or over-budget fixture must fail closed")
        self.assertFalse(scanner.clean, "An error must make the result unclean")

    def assert_redacted(self, scanner, forbidden):
        rendered = repr(scanner.findings) + repr(scanner.errors)
        for value in forbidden:
            self.assertTrue(value not in rendered, "Finding representation exposed fixture data")


class LimitsAndResultTests(GateCase):
    def test_limits_defaults_and_frozen_dataclass(self):
        limits = self.gate.Limits()
        self.assertTrue(dataclasses.is_dataclass(limits), "Limits must be a dataclass")
        self.assertEqual(dataclasses.asdict(limits), {
            "max_file_bytes": 64 * 1024 * 1024,
            "max_entry_bytes": 32 * 1024 * 1024,
            "max_total_bytes": 256 * 1024 * 1024,
            "max_entries": 10000,
            "max_depth": 3,
            "max_ratio": 200.0,
            "max_strings": 2000000,
        })
        with self.assertRaises(dataclasses.FrozenInstanceError):
            limits.max_depth = 4

    def test_new_scanner_and_empty_input_are_clean(self):
        scanner = self.gate.Scanner(limits=self.gate.Limits())
        self.check_result_shape(scanner)
        self.assert_clean(scanner)
        self.assert_clean(self.scan())

    def test_scan_bytes_default_name(self):
        scanner = self.gate.Scanner(limits=self.gate.Limits())
        self.scan_into(scanner, classic_token().encode())
        self.check_result_shape(scanner)
        self.assert_rule(scanner, "SECRET_TOKEN")

    def test_findings_accumulate_without_losing_prior_results(self):
        scanner = self.scan(classic_token().encode(), "first.txt")
        before = set(scanner.findings)
        self.scan_into(scanner, b"ordinary text", "second.txt")
        self.assertTrue(before <= scanner.findings, "A later clean file erased findings")
        self.check_result_shape(scanner)

    def test_repeated_scan_deduplicates_findings(self):
        scanner = self.scan(classic_token().encode())
        before = set(scanner.findings)
        self.scan_into(scanner, classic_token().encode(), "fixture.bin")
        self.assertTrue(before == scanner.findings, "Identical findings must deduplicate")

    def test_line_number_for_plain_text(self):
        scanner = self.scan(("first\nsecond\n" + classic_token() + "\nfourth\n").encode(), "source.txt")
        self.assert_rule(scanner, "SECRET_TOKEN")
        self.assertTrue(any(item.rule == "SECRET_TOKEN" and item.line == 3 for item in scanner.findings), "Missing source line")

    def test_line_number_for_crlf(self):
        scanner = self.scan(("first\r\n" + classic_token() + "\r\n").encode())
        self.assertTrue(any(item.rule == "SECRET_TOKEN" and item.line == 2 for item in scanner.findings), "Incorrect CRLF line")

    def test_references_and_representations_are_redacted(self):
        name = "unique-fixture-name-8923.txt"
        payload = classic_token() + "\n" + windows_home()
        scanner = self.scan(payload.encode(), name)
        self.assert_rule(scanner, "SECRET_TOKEN")
        self.assert_rule(scanner, "HOST_PATH")
        self.assert_redacted(scanner, (name, classic_token(), windows_home(), "FixturePerson"))

    def test_errors_are_redacted(self):
        name = "unique-damaged-name-7645.zip"
        scanner = self.scan(b"PK\x03\x04" + classic_token().encode(), name)
        self.assert_error(scanner)
        self.assert_redacted(scanner, (name, classic_token()))


class ContentTests(GateCase):
    def test_github_classic_token_variants(self):
        for kind in ("p", "o", "u", "s", "r"):
            with self.subTest(variant=kind):
                self.assert_rule(self.scan(classic_token(kind).encode()), "SECRET_TOKEN")

    def test_github_fine_grained_token(self):
        self.assert_rule(self.scan(fine_token().encode()), "SECRET_TOKEN")

    def test_tokens_in_source_and_json(self):
        token = classic_token()
        payloads = (json.dumps({"value": token}), 'val value = "' + token + '"', "Authorization: Bearer " + token)
        for index, payload in enumerate(payloads):
            with self.subTest(case=index):
                self.assert_rule(self.scan(payload.encode()), "SECRET_TOKEN")

    def test_short_token_prefixes_are_clean(self):
        self.assert_clean(self.scan((" ".join(("gh" + "p_", "git" + "hub_pat_", "gh" + "p_short"))).encode()))

    def test_url_credentials(self):
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme):
                payload = credential_url().replace("https", scheme, 1)
                self.assert_rule(self.scan(payload.encode()), "CREDENTIAL_URL")

    def test_percent_encoded_url_credentials(self):
        payload = "".join(("https://", "fixture%40user", ":", "not%3Areal", "@example.test/path"))
        self.assert_rule(self.scan(payload.encode()), "CREDENTIAL_URL")

    def test_public_urls_email_and_credits_are_clean(self):
        payload = "\n".join((
            "https://github.com/example/project",
            "https://example.test/docs?q=android",
            "Contact maintainer@example.test",
            "Credits: Example Author <author@example.test>",
            "Licensed under Apache-2.0",
        ))
        self.assert_clean(self.scan(payload.encode()))

    def test_private_key_headers(self):
        for label in ("PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "OPENSSH PRIVATE KEY"):
            with self.subTest(kind=label):
                payload = "".join(("-----", "BEGIN ", label, "-----\n", "YWJjZGVm\n", "-----", "END ", label, "-----"))
                self.assert_rule(self.scan(payload.encode()), "PRIVATE_KEY")

    def test_public_key_and_certificate_are_clean(self):
        for label in ("PUBLIC KEY", "CERTIFICATE"):
            with self.subTest(kind=label):
                payload = "".join(("-----", "BEGIN ", label, "-----\nYWJj\n-----", "END ", label, "-----"))
                self.assert_clean(self.scan(payload.encode()))

    def test_obvious_credential_assignments(self):
        for key in ("password", "api_key", "token"):
            with self.subTest(key=key):
                self.assert_rule(self.scan(assignment(key).encode()), "CREDENTIAL_ASSIGNMENT")

    def test_json_credential_assignment(self):
        self.assert_rule(self.scan(json.dumps({"pass" + "word": "NotReal928Secret"}).encode()), "CREDENTIAL_ASSIGNMENT")

    def test_placeholder_assignments_are_clean(self):
        for value in ("", "<TOKEN>", "YOUR_TOKEN", "${TOKEN}"):
            with self.subTest(placeholder=value):
                self.assert_clean(self.scan(assignment("token", value).encode()))

    def test_generic_credential_words_are_clean(self):
        self.assert_clean(self.scan(b"Password field. Token parser. API key documentation. Session lifecycle. Device settings."))

    def test_windows_user_path(self):
        self.assert_rule(self.scan(windows_home().encode()), "HOST_PATH")

    def test_concrete_checkout_paths(self):
        for index, segment in enumerate(("work" + "spaces", "jf" + "px")):
            with self.subTest(case=index):
                self.assert_rule(self.scan(checkout_path(segment).encode()), "HOST_PATH")

    def test_unix_home_paths(self):
        paths = (unix_home(), "/".join(("", "Us" + "ers", "FixturePerson", "Documents", "note.txt")))
        for index, path in enumerate(paths):
            with self.subTest(case=index):
                self.assert_rule(self.scan(path.encode()), "HOST_PATH")

    def test_portable_user_path_placeholders_are_clean(self):
        paths = [
            "\\".join(("C:", "Us" + "ers", person, "Documents", "note.txt"))
            for person in ("YourName", "<USER>", "username")
        ]
        paths += [
            "\\".join(("%USERPROFILE%", "Documents", "note.txt")),
            "/".join(("$HOME", "Documents", "note.txt")),
            "/".join(("${HOME}", "Documents", "note.txt")),
            "/".join(("~", "Documents", "note.txt")),
        ]
        for index, path in enumerate(paths):
            with self.subTest(case=index):
                self.assert_clean(self.scan(path.encode()))

    def test_copilot_state_path(self):
        scanner = self.scan(copilot_location().encode())
        self.assert_rule(scanner, "COPILOT_STATE")
        self.assert_redacted(scanner, (copilot_location(), "FixturePerson"))

    def test_session_and_device_identifiers(self):
        for key in ("session_id", "device_id"):
            with self.subTest(key=key):
                self.assert_rule(self.scan(identifier_assignment(key).encode()), "SESSION_DEVICE_ID")

    def test_android_system_service_and_generic_session_code_are_clean(self):
        payload = b"""val manager = context.getSystemService(Context.DEVICE_POLICY_SERVICE)
fun onSessionStarted() = Unit
val deviceName = "Android"
class SessionManager
"""
        self.assert_clean(self.scan(payload, "Main.kt"))

    def test_json_escaped_paths_and_nested_backslashes(self):
        value = windows_home()
        for depth in (1, 2, 3):
            with self.subTest(depth=depth):
                value = json.dumps(value)
                self.assert_rule(self.scan(value.encode()), "HOST_PATH")

    def test_json_unicode_escaped_token_and_path(self):
        for index, (value, rule) in enumerate(((classic_token(), "SECRET_TOKEN"), (windows_home(), "HOST_PATH"))):
            with self.subTest(case=index):
                payload = '{"value":"' + escaped_unicode(value) + '"}'
                self.assert_rule(self.scan(payload.encode()), rule)

    def test_utf16_endianness_with_and_without_bom(self):
        text = "ordinary prefix\n" + classic_token() + "\n" + windows_home()
        for codec, bom in (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")):
            for with_bom in (False, True):
                with self.subTest(codec=codec, bom=with_bom):
                    scanner = self.scan((bom if with_bom else b"") + text.encode(codec), "asset.txt")
                    self.assert_rule(scanner, "SECRET_TOKEN")
                    self.assert_rule(scanner, "HOST_PATH")

    def test_utf8_bom(self):
        self.assert_rule(self.scan(b"\xef\xbb\xbf" + classic_token().encode()), "SECRET_TOKEN")

    def test_clean_unicode(self):
        text = "Android documentation \u4e2d\u6587 \U0001f642"
        for codec in ("utf-8", "utf-16-le", "utf-16-be"):
            with self.subTest(codec=codec):
                self.assert_clean(self.scan(text.encode(codec)))

    def test_unknown_binary_ascii_fallback(self):
        # This only promises recovery of a contiguous printable run, not decoding
        # arbitrary binary formats or finding encrypted/obfuscated secrets.
        data = b"\x91\x00\xfe\x02" + classic_token().encode() + b"\x00\xff\x80"
        self.assert_rule(self.scan(data), "SECRET_TOKEN")

    def test_unknown_binary_without_printable_sensitive_data(self):
        self.assert_clean(self.scan(bytes(range(32)) + b"\xff\xfe\x80\x81\x00"))


class FilenameTests(GateCase):
    def test_backup_names(self):
        for index, name in enumerate(("settings.bak", "source.backup", "Main.kt~")):
            with self.subTest(case=index):
                self.assert_rule(self.scan(b"ordinary text", name), "FORBIDDEN_FILE")

    def test_credential_filenames(self):
        names = ("." + "env", "credentials.json", "auth.json", "capi." + "local.token")
        for index, name in enumerate(names):
            with self.subTest(case=index):
                self.assert_rule(self.scan(b"{}", name), "FORBIDDEN_FILE")

    def test_log_and_session_filenames(self):
        names = ("copilot" + ".log", "session-" + "state.json")
        for index, name in enumerate(names):
            with self.subTest(case=index):
                self.assert_rule(self.scan(b"{}", name), "FORBIDDEN_FILE")

    def test_scan_name_without_payload(self):
        scanner = self.gate.Scanner(limits=self.gate.Limits())
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                scanner.scan_name("settings.bak")
        except Exception:
            raise self.failureException("Name scanner raised instead of returning a safe result") from None
        self.check_result_shape(scanner)
        self.assert_rule(scanner, "FORBIDDEN_FILE")

    def test_nested_names_both_separators(self):
        for separator in ("/", "\\"):
            with self.subTest(separator=separator):
                self.assert_rule(self.scan(b"{}", separator.join(("assets", "settings.bak"))), "FORBIDDEN_FILE")

    def test_clean_source_filenames(self):
        for name in ("Main.kt", "build.gradle.kts", "README.md", "backup_utils.py", "SessionManager.kt", "credentials_parser.py"):
            with self.subTest(name=name):
                self.assert_clean(self.scan(b"ordinary source text", name))

    def test_backup_words_in_contents_do_not_trigger_filename_rule(self):
        payload = b"Restore settings.bak or source.backup. Old backups are documented here."
        self.assert_clean(self.scan(payload, "guide.txt"))

    def test_filename_and_content_are_both_scanned(self):
        scanner = self.scan(classic_token().encode(), "settings.bak")
        self.assert_rule(scanner, "FORBIDDEN_FILE")
        self.assert_rule(scanner, "SECRET_TOKEN")


class ArchiveTests(GateCase):
    def test_clean_apk(self):
        payload = archive([("assets/readme.txt", b"ordinary asset"), ("res/raw/help.txt", b"Android help")])
        self.assert_clean(self.scan(payload, "fixture.apk"))

    def test_empty_zip_is_clean(self):
        self.assert_clean(self.scan(archive([]), "fixture.zip"))

    def test_stored_and_deflated_zip_members(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(compression=compression):
                payload = archive([("assets/value.txt", classic_token().encode())], compression)
                self.assert_rule(self.scan(payload, "fixture.apk"), "SECRET_TOKEN")

    def test_zip_magic_without_zip_extension(self):
        payload = archive([("asset.txt", classic_token().encode())])
        self.assert_rule(self.scan(payload, "fixture.bin"), "SECRET_TOKEN")

    def test_forbidden_archive_member_name(self):
        payload = archive([("assets/settings.bak", b"ordinary asset")])
        self.assert_rule(self.scan(payload, "fixture.apk"), "FORBIDDEN_FILE")

    def test_nested_archives(self):
        nested = archive([("asset.txt", classic_token().encode())])
        outer = archive([("assets/nested.zip", nested)])
        self.assert_rule(self.scan(outer, "fixture.apk"), "SECRET_TOKEN")

    def test_nested_forbidden_member(self):
        nested = archive([("settings.bak", b"ordinary asset")])
        self.assert_rule(self.scan(archive([("inner.zip", nested)]), "fixture.zip"), "FORBIDDEN_FILE")

    def test_multiple_member_findings(self):
        payload = archive([("first.txt", classic_token().encode()), ("second.txt", windows_home().encode())])
        scanner = self.scan(payload, "fixture.apk")
        self.assert_rule(scanner, "SECRET_TOKEN")
        self.assert_rule(scanner, "HOST_PATH")
        self.assert_redacted(scanner, ("first.txt", "second.txt", windows_home(), classic_token()))

    def test_member_references_distinguish_members(self):
        scanner = self.scan(archive([("first.txt", classic_token().encode()), ("second.txt", classic_token().encode())]), "fixture.zip")
        refs = {item.reference for item in scanner.findings if item.rule == "SECRET_TOKEN"}
        self.assertGreaterEqual(len(refs), 2, "Member identity must not collapse distinct members")

    def test_truncated_zip(self):
        payload = archive([("asset.txt", b"ordinary text")])
        self.assert_error(self.scan(payload[:-16], "fixture.zip"))

    def test_malformed_zip_header(self):
        self.assert_error(self.scan(b"PK\x03\x04\x00\x00", "fixture.zip"))

    def test_encrypted_member_fails_closed(self):
        self.assert_error(self.scan(encrypted_archive(), "fixture.apk"))

    def test_crc_corruption_fails_closed(self):
        self.assert_error(self.scan(bad_crc_archive(), "fixture.zip"))

    def test_bad_nested_archive_fails_closed(self):
        self.assert_error(self.scan(archive([("nested.zip", b"PK\x03\x04\x00")]), "fixture.apk"))

    def test_scanning_retains_findings_when_another_member_is_invalid(self):
        payload = archive([("value.txt", classic_token().encode()), ("broken.zip", b"PK\x03\x04\x00")])
        scanner = self.scan(payload, "fixture.apk")
        self.assert_rule(scanner, "SECRET_TOKEN", allow_errors=True)
        self.assert_error(scanner)


class BudgetTests(GateCase):
    def test_file_byte_limit_boundary(self):
        self.assert_clean(self.scan(b"x" * 8, max_file_bytes=8))
        self.assert_error(self.scan(b"x" * 9, max_file_bytes=8))

    def test_archive_entry_bytes(self):
        self.assert_clean(self.scan(archive([("asset.txt", b"x" * 32)]), "fixture.zip", max_entry_bytes=32))
        self.assert_error(self.scan(archive([("asset.txt", b"x" * 33)]), "fixture.zip", max_entry_bytes=32))

    def test_archive_member_count(self):
        self.assert_clean(self.scan(archive([("a.txt", b"x"), ("b.txt", b"y")]), "fixture.zip", max_entries=2))
        payload = archive([(str(index) + ".txt", b"x") for index in range(3)])
        self.assert_error(self.scan(payload, "fixture.zip", max_entries=2))

    def test_total_bytes_accumulate_across_files(self):
        scanner = self.scan(b"abcde", "first.txt", max_total_bytes=8)
        self.assert_clean(scanner)
        self.scan_into(scanner, b"fghij", "second.txt")
        self.check_result_shape(scanner)
        self.assert_error(scanner)

    def test_entries_accumulate_across_archives(self):
        scanner = self.scan(archive([("a.txt", b"x")]), "first.zip", max_entries=1)
        self.assert_clean(scanner)
        self.scan_into(scanner, archive([("b.txt", b"y")]), "second.zip")
        self.check_result_shape(scanner)
        self.assert_error(scanner)

    def test_total_uncompressed_byte_budget(self):
        payload = archive([("a.txt", b"x" * 800), ("b.txt", b"y" * 800)], zipfile.ZIP_DEFLATED)
        self.assert_error(self.scan(payload, "fixture.zip", max_total_bytes=1200))

    def test_nested_global_byte_budget(self):
        inner = archive([("a.txt", b"x" * 800)], zipfile.ZIP_DEFLATED)
        payload = archive([("one.zip", inner), ("two.zip", inner)], zipfile.ZIP_DEFLATED)
        self.assert_error(self.scan(payload, "fixture.zip", max_total_bytes=1200))

    def test_nested_global_entry_budget(self):
        inner = archive([("a.txt", b"one"), ("b.txt", b"two")])
        payload = archive([("one.zip", inner), ("two.zip", inner)])
        self.assert_error(self.scan(payload, "fixture.zip", max_entries=3))

    def test_depth_budget(self):
        payload = archive([("asset.txt", b"ordinary asset")])
        for _ in range(3):
            payload = archive([("nested.zip", payload)])
        self.assert_error(self.scan(payload, "fixture.zip", max_depth=1))

    def test_compression_ratio_budget(self):
        payload = archive([("asset.txt", b"x" * 8192)], zipfile.ZIP_DEFLATED)
        self.assert_error(self.scan(payload, "fixture.zip", max_ratio=2.0))

    def test_dex_string_count_budget(self):
        self.assert_error(self.scan(dex_file(["one", "two"]), "classes.dex", max_strings=1))

    def test_resource_string_count_budget(self):
        self.assert_error(self.scan(android_file(["one", "two"]), "resources.arsc", max_strings=1))


class DexTests(GateCase):
    def test_clean_minimal_dex(self):
        self.assert_clean(self.scan(dex_file(["ordinary text", "Android", "\U0001f642"]), "classes.dex"))

    def test_empty_dex_string_table(self):
        self.assert_clean(self.scan(dex_file([]), "classes.dex"))

    def test_dex_sensitive_strings(self):
        scanner = self.scan(dex_file(["ordinary", classic_token(), windows_home()]), "classes.dex")
        self.assert_rule(scanner, "SECRET_TOKEN")
        self.assert_rule(scanner, "HOST_PATH")

    def test_dex_mutf8_nul_and_supplementary_surrogates(self):
        text = "prefix\0\U0001f642 " + classic_token() + " \U0001f680"
        self.assert_rule(self.scan(dex_file([text]), "classes.dex"), "SECRET_TOKEN")

    def test_dex_multibyte_uleb128_length(self):
        text = "\u4e2d" * 140 + " " + classic_token()
        self.assert_rule(self.scan(dex_file([text]), "classes.dex"), "SECRET_TOKEN")

    def test_dex_in_apk(self):
        payload = archive([("classes.dex", dex_file([classic_token()]))])
        self.assert_rule(self.scan(payload, "fixture.apk"), "SECRET_TOKEN")

    def test_dex_string_ids_offset_out_of_bounds(self):
        data = bytearray(dex_file(["ordinary"]))
        struct.pack_into("<I", data, 60, len(data) + 64)
        self.assert_error(self.scan(seal_dex(data), "classes.dex"))

    def test_dex_string_data_offset_out_of_bounds(self):
        data = bytearray(dex_file(["ordinary"]))
        struct.pack_into("<I", data, 112, len(data) + 64)
        self.assert_error(self.scan(seal_dex(data), "classes.dex"))

    def test_dex_truncated_header(self):
        self.assert_error(self.scan(b"dex\n035\0" + b"\0" * 24, "classes.dex"))

    def test_dex_unterminated_string(self):
        data = bytearray(dex_file(["ordinary"]))
        offset = struct.unpack_from("<I", data, 112)[0]
        data[offset + 1 + len(mutf8("ordinary"))] = ord("x")
        self.assert_error(self.scan(seal_dex(data), "classes.dex"))

    def test_dex_mismatched_utf16_length(self):
        data = bytearray(dex_file(["ordinary"]))
        offset = struct.unpack_from("<I", data, 112)[0]
        data[offset] = 100
        self.assert_error(self.scan(seal_dex(data), "classes.dex"))

    def test_dex_truncated_uleb128(self):
        data = bytearray(dex_file(["ordinary"]))
        offset = struct.unpack_from("<I", data, 112)[0]
        data[offset:] = b"\x80" * 5
        self.assert_error(self.scan(seal_dex(data), "classes.dex"))

    def test_dex_invalid_header_size(self):
        data = bytearray(dex_file(["ordinary"]))
        struct.pack_into("<I", data, 36, 16)
        self.assert_error(self.scan(seal_dex(data), "classes.dex"))

    def test_dex_declared_file_size_exceeds_payload(self):
        data = bytearray(dex_file(["ordinary"]))
        struct.pack_into("<I", data, 32, len(data) + 128)
        self.assert_error(self.scan(bytes(data), "classes.dex"))


class AndroidStringPoolTests(GateCase):
    def test_clean_resource_and_xml_pools(self):
        for xml in (False, True):
            for utf8 in (False, True):
                with self.subTest(xml=xml, utf8=utf8):
                    self.assert_clean(self.scan(android_file(["Android", "\u4e2d\u6587", "\U0001f642"], utf8, xml), "asset.bin"))

    def test_empty_string_pool(self):
        self.assert_clean(self.scan(android_file([]), "resources.arsc"))

    def test_sensitive_resource_and_xml_pools(self):
        for xml in (False, True):
            for utf8 in (False, True):
                with self.subTest(xml=xml, utf8=utf8):
                    name = "AndroidManifest.xml" if xml else "resources.arsc"
                    scanner = self.scan(android_file([classic_token(), windows_home()], utf8, xml), name)
                    self.assert_rule(scanner, "SECRET_TOKEN")
                    self.assert_rule(scanner, "HOST_PATH")

    def test_supplementary_surrogate_lengths(self):
        for utf8 in (False, True):
            with self.subTest(utf8=utf8):
                text = "\U0001f642" * 70 + " " + classic_token()
                self.assert_rule(self.scan(android_file([text], utf8), "resources.arsc"), "SECRET_TOKEN")

    def test_utf8_two_byte_length_fields(self):
        text = "\u4e2d" * 140 + " " + classic_token()
        self.assert_rule(self.scan(android_file([text]), "resources.arsc"), "SECRET_TOKEN")

    def test_utf16_extended_length_field(self):
        text = "x" * 32768 + " " + classic_token()
        self.assert_rule(self.scan(android_file([text], False), "resources.arsc"), "SECRET_TOKEN")

    def test_android_pools_inside_apk(self):
        payload = archive([
            ("resources.arsc", android_file([classic_token()])),
            ("AndroidManifest.xml", android_file([windows_home()], False, True)),
        ])
        scanner = self.scan(payload, "fixture.apk")
        self.assert_rule(scanner, "SECRET_TOKEN")
        self.assert_rule(scanner, "HOST_PATH")

    def test_pool_offset_out_of_bounds(self):
        data = bytearray(android_file(["ordinary"]))
        struct.pack_into("<I", data, 12 + 28, len(data) + 64)
        self.assert_error(self.scan(bytes(data), "resources.arsc"))

    def test_pool_strings_start_out_of_bounds(self):
        data = bytearray(android_file(["ordinary"]))
        struct.pack_into("<I", data, 12 + 20, len(data) + 64)
        self.assert_error(self.scan(bytes(data), "resources.arsc"))

    def test_pool_chunk_size_exceeds_parent(self):
        data = bytearray(android_file(["ordinary"]))
        struct.pack_into("<I", data, 12 + 4, len(data) + 64)
        self.assert_error(self.scan(bytes(data), "resources.arsc"))

    def test_zero_sized_chunk_fails_without_looping(self):
        data = bytearray(android_file(["ordinary"]))
        struct.pack_into("<I", data, 12 + 4, 0)
        self.assert_error(self.scan(bytes(data), "resources.arsc"))

    def test_truncated_root_chunk(self):
        for xml in (False, True):
            with self.subTest(xml=xml):
                self.assert_error(self.scan(android_file(["ordinary"], xml=xml)[:-3], "asset.bin"))

    def test_utf8_length_out_of_bounds(self):
        data = bytearray(android_file(["ordinary"]))
        start = 12 + struct.unpack_from("<I", data, 12 + 20)[0]
        data[start + 1] = 0x7F
        self.assert_error(self.scan(bytes(data), "resources.arsc"))

    def test_utf8_utf16_unit_length_mismatch(self):
        data = bytearray(android_file(["ordinary"]))
        start = 12 + struct.unpack_from("<I", data, 12 + 20)[0]
        data[start] = 1
        self.assert_error(self.scan(bytes(data), "resources.arsc"))

    def test_utf16_length_out_of_bounds(self):
        data = bytearray(android_file(["ordinary"], False))
        start = 12 + struct.unpack_from("<I", data, 12 + 20)[0]
        struct.pack_into("<H", data, start, 0x7FFF)
        self.assert_error(self.scan(bytes(data), "resources.arsc"))

    def test_missing_pool_string_terminators(self):
        for utf8 in (False, True):
            with self.subTest(utf8=utf8):
                data = bytearray(android_file(["ordinary"], utf8))
                start = 12 + struct.unpack_from("<I", data, 12 + 20)[0]
                terminator = start + 2 + (8 if utf8 else 16)
                data[terminator] = ord("x")
                self.assert_error(self.scan(bytes(data), "resources.arsc"))


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_gate()
        if shutil.which("git") is None:
            raise unittest.SkipTest("Git is not available")

    def run_process(self, command, cwd):
        try:
            return subprocess.run(
                command, cwd=cwd, env=subprocess_environment(), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                timeout=60, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise self.failureException("Isolated subprocess failed or timed out") from None

    def git(self, repo, *arguments):
        command = [
            "git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
            "-c", "init.templateDir=",
            "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgSign=false",
            "-c", "core.autocrlf=false", "-c", "core.symlinks=true",
            *arguments,
        ]
        result = self.run_process(command, repo)
        self.assertTrue(result.returncode == 0, "Isolated Git fixture operation failed")
        return result.stdout.strip()

    @contextlib.contextmanager
    def repo(self):
        with scratch_directory() as root:
            self.git(root, "init", "--quiet", "--initial-branch=main")
            self.write(root, "source.txt", b"ordinary source\n")
            self.git(root, "add", "--", "source.txt")
            self.git(root, "commit", "--quiet", "-m", "Initial fixture")
            yield root

    def write(self, repo, name, payload):
        try:
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        except OSError:
            raise self.failureException("Cannot write isolated fixture") from None

    def cli(self, repo, *arguments, default_repo=False):
        command = [sys.executable, str(IMPLEMENTATION)]
        if not default_repo:
            command.extend(("--repo", str(repo)))
        command.extend(arguments)
        result = self.run_process(command, repo)
        output = result.stdout + result.stderr
        forbidden = (
            str(repo), repo.as_posix(), str(IMPLEMENTATION), IMPLEMENTATION.as_posix(),
            classic_token(), fine_token(), windows_home(), "FixturePerson",
            unix_home(), credential_url(), copilot_location(),
            checkout_path("work" + "spaces"), checkout_path("jf" + "px"),
            "NotReal928Secret", "NotReal928",
            json.loads(identifier_assignment("session_id"))["session_id"],
            "source.txt", "staged.txt", "untracked.txt", "settings.bak",
            "linked.txt", "untracked.bak", ".gitignore", "refs/private/fixture",
            "refs/original/refs/heads/main", "unique-cli-sentinel-8392",
            "missing-public-fixture-ref", "Traceback (most recent call last)",
        )
        for value in forbidden:
            self.assertTrue(value not in output, "CLI output exposed fixture data or diagnostics")
        self.assertTrue(not re.search(r"\b(?:[A-Za-z]:[\\/]|/(?:home|Users)/)", output), "CLI exposed an absolute path")
        return result.returncode, output

    def assert_exit(self, result, code, rule=None):
        actual, output = result
        self.assertEqual(actual, code, "Unexpected privacy gate exit status")
        report = re.sub(r"file@[0-9a-fA-F]+", "", output)
        self.assertTrue(re.search(r"\b\d+\b", report) is not None, "CLI omitted numeric counts")
        if rule:
            self.assertTrue(rule in output, "CLI omitted expected rule ID")
            self.assertTrue(re.search(r"file@[0-9a-fA-F]+", output) is not None, "CLI omitted opaque reference")
        if code == 2:
            self.assertTrue("ERROR_" in output, "Scan errors need error rule IDs")

    def test_clean_current_index(self):
        with self.repo() as repo:
            self.assert_exit(self.cli(repo), 0)

    def test_default_repo_is_cwd(self):
        with self.repo() as repo:
            self.assert_exit(self.cli(repo, default_repo=True), 0)

    def test_cli_redacts_content_rule_categories(self):
        cases = (
            (fine_token(), "SECRET_TOKEN"),
            (credential_url(), "CREDENTIAL_URL"),
            (assignment(), "CREDENTIAL_ASSIGNMENT"),
            (windows_home(), "HOST_PATH"),
            (copilot_location(), "COPILOT_STATE"),
            (identifier_assignment("device_id"), "SESSION_DEVICE_ID"),
            ("".join(("-----", "BEGIN ", "PRIVATE KEY", "-----")), "PRIVATE_KEY"),
        )
        with self.repo() as repo:
            for index, (payload, rule) in enumerate(cases):
                with self.subTest(case=index):
                    text = "unique-cli-sentinel-8392\nordinary second line\n" + payload + "\n"
                    self.write(repo, "source.txt", text.encode())
                    result = self.cli(repo)
                    self.assert_exit(result, 1, rule)
                    report = re.sub(r"file@[0-9a-fA-F]+", "", result[1])
                    self.assertTrue(re.search(r"\b3\b", report) is not None, "CLI omitted source line number")

    def test_untracked_sensitive_file_is_excluded(self):
        with self.repo() as repo:
            self.write(repo, "untracked.txt", classic_token().encode())
            self.write(repo, "untracked.bak", b"ordinary backup")
            self.assert_exit(self.cli(repo), 0)

    def test_staged_new_file_is_included(self):
        with self.repo() as repo:
            self.write(repo, "staged.txt", classic_token().encode())
            self.git(repo, "add", "--", "staged.txt")
            self.assert_exit(self.cli(repo), 1, "SECRET_TOKEN")

    def test_tracked_ignored_backup_is_included(self):
        with self.repo() as repo:
            self.write(repo, ".gitignore", b"*.bak\n")
            self.write(repo, "settings.bak", b"ordinary backup")
            self.git(repo, "add", "--", ".gitignore")
            self.git(repo, "add", "-f", "--", "settings.bak")
            self.assert_exit(self.cli(repo), 1, "FORBIDDEN_FILE")

    def test_working_tree_modification_not_index_blob(self):
        with self.repo() as repo:
            self.write(repo, "source.txt", classic_token().encode())
            self.assert_exit(self.cli(repo), 1, "SECRET_TOKEN")
            self.assert_exit(self.cli(repo, "--revision", "HEAD"), 0)

    def test_staged_new_uses_current_working_tree_contents(self):
        with self.repo() as repo:
            self.write(repo, "staged.txt", classic_token().encode())
            self.git(repo, "add", "--", "staged.txt")
            self.write(repo, "staged.txt", b"ordinary replacement")
            self.assert_exit(self.cli(repo), 0)

    def test_revision_reads_immutable_blob(self):
        with self.repo() as repo:
            self.write(repo, "source.txt", classic_token().encode())
            self.git(repo, "add", "--", "source.txt")
            self.git(repo, "commit", "--quiet", "-m", "Sensitive fixture")
            self.write(repo, "source.txt", b"ordinary working tree")
            self.assert_exit(self.cli(repo), 0)
            self.assert_exit(self.cli(repo, "--revision", "HEAD"), 1, "SECRET_TOKEN")

    def test_revision_includes_forbidden_filename(self):
        with self.repo() as repo:
            self.write(repo, "settings.bak", b"ordinary backup")
            self.git(repo, "add", "-f", "--", "settings.bak")
            self.git(repo, "commit", "--quiet", "-m", "Backup fixture")
            self.assert_exit(self.cli(repo, "--revision", "HEAD"), 1, "FORBIDDEN_FILE")

    def test_revision_excludes_staged_new_files(self):
        with self.repo() as repo:
            self.write(repo, "staged.txt", classic_token().encode())
            self.git(repo, "add", "--", "staged.txt")
            self.assert_exit(self.cli(repo, "--revision", "HEAD"), 0)

    def test_history_finds_removed_ancestor_content(self):
        with self.repo() as repo:
            self.write(repo, "source.txt", classic_token().encode())
            self.git(repo, "add", "--", "source.txt")
            self.git(repo, "commit", "--quiet", "-m", "Sensitive ancestor")
            self.write(repo, "source.txt", b"ordinary replacement")
            self.git(repo, "add", "--", "source.txt")
            self.git(repo, "commit", "--quiet", "-m", "Clean tip")
            self.assert_exit(self.cli(repo, "--revision", "refs/heads/main"), 0)
            self.assert_exit(self.cli(repo, "--history", "refs/heads/main"), 1, "SECRET_TOKEN")

    def test_history_tag_ref(self):
        with self.repo() as repo:
            self.git(repo, "tag", "public-fixture")
            self.assert_exit(self.cli(repo, "--history", "refs/tags/public-fixture"), 0)

    def test_history_annotated_tag_ref(self):
        with self.repo() as repo:
            self.git(repo, "tag", "-a", "public-fixture", "-m", "Public fixture")
            self.assert_exit(self.cli(repo, "--history", "refs/tags/public-fixture"), 0)

    def test_public_history_excludes_private_and_original_refs(self):
        with self.repo() as repo:
            base = self.git(repo, "rev-parse", "HEAD")
            self.write(repo, "source.txt", classic_token().encode())
            self.git(repo, "add", "--", "source.txt")
            self.git(repo, "commit", "--quiet", "-m", "Private fixture")
            private_commit = self.git(repo, "rev-parse", "HEAD")
            self.git(repo, "update-ref", "refs/original/refs/heads/main", private_commit)
            self.git(repo, "update-ref", "refs/private/fixture", private_commit)
            self.git(repo, "reset", "--hard", "--quiet", base)
            self.assert_exit(self.cli(repo, "--history", "refs/heads/main"), 0)
            self.assert_exit(self.cli(repo, "--history", "refs/private/fixture"), 2)
            self.assert_exit(self.cli(repo, "--history", "refs/original/refs/heads/main"), 2)

    def test_public_history_excludes_unrelated_head(self):
        with self.repo() as repo:
            self.git(repo, "checkout", "--quiet", "-b", "other")
            self.write(repo, "source.txt", classic_token().encode())
            self.git(repo, "add", "--", "source.txt")
            self.git(repo, "commit", "--quiet", "-m", "Other head fixture")
            self.git(repo, "checkout", "--quiet", "main")
            self.assert_exit(self.cli(repo, "--history", "refs/heads/main"), 0)
            self.assert_exit(self.cli(repo, "--history", "refs/heads/other"), 1, "SECRET_TOKEN")

    def test_history_rejects_revision_expressions_and_all(self):
        with self.repo() as repo:
            commit = self.git(repo, "rev-parse", "HEAD")
            for index, ref in enumerate(("HEAD", commit, "refs/heads/main~0", "--all")):
                with self.subTest(case=index):
                    self.assert_exit(self.cli(repo, "--history=" + ref), 2)

    def test_invalid_revision_fails_closed(self):
        with self.repo() as repo:
            self.assert_exit(self.cli(repo, "--revision", "missing-public-fixture-ref"), 2)

    def test_invalid_history_ref_fails_closed(self):
        with self.repo() as repo:
            self.assert_exit(self.cli(repo, "--history", "refs/heads/missing-public-fixture-ref"), 2)

    def test_missing_tracked_working_tree_file_fails_closed(self):
        with self.repo() as repo:
            (repo / "source.txt").unlink()
            self.assert_exit(self.cli(repo), 2)
            self.assert_exit(self.cli(repo, "--revision", "HEAD"), 0)

    def test_staged_deleted_file_is_excluded(self):
        with self.repo() as repo:
            self.git(repo, "rm", "--quiet", "--", "source.txt")
            self.assert_exit(self.cli(repo), 0)

    def test_non_repository_fails_closed(self):
        with scratch_directory() as repo:
            self.assert_exit(self.cli(repo), 2)

    def test_findings_and_scan_errors_have_error_exit_precedence(self):
        with self.repo() as repo:
            self.write(repo, "staged.txt", classic_token().encode())
            self.git(repo, "add", "--", "staged.txt")
            (repo / "source.txt").unlink()
            self.assert_exit(self.cli(repo), 2)

    def test_tracked_symlink_fails_closed(self):
        with self.repo() as repo:
            self.write(repo, "untracked.txt", classic_token().encode())
            try:
                (repo / "linked.txt").symlink_to("untracked.txt")
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable")
            self.git(repo, "add", "--", "linked.txt")
            index_entry = self.git(repo, "ls-files", "--stage", "--", "linked.txt")
            if not index_entry.startswith("120000 "):
                self.skipTest("Git cannot preserve symlinks on this platform")
            self.assert_exit(self.cli(repo), 2)

    def test_revision_symlink_fails_closed(self):
        with self.repo() as repo:
            try:
                (repo / "linked.txt").symlink_to("source.txt")
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable")
            self.git(repo, "add", "--", "linked.txt")
            index_entry = self.git(repo, "ls-files", "--stage", "--", "linked.txt")
            if not index_entry.startswith("120000 "):
                self.skipTest("Git cannot preserve symlinks on this platform")
            self.git(repo, "commit", "--quiet", "-m", "Link fixture")
            self.assert_exit(self.cli(repo, "--revision", "HEAD"), 2)


if __name__ == "__main__":
    unittest.main()
