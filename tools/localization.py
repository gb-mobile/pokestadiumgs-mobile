#!/usr/bin/env python3
"""Generate native-offset language asset overlays for Stadium 2 ROMs."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import struct
import subprocess
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

ROM_SIZE = 0x4000000
EXEC_PATCH_MAGIC = b"S2EP1"
EXEC_PATCH_UNIT = 16
MASTER_SLOT_COUNT = 19
RAW_MASTER_SLOTS = frozenset((9, 10))
RESIDENT_AUDIO_OFFSET = 0x140
CHARSET_SIGNATURE = bytes.fromhex(
    "0034350f00060d37030438393a3b3c3d101112131415161718193e42"
)
POST_AUDIO_SIGNATURE = bytes.fromhex(
    "dde1d320d53cda010010db7fd7000390001d91001df102fdfd00fb0017c4ece8"
    "00000f003f000000df7f0660f2fcc20034e4f4f600600061006b0075007f0080"
)
REST_BIN_RE = re.compile(
    r"^\s*-\s*\[\s*(0x[0-9a-fA-F]+)\s*,\s*bin\s*\]"
    r"\s*#\s*rest of ROM\s*$", re.MULTILINE
)


@dataclass(frozen=True)
class Language:
    code: str
    rom_label: str
    script: str
    cart_id: bytes


LANGUAGES = (
    Language("en", "English", "en_msg.txt", b"NP3E"),
    Language("fr", "France", "fr_msg.txt", b"NP3F"),
    Language("de", "Germany", "de_msg.txt", b"NP3D"),
    Language("it", "Italy", "it_msg.txt", b"NP3I"),
    Language("es", "Spain", "es_msg.txt", b"NP3S"),
)
PAL_BASE = Language("pal", "Europe", "en_msg.txt", b"NP3P")
PAL_CRC_KEY = "2952369C-B6E4C3A8"


def digest(name: str, data: bytes) -> str:
    return hashlib.new(name, data).hexdigest()


def normalize_rom(data: bytes) -> tuple[bytes, str]:
    """Convert z64/v64/n64 byte order to big-endian z64."""
    magic = data[:4]
    if magic == bytes.fromhex("80371240"):
        return data, "z64"
    out = bytearray(len(data))
    if magic == bytes.fromhex("37804012"):
        out[0::2], out[1::2] = data[1::2], data[0::2]
        return bytes(out), "v64"
    if magic == bytes.fromhex("40123780"):
        out[0::4], out[1::4] = data[3::4], data[2::4]
        out[2::4], out[3::4] = data[1::4], data[0::4]
        return bytes(out), "n64"
    raise ValueError(f"unknown N64 byte order ({magic.hex()})")


def locate_asset_root(rom: bytes) -> int:
    hits = []
    start = 0
    while (hit := rom.find(CHARSET_SIGNATURE, start)) >= 0:
        hits.append(hit)
        start = hit + 1
    if len(hits) != 1:
        raise ValueError(f"expected one asset character table; found {len(hits)}")
    root = hits[0] - 0x70
    if not 0x400000 <= root < 0x500000 or root % 0x10:
        raise ValueError(f"implausible asset root 0x{root:X}")
    return root


def locate_post_audio(rom: bytes) -> int:
    """Locate the unique common-data boundary after the resident sound data."""
    first = rom.find(POST_AUDIO_SIGNATURE)
    if first < 0 or rom.find(POST_AUDIO_SIGNATURE, first + 1) >= 0:
        raise ValueError("expected one post-audio boundary signature")
    return first


def disassembly_tail_start(repo: Path) -> int:
    path = repo / "yamls/us/rom.yaml"
    match = REST_BIN_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"cannot locate the rest-of-ROM bin in {path}")
    return int(match.group(1), 16)


def parse_script(path: Path) -> list[list[str]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    groups: list[list[str]] = []
    pos = 0
    while pos < len(lines):
        if pos + 2 >= len(lines) or lines[pos] != "~~~~~~~~~~~~~~~":
            raise ValueError(f"{path}:{pos + 1}: malformed separator")
        match = re.fullmatch(r"Text File\s*:\s*(\d+)", lines[pos + 1])
        if not match or int(match.group(1)) != len(groups):
            raise ValueError(f"{path}:{pos + 2}: unexpected text-file index")
        if lines[pos + 2] != "~~~~~~~~~~~~~~~":
            raise ValueError(f"{path}:{pos + 3}: malformed separator")
        pos += 3
        group = []
        while pos < len(lines) and lines[pos] != "~~~~~~~~~~~~~~~":
            group.append(lines[pos])
            pos += 1
        groups.append(group)
    return groups


def script_stats(path: Path, groups: list[list[str]], assets: bytes) -> dict:
    entries = [entry for group in groups for entry in group]
    # Full substring validation is quadratic in ROM size. Sample evenly across
    # the dump; the asset SHA-256 remains the authoritative byte-level check.
    stride = max(1, len(entries) // 64)
    samples = entries[::stride][:64]
    searched = found = 0
    for entry in samples:
        try:
            encoded = entry.replace(r"\n", "\n").encode("cp1252")
        except UnicodeEncodeError:
            continue
        if len(encoded) >= 5:
            searched += 1
            found += encoded in assets
    return {
        "file": path.name,
        "sha256": digest("sha256", path.read_bytes()),
        "text_files": len(groups),
        "entries": len(entries),
        "literal_newline_controls": sum(x.count(r"\n") for x in entries),
        "cp1252_samples_searched": searched,
        "cp1252_samples_found": found,
    }


def input_rom(root: Path, label: str) -> Path:
    exact = root / f"Pokemon Stadium 2 ({label}).n64"
    if exact.exists():
        return exact
    matches = sorted(root.glob(f"*({label}).*"))
    if len(matches) != 1:
        raise FileNotFoundError(f"cannot uniquely locate the {label} ROM in {root}")
    return matches[0]


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)


def make_executable_patch(base: bytes, target: bytes) -> tuple[bytes, dict]:
    """Encode target as aligned copies from the PAL base plus literal changes."""
    if len(base) % EXEC_PATCH_UNIT or len(target) % EXEC_PATCH_UNIT:
        raise ValueError("executable prefixes must be 16-byte aligned")
    base_units = [
        base[pos:pos + EXEC_PATCH_UNIT]
        for pos in range(0, len(base), EXEC_PATCH_UNIT)
    ]
    target_units = [
        target[pos:pos + EXEC_PATCH_UNIT]
        for pos in range(0, len(target), EXEC_PATCH_UNIT)
    ]
    matcher = difflib.SequenceMatcher(None, base_units, target_units, autojunk=True)
    stream = bytearray()
    copied = literal = operations = 0
    stream += EXEC_PATCH_MAGIC
    stream += struct.pack(">II", len(base), len(target))
    stream += hashlib.sha256(base).digest()
    stream += hashlib.sha256(target).digest()
    for tag, first_start, first_end, second_start, second_end in matcher.get_opcodes():
        if tag == "equal":
            start = first_start * EXEC_PATCH_UNIT
            size = (first_end - first_start) * EXEC_PATCH_UNIT
            stream += b"C" + struct.pack(">II", start, size)
            copied += size
            operations += 1
        elif second_end > second_start:
            data = target[
                second_start * EXEC_PATCH_UNIT:second_end * EXEC_PATCH_UNIT
            ]
            stream += b"D" + struct.pack(">I", len(data)) + data
            literal += len(data)
            operations += 1
    encoded = zlib.compress(bytes(stream), 9)
    return encoded, {
        "unit": EXEC_PATCH_UNIT,
        "operations": operations,
        "copied_bytes": copied,
        "literal_bytes": literal,
        "identical_ratio": copied / len(target),
        "compressed_bytes": len(encoded),
        "base_prefix_sha256": digest("sha256", base),
        "target_prefix_sha256": digest("sha256", target),
        "patch_sha256": digest("sha256", encoded),
    }


def apply_executable_patch(base: bytes, encoded: bytes) -> bytes:
    """Apply a verified executable delta created by make_executable_patch."""
    stream = memoryview(zlib.decompress(encoded))
    if bytes(stream[:5]) != EXEC_PATCH_MAGIC:
        raise ValueError("invalid executable patch magic")
    base_size, target_size = struct.unpack(">II", stream[5:13])
    base_hash = bytes(stream[13:45])
    target_hash = bytes(stream[45:77])
    if len(base) != base_size or hashlib.sha256(base).digest() != base_hash:
        raise ValueError("executable patch PAL base verification failed")
    output = bytearray()
    pos = 77
    while pos < len(stream):
        opcode = bytes(stream[pos:pos + 1])
        pos += 1
        if opcode == b"C":
            start, size = struct.unpack(">II", stream[pos:pos + 8])
            pos += 8
            if start + size > len(base):
                raise ValueError("executable patch copy is out of bounds")
            output += base[start:start + size]
        elif opcode == b"D":
            size = struct.unpack(">I", stream[pos:pos + 4])[0]
            pos += 4
            if pos + size > len(stream):
                raise ValueError("executable patch data is truncated")
            output += stream[pos:pos + size]
            pos += size
        else:
            raise ValueError("unknown executable patch opcode")
    result = bytes(output)
    if len(result) != target_size or hashlib.sha256(result).digest() != target_hash:
        raise ValueError("executable patch result verification failed")
    return result


def apply_mobile_layer(repo: Path, code: str, rom: bytes) -> bytes:
    """Apply an optional, expected-byte-checked mobile feature layer."""
    path = repo / f"assets/localization/mobile/{code}.json"
    if not path.exists():
        return rom
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec.get("format") != 1:
        raise ValueError(f"unsupported mobile patch format in {path}")
    output = bytearray(rom)
    used_ranges = []
    for index, patch in enumerate(spec.get("patches", [])):
        offset = int(str(patch["offset"]), 0)
        if "replacement_file" in patch:
            replacement = (path.parent / patch["replacement_file"]).read_bytes()
            size = int(patch.get("size", len(replacement)))
            if len(replacement) != size:
                raise ValueError(f"mobile patch {index} replacement has the wrong size")
            expected = bytes(output[offset:offset + size])
            if digest("sha256", expected) != patch["expected_sha256"]:
                raise ValueError(f"mobile patch {index} expected hash does not match")
        else:
            expected = bytes.fromhex(patch["expected"])
            replacement = bytes.fromhex(patch["replacement"])
        if len(expected) != len(replacement):
            raise ValueError(f"mobile patch {index} must preserve allocated size")
        end = offset + len(expected)
        if offset < 0 or end > ROM_SIZE:
            raise ValueError(f"mobile patch {index} is out of ROM bounds")
        if any(offset < prior_end and prior_start < end for prior_start, prior_end in used_ranges):
            raise ValueError(f"mobile patch {index} overlaps another patch")
        if output[offset:offset + len(expected)] != expected:
            raise ValueError(f"mobile patch {index} expected bytes do not match")
        output[offset:offset + len(replacement)] = replacement
        used_ranges.append((offset, end))
    return bytes(output)


def extract(repo: Path, inputs: Path, codes: list[str]) -> None:
    wanted = set(codes)
    unknown = wanted - {x.code for x in LANGUAGES}
    if unknown:
        raise ValueError(f"unknown language(s): {', '.join(sorted(unknown))}")
    link_root = disassembly_tail_start(repo)
    target_length = ROM_SIZE - link_root

    pal_path = input_rom(inputs, PAL_BASE.rom_label)
    pal_rom, pal_order = normalize_rom(pal_path.read_bytes())
    if len(pal_rom) != ROM_SIZE or pal_rom[0x3B:0x3F] != PAL_BASE.cart_id:
        raise ValueError(f"unexpected English PAL ROM in {pal_path}")
    pal_root = locate_asset_root(pal_rom)
    write_bytes(repo / "baseroms/pal/baserom.z64", pal_rom)
    manifest = {
        "format": 6,
        "layout": "english-pal-base-plus-regional-executable-deltas-and-assets",
        "disassembly_asset_root": f"0x{link_root:X}",
        "target_asset_length": target_length,
        "pal_base": {
            "rom": pal_path.name,
            "source_byte_order": pal_order,
            "normalized_md5": digest("md5", pal_rom),
            "sha256": digest("sha256", pal_rom),
            "cart_id": PAL_BASE.cart_id.decode(),
            "asset_root": f"0x{pal_root:X}",
            "audio_end": f"0x{locate_post_audio(pal_rom):X}",
        },
        "languages": {},
    }
    reference_shape = None
    for language in LANGUAGES:
        if language.code not in wanted:
            continue
        rom_path = input_rom(inputs, language.rom_label)
        rom, order = normalize_rom(rom_path.read_bytes())
        if len(rom) != ROM_SIZE:
            raise ValueError(f"{rom_path} is not a 64 MiB ROM")
        if rom[0x3B:0x3F] != language.cart_id:
            raise ValueError(f"unexpected cart ID in {rom_path}")
        source_root = locate_asset_root(rom)
        tail = rom[source_root:]
        if len(tail) > target_length:
            overflow = tail[target_length:]
            if any(x != 0xFF for x in overflow):
                raise ValueError(f"{language.code} assets overflow the US slot")
            tail = tail[:target_length]
        else:
            tail += b"\xFF" * (target_length - len(tail))
        groups = parse_script(inputs / language.script)
        shape = [len(group) for group in groups]
        if reference_shape is None:
            reference_shape = shape
        elif shape != reference_shape:
            raise ValueError(f"{language.script} does not align with English")
        if language.code == "en":
            expected = (repo / "baseroms/us/checksum.md5").read_text().split()[0]
            if digest("md5", rom) != expected:
                raise ValueError("English ROM does not match the disassembly")
            write_bytes(repo / "baseroms/us/baserom.z64", rom)
        else:
            write_bytes(
                repo / f"assets/localization/{language.code}/{link_root:X}.bin", tail
            )
            exec_patch, exec_stats = make_executable_patch(
                pal_rom[:pal_root], rom[:source_root]
            )
            write_bytes(
                repo / f"assets/localization/{language.code}/executable.patch.zlib",
                exec_patch,
            )
        details = {
            "rom": rom_path.name,
            "source_byte_order": order,
            "normalized_md5": digest("md5", rom),
            "cart_id": language.cart_id.decode(),
            "source_asset_root": f"0x{source_root:X}",
            "source_audio_end": f"0x{locate_post_audio(rom):X}",
            "asset_sha256": digest("sha256", tail),
            "script": script_stats(inputs / language.script, groups, rom[source_root:]),
        }
        if language.code != "en":
            details["executable_patch"] = exec_stats
        manifest["languages"][language.code] = details
        print(
            f"{language.code}: source 0x{source_root:X}; "
            f"English archive map 0x{link_root:X}; "
            f"{details['script']['entries']} entries"
        )
    out = repo / "assets/localization/manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def verify(repo: Path, inputs: Path) -> None:
    path = repo / "assets/localization/manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    start = int(manifest["disassembly_asset_root"], 16)
    expected_length = manifest["target_asset_length"]
    pal_details = manifest["pal_base"]
    pal = (repo / "baseroms/pal/baserom.z64").read_bytes()
    if (
        len(pal) != ROM_SIZE
        or digest("md5", pal) != pal_details["normalized_md5"]
        or digest("sha256", pal) != pal_details["sha256"]
        or locate_asset_root(pal) != int(pal_details["asset_root"], 16)
        or locate_post_audio(pal) != int(pal_details["audio_end"], 16)
    ):
        raise ValueError("English PAL base verification failed")
    print("pal base: OK")
    for code, details in manifest["languages"].items():
        if code == "en":
            data = (repo / "baseroms/us/baserom.z64").read_bytes()
            valid = digest("md5", data) == details["normalized_md5"]
        else:
            data = (repo / f"assets/localization/{code}/{start:X}.bin").read_bytes()
            valid = len(data) == expected_length and digest("sha256", data) == details["asset_sha256"]
        audio_end = locate_post_audio(data)
        if code != "en":
            audio_end += int(details["source_asset_root"], 16)
        valid &= audio_end == int(details["source_audio_end"], 16)
        script = inputs / details["script"]["file"]
        valid &= digest("sha256", script.read_bytes()) == details["script"]["sha256"]
        valid &= len(parse_script(script)) == 330
        if code != "en":
            patch = (
                repo / f"assets/localization/{code}/executable.patch.zlib"
            ).read_bytes()
            patch_details = details["executable_patch"]
            valid &= digest("sha256", patch) == patch_details["patch_sha256"]
            prefix = apply_executable_patch(
                pal[:int(pal_details["asset_root"], 16)], patch
            )
            valid &= digest("sha256", prefix) == patch_details["target_prefix_sha256"]
            rebuilt = prefix + data[:ROM_SIZE - len(prefix)]
            valid &= digest("md5", rebuilt) == details["normalized_md5"]
        if not valid:
            raise ValueError(f"verification failed for {code}")
        print(f"{code}: OK")


def update_n64_checksum(data: bytearray) -> None:
    """Update the CIC-NUS-6103 CRC1/CRC2 used by every Stadium 2 ROM."""
    if zlib.crc32(data[0x40:0x1000]) & 0xFFFFFFFF != 0x0B050EE0:
        raise ValueError("unsupported N64 IPL3; expected CIC-NUS-6103")

    mask = 0xFFFFFFFF
    seed = 0xA3886759
    t1 = t2 = t3 = t4 = t5 = t6 = seed
    for offset in range(0x1000, 0x101000, 4):
        value = int.from_bytes(data[offset:offset + 4], "big")
        total = (t6 + value) & mask
        if total < t6:
            t4 = (t4 + 1) & mask
        t6 = total
        t3 ^= value
        shift = value & 0x1F
        rotated = value if shift == 0 else (
            (value << shift) | (value >> (32 - shift))
        ) & mask
        t5 = (t5 + rotated) & mask
        if t2 > value:
            t2 ^= rotated
        else:
            t2 ^= t6 ^ value
        t1 = (t1 + (t5 ^ value)) & mask
    crc1 = ((t6 ^ t4) + t3) & mask
    crc2 = ((t5 ^ t2) + t1) & mask
    data[0x10:0x18] = struct.pack(">II", crc1, crc2)

def finalize_rom(path: Path) -> None:
    """Force a linked overlay to 64 MiB and update its N64 boot checksum."""
    data = path.read_bytes()
    if len(data) > ROM_SIZE:
        overflow = data[ROM_SIZE:]
        if any(value != 0xFF for value in overflow):
            raise ValueError(
                f"{path} has {len(overflow)} non-padding overflow bytes"
            )
        data = data[:ROM_SIZE]
    elif len(data) < ROM_SIZE:
        data += b"\xFF" * (ROM_SIZE - len(data))
    output = bytearray(data)
    update_n64_checksum(output)
    path.write_bytes(output)
    print(f"finalized {path} ({len(output)} bytes)")


def master_pointers(data: bytes, root: int) -> list[int]:
    pointers = [
        int.from_bytes(data[root + 0x10 + index * 4:root + 0x14 + index * 4], "big")
        for index in range(MASTER_SLOT_COUNT)
    ]
    if any(pointer < root or pointer >= ROM_SIZE or pointer % 0x10 for pointer in pointers):
        raise ValueError("invalid archive pointer in filesystem master table")
    if len(set(pointers)) != MASTER_SLOT_COUNT:
        raise ValueError("duplicate archive pointer in filesystem master table")
    return pointers


def repack_archive_slots(base: bytes, asset: bytes, source_root: int, target_root: int) -> bytes:
    source_pointers = master_pointers(asset, 0)
    target_pointers = master_pointers(base, target_root)
    ordered_targets = sorted(target_pointers)
    output = bytearray(base)
    for index, (source, target) in enumerate(zip(source_pointers, target_pointers)):
        source_offset = source - source_root
        next_target = next((value for value in ordered_targets if value > target), ROM_SIZE)
        capacity = next_target - target
        size = capacity if index in RAW_MASTER_SLOTS else int.from_bytes(
            asset[source_offset + 8:source_offset + 12], "big"
        )
        if not 0 < size <= capacity or source_offset < 0 or source_offset + size > len(asset):
            raise ValueError(f"regional archive {index} does not fit the PAL slot")
        output[target:next_target] = b"\xFF" * capacity
        output[target:target + size] = asset[source_offset:source_offset + size]
    return bytes(output)


def repack_resident_audio(base: bytes, asset: bytes, source_root: int, target_root: int) -> bytes:
    source_end = locate_post_audio(asset)
    target_end = locate_post_audio(base)
    source_start = RESIDENT_AUDIO_OFFSET
    target_start = target_root + RESIDENT_AUDIO_OFFSET
    source_size = source_end - source_start
    capacity = target_end - target_start
    if not 0 < source_size <= capacity:
        raise ValueError("regional resident audio does not fit the PAL slot")
    output = bytearray(base)
    output[target_start:target_end] = b"\xFF" * capacity
    output[target_start:target_start + source_size] = asset[source_start:source_end]
    return bytes(output)


def validate_overlay(pal: bytes, rom: bytes, asset: bytes, source_root: int, target_root: int) -> None:
    if rom[:target_root] != pal[:target_root]:
        raise ValueError("overlay executable differs from English PAL")
    source_audio_end = locate_post_audio(asset)
    source_audio = asset[RESIDENT_AUDIO_OFFSET:source_audio_end]
    target_audio_start = target_root + RESIDENT_AUDIO_OFFSET
    if rom[target_audio_start:target_audio_start + len(source_audio)] != source_audio:
        raise ValueError("overlay audio verification failed")
    source_pointers = master_pointers(asset, 0)
    target_pointers = master_pointers(pal, target_root)
    ordered_sources = sorted(source_pointers)
    for index, (source, target) in enumerate(zip(source_pointers, target_pointers)):
        source_offset = source - source_root
        size = (
            next((value for value in ordered_sources if value > source), ROM_SIZE) - source
            if index in RAW_MASTER_SLOTS
            else int.from_bytes(asset[source_offset + 8:source_offset + 12], "big")
        )
        if rom[target:target + size] != asset[source_offset:source_offset + size]:
            raise ValueError(f"overlay archive {index} verification failed")


def export_mobile_text(inputs: Path, output: Path, codes: list[str]) -> None:
    """Export the known Mobile Stadium message range as editable UTF-8 JSON."""
    script_by_code = {language.code: language.script for language in LANGUAGES}
    script_by_code["us"] = script_by_code["en"]
    script_by_code["ja"] = "ja-Hrkt_msg.txt"
    unknown = set(codes) - set(script_by_code)
    if unknown:
        raise ValueError(f"unknown language(s): {', '.join(sorted(unknown))}")
    output.mkdir(parents=True, exist_ok=True)
    for code in codes:
        groups = parse_script(inputs / script_by_code[code])
        if len(groups) <= 54 or len(groups[54]) < 125 or len(groups[25]) < 19:
            raise ValueError(f"{script_by_code[code]} lacks text file 54 entries 86-124")
        document = {
            "format": 2,
            "language": code,
            "source_script": script_by_code[code],
            "text_file": 54,
            "first_entry": 86,
            "last_entry": 124,
            "control_codes": {
                "literal_newline": r"\n",
                "note": "Keep backslash-n controls and all other bracketed variables unchanged.",
            },
            "entries": [
                {"index": index, "text": groups[54][index]}
                for index in range(86, 125)
            ],
            "additional_text_files": [{
                "text_file": 25,
                "first_entry": 0,
                "last_entry": 18,
                "entries": [
                    {"index": index, "text": groups[25][index]}
                    for index in (0, 14, 18)
                ],
            }],
        }
        destination = output / f"{code}.json"
        destination.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"exported {destination}")


def compose(
    repo: Path, codes: list[str], official_layout: bool = False,
    regional_ids: bool = False, mobile_stadium: bool = False,
) -> None:
    """Build safe PAL overlays by default or exact official layouts on request."""
    if official_layout and regional_ids:
        raise ValueError("--regional-ids cannot be combined with --official-layout")
    if mobile_stadium and not official_layout:
        raise ValueError("--mobile-stadium requires --official-layout")
    manifest = json.loads((repo / "assets/localization/manifest.json").read_text())
    asset_storage_root = int(manifest["disassembly_asset_root"], 16)
    english_pal = (repo / "baseroms/pal/baserom.z64").read_bytes()
    target_root = locate_asset_root(english_pal)
    pal_details = manifest["pal_base"]
    if len(english_pal) != ROM_SIZE or digest("md5", english_pal) != pal_details["normalized_md5"]:
        raise ValueError("the normalized English PAL base is not compatible")
    for code in codes:
        if code == "us":
            if not (official_layout and mobile_stadium):
                raise ValueError(
                    "language 'us' is only available with "
                    "--official-layout --mobile-stadium"
                )
            rom = (repo / "baseroms/us/baserom.z64").read_bytes()
            profile = json.loads(
                (repo / "assets/localization/mobile/profiles/us.json").read_text()
            )
            if (
                len(rom) != ROM_SIZE
                or digest("sha256", rom) != profile["source_sha256"]
                or rom[0x3B:0x3F] != b"NP3E"
            ):
                raise ValueError("the normalized NTSC English base is not compatible")
        elif code == "au":
            rom = english_pal
        elif code not in manifest["languages"]:
            raise ValueError(f"cannot compose language {code!r}")
        else:
            details = manifest["languages"][code]
            if code == "en":
                rom = english_pal
            elif official_layout:
                source_root = int(details["source_asset_root"], 16)
                asset = (
                    repo / f"assets/localization/{code}/{asset_storage_root:X}.bin"
                ).read_bytes()
                patch = (
                    repo / f"assets/localization/{code}/executable.patch.zlib"
                ).read_bytes()
                rom = apply_executable_patch(english_pal[:target_root], patch)
                if len(rom) != source_root:
                    raise ValueError(f"regional executable size mismatch for {code}")
                rom += asset[:ROM_SIZE - source_root]
                if digest("md5", rom) != details["normalized_md5"]:
                    raise ValueError(
                        f"official regional reconstruction failed for {code}"
                    )
            else:
                source_root = int(details["source_asset_root"], 16)
                asset = (
                    repo / f"assets/localization/{code}/{asset_storage_root:X}.bin"
                ).read_bytes()
                base = repack_resident_audio(
                    english_pal, asset, source_root, target_root
                )
                rom = repack_archive_slots(
                    base, asset, source_root, target_root
                )
                # The checksum and executable remain English PAL, so its NP3P
                # identity must remain intact as well. Project64 keys runtime
                # settings by CRC1/CRC2/country.
                validate_overlay(
                    english_pal, rom, asset, source_root, target_root
                )
                if regional_ids:
                    rom = bytearray(rom)
                    rom[0x3B:0x3F] = details["cart_id"].encode("ascii")
                    rom = bytes(rom)
        if mobile_stadium:
            rom = apply_mobile_layer(repo, code, rom)
        output_root = (
            repo / "build/mobile-stadium" if mobile_stadium else
            repo / "build/official-layout" if official_layout else
            repo / "build/regional-id" if regional_ids else repo / "build"
        )
        output = output_root / (
            "pokestadiumgs-ntsc-en.z64"
            if code == "us" else f"pokestadiumgs-pal-{code}.z64"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(rom)
        finalize_rom(output)
        output_sha256 = digest("sha256", output.read_bytes())
        print(f"composed {code}: {output} (SHA-256 {output_sha256})")


def _clone_rdb_section(text: str, source_key: str, target_key: str, name: str) -> str:
    section_re = re.compile(
        rf"(?ms)^\[{re.escape(source_key)}\]\r?\n.*?(?=^\[|\Z)"
    )
    source = section_re.search(text)
    if source is None:
        raise ValueError(f"Project64 database has no [{source_key}] template")
    section = source.group(0)
    section = section.replace(f"[{source_key}]", f"[{target_key}]", 1)
    section = re.sub(
        r"(?m)^Good Name=[^\r\n]*(\r?)$",
        lambda match: f"Good Name={name}{match.group(1)}",
        section, count=1,
    )
    target_re = re.compile(
        rf"(?ms)^\[{re.escape(target_key)}\]\r?\n.*?(?=^\[|\Z)"
    )
    if target_re.search(text):
        return target_re.sub(lambda _: section, text, count=1)
    separator = "" if text.endswith(("\n", "\r")) else "\r\n"
    return text + separator + section


def configure_project64(config: Path, codes: list[str]) -> None:
    """Install PAL overlay identities by cloning Project64's English profile."""
    selected = {language.code: language for language in LANGUAGES}
    for code in codes:
        if code not in selected:
            raise ValueError(f"cannot configure language {code!r}")
    targets = [selected[code] for code in codes if code != "en"]
    source_key = f"{PAL_CRC_KEY}-C:50"
    for filename in ("Project64.rdb", "Video.rdb"):
        path = config / filename
        text = path.read_bytes().decode("latin-1")
        for language in targets:
            country = language.cart_id[-1]
            target_key = f"{PAL_CRC_KEY}-C:{country:02X}"
            text = _clone_rdb_section(
                text, source_key, target_key,
                f"Pokemon Stadium 2 ({chr(country)}) [English-layout overlay]",
            )
        path.write_bytes(text.encode("latin-1"))
        print(f"configured {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=(
            "extract", "verify", "finalize", "compose", "export-mobile-text",
            "configure-project64"
        )
    )
    parser.add_argument("roms", nargs="*", type=Path)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--input-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--languages", nargs="+", default=[x.code for x in LANGUAGES])
    parser.add_argument(
        "--official-layout", action="store_true",
        help="build exact regional executable/layout deltas",
    )
    parser.add_argument(
        "--mobile-stadium", action="store_true",
        help="apply the opt-in Mobile Stadium layer (requires --official-layout)",
    )
    parser.add_argument(
        "--regional-ids", action="store_true",
        help="use native language IDs for English-layout overlays (needs emulator profile)",
    )
    parser.add_argument(
        "--project64-config", type=Path,
        help="Project64 Config directory for configure-project64",
    )
    args = parser.parse_args()
    try:
        if args.command == "extract":
            extract(args.repo.resolve(), args.input_root.resolve(), args.languages)
        elif args.command == "verify":
            verify(args.repo.resolve(), args.input_root.resolve())
        elif args.command == "finalize":
            if not args.roms:
                raise ValueError("finalize requires at least one ROM path")
            for rom in args.roms:
                finalize_rom(rom)
        elif args.command == "compose":
            compose(
                args.repo.resolve(), args.languages,
                args.official_layout, args.regional_ids, args.mobile_stadium,
            )
        elif args.command == "export-mobile-text":
            export_mobile_text(
                args.input_root.resolve(),
                args.repo.resolve() / "assets/localization/mobile/text",
                args.languages,
            )
        else:
            if args.project64_config is None:
                raise ValueError("configure-project64 requires --project64-config")
            configure_project64(args.project64_config.resolve(), args.languages)
    except (FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
