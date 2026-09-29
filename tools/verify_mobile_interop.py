#!/usr/bin/env python3
"""Verify the western Mobile Crystal <-> Mobile Stadium build contract."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

CRYSTAL_UPLOAD_OFFSET = 0xE001
CRYSTAL_UPLOAD_SIZE = 0x1B5
CRYSTAL_DOWNLOAD_OFFSET = 0xF000
CRYSTAL_DOWNLOAD_SIZE = 0x1000
CRYSTAL_FLAG_OFFSET = 0xE000
CRYSTAL_ADAPTER_STATUS1_OFFSET = 0x8B10
CRYSTAL_ADAPTER_STATUS2_OFFSET = 0xE79A
ROM_SIZE = 0x4000000
OVERLAY_ROM = 0x3FEDFD0
OVERLAY_VRAM = 0x84500000
OVERLAY_CODE = OVERLAY_VRAM + 0x20
OVERLAY_MIN_FILE_SIZE = 0x1790
LANGUAGES = ("us", "en", "au", "fr", "de", "it", "es")
CRYSTAL_ROM_SIZE = 0x200000
N64PS3_TOTAL_SIZE = 6 + 2 + 128 * 2 * 2
N64PS3_CRC_POLY = 0xC387
N64PS3_CRC_INIT = 0xFEFE


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read_text(path: Path) -> str:
    require(path.is_file(), f"missing {path}")
    return path.read_text(encoding="utf-8")


def stadium_crc(data: bytes, initial: int = N64PS3_CRC_INIT) -> int:
    table = []
    for value in range(256):
        remainder = 0
        current = value
        for _ in range(8):
            remainder = (remainder >> 1) ^ (N64PS3_CRC_POLY if (remainder ^ current) & 1 else 0)
            current >>= 1
        table.append(remainder)
    crc = initial
    for value in data:
        crc = (crc >> 8) ^ table[(crc & 0xFF) ^ value]
    return crc


def verify_crystal_stadium_checksums(data: bytes) -> tuple[int, int]:
    require(len(data) == CRYSTAL_ROM_SIZE, "Crystal ROM is not the required 2 MiB MBC30 image")
    offset = len(data) - N64PS3_TOTAL_SIZE
    require(data[offset:offset + 6] == b"N64PS3", "Crystal ROM lacks the Stadium N64PS3 header")
    stored_crc = int.from_bytes(data[offset + 6:offset + 8], "big")
    calculated_crc = stadium_crc(data[offset + 8:offset + N64PS3_TOTAL_SIZE])
    require(calculated_crc == stored_crc,
            f"Crystal N64PS3 table CRC mismatch: {calculated_crc:04X} != {stored_crc:04X}")

    work = bytearray(data)
    work[0x14E:0x150] = bytes(2)
    work[offset + 6:] = bytes(len(work) - offset - 6)
    mismatches = 0
    for index in range(128 * 2):
        start = index * 0x2000
        checksum = (N64PS3_CRC_INIT + sum(work[start:start + 0x2000])) & 0xFFFF
        stored = int.from_bytes(data[offset + 8 + index * 2:offset + 10 + index * 2], "big")
        mismatches += checksum != stored
        work[offset + 8 + index * 2:offset + 10 + index * 2] = checksum.to_bytes(2, "big")
    require(mismatches == 0, f"Crystal has {mismatches} stale N64PS3 half-bank checksums")

    global_work = bytearray(data)
    stored_global = int.from_bytes(global_work[0x14E:0x150], "big")
    global_work[0x14E:0x150] = bytes(2)
    calculated_global = sum(global_work) & 0xFFFF
    require(calculated_global == stored_global,
            f"Crystal global checksum mismatch: {calculated_global:04X} != {stored_global:04X}")
    return stored_crc, stored_global


def verify_crystal(repo: Path, save: Path) -> list[str]:
    root = repo.parent
    crystal = root / "pokecrystal-mobile-eng"
    rom = crystal / "pokecrystal.gbc"
    data = rom.read_bytes()
    require(data[0x13F:0x143] == b"BXTE", "Crystal ROM header is not BXTE")
    stadium_crc_value, global_checksum = verify_crystal_stadium_checksums(data)

    raw = save.read_bytes()
    require(len(raw) >= 0x10000, f"Crystal save is too short: 0x{len(raw):X}")
    require(raw[CRYSTAL_FLAG_OFFSET] == 1, "Crystal Mobile Stadium flag is not enabled")
    status1 = raw[CRYSTAL_ADAPTER_STATUS1_OFFSET]
    status2 = raw[CRYSTAL_ADAPTER_STATUS2_OFFSET]
    require(status1 != 0xFF and (status1 ^ status2) == 0xFF,
            f"Crystal Mobile Adapter pair is invalid: {status1:02X}/{status2:02X}")
    upload = raw[CRYSTAL_UPLOAD_OFFSET:CRYSTAL_UPLOAD_OFFSET + CRYSTAL_UPLOAD_SIZE]
    require(len(upload) == CRYSTAL_UPLOAD_SIZE, "Crystal replay upload is truncated")
    require(upload[0x1B1:0x1B3] == b"P3", "Crystal replay upload lacks P3 marker")
    checksum = sum(upload[:0x1B3]) & 0xFFFF
    stored = int.from_bytes(upload[0x1B3:0x1B5], "little")
    require(checksum == stored, f"Crystal upload checksum mismatch: {checksum:04X} != {stored:04X}")
    download = raw[CRYSTAL_DOWNLOAD_OFFSET:CRYSTAL_DOWNLOAD_OFFSET + CRYSTAL_DOWNLOAD_SIZE]
    distribution = repo.parent / "examplebattledata_corrected.bin"
    distribution_result = "No canonical distribution fixture was supplied"
    if distribution.is_file():
        distribution_data = distribution.read_bytes()
        require(len(distribution_data) == 0xFFE,
                "examplebattledata_corrected.bin is not the required 0xFFE-byte distribution")
        require(distribution_data[0xFFA:0xFFC] == b"P3" and
                (sum(distribution_data[:0xFFC]) & 0xFFFF) ==
                int.from_bytes(distribution_data[0xFFC:0xFFE], "little"),
                "examplebattledata_corrected.bin has an invalid P3 frame")
        require(distribution_data[4 + 0x24:4 + 0x26] == bytes.fromhex("490E") and
                distribution_data[4 + 0x200:4 + 0x202] == bytes.fromhex("36E5"),
                "corrected first-replay trainer IDs are not at 0x24/0x200")
        description = distribution_data[0xF1C:0xFE1]
        require(b"Test file by " in description and b"OtherLiz" in description and
                b"newer consoles" in description,
                "western payload description is missing at DLD offset 0xF1C")
        require(distribution_data[0xFE1] & 3 == 3,
                "canonical payload does not request both Delibird console upgrades")
        distribution_result = (
            "Canonical distribution fixture matches the current Crystal save"
            if download[:0xFFE] == distribution_data else
            "Canonical fixture and current save are independently valid P3 distributions"
        )
    download_checksum = sum(download[:0xFFC]) & 0xFFFF
    download_stored = int.from_bytes(download[0xFFC:0xFFE], "little")
    download_valid = (download[0xFFA:0xFFC] == b"P3" and
                      download_checksum == download_stored)
    require(download_valid, "Crystal save lacks a valid downloaded P3 payload")
    replay_count = 0
    for start in range(0, 0xDB0, 0x490):
        marker = download[start + 0x490:start + 0x492]
        stored = int.from_bytes(download[start + 0x492:start + 0x494], "little")
        calculated = sum(download[start + 4:start + 0x492]) & 0xFFFF
        replay_count += marker == b"P3" and calculated == stored
    organizer_count = 0
    for start in range(0, 0x168, 0x48):
        base = 0xDB4 + start
        marker = download[base + 0x44:base + 0x46]
        stored = int.from_bytes(download[base + 0x46:base + 0x48], "little")
        calculated = sum(download[base:base + 0x46]) & 0xFFFF
        organizer_count += marker == b"P3" and calculated == stored
    require(replay_count == 3,
            f"expected 3 valid Crystal replay slots, found {replay_count}")
    require(organizer_count == 5,
            f"expected 5 valid Crystal organizer slots, found {organizer_count}")
    replay_stored = int.from_bytes(download[0x492:0x494], "little")

    friend = raw[0xE001:0xE001 + 0x1B5]
    require(len(friend) == 0x1B5 and friend[0x1B1:0x1B3] == b"P3",
            "Crystal save lacks the native western Friend Data marker")
    friend_stored = int.from_bytes(friend[0x1B3:0x1B5], "little")
    friend_calculated = sum(friend[:0x1B3]) & 0xFFFF
    require(friend_stored == friend_calculated,
            f"Crystal Friend Data checksum mismatch: "
            f"{friend_stored:04X} != {friend_calculated:04X}")

    first_replay = download[4:0x494]
    require(len(first_replay) == 0x490 and
            first_replay[0x48C:0x48E] == b"P3",
            "first western replay is not a native 0x490-byte P3 record")

    for suffix in ("eng", "fra", "ger", "ita", "spa"):
        crystal_repo = root / f"pokecrystal-mobile-{suffix}"
        mobile40 = read_text(crystal_repo / "mobile" / "mobile_40.asm")
        require("ld bc, wc7bd - wc608" in mobile40,
                f"{suffix}: Crystal upload length is no longer symbolically 0x1B5")
        require("ld de, s7_a001" in mobile40,
                f"{suffix}: Crystal upload is no longer written at bank 7 $A001")
        require(mobile40.count("call .CopyAllFromOT") == 3 and
                ".CopyAllFromOT:" in mobile40 and
                ".CopyNonEggArray:" not in mobile40,
                f"{suffix}: Friend Data producer does not retain all native party slots")
        require("ld hl, wOTPlayerID" in mobile40 and "ld de, wc608 + 11" in mobile40,
                f"{suffix}: Friend Data producer does not store TID at E00C/E00D")
        require("ld de, wc608 + 13" in mobile40 and
                "ld de, wc608 + 79" in mobile40 and
                "ld de, wc608 + 145" in mobile40,
                f"{suffix}: western Friend Data array offsets do not match FriendDataStructure.txt")
        require("ld [wc7b9], a" in mobile40 and "ld [wc7ba], a" in mobile40,
                f"{suffix}: Crystal P3 replay marker writes are missing")
    mobile45 = read_text(crystal / "mobile" / "mobile_45_stadium.asm")
    require("ld de, s7_b000" in mobile45 and "ld bc, $1000" in mobile45,
            "Crystal Stadium download no longer targets bank 7 $B000-$BFFF")
    sram = read_text(crystal / "ram" / "sram.asm")
    require("sMobileStadiumFlag:: db" in sram and "s7_a001:: ds $799" in sram and
            "sMobileAdapterStatus2:: db" in sram and "ds $865" in sram and
            "s7_b000:: ds $fea" in sram,
            "Crystal bank-7 Mobile Stadium SRAM layout changed")
    return [
        "Crystal header BXTE",
        f"Crystal N64PS3 checksums valid (CRC 0x{stadium_crc_value:04X}, global 0x{global_checksum:04X})",
        f"Crystal upload 0x1B5 bytes, P3 marker, checksum 0x{stored:04X}",
        f"Crystal native western Friend Data valid (checksum 0x{friend_stored:04X})",
        (f"Crystal western download block at 0xF000: {replay_count} replay "
         f"and {organizer_count} organizer records (payload 0x{download_stored:04X})"),
        "Crystal stores native western 0x490 P3 replay records",
        distribution_result,
        f"Crystal Mobile Adapter pair valid at 0x8B10/0xE79A ({status1:02X}/{status2:02X})",
        "Crystal Mobile Stadium flag enabled at SRAM file offset 0xE000",
    ]


def verify_stadium_source(repo: Path) -> list[str]:
    header = read_text(repo / "include" / "mobile_stadium.h")
    overlay = read_text(repo / "src" / "mobile_stadium_overlay.c")
    require("MobileStadiumTeamSource; /* 0x1DC */" in header,
            "western replay player stride is not 0x1DC")
    require("u8 pad0[0xB];" in header and
            "u8 trainerIndex;\n    u8 controllerIndex;\n    u8 name[1];" in
            header.replace("\r\n", "\n") and "u8 padE[0x1A];" in header,
            "western replay portrait/expression/name offsets are not 0xB/0xC/0xD")
    require("MobileStadiumPokemon; /* 0x58" in header,
            "western converted Pokemon stride is not 0x58")
    require("MobileStadiumTeam; /* 0x220" in header and
            "MobileStadiumTeamCopy; /* 0x220" in header,
            "western converted team buffers are not 0x220")
    require("u8 bytes[0x16];" in header and
            "MobileStadiumBattlePlayer; /* 0x16" in header,
            "resident battle-player descriptor is not using its 0x16 stride")
    require("u8 name[0xC];\n    u8 trainerIdHi;\n    u8 trainerIdLo;" in
            header.replace("\r\n", "\n"),
            "western Fragment 7 Friend name/ID header is not preserved")
    require("u8 pad1[3];" in header and
            "dst[i].trainerId" not in overlay,
            "preview trainer ID is being written into resident team padding")
    require("sizeof(MobileStadiumTeamCopy)" in overlay and
            "_bzero(teams[i], sizeof(MobileStadiumTeamCopy));" in overlay,
            "temporary selected-team buffers do not use the western record size")
    require("_bzero(dst, sizeof(*dst));" not in overlay and
            "dst->selection = 1;" not in overlay,
            "Fragment 7's controller-owned selection field is being preloaded")
    require("func_8006A990(rules->rule);" in overlay and
            "mobile_select_friend_rule" not in overlay,
            "Friend Data no longer uses the proven western rule initializer")
    require("dst[0x0C] = raw[0x0B];" in overlay and
            "dst[0x0D] = raw[0x0C];" in overlay,
            "western Friend TID is not mapped from E00C/E00D to Fragment 7")
    require("mobile_is_egg_name" not in overlay and
            "output * 0x58" not in overlay and
            "converted[0x1D] = party[0x1F];" not in overlay and
            "extern void mobile_friend_transposer(void*, void*);" in overlay and
            "mobile_friend_transposer(source, party)" in overlay and
            "func_80051690(converted, source);" in overlay and
            "dst + 0x10 + i * 0x58" in overlay,
            "Stadium no longer preserves the last working native converter contract")
    # The portrait selector consumes the low caught-data byte after the
    # retained func_80051690 conversion. Verify that the converter contract is
    # value-preserving for both genders rather than merely matching WINTER's
    # fixture value.
    for caught_low in (0x00, 0x80, 0x85, 0xFF):
        raw_party = bytearray(0x30)
        stadium_core = bytearray(0x24)
        raw_party[0x1E] = caught_low
        stadium_core[0x21] = raw_party[0x1E]
        require(stadium_core[0x21] == caught_low,
                "western Friend OT-gender byte is not value-preserving")
    require("WINTER" not in overlay and "41574" not in overlay and
            "0xA266" not in overlay,
            "Friend identity fixture values leaked into runtime code")
    require("mobile_copy_source_teams" in overlay and
            "mobile_copy_selected_teams" in overlay,
            "removed Japanese team converters are not ported for western layouts")
    require("MobileStadiumData; /* 0x490" in header,
            "western replay object is not 0x490")
    require("s32 unk424;" in header and "u16 rule;" in header and "u16 level;" in header,
            "western shifted replay metadata fields are missing")
    require("MobileStadiumData* data;\n    u8 pad24[8];\n    MobileStadiumScreenData* screenData;" in
            header.replace("\r\n", "\n"),
            "screen-data pointer is not at retained context offset 0x2C")
    require("context->data = func_80002974" not in overlay,
            "controller replaces the retained western replay allocation")
    require("mobile_download_buffers[4]" in overlay and
            "MOBILE_CRYSTAL_DOWNLOAD_OFFSET 0xF000" in overlay,
            "international DLD0 table/Crystal download offset is missing")
    require("static s32 mobile_read_p3" in overlay and
            "mobile_hardware_read(controller, data," in overlay and
            "mobile_hardware_read_blocks(controller, data," in overlay and
            "retry < 3" in overlay and
            "if (mobile_p3_frame_valid(data))" in overlay and
            "return mobile_p3_frame_valid(data);" in overlay,
            "the DLD0 loader does not preserve the emulator path plus hardware-safe fallback")
    require("return mobile_hardware_read(controller, data, offset, length);" in overlay and
            "block += 0x20" not in overlay and
            "mobile_invalidate_mapper_bank(controller);" in overlay,
            "Mobile SRAM retries do not use the retail full-request driver path")
    require("MOBILE_REPLAY_STRIDE 0x490" in overlay and
            "MOBILE_RECORD_BASE 0xDB4" in overlay,
            "native western DLD0 layout is missing")
    require("data[0] > 3" in overlay and "data[1] > 5" in overlay and
            "mobile_validate_p3(data, 0xFFC, data + 0xFFA)" in overlay and
            "mobile_validate_p3(record, 0x48E, record + 0x48C)" in overlay and
            "mobile_validate_p3(record, 0x46, record + 0x44)" in overlay and
            "void mobile_load_download(s32 controller)" in overlay and
            "void mobile_load_downloads(void)" in overlay,
            "international DLD0 end-to-end validation is missing")
    require("_bzero(data, MOBILE_CRYSTAL_DOWNLOAD_SIZE);" in overlay,
            "invalid Crystal P3 frames are not normalized to empty data")
    load_start = overlay.index("void mobile_load_download(s32 controller)")
    load_end = overlay.index("void mobile_load_downloads(void)", load_start)
    load_body = overlay[load_start:load_end]
    require("mobile_read_p3(controller, data);" not in load_body and
            "mobile_read_friend(controller, data);" not in load_body and
            "func_80002974(" not in load_body,
            "mapped overlay still allocates or accesses the physical Transfer Pak")
    downloads_start = overlay.index("void mobile_load_downloads(void)")
    downloads_end = overlay.index(
        "static void mobile_copy_source_teams", downloads_start
    )
    downloads_body = overlay[downloads_start:downloads_end]
    require("mobile_session_load" not in overlay and
            "mobile_load_download(controller);" in downloads_body,
            "overlay does not finalize the resident-prefetched Mobile data")
    require("resident state wrapper performs physical E000/F000 reads" in overlay and
            "mobile_friend_record_valid(raw + 1)" in overlay,
            "overlay does not finalize resident-prefetched Mobile SRAM")
    def friend_valid(record: bytes) -> bool:
        return (len(record) >= 0x1B5 and record[0x1B1:0x1B3] == b"P3" and
                (sum(record[:0x1B3]) & 0xFFFF) ==
                int.from_bytes(record[0x1B3:0x1B5], "little"))

    valid_friend = bytearray(0x1B5)
    valid_friend[0] = 0x80
    valid_friend[0x1B1:0x1B3] = b"P3"
    valid_friend[0x1B3:0x1B5] = (
        sum(valid_friend[:0x1B3]) & 0xFFFF
    ).to_bytes(2, "little")
    require(friend_valid(valid_friend),
            "synthetic western Friend record does not validate")
    corrupt_friend = bytearray(valid_friend)
    corrupt_friend[0x20] ^= 1
    require(not friend_valid(bytes(0x1B5)) and
            not friend_valid(corrupt_friend),
            "absent or checksum-corrupt Friend data is accepted")
    wrong_marker = bytearray(valid_friend)
    wrong_marker[0x1B1:0x1B3] = b"XX"
    wrong_marker[0x1B3:0x1B5] = (
        sum(wrong_marker[:0x1B3]) & 0xFFFF
    ).to_bytes(2, "little")
    require(not friend_valid(wrong_marker),
            "checksum-valid data without P3 is accepted as Friend data")
    for language in ("en", "fr", "de", "it", "es"):
        messages = read_text(repo / "stadium2-localization-kit" /
                             f"{language}_msg.txt").splitlines()
        require(
            len(messages) >= 5227 and messages[5226].strip() and
            messages[5226].strip() != "#",
            f"{language}: localized Crystal activation message 5227 is missing"
        )
    download_call = overlay.index("mobile_load_downloads();")
    rules_allocate = overlay.index("rules = func_80002974(0x44, 0);")
    finish_call = overlay.index(
        "mobile_finish_scan_load(MOBILE_FRAGMENT_ID(D_81A00000)"
    )
    fragment7_call = overlay.index("MOBILE_FRAGMENT_LOAD_AND_CALL(D_83000000")
    entry_point = overlay.index("void mobile_stadium_entry(void)")
    require(download_call < rules_allocate < finish_call < fragment7_call < entry_point and
            "mobile_guarded_scan(" not in overlay and
            "func_800353B4(7, 0, 0);" not in overlay,
            "Mobile overlay scans/acquires after its TLB mapping exists")
    pool_push = overlay.index("func_80002B34('btlp');", entry_point)
    pool_pop = overlay.index("func_80002BE8('btlp');", pool_push)
    require("restartMusic" not in overlay and
            overlay.count("func_800354E4(0x41);") == 0 and
            fragment7_call < entry_point < pool_push < pool_pop,
            "Mobile battle transition contains an unsafe music interposer")
    require("data[0] = replayCount;" not in overlay and
            "data[1] = recordCount;" not in overlay,
            "the western loader still overwrites authoritative DLD0 counts")
    require("s32 mobile_download_copy(" in overlay and
            "command == 0x26" in overlay and "command == 0x28" in overlay and
            "command != 0x27" in overlay,
            "international command 0x26-0x28 copy handler is missing")
    require("index * 0x48" in overlay and "0x44);" in overlay,
            "command 0x26 must copy only the 0x44-byte rule body")
    require("index * MOBILE_REPLAY_STRIDE" in overlay and
            "MOBILE_REPLAY_STRIDE);" in overlay,
            "native western replay copy is missing")
    require("MOBILE_FRIEND_RAW_OFFSET 0x1000" in overlay and
            "MOBILE_FRIEND_RECORD_SIZE 0x1B5" in overlay and
            "MOBILE_FRIEND_VALID_OFFSET 0x11BF" in overlay and
            "MOBILE_FRIEND_TEAM_OFFSET 0x11C0" in overlay and
            "MOBILE_FRIEND_TEAM_SIZE 0x220" in overlay and
            "MOBILE_CRYSTAL_BUFFER_SIZE 0x13E0" in overlay and
            "mobile_convert_friend_team" in overlay and
            "raw + 0x91 + i * 0x30" in overlay and
            "raw + 0x0D + i * 0x0B" in overlay and
            "raw + 0x4F + i * 0x0B" in overlay and
            "mobile_friend_transposer(source, party)" in overlay and
            "func_80051690(converted, source)" in overlay and
            "static void mobile_read_friend(s32 controller" in overlay and
            "mobile_hardware_read_blocks(controller, raw, 0xE000, 0x1C0)" in overlay and
            "mobile_friend_record_valid" in overlay,
            "native western Friend Data loader is missing")
    command_source = read_text(repo / "src" / "mobile_stadium_command.s")
    require("mobile_download_copy_trampoline" in command_source and
             "mobile_download_copy_continue" in command_source,
             "download dispatcher trampoline is missing")
    require("or      $a0, $s2, $zero" in command_source and
            "lw      $a0, 0x30($sp)" not in command_source,
            "download trampoline must use func_80062390's live destination")
    require("s32 battle[4] = { 0 };" in overlay and
            "battle[1] = -1;" in overlay and
            "func_8006A990(rules->rule);" in overlay and
            "func_8006BCA4(rules->rule);" in overlay,
            "Friend battle handoff differs from the working western engine")
    tool = read_text(repo / "tools" / "mobile_stadium.py")
    require("MOBILE_ORGANIZE_LAYOUT = {" in tool and
            "def mobile_organize_patches(" in tool and
            tool.count(
                "patches.extend(mobile_organize_patches(code, source_rom, profile))"
            ) == 2,
            "the Organize Data bar layout is missing from both build paths")
    require('"mobile_friend_transposer": "rmonPrintf"' in tool and
            '"mobile_friend_transposer_cave": "__rmonSendFault"' in tool and
            "def mobile_friend_transposer(" in tool and
            "def mobile_friend_transposer_body(" in tool and
            "replacement_friend_transposer" in tool and
            "i(0x28, 4, 0, 8)" in tool and
            "i(9, 0, 8, 0x14)" in tool and
            "i(0x28, 4, 0, 1)" in tool and
            "i(0x24, 5, 9, 1), i(0x28, 4, 9, 3)" in tool and
            "i(0x24, 5, 9, 2), i(0x28, 4, 9, 4)" in tool and
            "friend_transposer_size = 0x54" in tool and
            "friend_transposer_cave_size = 0x74" in tool,
            "western Friend transposer is not installed in resident debug code")
    require("0x8B10" in tool and "0xE79A" in tool and "0xE780" in tool,
            "western Mobile Adapter status offsets are not restored")
    require("def mobile_unlock_wrapper" in tool,
            "resident Crystal-to-Stadium unlock bridge is missing")
    require('"mobile_unlock_load_record": "func_8005487C"' in tool and
            '"mobile_unlock_write_record": "func_800548C4"' in tool,
            "Mobile Stadium unlock is not using its record 0x14 API")
    require('"mobile_delivery_load_record": "func_8005493C"' in tool and
            '"mobile_delivery_write_record": "func_80054974"' in tool and
            'symbols["mobile_delivery_load_record"]' in tool and
            'symbols["mobile_delivery_write_record"]' in tool,
            "Delibird record 3 is not isolated from the menu-unlock API")
    require('"mobile_state_session": "__rmonIOhandler"' in tool and
            "def mobile_state_session_wrapper" in tool and
            "def mobile_reacquire_prefetch_wrapper" in tool and
            "restore_checkpoint_overlay_direct_load" in tool and
            "restore_checkpoint_overlay_sessionless" in tool and
            "patch_checkpoint_overlay_guarded_scan" in tool and
            "patch_checkpoint_overlay_finish_scan_load" in tool and
            'symbols["mobile_guarded_scan"]' in tool and
            'symbols["mobile_finish_scan_load"]' in tool and
            'symbols["mobile_scanner_release_call"]' in tool and
            "mobile_gbpak_release" in tool,
            "resident Mobile SRAM prefetch bridge is missing")
    require("def mobile_download_copy_wrapper" in tool and
            "Fragment 7 replaces that mapping" in tool,
            "download commands are not protected from fragment-window eviction")
    require("successful earlier scan: skip cart I/O" in tool and
            "Do not load Stadium's FlashRAM record before" in tool,
            "the session-safe Lab post-save scan guard is missing")
    require("mobile_enable_crystal" in overlay and
            "mobile_unlock_write_crystal" in overlay and "0xE000" in overlay,
            "Mobile Stadium-to-Crystal flag bridge is missing")

    asm_root = repo / "asm" / "us" / "nonmatchings"
    if asm_root.is_dir():
        replay_copy = read_text(asm_root / "fragments" / "7" /
                                "fragment7_C4C10" / "func_8300B0DC.s")
        require("24080490" in replay_copy, "regional replay list does not stride by 0x490")
        require(all(op in replay_copy for op in ("244A048C", "2479048C", "24F9048C")),
                "regional 0x490 replay copies are missing")
        selected = read_text(asm_root / "fragments" / "7" /
                             "fragment7_BA250" / "func_83007568.s")
        require("250A048C" in selected and "8DED0424" in selected and "8DEE0428" in selected,
                "selected western replay copy/metadata offsets do not match")
        allocator = read_text(asm_root / "6AA80" / "func_8006C9F4.s")
        require("24040440" in allocator and "24050440" in allocator,
                "retained western team allocation/clear is not 0x440")

    mirrors = (
        repo / "stadium2-localization-kit",
        repo / "EUR_Language_Build" / "mobile",
    )
    for mirror in mirrors:
        if not (mirror / "include" / "mobile_stadium.h").is_file():
            continue
        require((mirror / "include" / "mobile_stadium.h").read_bytes() ==
                (repo / "include" / "mobile_stadium.h").read_bytes(),
                f"stale mirrored header: {mirror}")
        mirror_overlay = read_text(mirror / "src" / "mobile_stadium_overlay.c")
        require("dst[0x0C] = raw[0x0B];" in mirror_overlay and
                "dst[0x0D] = raw[0x0C];" in mirror_overlay and
                "mobile_friend_transposer(source, party)" in mirror_overlay and
                "dst + 0x10 + i * 0x58" in mirror_overlay and
                "mobile_is_egg_name" not in mirror_overlay,
                f"stale mirrored Friend converter: {mirror}")
        require((mirror / "src" / "mobile_stadium_command.s").read_bytes() ==
                (repo / "src" / "mobile_stadium_command.s").read_bytes(),
                f"stale mirrored dispatcher trampoline: {mirror}")
    return [
        "Western player stride 0x1DC (+8 bytes per player)",
        "Resident battle-player descriptor remains 0x16 bytes in western builds",
        "Western converted Pokemon/team strides 0x58/0x220 (0x440 for two teams)",
        "Western replay object/copy size 0x490",
        "Western portrait/expression/name fields use player offsets 0x0B/0x0C/0x0D",
        "Fragment 7 trainer-ID previews match corrected DLD0 offsets 0x24/0x200",
        "Replay metadata shifted to 0x424/0x428 and rule/level to 0x488/0x48A",
        "Restored DLD0 loader reads native Western data at raw SRAM 0xF000",
        "Commands 0x26-0x28 use a resident copy handler that survives Fragment 7 loading",
        "Friend Data command 0x28 reads the native western record at raw SRAM 0xE001",
        "Friend difficulty selection uses the proven resident western rule adapter",
        "Friend party_struct native box data and Stat Experience feed Stadium's stat converter",
        "DLD0 retries use the same full logical read as retail Japanese Stadium",
        "Mobile SRAM is prefetched before the retained scanner releases the physical Pak",
        "Late Crystal-picker Friend reads are finalized before Fragment 7 queries their count",
        "A successful Crystal scan suppresses Mobile SRAM reads during later Lab rescans",
        "Crystal Mobile Mode status at 0x8B10/0xE79A unlocks Stadium record 0x14",
        "Stadium sets Crystal sMobileStadiumFlag at raw SRAM offset 0xE000",
        "The retained mapper selects MBC30 banks 4-7; no four-bank mask is introduced",
        "Battle Data menu reads the western payload description at DLD offset 0xF1C",
        "DLD byte 0xFE1 upgrades Delibird record 3 and runs its retained finalizer",
    ]


def verify_builds(repo: Path) -> list[str]:
    sys.path.insert(0, str(repo / "tools"))
    from mobile_stadium import (
        archive_resource, encode_texture, jpeg_dimensions, jpeg_payload,
        jpeg_require_stadium_layout, mobile_friend_transposer,
        mobile_friend_transposer_body, mobile_download_copy_wrapper,
        mobile_friend_copy_converter, mobile_friend_rule_wrapper,
        mobile_scanner_prefetch_wrapper, mobile_menu_unlock_wrapper,
        mobile_menu_music_wrapper, mobile_ptp0_wrapper,
        mobile_first_unlock_query_patch_info,
        mobile_organize_layout_applied,
        MOBILE_FIRST_UNLOCK_QUERY_OFFSET,
        mobile_delivery_wrapper, mobile_description_patch_info,
        n64_odd_row_dword_swap, normalize_rom, read_rgba_png, state_stub,
        yay0_decode,
    )
    from sync_localization_kit import battle_demo_jpeg

    config_paths = (
        repo / "assets" / "localization" / "mobile" / "builds.json",
        repo / "stadium2-localization-kit" / "mobile" / "assets" / "builds.json",
        repo / "EUR_Language_Build" / "mobile" / "assets" / "builds.json",
    )
    configs = [json.loads(read_text(path)) for path in config_paths]
    require(configs[0] == configs[1] == configs[2], "the three builds.json copies differ")
    config = configs[0]
    require(tuple(config) == LANGUAGES, "builds.json language order/set differs")

    help_names = {
        55: "archive13_055_jpeg_144x96.jpg",
        **{
            index: f"archive13_{index:03d}_jpeg_144x112.jpg"
            for index in (56, 57, 58)
        },
    }
    for asset_language in LANGUAGES:
        primary = repo / "assets" / "localization" / "mobile" / "graphics" / asset_language
        portable = (repo / "stadium2-localization-kit" / "mobile" /
                    "assets" / "graphics" / asset_language)
        eur = (repo / "EUR_Language_Build" / "mobile" /
               "assets" / "graphics" / asset_language)
        incoming = repo.parent / "NewGraphics" / "mobile" / "assets" / "graphics" / asset_language
        legacy = portable / "archive1_033_000A70_ia8_24x26_1.jpg"
        require(not legacy.exists(),
                f"{asset_language}: obsolete misnamed Battle Data screenshot remains")
        for name in help_names.values():
            image = (primary / name).read_bytes()
            require(len(image) > 1000 and image[:2] == b"\xFF\xD8" and
                    image[-2:] == b"\xFF\xD9",
                    f"{asset_language}: missing/non-image Mobile help asset {name}")
            jpeg_require_stadium_layout(image, f"{asset_language}/{name}")
            portable_image = (portable / name).read_bytes()
            if name == "archive13_055_jpeg_144x96.jpg":
                require(jpeg_dimensions(portable_image) == (144, 96),
                        f"{asset_language}: editable resource 55 has wrong dimensions")
                expected = battle_demo_jpeg(portable / name, asset_language)
                require(image == expected,
                        f"{asset_language}: resource 55 ROM conversion is stale")
            else:
                require(portable_image == image,
                        f"{asset_language}: stale portable Mobile help asset {name}")
            require((eur / name).read_bytes() == image,
                    f"{asset_language}: stale EUR Mobile help asset {name}")
            incoming_image = (incoming / name).read_bytes()
            require(len(incoming_image) > 1000 and
                    incoming_image[:2] == b"\xFF\xD8" and
                    incoming_image[-2:] == b"\xFF\xD9",
                    f"{asset_language}: missing NewGraphics Mobile help asset {name}")

    results = [
        "Archive 13 help screenshots 55-58 are editable in every Western asset set",
        "Archive 1 resource 99 battle title is embedded from every Western PNG",
        "Localization-kit Mobile JSON strings are embedded in every Western ROM",
        "Italian Archive 1 resource 33 icon is embedded from its editable PNG",
        "Physical Transfer Pak reads run before the dedicated session release",
        "Friend E000 blocks survive the menu pool reset in persistent resident BSS",
    ]
    for language in LANGUAGES:
        text_language = "en" if language == "au" else language
        override_language = "en" if language in {"us", "au"} else language
        text_document = json.loads(read_text(
            repo / "assets" / "localization" / "mobile" / "text" /
            f"{text_language}.json"
        ))
        if language != "us":
            kit_text = json.loads(read_text(
                repo / "stadium2-localization-kit" / "mobile" / "assets" /
                "text" / f"{text_language}.json"
            ))
            require(text_document == kit_text,
                    f"{text_language}: localization-kit Mobile JSON is not synchronized")
        extra_files = {
            item["text_file"]: item for item in
            text_document.get("additional_text_files", [])
        }
        require(25 in extra_files and
                {item["index"] for item in extra_files[25]["entries"]} == {0, 14, 18},
                f"{language}: GB Game Pak Mobile strings are not editable in JSON")
        name = "pokestadiumgs-ntsc-en.z64" if language == "us" else f"pokestadiumgs-pal-{language}.z64"
        path = repo / "build" / "mobile-stadium" / name
        rom = path.read_bytes()
        require(len(rom) == ROM_SIZE, f"{name}: expected 64 MiB, got {len(rom)}")
        text_blocks = [{
            "text_file": text_document["text_file"],
            "entries": text_document["entries"],
        }] + text_document.get("additional_text_files", [])
        override_path = (
            repo / "assets" / "localization" / "mobile" /
            "text-overrides" / f"{override_language}.json"
        )
        if override_path.exists():
            text_blocks += json.loads(read_text(override_path)).get("text_files", [])
        # The importer applies generated xx_msg.txt overrides after the base
        # localization JSON. Validate the same last-writer-wins view here.
        expected_text_entries = {}
        for block in text_blocks:
            for item in block["entries"]:
                expected_text_entries[(block["text_file"], item["index"])] = item["text"]
        text_resources = {}
        for (text_file, index), text in expected_text_entries.items():
            resource = text_resources.setdefault(
                text_file, archive_resource(rom, 2, text_file)
            )
            count = int.from_bytes(resource[:4], "big")
            require(index < count,
                    f"{name}: text file {text_file} lacks entry {index}")
            start = int.from_bytes(
                resource[4 + index * 4:8 + index * 4], "big"
            )
            end = resource.find(b"\0", start)
            expected = text.replace(r"\n", "\n").encode("cp1252")
            require(end >= 0 and resource[start:end] == expected,
                    f"{name}: Mobile text file {text_file} "
                    f"entry {index} was not imported")
        graphics_language = "en" if language == "au" else language
        for resource_index, help_name in help_names.items():
            expected_help = (
                repo / "assets" / "localization" / "mobile" / "graphics" /
                graphics_language / help_name
            ).read_bytes()
            embedded_help = jpeg_payload(
                archive_resource(rom, 13, resource_index)
            )
            require(embedded_help == expected_help,
                    f"{name}: Archive 13 help screenshot {resource_index} was not imported")
        title_name = "archive1_099_000010_ia8_320x37.png"
        title_path = (
            repo / "assets" / "localization" / "mobile" / "graphics" /
            graphics_language / title_name
        )
        width, height, pixels = read_rgba_png(title_path)
        require((width, height) in {(320, 36), (320, 37)},
                f"{name}: battle-title PNG has wrong dimensions")
        if height == 36:
            pixels += bytes(width * 4)
            height = 37
        expected_title = n64_odd_row_dword_swap(
            encode_texture(pixels, "ia8"), width, height
        )
        packed_title = archive_resource(rom, 1, 99)
        require(packed_title[:8] == b"PERS-SZP",
                f"{name}: Archive 1 resource 99 is not PERS-SZP")
        title_resource = yay0_decode(packed_title[0x18:])
        require(title_resource[0x10:0x10 + len(expected_title)] == expected_title,
                f"{name}: Archive 1 resource 99 battle title was not imported")
        if language == "it":
            icon_name = "archive1_033_000A70_ia8_24x26.png"
            icon_path = (
                repo / "assets" / "localization" / "mobile" / "graphics" /
                "it" / icon_name
            )
            width, height, pixels = read_rgba_png(icon_path)
            expected_icon = n64_odd_row_dword_swap(
                encode_texture(pixels, "ia8"), width, height
            )
            packed = archive_resource(rom, 1, 33)
            require(packed[:8] == b"PERS-SZP",
                    f"{name}: Archive 1 resource 33 is not PERS-SZP")
            resource = yay0_decode(packed[0x18:])
            require(resource[0xA70:0xA70 + len(expected_icon)] == expected_icon,
                    f"{name}: updated Italian Archive 1 resource 33 icon was not imported")
        crystal_id = config[language]["crystal_id"].encode("ascii")
        service_url = config[language]["service_url"].encode("ascii")
        require(rom.count(crystal_id) >= 2, f"{name}: missing Crystal ID {crystal_id.decode()}")
        require(rom.count(service_url) == 1, f"{name}: service URL missing or duplicated")
        profile = json.loads(read_text(
            repo / "assets" / "localization" / "mobile" / "profiles" /
            f"{language}.json"
        ))
        source_rom = normalize_rom(
            (repo.parent / profile["source_rom"]).read_bytes()
        )
        symbols = {key: int(value, 16) for key, value in profile["symbols"].items()}
        require("mobile_friend_counts" in symbols,
                f"{name}: Friend Data count array is missing from the profile")
        require("mobile_delivery_select_record" in symbols and
                "mobile_delivery_finalize_record" in symbols and
                "mobile_delivery_cave" in symbols and
                "mobile_delivery_cave_end" in symbols,
                f"{name}: Delibird delivery consumer is missing from the profile")
        copy_table = symbols["mobile_download_copy_table"] - 0x80000400 + 0x1000
        copy_targets = [
            int.from_bytes(rom[copy_table + index * 4:copy_table + index * 4 + 4], "big")
            for index in (6, 7, 8)
        ]
        expected_copy_target = symbols["mobile_download_copy_cave"]
        require(copy_targets == [expected_copy_target] * 3,
                f"{name}: commands 0x26-0x28 do not route to the resident "
                "replay-copy handler")
        copy_cave = expected_copy_target - 0x80000400 + 0x1000
        copy_handler = rom[copy_cave:copy_cave + 0x280]
        continuation_jump = (
            2 << 26 |
            ((symbols["mobile_download_copy_continue"] >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        expected_resident_copy = (
            mobile_download_copy_wrapper(profile, 0x100) +
            mobile_friend_copy_converter(profile, 0x180)
        )
        require(copy_handler == expected_resident_copy and
                copy_handler.count(continuation_jump) == 3,
                f"{name}: resident replay-copy handler is malformed")
        fragment7_start = int(profile["fragments"]["7"], 16)
        fragment7_end = int(profile["fragments"]["8"], 16)
        fragment7 = rom[fragment7_start:fragment7_end]
        require(fragment7.count(bytes.fromhex("95870024")) == 1 and
                fragment7.count(bytes.fromhex("95A70200")) == 1 and
                fragment7.count(bytes.fromhex("95670024")) == 1 and
                bytes.fromhex("95870026") not in fragment7 and
                bytes.fromhex("95A70202") not in fragment7 and
                bytes.fromhex("95670026") not in fragment7,
                f"{name}: trainer-ID preview offsets do not match corrected DLD0")
        local_portrait_prefix = bytes.fromhex(
            "8e0e00383c010003342101208dcf0014241800010201202124050040"
        )
        local_portrait = fragment7.find(local_portrait_prefix)
        require(local_portrait >= 0 and
                fragment7.find(local_portrait_prefix, local_portrait + 4) < 0,
                f"{name}: Fragment 7 local-player portrait request is ambiguous")
        local_portrait_call = local_portrait + len(local_portrait_prefix)
        portrait_handoff = bytes.fromhex("01e03025")
        require((int.from_bytes(
                    fragment7[local_portrait_call:local_portrait_call + 4], "big"
                ) >> 26) == 3 and
                fragment7[local_portrait_call + 4:local_portrait_call + 8] ==
                    portrait_handoff and
                bytes.fromhex("34212128") in
                    fragment7[local_portrait_call + 8:local_portrait_call + 0x30] and
                bytes.fromhex("24060001") in
                    fragment7[local_portrait_call + 8:local_portrait_call + 0x30],
                f"{name}: Fragment 7 regional portrait/team handoff is wrong")
        # The Organize Data option bar is retail fragment 7 data: four label
        # positions in the text entries and four hand-cursor positions in the
        # item array, compacted per script so long official translations fit.
        # Assert the compacted layout is present and no retail coordinate
        # survives, which is what a silently dropped patch would look like.
        require(mobile_organize_layout_applied(fragment7, language),
                f"{name}: Organize Data option bar is not this build's "
                "compacted layout")
        friend_battle_gender = bytes.fromhex(
            "2402004010800003acc2000010000004ac700000"
            "8e49001401205025ac6a0000"
        )
        inverted_friend_battle_gender = bytes.fromhex(
            "2402004010800003acc2000010000004ac700000"
            "8e4900142d2a0001ac6a0000"
        )
        native_friend_battle_gender = bytes.fromhex(
            "2402004010800003acc2000010000003ac700000"
            "8e490014ac690000"
        )
        require(rom.count(friend_battle_gender) +
                    rom.count(native_friend_battle_gender) == 1 and
                inverted_friend_battle_gender not in rom,
                f"{name}: retained Friend battle screens invert the local "
                "Crystal portrait gender")
        hook = symbols["mobile_unlock_scan"] + 0x308 - 0x80000400 + 0x1000
        cave = symbols["mobile_unlock_cave"] - 0x80000400 + 0x1000
        expected_hook = (
            3 << 26 | ((symbols["mobile_unlock_cave"] >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big") + bytes.fromhex("93a40093")
        require(rom[hook:hook + 8] == expected_hook,
                f"{name}: Crystal unlock scanner hook is missing")
        resident = rom[cave:cave + 0xD0]
        reverse_unlock_call = (
            3 << 26 |
            (((symbols["mobile_chunk_cave"] + 0xB0) >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        require(bytes.fromhex("34068b00") in resident and
                bytes.fromhex("3406e780") in resident and
                bytes.fromhex("93a80030") in resident and
                bytes.fromhex("93a9005a") in resident and
                bytes.fromhex("3406e000") not in resident and
                reverse_unlock_call not in resident,
                f"{name}: western Crystal recognition offsets are missing or the "
                "shared scanner still pre-consumes Crystal's first entry")
        load_record_call = (
            3 << 26 | ((symbols["mobile_unlock_load_record"] >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        first_status_read = resident.find(bytes.fromhex("27a5002034068b00"))
        second_status_read = resident.find(bytes.fromhex("27a500403406e780"))
        record_load = resident.find(load_record_call)
        require(resident.startswith(bytes.fromhex(
                    "27bdff90afbf006c00808025"
                )) and 0 <= first_status_read < second_status_read < record_load and
                resident[0xCC] == 0,
                f"{name}: session guard or Crystal-before-FlashRAM ordering is missing")
        crystal_write_call = (
            3 << 26 | ((symbols["mobile_unlock_write_crystal"] >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        require(symbols["mobile_unlock_write_crystal"] != symbols["mobile_unlock_read"] + 0x60,
                f"{name}: raw Transfer Pak write primitive is unsafe")
        require(bytes.fromhex("97a2001234420004a7a20012") in resident and
                bytes.fromhex("93a8001535080001a3a80015") not in resident,
                f"{name}: scanner must set availability without pre-consuming "
                "the first-unlock notification")
        expected_continue = bytes.fromhex("27bd0070") + (
            2 << 26 | ((symbols["mobile_unlock_continue"] >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big") + bytes.fromhex("02002025")
        require(expected_continue in resident,
                f"{name}: Crystal scanner continuation tail is incorrect")
        chunk_cave = symbols["mobile_chunk_cave"]
        chunk_call = (
            3 << 26 | ((chunk_cave >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        for call_address in (
            symbols["mobile_box_read"] + 0x60,
            symbols["mobile_full_save_read"] + 0x24,
        ):
            call_offset = call_address - 0x80000400 + 0x1000
            require(rom[call_offset:call_offset + 4] == chunk_call,
                    f"{name}: large Crystal SRAM read is not routed through the resident trampoline")
        cave_offset = chunk_cave - 0x80000400 + 0x1000
        cave = rom[cave_offset:cave_offset + 0x108]
        require(cave[:4] == bytes.fromhex("27bdffd0") and
                cave.count(bytes.fromhex("24141000")) == 1 and
                cave[0xB0:0xB4] == bytes.fromhex("27bdffd0") and
                bytes.fromhex("3406e000") in cave[0xB0:] and
                crystal_write_call in cave[0xB0:],
                f"{name}: resident Crystal read/reverse-unlock helpers are missing")
        transposer_offset = (
            symbols["mobile_friend_transposer"] - 0x80000400 + 0x1000
        )
        transposer_cave = symbols["mobile_friend_transposer_cave"]
        transposer = mobile_friend_transposer(
            symbols["mobile_friend_transposer"], transposer_cave, 0x54
        )
        require(rom[transposer_offset:transposer_offset + len(transposer)] == transposer,
                f"{name}: western Friend Data transposer entry is missing")
        transposer_cave_offset = transposer_cave - 0x80000400 + 0x1000
        transposer_body = mobile_friend_transposer_body(0x74)
        require(rom[transposer_cave_offset:
                    transposer_cave_offset + len(transposer_body)] ==
                transposer_body,
                f"{name}: western Friend Data layout converter is missing")
        overlay_blob = rom[OVERLAY_ROM:OVERLAY_ROM + 0x4000]
        require(
            bytes.fromhex(
                "afa7000c8cae002024050002000010250080182500c03825"
            ) in overlay_blob or bytes.fromhex(
                "02a0202502002825"
            ) in overlay_blob,
            f"{name}: Fragment 7's checkpoint controller-selection path "
            "is not preserved",
        )
        pal_count_clear = (
            language != "us" and
            bytes.fromhex("a2000000a2000001") in overlay_blob and
            bytes.fromhex("240510001000fcbb02202025") not in overlay_blob
        )
        us_clear = (
            bytes.fromhex("1000035202002025") in overlay_blob and
            bytes.fromhex("240510001000fcac00000000") in overlay_blob
        )
        sealed_us_clear = (
            bytes.fromhex("1000033502002025") in overlay_blob and
            bytes.fromhex("240510001000fcc902002825") in overlay_blob
        )
        # A fresh IDO build can keep the validated download pointer in s0
        # and emit the source-level `_bzero(data, 0x1000)` directly.  Older
        # checked-in overlays reached the same operation through one of the
        # migrated branch arms above.  Verify the direct form by its complete
        # call setup and the subsequent retained Friend-valid-byte clear,
        # rather than depending on a compiler-specific branch displacement.
        bzero_call = (
            3 << 26 | ((symbols["_bzero"] >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        native_clear_setup = bytes.fromhex("02002025") + bzero_call + bytes.fromhex("24051000")
        native_clear_at = overlay_blob.find(native_clear_setup)
        native_us_clear = (
            native_clear_at >= 0 and
            bytes.fromhex("a20011bf") in
            overlay_blob[native_clear_at:native_clear_at + 0x60]
        )
        require(
            pal_count_clear or us_clear or sealed_us_clear or native_us_clear,
            f"{name}: invalid Crystal P3 frames are not normalized safely",
        )
        session_address = symbols["mobile_state_session"]
        session_offset = session_address - 0x80000400 + 0x1000
        session_size = symbols["mobile_download_copy_cave"] - session_address
        session = rom[session_offset:session_offset + session_size]
        fragment_size = int.from_bytes(
            rom[OVERLAY_ROM + 0x18:OVERLAY_ROM + 0x1C], "big"
        )
        overlay_file_end = int.from_bytes(
            rom[OVERLAY_ROM + 0x14:OVERLAY_ROM + 0x18], "big"
        )
        overlay_bss_end = int.from_bytes(
            rom[OVERLAY_ROM + 0x1C:OVERLAY_ROM + 0x20], "big"
        )
        require(fragment_size >= OVERLAY_MIN_FILE_SIZE and
                overlay_bss_end >= OVERLAY_MIN_FILE_SIZE,
                f"{name}: Mobile overlay does not map both controller pages")
        if overlay_file_end == 0x16C8:
            # FriendDataReads/Saturday-checkpoint compiler layout.  The
            # historical loader at 0x940 used to allocate and perform Pak
            # reads while executing from the 0x84500000 fragment, evicting
            # itself and faulting on return at 0x84500934.  It must instead
            # look up the resident prefetched buffer; its E000/F000 read JALs
            # and their R_MIPS_26 relocations must both be absent.
            legacy_lookup = bytes.fromhex(
                "8fa40020000470803c018013002e08218c308730"
                "12000014000000001000000800000000"
            )
            require(overlay_blob[0x950:0x950 + len(legacy_lookup)] ==
                    legacy_lookup and
                    overlay_blob[0x5E8:0x5EC] == bytes.fromhex("00001025") and
                    overlay_blob[0x6DC:0x6E0] == bytes.fromhex("00001025"),
                    f"{name}: legacy mapped loader can still touch the Transfer Pak")
            relocation_count = int.from_bytes(
                overlay_blob[overlay_file_end:overlay_file_end + 4], "big"
            )
            relocation_entries = [
                int.from_bytes(
                    overlay_blob[overlay_file_end + 4 + i * 4:
                                 overlay_file_end + 8 + i * 4], "big"
                )
                for i in range(relocation_count)
            ]
            require(not any(
                        entry >> 24 == 4 and
                        (entry & 0x00FFFFFF) in (0x5E8, 0x6DC)
                        for entry in relocation_entries
                    ),
                    f"{name}: removed legacy Pak reads still have relocations")
        elif overlay_file_end == 0x1638:
            # Cached PAL IDO layout.  The resident-buffer finalizer must run
            # before the first retained allocator.  Overlay-internal transfers
            # must also be PC-relative: injected absolute J/JAL instructions
            # have no relocation entries and retain invalid 0x84500000 link
            # addresses when the PAL loader moves this fragment at runtime.
            def overlay_jal(address: int) -> bytes:
                return (
                    3 << 26 | ((address >> 2) & 0x03FFFFFF)
                ).to_bytes(4, "big")

            def overlay_jump(address: int) -> bytes:
                return (
                    2 << 26 | ((address >> 2) & 0x03FFFFFF)
                ).to_bytes(4, "big")

            def overlay_relative(source: int, target: int,
                                 instruction: int) -> bytes:
                delta = target - (source + 4)
                require(delta & 3 == 0 and -0x20000 <= delta < 0x20000,
                        f"{name}: PAL relative bridge is out of range")
                return (instruction | ((delta >> 2) & 0xFFFF)).to_bytes(
                    4, "big"
                )

            def overlay_bal(source: int, target: int) -> bytes:
                return overlay_relative(source, target, 0x04110000)

            def overlay_branch(source: int, target: int) -> bytes:
                return overlay_relative(source, target, 0x10000000)

            wrapper = overlay_blob[0x170:0x188]
            require(
                overlay_blob[0xC80:0xC84] == overlay_bal(0xC80, 0x170) and
                overlay_blob[0xD18:0xD1C] == bytes(4) and
                wrapper[0:4] == overlay_bal(0x170, 0x934) and
                wrapper[4:8] == bytes(4) and
                wrapper[8] >> 2 == 3 and
                wrapper[12:16] == bytes(4) and
                wrapper[16:20] == overlay_branch(0x180, 0xC88) and
                wrapper[20:24] == bytes(4),
                f"{name}: PAL finalizer bridge is not relocation-safe"
            )
            relocation_count = int.from_bytes(
                overlay_blob[overlay_file_end:overlay_file_end + 4], "big"
            )
            relocation_entries = {
                int.from_bytes(overlay_blob[offset:offset + 4], "big")
                for offset in range(
                    overlay_file_end + 4,
                    overlay_file_end + 4 + relocation_count * 4,
                    4,
                )
            }
            for offset in range(0x20, overlay_file_end, 4):
                word = int.from_bytes(overlay_blob[offset:offset + 4], "big")
                if word >> 26 not in (2, 3):
                    continue
                target = 0x80000000 | ((word & 0x03FFFFFF) << 2)
                if OVERLAY_CODE <= target < OVERLAY_VRAM + overlay_file_end:
                    require((4 << 24) | offset in relocation_entries,
                            f"{name}: overlay J/JAL at 0x{offset:X} lacks R_MIPS_26")
        require(session == mobile_scanner_prefetch_wrapper(
                    profile, symbols["func_80065748"], session_size
                ),
                f"{name}: scanner prefetch promotion wrapper is missing")
        require(bytes.fromhex(
                    "14400004924800002d01000414200002"
                    "00000000a6400000"
                ) in session,
                f"{name}: uninitialized F000 counts reach the Battle Data UI")
        require(symbols["mobile_friend_raw_cache"] ==
                symbols["mobile_transfer_contexts"] + 0x6A90,
                f"{name}: persistent Friend cache is outside reserved RMON BSS")
        stub_offset = int(profile["state18_rom_offset"], 16)
        require(rom[stub_offset:stub_offset + 0x48] == state_stub(
                    symbols["mobile_state_session"], OVERLAY_ROM,
                    OVERLAY_ROM + fragment_size, 0x48
                ),
                f"{name}: Mobile entry bypasses the Friend-cache promotion")

        def jal_bytes(address: int) -> bytes:
            return (
                3 << 26 | ((address >> 2) & 0x03FFFFFF)
            ).to_bytes(4, "big")

        def jump_bytes(address: int) -> bytes:
            return (
                2 << 26 | ((address >> 2) & 0x03FFFFFF)
            ).to_bytes(4, "big")

        # Compiler scheduling and ordinary source edits move these calls, so
        # verify their relationship instead of pinning them to one historical
        # mobile.raw.bin layout.  Scan/acquire belongs in the resident state
        # wrapper; the mapped overlay may only validate/copy the prefetched
        # data and load the next fragment.  No Pak remains acquired here.
        guarded_scan_call = jal_bytes(symbols["mobile_guarded_scan"])
        premap_setup = (
            jal_bytes(symbols["func_800354E4"]) + bytes.fromhex("24040041")
        )
        finish_scan_call = jal_bytes(symbols["mobile_finish_scan_load"])
        legacy_finish_calls = [
            jal_bytes(symbols["mobile_state_session"] + offset)
            for offset in (0x180, 0x280, 0x2C0)
            if symbols["mobile_state_session"] + offset !=
               symbols["mobile_finish_scan_load"]
        ]
        fragment_load_call = jal_bytes(symbols["func_800047D8"])
        overlay_map_call = jal_bytes(symbols["func_80065748"])
        # Only inspect executable bytes.  Searching the entire reserved ROM
        # tail allowed unrelated data after the fragment to satisfy these
        # call checks, masking stale linker symbols in emitted overlays.
        overlay_code = overlay_blob[0x20:overlay_file_end]
        require(not any(call in overlay_code for call in legacy_finish_calls),
                f"{name}: Mobile overlay calls a stale resident finish entry")
        if language != "us":
            # The cached PAL controller must use the resident epilogue.  Its
            # historical +0x2c0 address now falls in the scanner restore path;
            # treating a later ordinary fragment load as the finalizer masked
            # the resulting unmapped 0x81a00020 call in earlier verification.
            require(finish_scan_call in overlay_code,
                    f"{name}: PAL Mobile overlay bypasses the live resident "
                    "finish entry")
        overlay_map_offsets = [
            offset for offset in range(0, len(overlay_code), 4)
            if overlay_code[offset:offset + 4] == overlay_map_call
        ]
        overlay_map_offset = -1
        finish_scan_offset = overlay_code.find(finish_scan_call)
        if finish_scan_offset < 0:
            # The retained checkpoint image calls the retail finalizer
            # directly.  Identify that particular call by its position just
            # before the next fragment-map call; unrelated command handlers
            # also use func_800047D8 earlier in the overlay.
            direct = [
                offset for offset in range(0, len(overlay_code), 4)
                if overlay_code[offset:offset + 4] == fragment_load_call
            ]
            pairs = [
                (finish, mapped)
                for mapped in overlay_map_offsets
                for finish in direct
                if 0 < mapped - finish <= 0x40
            ]
            if pairs:
                finish_scan_offset, overlay_map_offset = pairs[0]
        elif overlay_map_offsets:
            overlay_map_offset = next(
                (mapped for mapped in overlay_map_offsets
                 if mapped > finish_scan_offset), -1
            )
        setup_offsets = [
            offset for offset in range(0, len(overlay_code) - 7, 4)
            if overlay_code[offset:offset + 8] == premap_setup
        ]
        pool_push_call = jal_bytes(symbols["func_80002B34"])
        menu_music_call = jal_bytes(symbols["mobile_music_cave"])
        pool_push_offsets = [
            offset for offset in range(8, len(overlay_code) - 4, 4)
            if overlay_code[offset:offset + 4] == menu_music_call and (
                overlay_code[offset - 8:offset] == bytes.fromhex(
                    "3c04627434846c70"
                ) or (
                    overlay_code[offset - 4:offset] == bytes.fromhex(
                        "3c046274"
                    ) and
                    overlay_code[offset + 4:offset + 8] == bytes.fromhex(
                        "34846c70"
                    )
                )
            )
        ]
        pool_pop_call = jal_bytes(symbols["func_80002BE8"])
        pool_pop_offsets = [
            offset for offset in range(8, len(overlay_code) - 4, 4)
            if overlay_code[offset:offset + 4] == pool_pop_call and (
                overlay_code[offset - 8:offset] == bytes.fromhex(
                    "3c04627434846c70"
                ) or (
                    overlay_code[offset - 4:offset] == bytes.fromhex(
                        "3c046274"
                    ) and
                    overlay_code[offset + 4:offset + 8] == bytes.fromhex(
                        "34846c70"
                    )
                )
            )
        ]
        final_pop_offsets = [
            offset for offset in range(8, len(overlay_code) - 4, 4)
            if overlay_code[offset:offset + 4] == pool_pop_call and (
                overlay_code[offset - 8:offset] == bytes.fromhex(
                    "3c04627434847063"
                ) or (
                    overlay_code[offset - 4:offset] == bytes.fromhex(
                        "3c046274"
                    ) and
                    overlay_code[offset + 4:offset + 8] == bytes.fromhex(
                        "34847063"
                    )
                )
            )
        ]
        sequence_41 = (
            jal_bytes(symbols["func_800354E4"]) + bytes.fromhex("24040041")
        )
        music_cave_address = symbols["mobile_music_cave"]
        music_cave_end = symbols["mobile_music_cave_end"]
        music_cave_offset = music_cave_address - 0x80000400 + 0x1000
        require(rom[music_cave_offset:
                    music_cave_offset + music_cave_end - music_cave_address] ==
                mobile_menu_music_wrapper(
                    profile, music_cave_end - music_cave_address
                ),
                f"{name}: resident Mobile menu-music wrapper is missing")
        require(len(pool_push_offsets) == 1 and
                len(pool_pop_offsets) == 1 and
                len(final_pop_offsets) == 1 and
                pool_push_offsets[0] < pool_pop_offsets[0] <
                final_pop_offsets[0] and
                overlay_code[pool_push_offsets[0]:pool_push_offsets[0] + 4] ==
                menu_music_call and
                overlay_code[pool_pop_offsets[0]:pool_pop_offsets[0] + 4] ==
                pool_pop_call and
                overlay_code[final_pop_offsets[0]:final_pop_offsets[0] + 4] ==
                pool_pop_call and
                sequence_41 not in overlay_code and
                bytes.fromhex("241effff") not in overlay_code,
                f"{name}: battle transition does not preserve checkpoint pool lifecycle")
        require(overlay_code.find(guarded_scan_call) < 0 and
                finish_scan_offset >= 0 and
                overlay_map_offset > finish_scan_offset,
                f"{name}: Mobile overlay has an invalid mapped setup/Pak call")
        require(bytes.fromhex("8e2e000055c0000426100001") not in overlay_code,
                f"{name}: missing prefetch can reacquire the Pak from the mapped overlay")
        scanner_release_offset = (
            symbols["mobile_scanner_release_call"] - 0x80000400 + 0x1000
        )
        scanner_release_call = jal_bytes(symbols["mobile_scan_release_hook"])
        require(rom[scanner_release_offset:scanner_release_offset + 4] ==
                scanner_release_call,
                f"{name}: scanner does not route through the pre-release cache hook")
        acquire_call = jal_bytes(symbols["mobile_gbpak_acquire"])
        ready_call = jal_bytes(symbols["mobile_gbpak_ready"])
        release_call = jal_bytes(symbols["mobile_gbpak_release"])
        page_read_call = jal_bytes(symbols["mobile_unlock_read"])
        overlay_call = jal_bytes(symbols["func_80065748"])
        scan_offset = symbols["mobile_data_scan"] - 0x80000400 + 0x1000
        scanner = rom[scan_offset:scan_offset + 0x110]
        acquire_at = scanner.find(acquire_call)
        hook_at = scanner.find(scanner_release_call, acquire_at + 4)
        ready_at = session.find(ready_call, 0x140)
        first_read_at = session.find(page_read_call, ready_at + 4)
        second_read_at = session.find(page_read_call, first_read_at + 4)
        release_at = session.find(release_call, second_read_at + 4)
        overlay_at = session.find(overlay_call, 0)
        require(0 <= acquire_at < hook_at and
                0 <= overlay_at < 0x140 <= ready_at < first_read_at <
                second_read_at < release_at,
                f"{name}: physical Pak lifecycle is not acquire/ready/read/read/release")
        crystal_write_at = session.find(crystal_write_call, first_read_at + 4)
        require(crystal_write_at < 0 or crystal_write_at > release_at,
                f"{name}: ordinary Mobile scan consumes Crystal's first-use "
                "flag before the retained popup callback")
        ptp0_cave_address = symbols["mobile_ptp0_cave"]
        ptp0_cave_end = symbols["mobile_ptp0_cave_end"]
        ptp0_cave_offset = ptp0_cave_address - 0x80000400 + 0x1000
        ptp0_size = ptp0_cave_end - ptp0_cave_address
        ptp0_helper = rom[ptp0_cave_offset:ptp0_cave_offset + ptp0_size]
        require(
            ptp0_helper == mobile_ptp0_wrapper(profile, ptp0_size),
            f"{name}: native Crystal PTP0 notification object builder is missing"
        )
        query = ptp0_helper[MOBILE_FIRST_UNLOCK_QUERY_OFFSET:]
        scanner_mask = symbols["mobile_scanner_active_mask"]
        scanner_mask_hi = (scanner_mask + 0x8000) >> 16
        scanner_mask_lo = scanner_mask & 0xFFFF
        scanner_mask_load = struct.pack(
            ">2I",
            0x3C080000 | scanner_mask_hi,
            0x8D080000 | scanner_mask_lo,
        )
        require(scanner_mask == symbols["mobile_transfer_contexts"] + 0x1D8 and
                scanner_mask_load in query,
                f"{name}: first-use popup callback does not use the Japanese "
                "32-bit scanner controller mask")
        query_acquire_at = query.find(acquire_call)
        query_read_at = query.find(page_read_call, query_acquire_at + 4)
        query_write_at = query.find(crystal_write_call, query_read_at + 4)
        query_release_at = query.find(release_call, query_write_at + 4)
        require(0 <= query_acquire_at < query_read_at < query_write_at <
                query_release_at,
                f"{name}: first-use popup callback does not perform the "
                "Japanese acquire/read/write/release transaction")
        first_unlock_query_call_offset, _ = (
            mobile_first_unlock_query_patch_info(source_rom)
        )
        first_unlock_query_address = (
            ptp0_cave_address + MOBILE_FIRST_UNLOCK_QUERY_OFFSET
        )
        require(
            rom[
                first_unlock_query_call_offset:
                first_unlock_query_call_offset + 4
            ] == jal_bytes(first_unlock_query_address),
            f"{name}: retained Game Pak selector does not call the "
            "one-shot Crystal first-entry notification query"
        )
        # PTP0 is the fixed picker-status object, not western Friend Data.
        # Match Japanese func_80059D94's 0x180-byte allocation/copy and ensure
        # no P3/footer gate can reject a fresh E000=0 save.
        require(bytes.fromhex("24040180") in ptp0_helper and
                bytes.fromhex("24060180") in ptp0_helper and
                bytes.fromhex("8e22006c") in ptp0_helper and
                bytes.fromhex("24090172") not in ptp0_helper and
                bytes.fromhex("924b0171") not in ptp0_helper and
                bytes.fromhex("924b0172") not in ptp0_helper and
                bytes.fromhex("924b0173") not in ptp0_helper and
                bytes.fromhex("01104021") in ptp0_helper and
                bytes.fromhex("a10901d4") in ptp0_helper and
                bytes.fromhex("a1090168") not in ptp0_helper and
                bytes.fromhex("240901b3") not in ptp0_helper,
                f"{name}: PTP0 helper does not match the native 0x180-byte "
                "status/refresh contract")
        e000_call_offset = (
            symbols["mobile_e000_wrapper_call"] - 0x80000400 + 0x1000
        )
        require(
            rom[e000_call_offset:e000_call_offset + 4] ==
            jal_bytes(ptp0_cave_address),
            f"{name}: E000 picker wrapper does not create the PTP0 notification"
        )
        # The scanner passes the release-state byte itself in a1.  A previous
        # hook treated it as an address, causing the address-0/1 TLB faults
        # seen on Mobile Stadium entry.
        require(bytes.fromhex("92290000") not in session[0x140:release_at],
                f"{name}: scanner hook dereferences the release-state byte")
        cache = symbols["mobile_transfer_bank_cache"]
        cache_hi = ((cache + 0x8000) >> 16) & 0xFFFF
        cache_lo = cache & 0xFFFF
        cache_store = (0xA02E0000 | cache_lo).to_bytes(4, "big")
        cache_store_at = rom.find(cache_store)
        # Physical Mobile reads now run in the resident live-Pak session,
        # before the scanner releases the Transfer Pak and maps this overlay.
        # Verify the resident driver's explicit mapper-cache invalidation and
        # its F000 request there.  The address-index register used by IDO is
        # allocation-dependent, so only the surrounding LUI and final store
        # are stable across fresh regional compilations.
        require(cache_hi == 0x8013 and cache_store_at >= 0 and
                bytes.fromhex("3c018013") in
                rom[max(0, cache_store_at - 0x10):cache_store_at] and
                bytes.fromhex("3406f000") in session,
                f"{name}: hardware-synchronised Mobile SRAM read is missing")
        friend_counts = symbols["mobile_friend_counts"]
        friend_hi = ((friend_counts + 0x8000) >> 16) & 0xFFFF
        friend_lo = friend_counts & 0xFFFF
        # The Friend loader must clear the retained command-0x28 count before
        # reading and set it to one only after the western P3 checksum passes.
        friend_count_address = (
            (0x3C0E0000 | friend_hi).to_bytes(4, "big") +
            (0x25CE0000 | friend_lo).to_bytes(4, "big")
        )
        native_friend_count_address = (
            (0x3C190000 | friend_hi).to_bytes(4, "big") +
            (0x27390000 | friend_lo).to_bytes(4, "big") +
            bytes.fromhex("03191021a0400000")
        )
        migrated_friend_count = (
            friend_count_address in overlay_blob and
            bytes.fromhex("a0400000") in overlay_blob and
            bytes.fromhex("a1e20000") in overlay_blob
        )
        native_friend_count = (
            native_friend_count_address in overlay_blob and
            bytes.fromhex("24020001a20211bf") in overlay_blob and
            bytes.fromhex("a1020000") in overlay_blob
        )
        require(migrated_friend_count or native_friend_count,
                f"{name}: validated Friend Data is not exposed to command 0x28")
        require(0x20 <= overlay_file_end <= fragment_size <=
                overlay_bss_end < 0x1800,
                f"{name}: Mobile overlay exceeds its last known-safe footprint")
        friend_rule_select = (
            3 << 26 | ((symbols["func_8006A990"] >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        friend_rule_adapter = symbols["mobile_download_copy_cave"] - 0x3BF0
        friend_rule_adapter_call = (
            3 << 26 | ((friend_rule_adapter >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        friend_rule_adapter_offset = friend_rule_adapter - 0x80000400 + 0x1000
        require(
            rom[friend_rule_adapter_offset:friend_rule_adapter_offset + 0x28] ==
            mobile_friend_rule_wrapper(profile, 0x28) and
            overlay_code.count(friend_rule_adapter_call) == 1 and
            friend_rule_select not in overlay_code,
            f"{name}: Friend difficulty does not use the resident western "
            "rule adapter"
        )
        friend_rule_call = (
            3 << 26 | ((symbols["func_8006BCA4"] >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        friend_rule_select_offset = overlay_blob.find(friend_rule_select)
        friend_rule_call_offsets = [
            offset for offset in range(0, len(overlay_blob) - 4, 4)
            if overlay_blob[offset:offset + 4] == friend_rule_call
        ]
        # Retained checkpoint compiler layouts may inline the rule-selection
        # helper, so the stable contract is the western battle handoff itself
        # and its required NOP delay slot.
        require(any(
                    overlay_blob[offset + 4:offset + 8] == bytes(4)
                    for offset in friend_rule_call_offsets
                ),
                f"{name}: Friend Data handoff targets the wrong western routine")
        result_entry = int(profile["menu_mobile_result_rom_offset"], 16)
        require(int.from_bytes(rom[result_entry:result_entry + 4], "big") ==
                chunk_cave + 0xA0,
                f"{name}: main-menu result 6 does not route to Mobile Stadium")
        route = int(profile["menu_route_rom_offset"], 16)
        require(rom[route:route + 4] == bytes.fromhex("24040001"),
                f"{name}: B/cancel no longer routes to the title screen")
        helper = cave[0xA0:0xB0]
        require(helper[4:8] == bytes.fromhex("24040012"),
                f"{name}: Mobile Stadium menu helper does not select state 0x12")
        menu_state = int(profile["menu_state_rom_offset"], 16)
        first_unlock_address = symbols["mobile_friend_transposer_cave"] + 0x74
        first_unlock_call = (
            3 << 26 | ((first_unlock_address >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        require(rom[menu_state:menu_state + 12] ==
                first_unlock_call + bytes.fromhex("0220282500000000"),
                f"{name}: main menu does not route through first-unlock helper")
        first_unlock_offset = first_unlock_address - 0x80000400 + 0x1000
        first_unlock_size = symbols["mobile_state_session"] - first_unlock_address
        first_unlock = rom[
            first_unlock_offset:first_unlock_offset + first_unlock_size
        ]
        write_record_call = (
            3 << 26 |
            ((symbols["mobile_unlock_write_record"] >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        require(bytes.fromhex("9628000292290005") in first_unlock and
                bytes.fromhex("10a0000b00003025") in first_unlock and
                bytes.fromhex("1520000900000000") in first_unlock and
                bytes.fromhex("a2290005") in first_unlock and
                write_record_call in first_unlock and
                bytes.fromhex("24100005afb000bc2405000124060001") in first_unlock and
                bytes.fromhex("27e8eecc") in first_unlock and
                bytes.fromhex("01000008") in first_unlock and
                bytes.fromhex("0a881a15") not in first_unlock,
                f"{name}: Japanese one-shot unlock/cursor state is missing")

        description_call_offset, _, description_fallback = (
            mobile_description_patch_info(source_rom)
        )
        description_helper = first_unlock_address + 0x80
        expected_description_call = (
            3 << 26 | ((description_helper >> 2) & 0x03FFFFFF)
        ).to_bytes(4, "big")
        require(rom[description_call_offset:description_call_offset + 4] ==
                expected_description_call,
                f"{name}: Battle Data menu does not call the downloaded-description accessor")
        delivery_address = symbols["mobile_delivery_cave"]
        delivery_size = symbols["mobile_delivery_cave_end"] - delivery_address
        delivery_offset = delivery_address - 0x80000400 + 0x1000
        require(rom[delivery_offset:delivery_offset + delivery_size] ==
                mobile_delivery_wrapper(profile, delivery_size),
                f"{name}: Delibird console-unlock consumer is malformed")
        delivery = rom[delivery_offset:delivery_offset + delivery_size]
        require(bytes.fromhex("97aa001001494825a7a90010") in delivery,
                f"{name}: Delibird bits are not ORed into record 3 offset +0")
        require(jal_bytes(symbols["func_80002B34"]) not in delivery and
                jal_bytes(symbols["func_80002BE8"]) not in delivery,
                f"{name}: Delibird description helper mutates the active Mobile menu pool")
        expected_menu_state = source_rom[menu_state:menu_state + 12]
        initializer_instruction = int.from_bytes(expected_menu_state[4:8], "big")
        require(initializer_instruction >> 26 == 3,
                f"{name}: source Mobile menu initializer is not a JAL")
        initializer = 0x80000000 | (
            (initializer_instruction & 0x03FFFFFF) << 2
        )
        fragment_magic = source_rom.rfind(
            b"FRAGMENT", max(0, menu_state - 0x20000), menu_state
        )
        require(fragment_magic >= 8,
                f"{name}: source main-menu fragment header is missing")
        initializer_rom_offset = (
            fragment_magic - 8 + initializer - 0x82200000
        )
        initializer_delta = initializer_rom_offset - (menu_state + 12)
        require(first_unlock == mobile_menu_unlock_wrapper(
                    profile, initializer_delta, first_unlock_size,
                    description_fallback, delivery_address
                ),
                f"{name}: downloaded-description accessor is malformed")
        results.append(f"{language}: {crystal_id.decode()} / URL / {hashlib.sha256(rom).hexdigest()[:16]}")
    return results


def verify_friend_fixture(save: Path) -> list[str]:
    """Validate the known German P2P record that exposed the western shift."""
    raw = save.read_bytes()
    require(len(raw) >= CRYSTAL_UPLOAD_OFFSET + CRYSTAL_UPLOAD_SIZE,
            "German Friend Data fixture is truncated")
    record = raw[CRYSTAL_UPLOAD_OFFSET:
                 CRYSTAL_UPLOAD_OFFSET + CRYSTAL_UPLOAD_SIZE]
    require(record[:8] == bytes.fromhex("96888D938491E150"),
            "German peer name is not WINTER<PK><TERM>")
    require(record[0x1B1:0x1B3] == b"P3" and
            (sum(record[:0x1B3]) & 0xFFFF) ==
            int.from_bytes(record[0x1B3:0x1B5], "little"),
            "German Friend Data P3 checksum is invalid")
    first_party = record[0x91:0x91 + 0x30]
    require(first_party[0] != 0 and first_party[6:8] == bytes.fromhex("A266"),
            "German peer OT ID is not 41574 (A266)")
    # The party_struct contains both the 0x20-byte native box data (including
    # Stat Experience) and calculated party stats at +0x24.  Stadium's
    # func_80051690 consumes a closely related western source structure: EXP
    # grows from three to four bytes and Crystal's reserved party byte is
    # omitted.  The resident transposer performs exactly those two shifts.
    require(first_party[:0x20] == bytes.fromhex(
                "A0AD2C67FA39A26602A123566B65A8614A66A85CA1093319280F0FFF00858138"),
            "German Friend native box-structure fixture changed unexpectedly")
    # Crystal stores caught data big-endian.  Bit 7 of its low byte is the
    # original trainer's gender; the first usable opponent Pokemon therefore
    # records WINTER as female.  Stadium keeps that pair at source +0x20/+0x21;
    # source +0x1e is instead an egg/validity-flags byte.
    caught_data = int.from_bytes(first_party[0x1D:0x1F], "big")
    require(((caught_data >> 7) & 1) == 1,
            "German Friend opponent is not marked as female")
    stadium_source = bytearray(0x24)
    stadium_source[:8] = first_party[:8]
    stadium_source[9:0x1D] = first_party[8:0x1C]
    stadium_source[0x1D] = first_party[0x1F]
    stadium_source[0x1E] = 0
    stadium_source[0x1F] = first_party[0x20]
    stadium_source[0x20:0x22] = first_party[0x1D:0x1F]
    stadium_source[0x22:0x24] = first_party[0x22:0x24]
    require(len(stadium_source) == 0x24 and
            stadium_source[0x1D] == 56 and
            stadium_source[0x1E] == 0 and
            stadium_source[0x1F] == first_party[0x20] and
            stadium_source[0x20:0x22] == first_party[0x1D:0x1F] and
            stadium_source[0x22:0x24] == first_party[0x22:0x24],
            "German Friend party structure is not transposed to Stadium's "
            "level/flags/status/caught-gender/HP offsets")
    require(first_party[0x24:0x2E] == bytes.fromhex(
                "00BD0091009400760076"),
            "German Friend active-stat fixture changed unexpectedly")
    egg_party = record[0x91 + 4 * 0x30:0x91 + 5 * 0x30]
    egg_name = record[0x0D + 4 * 0x0B:0x0D + 5 * 0x0B]
    require(egg_name[:3] == bytes.fromhex("848686") and
            egg_party[0] != 0 and egg_party[0x24:0x26] != b"\0\0",
            "German Friend Egg fixture changed unexpectedly")
    usable = []
    for index in range(6):
        party = record[0x91 + index * 0x30:0x91 + (index + 1) * 0x30]
        name = record[0x0D + index * 0x0B:0x0D + (index + 1) * 0x0B]
        is_egg = name[:3] == bytes.fromhex("848686") and (
            name[3] == 0x50 or name[3:5] == bytes.fromhex("7F50")
        )
        if party[0] and not is_egg:
            usable.append((party[0], party[0x1F]))
    require(usable == [(0xA0, 56), (0x11, 27), (0xA2, 25),
                       (0xB9, 20), (0x76, 18)],
            "German Friend Egg filtering/party compaction fixture is incorrect")
    return [
        "German Friend name is WINTER plus the E1 PK glyph",
        "German Friend OT ID is 41574 (A266) in the populated party record",
        "German Friend native box data retains Stat Experience for Stadium conversion",
        "German Friend caught data identifies the opponent as female",
        "Western transposition presents female caught data and level 56 at Stadium's expected offsets",
        "German Friend active battle stats are present at party +0x24",
        "Egg record is identified by its saved nickname despite retaining an underlying species",
        "Usable party compacts to five slots, retaining Feraligatr and levels 56/27/25/20/18",
    ]


def verify_message_sync(repo: Path) -> list[str]:
    """Exercise an xx_msg.txt edit through sync and text-archive rebuilding."""
    sys.path.insert(0, str(repo / "tools"))
    from mobile_stadium import text_patch
    from sync_localization_kit import LANGUAGES as MSG_LANGUAGES, sync_messages

    build = repo / "build"
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="msg-sync-test-", dir=build) as name:
        temporary = Path(name)
        temp_repo = temporary / "repo"
        temp_kit = temp_repo / "stadium2-localization-kit"
        source_kit = repo / "stadium2-localization-kit"
        text_dir = temp_kit / "mobile" / "assets" / "text"
        text_dir.mkdir(parents=True)
        for code in MSG_LANGUAGES:
            shutil.copyfile(source_kit / f"{code}_msg.txt", temp_kit / f"{code}_msg.txt")
            shutil.copyfile(
                source_kit / "mobile" / "assets" / "text" /
                f"{code}.msg-overrides.json",
                text_dir / f"{code}.msg-overrides.json",
            )
        shutil.copyfile(
            source_kit / "mobile" / "assets" / "text" / "msg-sync-state.json",
            text_dir / "msg-sync-state.json",
        )
        de_messages = temp_kit / "de_msg.txt"
        source = de_messages.read_text(encoding="utf-8")
        require("\nBISASAM\n" in source,
                "DE message fixture no longer contains text file 0 entry 0")
        de_messages.write_text(
            source.replace("\nBISASAM\n", "\nBISASAX\n", 1),
            encoding="utf-8",
        )
        require(sync_messages(temp_repo, temp_kit) == 1,
                "one xx_msg.txt edit did not produce exactly one override")
        override = json.loads(read_text(
            temp_repo / "assets" / "localization" / "mobile" /
            "text-overrides" / "de.json"
        ))
        matches = [
            item for block in override["text_files"] if block["text_file"] == 0
            for item in block["entries"]
            if item["index"] == 0 and item["text"] == "BISASAX"
        ]
        require(len(matches) == 1,
                "xx_msg.txt edit was not mapped to its text-file/entry pair")
        active_text = temp_repo / "assets" / "localization" / "mobile" / "text"
        active_text.mkdir(parents=True)
        shutil.copyfile(
            repo / "assets" / "localization" / "mobile" / "text" / "de.json",
            active_text / "de.json",
        )
        layer = temporary / "layer"
        layer.mkdir()
        rom = (repo / "build" / "mobile-stadium" /
               "pokestadiumgs-pal-de.z64").read_bytes()
        text_patch(temp_repo, "de", rom, layer)
        archive = (layer / "mobile-text-archive.bin").read_bytes()
        resource_offset = int.from_bytes(archive[0x10:0x14], "big")
        resource_size = int.from_bytes(archive[0x14:0x18], "big")
        resource = archive[resource_offset:resource_offset + resource_size]
        entry_offset = int.from_bytes(resource[4:8], "big")
        entry_end = resource.find(b"\0", entry_offset)
        require(resource[entry_offset:entry_end] == b"BISASAX",
                "xx_msg.txt override was not applied by the ROM text builder")
    return ["xx_msg.txt edits map to and rebuild the associated ROM string"]


def verify_n64_save(repo: Path, save: Path) -> list[str]:
    sys.path.insert(0, str(repo / "tools"))
    import mobile_save  # type: ignore
    decoded, byte_order = mobile_save.decode_save(save.read_bytes(), str(save))
    bank = decoded[:mobile_save.BANK_SIZE]
    appearance = int.from_bytes(bank[mobile_save.MOBILE_APPEARANCE_OFFSET:
                                     mobile_save.MOBILE_APPEARANCE_OFFSET + 2], "big")
    require(appearance & mobile_save.MOBILE_APPEARANCE_MASK,
            "Stadium Mobile Stadium appearance bit is not enabled")
    require(bank[mobile_save.MOBILE_SEEN_OFFSET] & mobile_save.MOBILE_SEEN_MASK,
            "Stadium Mobile Stadium seen/selectable bit is not enabled")
    return [f"N64 save menu enabled and selectable ({byte_order})"]


def verify_empty_crystal_download(save: Path) -> list[str]:
    """Keep an uninitialized F000 area on the explicit no-data path."""
    raw = save.read_bytes()
    require(len(raw) >= 0x10000,
            f"fresh Crystal save is too short: 0x{len(raw):X}")
    frame = raw[CRYSTAL_DOWNLOAD_OFFSET:
                CRYSTAL_DOWNLOAD_OFFSET + CRYSTAL_DOWNLOAD_SIZE]
    checksum = sum(frame[:0xFFC]) & 0xFFFF
    stored = int.from_bytes(frame[0xFFC:0xFFE], "little")
    valid = (
        frame[0] <= 3 and frame[1] <= 5 and
        frame[0xFFA:0xFFC] == b"P3" and checksum == stored
    )
    require(not valid,
            "fresh Crystal fixture unexpectedly advertises a valid P3 frame")
    # This is the precise normalization performed before the retained 0x26 /
    # 0x27 count query sees the frame.
    normalized = bytearray(frame)
    normalized[:] = bytes(CRYSTAL_DOWNLOAD_SIZE)
    require(normalized[0] == 0 and normalized[1] == 0,
            "invalid P3 fixture does not normalize to zero record counts")
    return [
        "fresh Crystal F000 frame is invalid and normalizes to zero Battle/Rule records"
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--crystal-save", type=Path)
    parser.add_argument("--n64-save", type=Path)
    parser.add_argument(
        "--source-only", action="store_true",
        help="validate the producer/consumer source contract before building",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    crystal_save = (args.crystal_save or repo.parent / "Saves" / "pokecrystal-mobile-enabled.sav").resolve()
    n64_save = (args.n64_save or repo.parent / "pokestadiumgs-ntsc-en.sav").resolve()

    if args.source_only:
        sections = (("Stadium western consumer", verify_stadium_source(repo)),)
    else:
        sections = [
            ("Crystal producer/consumer", verify_crystal(repo, crystal_save)),
            ("Stadium western consumer", verify_stadium_source(repo)),
            ("Per-build ROM metadata", verify_builds(repo)),
            ("Localization text workflow", verify_message_sync(repo)),
            ("N64 FlashRAM", verify_n64_save(repo, n64_save)),
        ]
        friend_fixture = repo.parent / "pokecrystal_ger.sav"
        if friend_fixture.is_file():
            sections.insert(1, (
                "Western Friend fixture",
                verify_friend_fixture(friend_fixture),
            ))
        fresh_fixture = repo.parent / "Fresh_pokecrystal.sav"
        if fresh_fixture.is_file():
            sections.insert(2, (
                "Fresh Crystal no-data fixture",
                verify_empty_crystal_download(fresh_fixture),
            ))
    print("Mobile Stadium interoperability verification: PASS")
    for title, lines in sections:
        print(f"\n[{title}]")
        for line in lines:
            print(f"  PASS {line}")
    print("\nContract: Crystal produces 0x1B5-byte western uploads; the service's 0xFFE-byte")
    print("distribution occupies the block at raw SRAM 0xF000; Stadium consumes native 0x490-byte")
    print("western replay records using the retained western Fragment 7 path.")
    print("The supplied Crystal save contains a checksummed P3 battle payload for end-to-end testing.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError) as exc:
        print(f"Mobile Stadium interoperability verification: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
