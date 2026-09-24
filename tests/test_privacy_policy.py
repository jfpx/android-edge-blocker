"""Additional publication-policy regressions, using only synthetic fixtures."""

import json
import os
import struct
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from test_privacy_gate import (
    GateCase, IMPLEMENTATION, PROJECT, android_file, archive, assignment,
    classic_token, dex_file, load_gate, seal_dex, windows_home,
)


class PublicationPolicyTests(GateCase):
    def test_license_contact_allowlist_is_exact_and_file_scoped(self):
        contact = "licensing" + "@fsf.org"
        for name in ("LICENSE", "COPYING", "META-INF/LICENSE.txt", "NOTICE"):
            with self.subTest(name=name):
                self.assert_clean(self.scan(contact.encode(), name))
        for name in ("README.md", "not-a-license.txt", "license_parser.py"):
            with self.subTest(name=name):
                self.assert_rule(self.scan(contact.encode(), name), "PERSONAL_EMAIL")
        other = "private-fixture" + "@fixture.invalid"
        self.assert_rule(self.scan(other.encode(), "LICENSE"), "PERSONAL_EMAIL")

    def test_public_noreply_credit_is_retained(self):
        contact = "223556219+Copilot" + "@users.noreply.github.com"
        self.assert_clean(self.scan(("Co-authored-by: Copilot <" + contact + ">").encode()))

    def test_private_report_and_signing_files_are_blocked(self):
        for name in ("audit.json", "audit.txt", "privacy-audit-fixture.json",
                     "private-report.txt", "source-review.json", "apk-review.txt",
                     "history-candidates.json", "commit-identities-masked.json",
                     "local-baseline.json", "secrets.json", "release.keystore",
                     "release.jks", "local.properties"):
            with self.subTest(name=name):
                self.assert_rule(self.scan(b"{}", name), "FORBIDDEN_FILE")

    def test_nested_private_report_is_blocked(self):
        data = archive([("assets/audit.json", b"{}")])
        self.assert_rule(self.scan(data, "fixture.apk"), "FORBIDDEN_FILE")

    def test_exact_forbidden_names_at_archive_root(self):
        for name in ("credentials.json", "audit.json", "." + "env", "auth.json"):
            with self.subTest(name=name):
                self.assert_rule(self.scan(archive([(name, b"{}")]), "fixture.zip"), "FORBIDDEN_FILE")

    def test_no_blanket_documentation_or_test_exemption(self):
        for name in ("README.md", "BUILD_GUIDE.md", "tests/test_fixture.py"):
            with self.subTest(name=name):
                self.assert_rule(self.scan(windows_home().encode(), name), "HOST_PATH")
                self.assert_rule(self.scan(classic_token().encode(), name), "SECRET_TOKEN")

    def test_only_placeholder_user_segment_is_exempt(self):
        data = windows_home().replace("FixturePerson", "username-real")
        self.assert_rule(self.scan(data.encode(), "example.txt"), "HOST_PATH")

    def test_header_path_fragments_are_not_home_paths(self):
        data = b"\xff\x01" + "\\".join(("D:", "a")).encode() + b"\x00\xfe"
        self.assert_clean(self.scan(data, "binary.bin"))

    def test_compressed_decoded_data_not_compressed_bytes(self):
        data = archive([("assets/docs.txt", windows_home().encode())], zipfile.ZIP_DEFLATED)
        self.assert_rule(self.scan(data, "fixture.apk"), "HOST_PATH")
        self.assert_clean(self.scan(archive([("classes.dex", dex_file(["Android documentation"]))],
                                            zipfile.ZIP_DEFLATED), "fixture.apk"))

    def test_zip_comment_is_publication_content(self):
        import io
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as bundle:
            bundle.comment = classic_token().encode()
        self.assert_rule(self.scan(data.getvalue(), "fixture.zip"), "SECRET_TOKEN")

    def test_short_home_path_still_detected(self):
        data = windows_home().split("Documents")[0].rstrip("\\")
        self.assert_rule(self.scan(data.encode()), "HOST_PATH")

    def test_credential_function_calls_are_not_values(self):
        self.assert_clean(self.scan(b"token = load_access_token()\nTOKEN = re.compile(pattern)\n"))
        payload = "token = " + "NotReal928Secret"
        self.assert_rule(self.scan(payload.encode()), "CREDENTIAL_ASSIGNMENT")

    def test_escaped_device_assignment(self):
        key = "device" + "_id"
        payload = json.dumps({key: "fixture-123456789"})
        self.assert_rule(self.scan(payload.encode()), "SESSION_DEVICE_ID")

    def test_incomplete_or_percent_prefixed_values_are_not_placeholders(self):
        for value in ("%NotReal928Secret", "${NotReal928Secret", "$env:NOT_REAL extra",
                      "YOUR_NotReal928Secret", "CHANGE_ME_NotReal928Secret"):
            with self.subTest(case=len(value)):
                self.assert_rule(self.scan(assignment(value=value).encode()), "CREDENTIAL_ASSIGNMENT")
        for value in ("%ACCESS_TOKEN%", "${ACCESS_TOKEN}", "$env:ACCESS_TOKEN", "REPLACE_TOKEN"):
            self.assert_clean(self.scan(assignment(value=value).encode()))

    def test_repeated_dex_offsets_scanned_once(self):
        data = bytearray(dex_file(["alpha", "beta"]))
        struct.pack_into("<I", data, 116, struct.unpack_from("<I", data, 112)[0])
        scanner = self.scan(seal_dex(data), "classes.dex")
        self.assert_clean(scanner)
        self.assertEqual(scanner.decoded_bytes, 5)

    def test_repeated_resource_offsets_scanned_once(self):
        data = bytearray(android_file(["alpha", "beta"]))
        struct.pack_into("<I", data, 12 + 28 + 4, 0)
        scanner = self.scan(bytes(data), "resources.arsc")
        self.assert_clean(scanner)
        self.assertEqual(scanner.decoded_bytes, 5)

    def test_decoded_string_byte_budget_accumulates(self):
        for name, payload in (("classes.dex", dex_file(["alpha"])),
                              ("resources.arsc", android_file(["alpha"]))):
            with self.subTest(name=name):
                scanner = self.gate.Scanner(limits=self.gate.Limits(max_total_bytes=4))
                method = scanner.scan_dex if name.endswith(".dex") else scanner.scan_android
                with self.assertRaises(self.gate.InvalidData):
                    method(payload, name)


