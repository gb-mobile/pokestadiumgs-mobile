#!/usr/bin/env python3
"""Port Mobile Stadium unlock state and battle data between Stadium 2 saves."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

BANK_SIZE = 0x10000
FLASH_SIZE = BANK_SIZE * 2
FOOTER_MAGIC = b"P3v0"

# Canonical big-endian offsets within either mirrored 64 KiB save bank.
RECORDS = {
    0x15: (0xC000, 0x1A0),
    0x14: (0xC1A0, 0x060),
    0x12: (0xC200, 0x20C),
    0x13: (0xC618, 0x054),
    0x18: (0xC66C, 0x250),
    0x19: (0xC8BC, 0x1B10),
    0x1A: (0xE3CC, 0x490),
    0x1B: (0xE85C, 0xBD0),
    0x1C: (0xF42C, 0x3F0),
    0x1D: (0xF81C, 0x648),
    0x1E: (0xFE64, 0x050),
}
VALIDATION_RECORDS = tuple(RECORDS.values()) + ((0xC40C, 0x20C),)
# The Japanese main-menu overlay tests bit 2 of the halfword at state + 2
# (record 0x14 + 6) and records that the unlock notification has been seen in
# bit 0 at state + 5 (record 0x14 + 9). The rest of record 0x14 is unrelated.
MOBILE_APPEARANCE_OFFSET = RECORDS[0x14][0] + 0x06
MOBILE_APPEARANCE_MASK = 0x0004
MOBILE_SEEN_OFFSET = RECORDS[0x14][0] + 0x09
MOBILE_SEEN_MASK = 0x01
MOBILE_DATA_RECORD = 0x1A


def international_name(text: str, size: int) -> bytes:
    encoded = bytearray()
    for character in text.upper():
        if "A" <= character <= "Z":
            encoded.append(0x80 + ord(character) - ord("A"))
        elif "0" <= character <= "9":
            encoded.append(0xF6 + ord(character) - ord("0"))
        else:
            encoded.append(0x7F)
    encoded.append(0x50)
    return bytes(encoded[:size]).ljust(size, b"\x50")


def localize_mobile_record(record: bytearray) -> None:
    """Replace Japanese fixed-width names without changing battle data."""
    for team_index in range(2):
        team = team_index * 0x1D4
        trainer = record[team + 6]
        controller = record[team + 7]
        record[team:team + 6] = international_name(
            f"TEAM{team_index + 1}", 6
        )
        record[team + 6] = trainer
        record[team + 7] = controller
        record[team + 8:team + 0x20] = international_name(
            f"PLAYER{team_index + 1}", 0x18
        )
        for pokemon_index in range(6):
            pokemon = team + 0x20 + pokemon_index * 0x3C
            record[pokemon + 0x24:pokemon + 0x30] = international_name(
                "POKEMON", 0xC
            )
            record[pokemon + 0x30:pokemon + 0x3C] = international_name(
                "MOBILE", 0xC
            )


def word_swap(data: bytes) -> bytes:
    if len(data) % 4:
        raise ValueError("save length is not divisible by four")
    return b"".join(data[offset : offset + 4][::-1] for offset in range(0, len(data), 4))


def restore_mirror(data: bytes, label: str) -> bytes:
    if len(data) == FLASH_SIZE:
        return data
    if len(data) == FLASH_SIZE - 0x100:
        if data[BANK_SIZE:] != data[: BANK_SIZE - 0x100]:
            raise ValueError(f"{label}: truncated second save bank is not a mirror")
        return data[:BANK_SIZE] * 2
    raise ValueError(
        f"{label}: expected a 0x20000-byte .fla "
        f"(or the supported 0x1FF00-byte truncated mirror), got 0x{len(data):X}"
    )


def record_bytes(bank: bytes, record_id: int) -> bytes:
    offset, size = RECORDS[record_id]
    return bank[offset : offset + size]


def valid_record(record: bytes) -> bool:
    return (
        len(record) >= 6
        and record[-6:-2] == FOOTER_MAGIC
        and sum(record[:-2]) & 0xFFFF == int.from_bytes(record[-2:], "big")
    )


def validate_bank(bank: bytes, label: str) -> None:
    invalid = [
        f"0x{offset:X}"
        for offset, size in VALIDATION_RECORDS
        if not valid_record(bank[offset : offset + size])
    ]
    if invalid:
        raise ValueError(f"{label}: invalid records: {', '.join(invalid)}")


def decode_save(raw: bytes, label: str) -> tuple[bytes, str]:
    raw = restore_mirror(raw, label)
    candidates = (("big-endian", raw), ("Project64 word-swapped", word_swap(raw)))
    valid: list[tuple[str, bytes]] = []
    for byte_order, decoded in candidates:
        try:
            validate_bank(decoded[:BANK_SIZE], label)
        except ValueError:
            continue
        valid.append((byte_order, decoded))
    if len(valid) != 1:
        raise ValueError(f"{label}: could not determine a unique save byte order")
    byte_order, decoded = valid[0]
    if decoded[:BANK_SIZE] != decoded[BANK_SIZE:]:
        raise ValueError(f"{label}: the two save banks do not match")
    return decoded, byte_order


def encode_save(decoded: bytes, byte_order: str) -> bytes:
    if byte_order == "big-endian":
        return decoded
    if byte_order == "Project64 word-swapped":
        return word_swap(decoded)
    raise ValueError(f"unsupported save byte order: {byte_order}")


def enable_mobile_stadium(target_path: Path, output_path: Path) -> None:
    """Enable the Stadium-side menu without replacing its battle-data record."""
    target_raw = target_path.read_bytes()
    target, byte_order = decode_save(target_raw, str(target_path))
    original_bank = target[:BANK_SIZE]
    patched_bank = bytearray(original_bank)
    appearance = int.from_bytes(
        patched_bank[MOBILE_APPEARANCE_OFFSET:MOBILE_APPEARANCE_OFFSET + 2],
        "big",
    )
    patched_bank[MOBILE_APPEARANCE_OFFSET:MOBILE_APPEARANCE_OFFSET + 2] = (
        appearance | MOBILE_APPEARANCE_MASK
    ).to_bytes(2, "big")
    patched_bank[MOBILE_SEEN_OFFSET] |= MOBILE_SEEN_MASK

    offset, size = RECORDS[0x14]
    record = patched_bank[offset:offset + size]
    record[-2:] = (sum(record[:-2]) & 0xFFFF).to_bytes(2, "big")
    patched_bank[offset:offset + size] = record
    validate_bank(patched_bank, str(output_path))
    for index, (before, after) in enumerate(zip(original_bank, patched_bank)):
        if not offset <= index < offset + size and before != after:
            raise AssertionError(f"unrelated target data changed at 0x{index:X}")

    output = encode_save(bytes(patched_bank) * 2, byte_order)
    if len(target_raw) == FLASH_SIZE - 0x100:
        output = output[:-0x100]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)
    print(f"Target byte order: {byte_order}")
    print(
        "Enabled Mobile Stadium without changing record 0x1A: "
        f"appearance bit 0x{MOBILE_APPEARANCE_MASK:04X} at "
        f"0x{MOBILE_APPEARANCE_OFFSET:X}, seen bit at "
        f"0x{MOBILE_SEEN_OFFSET:X}"
    )
    print(f"Wrote {output_path} ({len(output)} bytes)")
    print(f"SHA-256: {hashlib.sha256(output).hexdigest()}")

def port_mobile_data(source_path: Path, target_path: Path, output_path: Path) -> None:
    source_raw = source_path.read_bytes()
    target_raw = target_path.read_bytes()
    source, source_order = decode_save(source_raw, str(source_path))
    target, target_order = decode_save(target_raw, str(target_path))
    source_bank = source[:BANK_SIZE]
    original_target_bank = target[:BANK_SIZE]
    patched_bank = bytearray(original_target_bank)

    source_appearance = int.from_bytes(
        source_bank[MOBILE_APPEARANCE_OFFSET : MOBILE_APPEARANCE_OFFSET + 2], "big"
    )
    if not source_appearance & MOBILE_APPEARANCE_MASK:
        raise ValueError(f"{source_path}: Mobile Stadium appearance flag is not set")

    target_appearance = int.from_bytes(
        patched_bank[MOBILE_APPEARANCE_OFFSET : MOBILE_APPEARANCE_OFFSET + 2], "big"
    )
    patched_bank[MOBILE_APPEARANCE_OFFSET : MOBILE_APPEARANCE_OFFSET + 2] = (
        target_appearance | MOBILE_APPEARANCE_MASK
    ).to_bytes(2, "big")
    patched_bank[MOBILE_SEEN_OFFSET] |= MOBILE_SEEN_MASK

    record14_offset, record14_size = RECORDS[0x14]
    record14 = patched_bank[record14_offset : record14_offset + record14_size]
    record14[-2:] = (sum(record14[:-2]) & 0xFFFF).to_bytes(2, "big")
    patched_bank[record14_offset : record14_offset + record14_size] = record14

    data_offset, data_size = RECORDS[MOBILE_DATA_RECORD]
    mobile_record = bytearray(source_bank[data_offset : data_offset + data_size])
    localize_mobile_record(mobile_record)
    mobile_record[-2:] = (
        sum(mobile_record[:-2]) & 0xFFFF
    ).to_bytes(2, "big")
    patched_bank[data_offset : data_offset + data_size] = mobile_record

    validate_bank(patched_bank, str(output_path))
    allowed_ranges = (
        (record14_offset, record14_offset + record14_size),
        (data_offset, data_offset + data_size),
    )
    for offset in range(BANK_SIZE):
        if (
            not any(start <= offset < end for start, end in allowed_ranges)
            and patched_bank[offset] != original_target_bank[offset]
        ):
            raise AssertionError(f"unrelated target data changed at 0x{offset:X}")

    output = encode_save(bytes(patched_bank) * 2, target_order)
    if len(target_raw) == FLASH_SIZE - 0x100:
        output = output[:-0x100]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)

    print(f"Source byte order: {source_order}")
    print(f"Target byte order: {target_order}")
    record14_checksum = int.from_bytes(
        patched_bank[record14_offset + record14_size - 2 : record14_offset + record14_size],
        "big",
    )
    data_checksum = int.from_bytes(
        patched_bank[data_offset + data_size - 2 : data_offset + data_size], "big"
    )
    print(
        "Enabled Mobile Stadium: "
        f"appearance bit at 0x{MOBILE_APPEARANCE_OFFSET:X}, "
        f"seen bit at 0x{MOBILE_SEEN_OFFSET:X}, "
        f"record 0x14 checksum 0x{record14_checksum:04X}"
    )
    print(
        f"Copied battle-data record 0x{MOBILE_DATA_RECORD:02X}: "
        f"offset 0x{data_offset:X}, size 0x{data_size:X}, "
        f"checksum 0x{data_checksum:04X}"
    )
    print(f"Wrote {output_path} ({len(output)} bytes)")
    print(f"SHA-256: {hashlib.sha256(output).hexdigest()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="unlocked Japanese .fla")
    parser.add_argument("--target", required=True, type=Path, help="Stadium 2 save to preserve")
    parser.add_argument("--output", required=True, type=Path, help="new patched save")
    parser.add_argument(
        "--enable-only", action="store_true",
        help="enable the menu while preserving the existing battle-data record",
    )
    args = parser.parse_args()
    if args.output.resolve() in tuple(
        path.resolve() for path in (args.source, args.target) if path is not None
    ):
        parser.error("--output must not overwrite an input save")
    if args.enable_only:
        if args.source is not None:
            parser.error("--source cannot be used with --enable-only")
        enable_mobile_stadium(args.target, args.output)
    else:
        if args.source is None:
            parser.error("--source is required unless --enable-only is used")
        port_mobile_data(args.source, args.target, args.output)

if __name__ == "__main__":
    main()
