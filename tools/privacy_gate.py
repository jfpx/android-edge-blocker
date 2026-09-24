"""Offline, bounded publication checks. Findings never include matched values."""

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import zipfile
import zlib


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 64 * 1024 * 1024
    max_entry_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_entries: int = 10000
    max_depth: int = 3
    max_ratio: float = 200.0
    max_strings: int = 2000000


@dataclass(frozen=True)
class Finding:
    rule: str
    reference: str
    line: int | None = None


class InvalidData(Exception):
    pass


def require(condition):
    if not condition:
        raise InvalidData()


def reference(name):
    return "file@" + hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()[:16]


PLACEHOLDERS = {"yourname", "username", "user", "...", "…"}
TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|"
                   r"AKIA[A-Z0-9]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{32,})\b")
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")
CREDENTIAL_URL = re.compile(r"https?://[^\s/<>\"']+@[^\s/<>\"']+", re.I)
HOST_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]+(?:Users[\\/]+(?P<win><[^>\r\n]+>|[^\\/\s`\"']+))"
    r"|/(?:home|Users)/(?P<unix><[^>\r\n]+>|[^/\s`\"']+)"
    r"|[A-Za-z]:[\\/]+(?:workspaces?|jfpx)[\\/]+[^\s`\"']+)", re.I)
COPILOT = re.compile(r"\.copilot[\\/]+(?:session(?:-state)?|logs?|history|memory)(?:[\\/.\s]|$)", re.I)
ASSIGNMENT = re.compile(
    r"""(?i)(?<![\w])["']?(password|passwd|api[_-]?key|access[_-]?token|secret|token|"""
    r"""session[_-]?id|device[_-]?id|android[_-]?id|serial[_-]?number)["']?\s*[:=]\s*"""
    r"""(?:"([^"\r\n]*)"|'([^'\r\n]*)'|([A-Za-z0-9_./+@=-]{8,}))""")
EMAIL = re.compile(r"\b[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Exact upstream license contacts, and only in license/notice/copying files.
LICENSE_EMAILS = {"gnu" + "@gnu.org", "licensing" + "@fsf.org", "info" + "@fsf.org"}
FORBIDDEN = re.compile(
    r"(?i)(?:\.bak|\.backup|\.jks|\.keystore|~)$|^(?:\.env(?:\..+)?|credentials(?:\..+)?|secrets?\.(?:json|ya?ml)|auth\.json|"
    r"capi\.local\.token|copilot\.log|session-state(?:\..+)?|audit\.(?:json|txt)|"
    r"privacy[-_]audit.*|.*private[-_]report.*|(?:source-review|apk-review|history-candidates|"
    r"commit-identities-masked|local-baseline)\.(?:json|txt)|id_rsa|id_ed25519|local\.properties)$")


def placeholder(value):
    return (not value or value.casefold() in PLACEHOLDERS or
            re.fullmatch(r"<[^<>/\\\r\n]+>|\$\{[A-Za-z_][A-Za-z0-9_]*\}|"
                         r"\$env:[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%|"
                         r"(?:YOUR|REPLACE)_[A-Z0-9_]+|CHANGE_ME", value) is not None)


class Scanner:
    def __init__(self, limits=Limits()):
        self.limits = limits
        self.findings = set()
        self.errors = set()
        self.total_bytes = 0
        self.entries = 0
        self.strings = 0
        self.decoded_bytes = 0

    @property
    def clean(self):
        return not self.findings and not self.errors

    def add(self, rule, name, line=None):
        item = Finding(rule, reference(name), line)
        (self.errors if rule.startswith("ERROR_") else self.findings).add(item)

    def scan_name(self, name):
        parts = re.split(r"[\\/!]", name)
        if any(FORBIDDEN.search(p) or p.casefold() in {".copilot", "session-state"} for p in parts):
            self.add("FORBIDDEN_FILE", name)
        self.scan_text(name, name, numbered=False)

    def scan_text(self, text, name, numbered=True):
        # Decode JSON escapes without interpreting arbitrary Python escapes.
        variants = [text]
        for _ in range(3):
            previous = variants[-1]
            decoded = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), previous)
            decoded = decoded.replace("\\\\", "\\").replace("\\/", "/")
            if decoded == previous:
                break
            variants.append(decoded)
        for value in variants:
            for number, line in enumerate(value.splitlines(), 1):
                loc = number if numbered else None
                for rule, pattern in (("SECRET_TOKEN", TOKEN), ("PRIVATE_KEY", PRIVATE_KEY),
                                      ("CREDENTIAL_URL", CREDENTIAL_URL), ("COPILOT_STATE", COPILOT)):
                    if pattern.search(line):
                        self.add(rule, name, loc)
                for match in HOST_PATH.finditer(line):
                    user = match["win"] or match["unix"]
                    if user is None or not placeholder(user):
                        self.add("HOST_PATH", name, loc)
                for match in ASSIGNMENT.finditer(line):
                    key = match[1].lower().replace("-", "_")
                    content = next((v for v in match.groups()[1:] if v is not None), "")
                    if match[4] is not None and line[match.end():].lstrip().startswith("("):
                        continue
                    if placeholder(content):
                        continue
                    if key in {"session_id", "device_id", "android_id", "serial_number"}:
                        if len(content) >= 8:
                            self.add("SESSION_DEVICE_ID", name, loc)
                    elif len(content) >= 8:
                        self.add("CREDENTIAL_ASSIGNMENT", name, loc)
                for match in EMAIL.finditer(line):
                    email = match[0].lower()
                    domain = email.split("@")[1]
                    license_file = bool(re.search(r"(?:^|[\\/!])(?:license|notice|copying)(?:[.\-/\\!]|$)", name, re.I))
                    if (domain in {"example.test", "example.com", "example.org", "users.noreply.github.com"}
                            or (license_file and email in LICENSE_EMAILS)):
                        continue
                    self.add("PERSONAL_EMAIL", name, loc)

    def count_strings(self, count):
        self.strings += count
        require(self.strings <= self.limits.max_strings)

    def count_decoded_bytes(self, count):
        self.decoded_bytes += count
        require(self.decoded_bytes <= self.limits.max_total_bytes)

    def scan_bytes(self, data, name="input", _depth=0):
        self.scan_name(name)
        self.total_bytes += len(data)
        if (len(data) > self.limits.max_file_bytes or
                self.total_bytes > self.limits.max_total_bytes or _depth > self.limits.max_depth):
            self.add("ERROR_BUDGET", name)
            return
        try:
            suffix = Path(name).suffix.lower()
            if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")) or suffix in {".apk", ".zip", ".jar"}:
                self.scan_zip(data, name, _depth)
            elif data.startswith(b"dex\n") or suffix == ".dex":
                self.scan_dex(data, name)
            elif data[:4] in (b"\x02\x00\x0c\x00", b"\x03\x00\x08\x00", b"\x01\x00\x1c\x00") or suffix == ".arsc":
                self.scan_android(data, name)
            else:
                self.scan_plain(data, name)
        except (InvalidData, ValueError, IndexError, struct.error, UnicodeError,
                zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError, zlib.error):
            self.add("ERROR_DECODE", name)

    def scan_plain(self, data, name):
        if not data:
            return
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            self.scan_text(data.decode("utf-16"), name)
            return
        if len(data) % 2 == 0 and b"\0" in data:
            even, odd = data[::2], data[1::2]
            if odd.count(0) > len(odd) // 3 or even.count(0) > len(even) // 3:
                codec = "utf-16-le" if odd.count(0) >= even.count(0) else "utf-16-be"
                try:
                    self.scan_text(data.decode(codec), name)
                except UnicodeError:
                    pass
        try:
            text = data.decode("utf-8-sig")
            if "\0" not in text:
                self.scan_text(text, name)
                return
        except UnicodeError:
            pass
        # Unknown binaries get printable runs only, never compressed ZIP bytes.
        for match in re.finditer(rb"[\x20-\x7e]{6,}", data):
            self.scan_text(match[0].decode("ascii"), name, numbered=False)

    def scan_zip(self, data, name, depth):
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            self.scan_plain(bundle.comment, name + "!comment")
            for item in bundle.infolist():
                member = name + "!" + item.filename
                self.scan_name(member)
                self.scan_plain(item.comment, member + "!comment")
                self.entries += 1
                if (self.entries > self.limits.max_entries or
                        item.file_size > self.limits.max_entry_bytes or
                        item.file_size / max(1, item.compress_size) > self.limits.max_ratio or
                        self.total_bytes + item.file_size > self.limits.max_total_bytes):
                    self.add("ERROR_BUDGET", member)
                    continue
                if item.flag_bits & 1 or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    self.add("ERROR_ARCHIVE_MEMBER", member)
                    continue
                try:
                    is_directory = item.is_dir()
                    require(not is_directory or item.file_size == 0)
                    with bundle.open(item) as stream:
                        content = stream.read(self.limits.max_entry_bytes + 1)
                    require(len(content) == item.file_size)
                    if is_directory:
                        require(not content)
                    else:
                        self.scan_bytes(content, member, depth + 1)
                except (OSError, ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile, InvalidData, zlib.error):
                    self.add("ERROR_ARCHIVE_MEMBER", member)

    def scan_dex(self, data, name):
        require(len(data) >= 112 and re.fullmatch(rb"dex\n0(?:35|37|38|39|40)\0", data[:8]) is not None)
        size, header, endian = struct.unpack_from("<III", data, 32)
        require(size == len(data) and header == 112 and endian == 0x12345678)
        require(data[12:32] == hashlib.sha1(data[32:]).digest())
        require(struct.unpack_from("<I", data, 8)[0] == zlib.adler32(data[12:]) & 0xffffffff)
        count, ids = struct.unpack_from("<II", data, 56)
        data_size, data_offset = struct.unpack_from("<II", data, 104)
        require(data_offset >= 112 and data_offset + data_size == len(data))
        require(count == 0 or (ids >= 112 and ids + count * 4 <= data_offset))
        self.count_strings(count)
        seen_offsets = set()
        for index in range(count):
            offset = struct.unpack_from("<I", data, ids + 4 * index)[0]
            require(data_offset <= offset < len(data))
            if offset in seen_offsets:
                continue
            seen_offsets.add(offset)
            units = 0
            for shift in range(0, 35, 7):
                byte = data[offset]
                offset += 1
                units |= (byte & 127) << shift
                if byte < 128:
                    break
            else:
                raise InvalidData()
            remaining = self.limits.max_total_bytes - self.decoded_bytes
            end = data.find(b"\0", offset, min(len(data), offset + remaining + 1))
            require(end >= offset)
            self.count_decoded_bytes(end - offset)
            raw = data[offset:end].replace(b"\xc0\x80", b"\0")
            text = raw.decode("utf-8", "surrogatepass")
            require(len(text.encode("utf-16-le", "surrogatepass")) // 2 == units)
            text = text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
            self.scan_text(text, name + "!string:" + str(index), numbered=False)

    def scan_android(self, data, name):
        def length(offset, end, width):
            require(offset + width <= end)
            fmt = "<B" if width == 1 else "<H"
            value = struct.unpack_from(fmt, data, offset)[0]
            offset += width
            marker = 1 << (width * 8 - 1)
            if value & marker:
                require(offset + width <= end)
                value = ((value & (marker - 1)) << (width * 8)) | struct.unpack_from(fmt, data, offset)[0]
                offset += width
            return value, offset

        def chunks(start, end, depth=0):
            require(depth <= 8)
            offset = start
            while offset < end:
                require(offset + 8 <= end)
                kind, header, size = struct.unpack_from("<HHI", data, offset)
                require(8 <= header <= size and offset + size <= end)
                stop = offset + size
                if kind == 1:
                    require(header >= 28)
                    count, styles, flags, strings_start, styles_start = struct.unpack_from("<5I", data, offset + 8)
                    self.count_strings(count)
                    require(header + (count + styles) * 4 <= size)
                    require(header + (count + styles) * 4 <= strings_start <= size)
                    require(styles_start == 0 or strings_start <= styles_start <= size)
                    string_end = offset + (styles_start or size)
                    utf8 = bool(flags & 0x100)
                    seen_offsets = set()
                    for index in range(count):
                        relative = struct.unpack_from("<I", data, offset + header + index * 4)[0]
                        if relative in seen_offsets:
                            continue
                        seen_offsets.add(relative)
                        cursor = offset + strings_start + relative
                        units, cursor = length(cursor, string_end, 1 if utf8 else 2)
                        if utf8:
                            byte_count, cursor = length(cursor, string_end, 1)
                        else:
                            byte_count = units * 2
                        width = 1 if utf8 else 2
                        require(cursor + byte_count + width <= string_end)
                        require(data[cursor + byte_count:cursor + byte_count + width] == b"\0" * width)
                        self.count_decoded_bytes(byte_count)
                        text = data[cursor:cursor + byte_count].decode("utf-8" if utf8 else "utf-16-le")
                        require(len(text.encode("utf-16-le")) // 2 == units)
                        self.scan_text(text, name + "!pool:" + str(offset) + ":" + str(index), numbered=False)
                elif kind in {2, 3, 0x200}:
                    require(header >= {2: 12, 3: 8, 0x200: 284}[kind])
                    chunks(offset + header, stop, depth + 1)
                offset = stop
            require(offset == end)

        require(len(data) >= 8 and struct.unpack_from("<I", data, 4)[0] == len(data))
        chunks(0, len(data))


def git(repo, *args, maximum=64 * 1024 * 1024):
    # Capture diagnostics: Git errors can contain local paths or remote credentials.
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    with subprocess.Popen(["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env=env) as process:
        result = process.stdout.read(maximum + 1)
        if len(result) > maximum:
            process.kill()
            process.wait()
            raise InvalidData()
        require(process.wait(timeout=60) == 0)
    return result


def scan_tree(repo, scanner, revision=None):
    root = Path(repo).resolve()
    require(git(root, "rev-parse", "--show-toplevel").decode().strip().casefold() == str(root).replace("\\", "/").casefold()
            or Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() == root)
    if revision is not None:
        oid = git(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").decode().strip()
        rows = git(root, "ls-tree", "-rz", "--full-tree", oid).split(b"\0")
    else:
        rows = git(root, "ls-files", "--stage", "-z").split(b"\0")
    files = 0
    for row in rows:
        if not row:
            continue
        meta, raw_name = row.split(b"\t", 1)
        name = raw_name.decode("utf-8")
        fields = meta.decode().split()
        mode = fields[0]
        scanner.scan_name(name)
        files += 1
        if mode not in {"100644", "100755"} or (not revision and fields[2] != "0"):
            scanner.add("ERROR_FILE_TYPE", name)
            continue
        try:
            if revision is not None:
                oid = fields[2]
                size = int(git(root, "cat-file", "-s", oid))
                require(size <= scanner.limits.max_file_bytes)
                data = git(root, "cat-file", "blob", oid, maximum=scanner.limits.max_file_bytes)
            else:
                path = root / name
                require(not path.is_symlink() and path.resolve().is_relative_to(root))
                require(all(not p.is_symlink() and not getattr(p, "is_junction", lambda: False)()
                            for p in (path, *path.parents) if p != root and root in p.parents))
                require(path.is_file() and path.stat().st_size <= scanner.limits.max_file_bytes)
                with path.open("rb") as stream:
                    data = stream.read(scanner.limits.max_file_bytes + 1)
            scanner.scan_bytes(data, name)
        except (InvalidData, OSError, ValueError):
            scanner.add("ERROR_FILE_READ", name)
    return files


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        print("ERROR_ARGUMENT: 1")
        raise SystemExit(2)


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--revision", help="Scan an immutable commit tree instead of working files")
    group.add_argument("--history", help="Scan ancestors of one explicit refs/heads/ or refs/tags/ ref")
    parser.add_argument("--apk", help="Additionally scan a local APK, without uploading or executing it")
    args = parser.parse_args(argv)
    if any(value is not None and not value.strip() for value in (args.repo, args.revision, args.history, args.apk)):
        parser.error("Empty scope")
    scanner = Scanner()
    affected = []
    files = commits = 0
    try:
        if args.history is not None:
            require(args.history.startswith(("refs/heads/", "refs/tags/")))
            git(args.repo, "check-ref-format", args.history)
            git(args.repo, "show-ref", "--verify", "--hash", args.history)
            tip = git(args.repo, "rev-parse", "--verify", "--end-of-options", args.history + "^{commit}").decode().strip()
            revisions = git(args.repo, "rev-list", tip).decode().splitlines()
            require(len(revisions) <= 10000)
            for revision in revisions:
                current = Scanner()
                files += scan_tree(args.repo, current, revision)
                # Commit messages and identities are publication content too.
                current.scan_bytes(git(args.repo, "cat-file", "commit", revision), "commit@" + revision)
                commits += 1
                if not current.clean:
                    affected.append(revision)
                scanner.findings.update(current.findings)
                scanner.errors.update(current.errors)
                scanner.strings += current.strings
                scanner.entries += current.entries
        else:
            files = scan_tree(args.repo, scanner, args.revision)
        if args.apk is not None:
            with Path(args.apk).open("rb") as stream:
                scanner.scan_bytes(stream.read(scanner.limits.max_file_bytes + 1), "release.apk")
    except (InvalidData, OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        scanner.add("ERROR_INPUT", "input")
    print(json.dumps({
        "files": files, "commits": commits, "affected_commits": affected,
        "findings": len(scanner.findings), "errors": len(scanner.errors),
        "decoded_strings": scanner.strings, "archive_entries": scanner.entries,
        "results": [vars(f) for f in sorted(scanner.findings | scanner.errors,
                                           key=lambda f: (f.reference, f.line or 0, f.rule))],
    }, indent=2))
    return 2 if scanner.errors else 1 if scanner.findings else 0


if __name__ == "__main__":
    sys.exit(main())