class ArchiveDirectoryValidationTests(GateCase):
    def setUp(self):
        self.bundle = MagicMock()
        self.bundle.comment = b""
        factory = patch.object(self.gate.zipfile, "ZipFile")
        self.addCleanup(factory.stop)
        factory.start().return_value.__enter__.return_value = self.bundle

    def member(self, name, size):
        item = zipfile.ZipInfo(name)
        item.file_size = size
        item.compress_size = size
        return item

    def test_nonzero_directory_size_rejected_before_open(self):
        for size in (-1, 1, 4):
            with self.subTest(size=size):
                self.bundle.infolist.return_value = [self.member("docs/", size)]
                scanner = self.scan(b"fixture", "fixture.zip")
                self.assertEqual({item.rule for item in scanner.errors}, {"ERROR_ARCHIVE_MEMBER"})
                self.bundle.open.assert_not_called()
                self.assertEqual(scanner.total_bytes, len(b"fixture"))

    def test_zero_declared_directory_requires_empty_decoded_content(self):
        self.bundle.infolist.return_value = [self.member("docs/", 0)]
        stream = self.bundle.open.return_value.__enter__.return_value
        stream.read.return_value = b"benign"
        scanner = self.scan(b"fixture", "fixture.zip", max_entry_bytes=8)
        self.assertEqual({item.rule for item in scanner.errors}, {"ERROR_ARCHIVE_MEMBER"})
        stream.read.assert_called_once_with(9)

    def test_empty_directory_is_valid_and_counted(self):
        self.bundle.infolist.return_value = [self.member("docs/", 0)]
        stream = self.bundle.open.return_value.__enter__.return_value
        stream.read.return_value = b""
        scanner = self.scan(b"fixture", "fixture.zip", max_entry_bytes=8)
        self.assert_clean(scanner)
        self.assertEqual(scanner.entries, 1)
        self.assertEqual(scanner.total_bytes, len(b"fixture"))
        stream.read.assert_called_once_with(9)

    def test_normal_member_still_scanned_after_rejected_directory(self):
        directory = self.member("docs/", 1)
        regular = self.member("notes.txt", len(b"readme"))
        self.bundle.infolist.return_value = [directory, regular]
        stream = self.bundle.open.return_value.__enter__.return_value
        stream.read.return_value = b"readme"
        with patch.object(self.gate.Scanner, "scan_plain") as scan_plain:
            scanner = self.scan(b"fixture", "fixture.zip", max_entry_bytes=6)
        self.assertEqual({item.rule for item in scanner.errors}, {"ERROR_ARCHIVE_MEMBER"})
        self.bundle.open.assert_called_once_with(regular)
        stream.read.assert_called_once_with(7)
        scan_plain.assert_any_call(b"readme", "fixture.zip!notes.txt")
        self.assertEqual(scanner.entries, 2)
        self.assertEqual(scanner.total_bytes, len(b"fixture") + len(b"readme"))

    def test_normal_members_keep_aggregate_budget_and_bounded_reads(self):
        items = [self.member(name, 2) for name in ("a.txt", "b.txt", "c.txt")]
        self.bundle.infolist.return_value = items
        stream = self.bundle.open.return_value.__enter__.return_value
        stream.read.return_value = b"ok"
        scanner = self.scan(b"fixture", "fixture.zip", max_entry_bytes=2, max_total_bytes=11)
        self.assertEqual({item.rule for item in scanner.errors}, {"ERROR_BUDGET"})
        self.assertEqual(scanner.total_bytes, 11)
        self.assertEqual(scanner.entries, 3)
        self.assertEqual([call.args for call in self.bundle.open.call_args_list],
                         [(items[0],), (items[1],)])
        self.assertEqual([call.args for call in stream.read.call_args_list], [(3,), (3,)])


class ScopeArgumentTests(unittest.TestCase):
    def test_empty_selectors_fail_closed_without_printing_arguments(self):
        for selector in ("--repo", "--revision", "--history", "--apk"):
            with self.subTest(selector=selector):
                result = subprocess.run([sys.executable, "-B", str(IMPLEMENTATION), selector, ""],
                                        cwd=PROJECT, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 2)
                self.assertIn(b"ERROR_ARGUMENT", result.stdout)
                self.assertNotIn(str(PROJECT).encode(), result.stdout + result.stderr)

    def test_git_reads_disable_network_fetch_and_replacement_refs(self):
        gate = load_gate()
        with patch.object(gate.subprocess, "Popen") as popen:
            process = popen.return_value.__enter__.return_value
            process.stdout.read.return_value = b"fixture"
            process.wait.return_value = 0
            self.assertEqual(gate.git(PROJECT, "rev-parse", "HEAD"), b"fixture")
            env = popen.call_args.kwargs["env"]
            for name in ("GIT_NO_LAZY_FETCH", "GIT_NO_REPLACE_OBJECTS"):
                self.assertEqual(env[name], "1")
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
