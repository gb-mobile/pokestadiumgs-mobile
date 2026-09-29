#!/usr/bin/env python3
"""Build and audit the opt-in international Mobile Stadium controller."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROM_SIZE = 0x4000000
MAIN_VRAM = 0x80000400
MAIN_ROM = 0x1000
OVERLAY_VRAM = 0x84500000
OVERLAY_CODE = OVERLAY_VRAM + 0x20
OVERLAY_ROM = 0x3FEDFD0
# The retained state-18 fragment loader maps only the entry-side page when the
# file window is 0x1700 bytes.  The last runtime-confirmed Friend Data build
# uses 0x1790, which keeps both 0x84500000 and 0x84501000 mapped.  Code in the
# entry page calls helpers below +0x1000, so this is a mapping requirement, not
# ordinary ROM padding.
OVERLAY_MIN_FILE_SIZE = 0x1790
SCANNER_FINISH_OFFSET = 0x2D0
MOBILE_FIRST_UNLOCK_QUERY_OFFSET = 0x100

ROM_NAMES = {
    "us": "Pokemon Stadium 2 (English).n64",
    "en": "Pokemon Stadium 2 (Europe).z64",
    "au": "Pokemon Stadium 2 (Europe).z64",
    "fr": "Pokemon Stadium 2 (France).n64",
    "de": "Pokemon Stadium 2 (Germany).n64",
    "it": "Pokemon Stadium 2 (Italy).n64",
    "es": "Pokemon Stadium 2 (Spain).n64",
}

MOBILE_ROM_NAMES = {
    "us": "pokestadiumgs-ntsc-en.z64",
    "en": "pokestadiumgs-pal-en.z64",
    "au": "pokestadiumgs-pal-au.z64",
    "fr": "pokestadiumgs-pal-fr.z64",
    "de": "pokestadiumgs-pal-de.z64",
    "it": "pokestadiumgs-pal-it.z64",
    "es": "pokestadiumgs-pal-es.z64",
}

LANGUAGE_NAMES = {
    "us": "NTSC English",
    "en": "English",
    "au": "Australian English",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "es": "Spanish",
}

ASSET_LANGUAGE = {"au": "en"}
# The localization kit has one English message script.  Its explicit message
# overrides apply to all three English releases even though NTSC-US retains a
# separate base JSON for a handful of region-specific line wraps.
MESSAGE_OVERRIDE_LANGUAGE = {"us": "en", "au": "en"}
SOURCE_CRYSTAL_ID = b"BXTJ"
SOURCE_SERVICE_URL = (
    b"http://gameboy.datacenter.ne.jp/cgb/download?"
    b"n=/01/CGB-BXTJ/exchange/index.txt"
)

PACKAGE_FILES = (
    ("include/mobile_stadium.h", "include/mobile_stadium.h"),
    ("src/mobile_stadium_overlay.c", "src/mobile_stadium_overlay.c"),
    ("src/mobile_stadium_command.s", "src/mobile_stadium_command.s"),
)

# Names used by the recovered Japanese C -> retained US reference routine.
ALIASES = {
    "_bcopy": "_bcopy",
    "_bzero": "_bzero",
    "func_80002974": "func_80002984",
    # Native tagged main-pool allocation used by the Japanese PTP0 cartridge-
    # picker notification object.  The ordinary two-argument allocator above
    # cannot attach the controller-specific destructor expected at context
    # +0x6c.
    "mobile_tagged_alloc": "func_8000290C",
    "func_80002B34": "main_pool_push_state",
    "func_80002BE8": "main_pool_pop_state",
    "func_800047D8": "func_800047F8",
    "func_8000B4A0": "func_8000B580",
    "func_80026858": "func_80026938",
    "func_800353B4": "func_800354B4",
    "func_800354E4": "func_800355E4",
    "func_80051690": "func_80051830",
    "func_800616AC": "func_80060E48",
    "func_80065718": "func_80064CB8",
    "func_80065730": "func_80064CD0",
    "func_80065748": "func_80064CE8",
    "func_8006585C": "func_80064DFC",
    "func_8006A990": "func_80068AB0",
    "func_8006B99C": "func_8006A13C",
    "func_8006BAD8": "func_8006A278",
    "func_8006BB4C": "func_8006A2EC",
    "func_8006BB58": "func_8006A2F8",
    "func_8006BB88": "func_8006A328",
    "func_8006BC18": "func_8006A3B8",
    "func_8006BC40": "func_8006A3E0",
    "func_8006BC60": "func_8006A400",
    "func_8006BC84": "func_8006A424",
    "func_8006BCA4": "func_8006A444",
    "func_8006BCEC": "func_8006A48C",
    "func_8006BD0C": "func_8006A4AC",
    "func_8006BD7C": "func_8006A51C",
    "func_8006BE60": "func_8006A600",
    "func_8006C438": "func_8006ABD8",
    "func_8006C488": "func_8006AC28",
    "func_8006C4AC": "func_8006AC4C",
    "func_8006C4D0": "func_8006AC70",
    "func_8006C52C": "func_8006ACCC",
    "func_8006C570": "func_8006AD10",
    "func_8006C6F0": "func_8006AE90",
    "func_8006C714": "func_8006AEB4",
    "func_8006CC98": "func_8006B428",
    "func_8006CFFC": "func_8006B790",
    "func_8006D020": "func_8006B7B4",
    "func_8006D330": "func_8006BAC4",
    "func_8006DDC4": "func_8006C58C",
    "func_8006DDE4": "func_8006C5AC",
    "func_8006DE7C": "func_8006C644",
    "func_8006DE9C": "func_8006C664",
    "func_8006E208": "func_8006C9F4",
    # Resident routines used by the Japanese Crystal-mobile handshake that
    # was removed from the international executable.
    "mobile_unlock_scan": "func_8005A98C",
    "mobile_unlock_read": "func_800570CC",
    # The retail cartridge scanner brackets every physical GB Pak operation
    # with these routines.  Emulators tolerate SRAM reads after the scanner
    # has released the Pak, but real hardware does not.
    "mobile_gbpak_acquire": "func_80056960",
    "mobile_gbpak_release": "func_80056A5C",
    "mobile_gbpak_ready": "func_80057A5C",
    "mobile_data_scan": "func_80059EF8",
    # Dormant resident rmon handler used to acquire every active physical
    # Transfer Pak before mapping/calling the Mobile overlay and release them
    # immediately after its SRAM copies, before later fragment loads.
    "mobile_state_session": "__rmonIOhandler",
    # Retrying write-and-readback wrapper. Calling func_8005712C directly
    # leaves some Transfer Pak implementations in an in-progress state.
    "mobile_unlock_write_crystal": "func_80057BCC",
    # Stadium record 0x14 owns the persistent Mobile Stadium menu-unlock bit.
    # These are the retained western equivalents used by the working
    # Crystal-to-Stadium handshake.  Do not alias them to Delibird's record
    # API: the two routines operate on different save structures.
    "mobile_unlock_load_record": "func_8005487C",
    "mobile_unlock_write_record": "func_800548C4",
    "mobile_unlock_commit_record": "func_80051D64",
    # Delibird's Delivery record 3 uses the later load/write pair, matching
    # Japanese func_800547AC/func_800547E4 and func_80058810.
    "mobile_delivery_load_record": "func_8005493C",
    "mobile_delivery_write_record": "func_80054974",
    # Japanese func_800557B8 selects Stadium save record 3 before applying
    # the two Delibird's Delivery console-replacement bits from DLDx+0xFE1.
    # Its retained US counterpart is func_80055948.
    "mobile_delivery_select_record": "func_80055948",
    # Japanese func_80054CB8 is the retained save-record finalizer.  The
    # international equivalent moved to func_80054E48 (both have the same 29
    # call sites); it must run after committing Delibird record 3.
    "mobile_delivery_finalize_record": "func_80054E48",
    # The retail remote-monitor command handlers are unreachable in gameplay.
    # This isolated function body is large enough for the idempotent Delibird
    # save update without replacing osSyncPrintf or touching Mobile fragments.
    "mobile_delivery_cave": "__rmonExecute",
    "mobile_delivery_cave_end": "__rmonWriteWordTo",
    # The international E000 wrapper was retained, but its Japanese PTP0
    # loader was stripped.  This isolated, unused RMON memory-command body is
    # large enough to reconstruct that native object without enlarging a
    # fragment or disturbing any of the battle/friend-data resident bridges.
    "mobile_ptp0_cave": "__rmonReadMem",
    "mobile_ptp0_cave_end": "__rmonWriteMem",
    # Use an unreferenced RMON query as the resident music bridge.  Do not
    # replace __rmonWriteWordTo: breakpoint handling uses it during replay
    # transitions when Project64's debugger is enabled.
    "mobile_music_cave": "__rmonGetExeName",
    "mobile_music_cave_end": "__rmonGetRegionCount",
    "mobile_unlock_continue": "func_8005A92C",
    "mobile_unlock_cave": "osReadHost",
    # Recover the regional DLD0 pointer table used by commands 0x26/0x27.
    "mobile_download_command": "func_8005CDDC",
    "mobile_download_read_dispatch": "func_8005D698",
    "mobile_box_read": "func_80059050",
    "mobile_full_save_read": "func_8005910C",
    "mobile_chunk_cave": "kdebugserver",
    # Retail builds retain this unused KMC development initializer.  Unlike
    # the 0x84500000 fragment window, it remains mapped while Fragment 7 is
    # loaded, so commands 0x26/0x27 can safely dispatch here.
    "mobile_download_copy_cave": "__osInitialize_kmc",
    # rmonPrintf is an unused 0x54-byte retail debug routine.  Keeping the
    # entry trampoline here avoids growing the fragment-53 image beyond the
    # footprint accepted by the dormant regional loader.
    "mobile_friend_transposer": "rmonPrintf",
    # The full western party_struct transposition needs more than rmonPrintf's
    # 0x54 bytes once level/status/caught-data are put in their proper fields.
    # This dormant rmon fault sender provides an isolated 0x74-byte body while
    # the public entry point above remains stable for the linked overlay.
    "mobile_friend_transposer_cave": "__rmonSendFault",
}

FRAGMENTS = (3, 4, 5, 7, 8, 11, 12, 13, 20, 21, 27, 28, 79, 80, 81, 82)

# In the international main-menu overlay this sequence loads record 0x14's
# 16-byte state, then deliberately passes false for both Japanese-only Mobile
# Stadium arguments. Replace the argument setup/call with a resident helper
# that restores the retail Japanese availability/new-unlock semantics.
MOBILE_MENU_STATE_PATTERN = bytes.fromhex("022020258fa4009c00002825")
MOBILE_MENU_GRAPH_PATTERN = bytes.fromhex(
    "3c068221afbf0024afb1001c24c6def4240400013c0e82213c088221"
    "25cedf10acc400002412000224010005250806b0"
)
MOBILE_MENU_GRAPH_FLAG_OFFSET = 0x18
MOBILE_MENU_GRAPH_BASE_OFFSET = 0x2C
MOBILE_MENU_GRAPH_FLAG_REPLACEMENT = bytes.fromhex("90c50d39")
MOBILE_MENU_GRAPH_BASE_REPLACEMENT = bytes.fromhex("24c827bc")

# This resident main-menu routine is shared by Japanese and international
# Stadium.  Immediately after this context it asks a regional accessor for
# the text displayed in the lower Battle Data description panel.  Japanese
# returns DLDx+0xEEC; Nintendo reduced the international accessor to a fixed
# default string.  Western replay records are larger, so the same payload
# text begins at 0xF1C instead.
MOBILE_DESCRIPTION_CALL_CONTEXT = bytes.fromhex(
    "0220202518400004000000008e19003824180001af380014"
)
MOBILE_DESCRIPTION_HELPER_OFFSET = 0x80
MOBILE_DESCRIPTION_OFFSET = 0xF1C


def source_rom_path(
    repo: Path, input_root: Path, code: str, source_name: str | None = None
) -> Path:
    candidate = input_root / (source_name or ROM_NAMES[code])
    if candidate.is_file():
        return candidate
    if code == "us":
        candidate = repo / "baseroms/us/baserom.z64"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(candidate)


def normalize_rom(data: bytes) -> bytes:
    if data[:4] == bytes.fromhex("80371240"):
        return data
    output = bytearray(len(data))
    if data[:4] == bytes.fromhex("37804012"):
        output[0::2], output[1::2] = data[1::2], data[0::2]
        return bytes(output)
    if data[:4] == bytes.fromhex("40123780"):
        output[0::4], output[1::4] = data[3::4], data[2::4]
        output[2::4], output[3::4] = data[1::4], data[0::4]
        return bytes(output)
    raise ValueError("unknown N64 byte order")


def mobile_build_settings(repo: Path, code: str) -> tuple[bytes, bytes]:
    path = repo / "assets/localization/mobile/builds.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    if code not in settings:
        raise ValueError(f"{path}: missing settings for {code}")
    try:
        crystal_id = settings[code]["crystal_id"].encode("ascii")
        service_url = settings[code]["service_url"].encode("ascii")
    except (KeyError, UnicodeEncodeError) as exc:
        raise ValueError(
            f"{path}: {code} requires ASCII crystal_id and service_url"
        ) from exc
    if len(crystal_id) != len(SOURCE_CRYSTAL_ID):
        raise ValueError(f"{path}: {code} crystal_id must be exactly 4 ASCII bytes")
    if len(service_url) != len(SOURCE_SERVICE_URL):
        raise ValueError(
            f"{path}: {code} service_url must be exactly "
            f"{len(SOURCE_SERVICE_URL)} ASCII bytes"
        )
    return crystal_id, service_url


def mobile_identity_patches(repo: Path, code: str, source_rom: bytes) -> list[dict]:
    crystal_id, service_url = mobile_build_settings(repo, code)
    crystal_prefix = b"\x07PM_CRYSTAL\x00"
    crystal_entry = crystal_prefix + SOURCE_CRYSTAL_ID + b"\x00\x00"
    crystal_offset = source_rom.find(crystal_entry)
    if crystal_offset < 0:
        raise ValueError(f"{code}: Mobile Crystal metadata entry was not found")
    if source_rom.find(crystal_entry, crystal_offset + 1) >= 0:
        raise ValueError(f"{code}: Mobile Crystal metadata entry is ambiguous")
    url_offset = source_rom.find(SOURCE_SERVICE_URL)
    if url_offset < 0:
        raise ValueError(f"{code}: historical Mobile service URL was not found")
    if source_rom.find(SOURCE_SERVICE_URL, url_offset + 1) >= 0:
        raise ValueError(f"{code}: historical Mobile service URL is ambiguous")
    patches = [
        {
            "offset": f"0x{crystal_offset + len(crystal_prefix):X}",
            "expected": SOURCE_CRYSTAL_ID.hex(),
            "replacement": crystal_id.hex(),
        },
        {
            "offset": f"0x{url_offset:X}",
            "expected": SOURCE_SERVICE_URL.hex(),
            "replacement": service_url.hex(),
        },
    ]
    # Fragment 7 passes the local Crystal gender to the portrait loader.
    # Crystal and the converted western Friend structure both use 0=male and
    # 1=female.  The retained international expression ``1 - selection``
    # inverts that value.  Pass the selection byte through unchanged in every
    # region; team ownership is initialized separately by the overlay.
    portrait_prefix = bytes.fromhex(
        "8e0e00383c010003342101208dcf0014241800010201202124050040"
    )
    portrait_offset = source_rom.find(portrait_prefix)
    if portrait_offset < 0 or source_rom.find(
        portrait_prefix, portrait_offset + 4
    ) >= 0:
        raise ValueError(
            f"{code}: Fragment 7 local portrait request is ambiguous"
        )
    portrait_delay_offset = portrait_offset + len(portrait_prefix) + 4
    portrait_delay = source_rom[
        portrait_delay_offset:portrait_delay_offset + 4
    ]
    if portrait_delay != bytes.fromhex("030f3023"):
        raise ValueError(
            f"{code}: Fragment 7 portrait selector is {portrait_delay.hex()}, "
            "expected subu a2,t8,t7"
        )
    patches.append({
        "offset": f"0x{portrait_delay_offset:X}",
        "expected": portrait_delay.hex(),
        "replacement": "01e03025",  # or a2, t7, zero
    })
    return patches


# ---------------------------------------------------------------------------
# Organize Data option bar (retail fragment 7 data)
#
# Battle Data, Friend's Data and Rule Data share a single four-option bar built
# from message file 63 entries 33-36 ("Move to N64", "View", "Re-order",
# "Void").  Fragment 4's generic menu renderer takes each label's position from
# a signed 16-bit pair inside that option's 0x20-byte text entry and the hand
# cursor's position from a signed 16-bit pair inside the 24-byte menu item
# array.  A label always sits MOBILE_ORGANIZE_CURSOR_LEAD units to the right of
# its own cursor: the hand sprite is 32 units wide and the remaining 8 are
# padding, so that lead cannot be shortened to reclaim space.
#
# Retail spaces the four options for Japanese and English and leaves 67/53/70
# units between adjacent labels.  Slot 3 is consequently too narrow for several
# official translations: German "Ungueltig" renders 65 units wide against 56
# available.  German "Ansehen" already overruns slot 1 by 17 units in the
# shipped game, so the Re-order cursor draws over its tail.
#
# The tables below pack the options to the left with MOBILE_ORGANIZE_MARGIN
# units between one label and the next cursor, which is as close to a single
# cursor width as the sprite geometry permits.  Option 0 keeps its retail
# position in every build.  The values were solved from the rendered width of
# every shipped label, measured with this ROM's own font-id-8 advance table, so
# each slot holds the widest string its language can display.
#
# Coordinates are panel-relative in the renderer's 640-wide space.  They differ
# per language because the slots are shared: one geometry would have to be as
# wide as the widest translation in every slot, which spreads the short-label
# languages out instead of compacting them.  That costs nothing here because
# every language already builds its own ROM from its own patch specification.
#
# German additionally needs its Organize Data frame 12 units wider to fit
# "Ungueltig" without moving option 0.  The frame is drawn by a nine-slice
# routine that stretches its middle section, so a wider value is a supported
# input rather than a hack, and all five panels that share the retail rectangle
# are widened together so the box does not change size when the flow moves
# between the bar, its static variant, a message panel and the Yes/No prompt.
MOBILE_ORGANIZE_TEXT_FILE = 63
MOBILE_ORGANIZE_FIRST_MESSAGE = 33
MOBILE_ORGANIZE_CURSOR_LEAD = 40
MOBILE_ORGANIZE_MARGIN = 4
MOBILE_ORGANIZE_LABEL_Y = 11
MOBILE_ORGANIZE_CURSOR_Y = 9
MOBILE_ORGANIZE_ITEM_STRIDE = 24
MOBILE_ORGANIZE_ITEM_CURSOR_OFFSET = 6
MOBILE_ORGANIZE_ITEM_LIST_OFFSET = 0x10
MOBILE_ORGANIZE_ENTRY_LABEL_OFFSET = 4
MOBILE_ORGANIZE_RETAIL_CURSORS = (18, 181, 268, 412)
MOBILE_ORGANIZE_RETAIL_PANEL_X = 69
MOBILE_ORGANIZE_RETAIL_PANEL_WIDTH = 508
# The nine-slice frame reserves a corner strip on each side, and option
# labels are clipped at that inner edge rather than at the frame itself.
# Measured from a hardware/emulator capture: with a 520-unit frame the
# German label stopped between 505 and 510 units, so the usable inner
# width is the frame minus roughly 12 to 16 units.  Layouts below keep at
# least 14 units of slack against the worst of that range.
MOBILE_ORGANIZE_PANEL_INNER_MARGIN = 16
# Panel x, y, width, height as they appear in every retail build.
MOBILE_ORGANIZE_PANEL_RECT = bytes.fromhex("0045005f01fc004a")
MOBILE_ORGANIZE_PANEL_COUNT = 5
FRAGMENT7_VRAM = 0x83000000

# Language -> (cursor X for options 0-3, Organize Data panel width).
# Language -> (cursor X for options 0-3, panel X, panel width).
#
# Only the builds listed here get a corrected option bar; every other script
# keeps the retail geometry untouched, and mobile_organize_patches emits
# nothing for them.
#
# Italian uses the compact layout: the retail frame is left alone, option 0
# stays exactly where retail puts it, and options 1-3 move left so that a
# uniform 4-unit margin separates a label from the next cursor.  That is enough
# for "Vuoto", which the retail spacing clipped.
#
# German needs more than compaction: its four widest strings plus the three
# mandatory 40-unit cursor leads do not fit the retail frame's inner width at
# any margin.  Its frame therefore also moves 18 units left and grows by 26,
# ending at 585, inside the widest frame the retail game already draws, and its
# option 0 shifts 24 units left with it.  That leaves slack under both readings
# of where labels get clipped: the frame's inner edge, and an absolute screen
# limit.  Giving the short-label scripts this displaced frame was tried and
# reverted; it left them visibly lopsided.
MOBILE_ORGANIZE_LAYOUT = {
    "it": ((18, 171, 248, 357), 69, 508),
    "de": ((12, 166, 271, 399), 51, 534),
}
# NTSC-US and the Australian build display the English script.
MOBILE_ORGANIZE_LANGUAGE = {"us": "en", "au": "en"}


def mobile_organize_item_pattern(
    cursors: tuple[int, ...] = MOBILE_ORGANIZE_RETAIL_CURSORS,
) -> tuple[bytes, bytes]:
    """Build a pointer-independent template for the four-option item array."""
    pattern = bytearray()
    mask = bytearray()
    for index, cursor in enumerate(cursors):
        item = bytearray(MOBILE_ORGANIZE_ITEM_STRIDE)
        wanted = bytearray(MOBILE_ORGANIZE_ITEM_STRIDE)
        item[0] = index + 1
        item[3] = index                                  # previous option id
        item[4] = index + 2 if index < 3 else 0          # next option id
        struct.pack_into(">h", item, MOBILE_ORGANIZE_ITEM_CURSOR_OFFSET, cursor)
        struct.pack_into(">h", item, 8, MOBILE_ORGANIZE_CURSOR_Y)
        for position in range(0, MOBILE_ORGANIZE_ITEM_LIST_OFFSET):
            wanted[position] = 1
        # The text-list pointer is relocated per build; everything after it is
        # zero in every retail region.
        for position in range(MOBILE_ORGANIZE_ITEM_LIST_OFFSET + 4,
                              MOBILE_ORGANIZE_ITEM_STRIDE):
            wanted[position] = 1
        pattern += item
        mask += wanted
    return bytes(pattern), bytes(mask)


def mobile_organize_item_matches(
    fragment: bytes, cursors: tuple[int, ...] = MOBILE_ORGANIZE_RETAIL_CURSORS,
) -> list[int]:
    """Return every fragment offset holding this exact four-option item array."""
    pattern, mask = mobile_organize_item_pattern(cursors)
    # Option 0 never moves, so anchor on its fixed leading bytes and confirm the
    # whole 4 x 24-byte array, ignoring the relocated text-list pointers.
    prefix = pattern[:MOBILE_ORGANIZE_ITEM_LIST_OFFSET]
    matches = []
    position = 0
    while True:
        position = fragment.find(prefix, position)
        if position < 0:
            break
        candidate = fragment[position:position + len(pattern)]
        if len(candidate) == len(pattern) and all(
            found == wanted
            for found, wanted, compare in zip(candidate, pattern, mask)
            if compare
        ):
            matches.append(position)
        position += 4
    return matches


def mobile_organize_find_items(fragment: bytes, code: str) -> int:
    """Locate the retail four-option item array inside fragment 7."""
    matches = mobile_organize_item_matches(fragment)
    if len(matches) != 1:
        raise ValueError(
            f"{code}: Organize Data option item array is ambiguous "
            f"({len(matches)} candidates in fragment 7)"
        )
    return matches[0]


def mobile_organize_patches(code: str, source_rom: bytes,
                            profile: dict) -> list[dict]:
    """Compact the shared Organize Data option bar for this build's script."""
    layout_code = MOBILE_ORGANIZE_LANGUAGE.get(code, code)
    if layout_code not in MOBILE_ORGANIZE_LAYOUT:
        # This build ships the retail option bar unchanged.
        return []
    cursors, panel_x, panel_width = MOBILE_ORGANIZE_LAYOUT[layout_code]
    try:
        start = int(profile["fragments"]["7"], 16)
        end = int(profile["fragments"]["8"], 16)
    except KeyError as error:
        raise ValueError(
            f"{code}: profile has no fragment 7/8 bounds for the option bar"
        ) from error
    if not 0 < start < end <= ROM_SIZE:
        raise ValueError(f"{code}: implausible fragment 7 bounds")
    fragment = source_rom[start:end]

    items = mobile_organize_find_items(fragment, code)
    patches = []
    for index, cursor in enumerate(cursors):
        retail_cursor = MOBILE_ORGANIZE_RETAIL_CURSORS[index]
        item = items + index * MOBILE_ORGANIZE_ITEM_STRIDE
        cursor_offset = item + MOBILE_ORGANIZE_ITEM_CURSOR_OFFSET
        actual_cursor = struct.unpack_from(">h", fragment, cursor_offset)[0]
        if actual_cursor != retail_cursor:
            raise ValueError(
                f"{code}: Organize Data option {index} cursor is "
                f"{actual_cursor}, expected the retail {retail_cursor}"
            )
        list_address = struct.unpack_from(
            ">I", fragment, item + MOBILE_ORGANIZE_ITEM_LIST_OFFSET
        )[0]
        entry = list_address - FRAGMENT7_VRAM
        if not 0 <= entry <= len(fragment) - 8:
            raise ValueError(
                f"{code}: Organize Data option {index} text list "
                f"0x{list_address:08X} is outside fragment 7"
            )
        text_file, message, label_x, label_y = struct.unpack_from(
            ">HHhh", fragment, entry
        )
        expected_message = MOBILE_ORGANIZE_FIRST_MESSAGE + index
        if (text_file, message) != (MOBILE_ORGANIZE_TEXT_FILE, expected_message):
            raise ValueError(
                f"{code}: Organize Data option {index} draws message "
                f"{text_file}:{message}, expected "
                f"{MOBILE_ORGANIZE_TEXT_FILE}:{expected_message}"
            )
        if label_y != MOBILE_ORGANIZE_LABEL_Y:
            raise ValueError(
                f"{code}: Organize Data option {index} label Y is {label_y}, "
                f"expected {MOBILE_ORGANIZE_LABEL_Y}"
            )
        if label_x != retail_cursor + MOBILE_ORGANIZE_CURSOR_LEAD:
            raise ValueError(
                f"{code}: Organize Data option {index} label X is {label_x}, "
                f"expected cursor {retail_cursor} plus "
                f"{MOBILE_ORGANIZE_CURSOR_LEAD}"
            )
        label = cursor + MOBILE_ORGANIZE_CURSOR_LEAD
        if cursor != actual_cursor:
            patches.append({
                "offset": f"0x{start + cursor_offset:X}",
                "expected": struct.pack(">h", actual_cursor).hex(),
                "replacement": struct.pack(">h", cursor).hex(),
            })
        if label != label_x:
            label_offset = entry + MOBILE_ORGANIZE_ENTRY_LABEL_OFFSET
            patches.append({
                "offset": f"0x{start + label_offset:X}",
                "expected": struct.pack(">h", label_x).hex(),
                "replacement": struct.pack(">h", label).hex(),
            })

    if (panel_x, panel_width) != (
        MOBILE_ORGANIZE_RETAIL_PANEL_X, MOBILE_ORGANIZE_RETAIL_PANEL_WIDTH
    ):
        panels = []
        position = 0
        while True:
            position = fragment.find(MOBILE_ORGANIZE_PANEL_RECT, position)
            if position < 0:
                break
            panels.append(position)
            position += 4
        if len(panels) != MOBILE_ORGANIZE_PANEL_COUNT:
            raise ValueError(
                f"{code}: found {len(panels)} Organize Data panels sharing the "
                f"retail rectangle, expected {MOBILE_ORGANIZE_PANEL_COUNT}"
            )
        for panel in panels:
            # The matched bytes are the panel's x, y, width, height pair of
            # pairs: x at +0, width at +4.
            for offset, retail, wanted in (
                (panel, MOBILE_ORGANIZE_RETAIL_PANEL_X, panel_x),
                (panel + 4, MOBILE_ORGANIZE_RETAIL_PANEL_WIDTH, panel_width),
            ):
                actual = struct.unpack_from(">h", fragment, offset)[0]
                if actual != retail:
                    raise ValueError(
                        f"{code}: Organize Data panel field at 0x{offset:X} is "
                        f"{actual}, expected the retail {retail}"
                    )
                if wanted == actual:
                    continue
                patches.append({
                    "offset": f"0x{start + offset:X}",
                    "expected": struct.pack(">h", actual).hex(),
                    "replacement": struct.pack(">h", wanted).hex(),
                })
    return patches


def mobile_organize_layout_applied(fragment: bytes, code: str) -> bool:
    """Confirm a built fragment 7 carries this build's compacted option bar."""
    layout_code = MOBILE_ORGANIZE_LANGUAGE.get(code, code)
    if layout_code not in MOBILE_ORGANIZE_LAYOUT:
        # No correction for this build: the retail bar must survive intact.
        return (
            len(mobile_organize_item_matches(fragment)) == 1
            and fragment.count(MOBILE_ORGANIZE_PANEL_RECT) ==
            MOBILE_ORGANIZE_PANEL_COUNT
        )
    cursors, panel_x, panel_width = MOBILE_ORGANIZE_LAYOUT[layout_code]
    matches = mobile_organize_item_matches(fragment, cursors)
    if len(matches) != 1:
        return False
    if cursors != MOBILE_ORGANIZE_RETAIL_CURSORS and mobile_organize_item_matches(
        fragment
    ):
        return False
    items = matches[0]
    for index, cursor in enumerate(cursors):
        item = items + index * MOBILE_ORGANIZE_ITEM_STRIDE
        list_address = struct.unpack_from(
            ">I", fragment, item + MOBILE_ORGANIZE_ITEM_LIST_OFFSET
        )[0]
        entry = list_address - FRAGMENT7_VRAM
        if not 0 <= entry <= len(fragment) - 8:
            return False
        text_file, message, label_x, label_y = struct.unpack_from(
            ">HHhh", fragment, entry
        )
        if (text_file, message, label_y) != (
            MOBILE_ORGANIZE_TEXT_FILE,
            MOBILE_ORGANIZE_FIRST_MESSAGE + index,
            MOBILE_ORGANIZE_LABEL_Y,
        ):
            return False
        if label_x != cursor + MOBILE_ORGANIZE_CURSOR_LEAD:
            return False
    if (panel_x, panel_width) == (
        MOBILE_ORGANIZE_RETAIL_PANEL_X, MOBILE_ORGANIZE_RETAIL_PANEL_WIDTH
    ):
        return fragment.count(MOBILE_ORGANIZE_PANEL_RECT) == (
            MOBILE_ORGANIZE_PANEL_COUNT
        )
    adjusted = (
        struct.pack(">h", panel_x)
        + MOBILE_ORGANIZE_PANEL_RECT[2:4]
        + struct.pack(">h", panel_width)
        + MOBILE_ORGANIZE_PANEL_RECT[6:]
    )
    return (
        fragment.count(adjusted) == MOBILE_ORGANIZE_PANEL_COUNT
        and MOBILE_ORGANIZE_PANEL_RECT not in fragment
    )


def rom_database_key(rom: bytes) -> str:
    return (
        f"{rom[0x10:0x14].hex().upper()}-"
        f"{rom[0x14:0x18].hex().upper()}-C:{rom[0x3E]:02X}"
    )


def clone_rdb_section(
    text: str, source_key: str, target_key: str, good_name: str,
    overrides: dict[str, str] | None = None,
) -> str:
    section_re = re.compile(
        rf"(?ms)^\[{re.escape(source_key)}\]\r?\n"
        rf"(.*?)(?=^\[|\Z)"
    )
    match = section_re.search(text)
    if match is None:
        raise ValueError(f"Project64 profile [{source_key}] was not found")

    body = match.group(1)
    if re.search(r"(?m)^Good Name=", body):
        body = re.sub(r"(?m)^Good Name=.*$", f"Good Name={good_name}", body, count=1)
    else:
        body = f"Good Name={good_name}\r\n{body}"
    for name, value in (overrides or {}).items():
        if re.search(rf"(?m)^{re.escape(name)}=", body):
            body = re.sub(
                rf"(?m)^{re.escape(name)}=.*$",
                f"{name}={value}", body, count=1,
            )
        else:
            body = f"{body.rstrip()}\r\n{name}={value}\r\n"

    target_re = re.compile(
        rf"(?ms)^\[{re.escape(target_key)}\]\r?\n"
        rf".*?(?=^\[|\Z)"
    )
    replacement = f"[{target_key}]\r\n{body.rstrip()}\r\n\r\n"
    if target_re.search(text):
        return target_re.sub(lambda _match: replacement, text, count=1)
    return f"{text.rstrip()}\r\n\r\n{replacement}"


def configure_project64(
    repo: Path, input_root: Path, config: Path, languages: list[str],
    additional_mobile_rom_roots: list[Path] | None = None,
) -> None:
    database_paths = (config / "Project64.rdb", config / "Video.rdb")
    for database_path in database_paths:
        if not database_path.is_file():
            raise FileNotFoundError(database_path)

    rom_roots = [(repo / "build" / "mobile-stadium", "")]
    rom_roots.extend(
        (root.resolve(), f" [{root.resolve().name}]")
        for root in (additional_mobile_rom_roots or [])
    )
    profiles_by_target: dict[str, tuple[str, list[str]]] = {}
    for code in languages:
        if code not in ROM_NAMES:
            raise ValueError(f"Unsupported language code: {code}")
        source = normalize_rom(source_rom_path(repo, input_root, code).read_bytes())
        source_key = rom_database_key(source)
        for mobile_root, label in rom_roots:
            mobile_path = mobile_root / MOBILE_ROM_NAMES[code]
            if not mobile_path.is_file():
                raise FileNotFoundError(mobile_path)
            mobile = normalize_rom(mobile_path.read_bytes())
            if source[0x3B:0x3F] != mobile[0x3B:0x3F]:
                raise ValueError(
                    f"{mobile_path} does not match the {code} source ROM"
                )
            target_key = rom_database_key(mobile)
            display_name = f"{LANGUAGE_NAMES[code]}{label}"
            if target_key in profiles_by_target:
                prior_source, names = profiles_by_target[target_key]
                if prior_source != source_key:
                    raise ValueError(
                        f"Project64 key {target_key} has conflicting source profiles"
                    )
                names.append(display_name)
            else:
                profiles_by_target[target_key] = (source_key, [display_name])

    for database_path in database_paths:
        text = database_path.read_text(encoding="latin-1")
        for target_key, (source_key, names) in profiles_by_target.items():
            good_name = f"Pokemon Stadium 2 Mobile ({' / '.join(names)})"
            # The injected controller executes dynamically loaded overlays.
            # Project64's x86 recompiler can retain stale blocks across this
            # transition and fault immediately after a GB Pak is selected.
            overrides = (
                {"CPU Type": "Interpreter"}
                if database_path.name.lower() == "project64.rdb"
                else None
            )
            text = clone_rdb_section(
                text, source_key, target_key, good_name, overrides
            )
        try:
            database_path.write_text(text, encoding="latin-1", newline="")
        except PermissionError:
            print(
                f"WARNING: Could not update locked Project64 database "
                f"{database_path}; close Project64 and rerun this step to "
                "refresh its local profile."
            )
        else:
            print(f"Updated {database_path}")

    settings_path = config / "Project64.cfg"
    if not settings_path.is_file():
        raise FileNotFoundError(settings_path)
    settings = settings_path.read_text(encoding="latin-1")

    def enable_debugger(match: re.Match[str]) -> str:
        header, body = match.groups()
        debugger_setting = re.compile(r"(?m)^Debugger=.*$")
        if debugger_setting.search(body):
            body = debugger_setting.sub("Debugger=1", body, count=1)
        else:
            body = f"Debugger=1\r\n{body}"
        return f"{header}{body}"

    debugger_section = re.compile(
        r"(?ms)(^\[Debugger\]\r?\n)(.*?)(?=^\[|\Z)"
    )
    if debugger_section.search(settings):
        settings = debugger_section.sub(enable_debugger, settings, count=1)
    else:
        settings = f"{settings.rstrip()}\r\n\r\n[Debugger]\r\nDebugger=1\r\n"
    try:
        settings_path.write_text(settings, encoding="latin-1", newline="")
    except PermissionError:
        print(
            f"WARNING: Could not update locked Project64 settings "
            f"{settings_path}; close Project64 and rerun this step to "
            "refresh its local settings."
        )
    else:
        print(f"Enabled the built-in debugger in {settings_path}")


def code_symbols(repo: Path) -> list[tuple[int, str]]:
    result = []
    text = (repo / "linker_scripts/us/symbol_addrs_code.txt").read_text()
    for name, address in re.findall(
        r"^(\w+) = 0x([0-9A-Fa-f]+);.*type:func", text, re.MULTILINE
    ):
        value = int(address, 16)
        if MAIN_VRAM <= value < 0x80080000:
            result.append((value, name))
    return sorted(result)


def normalized_word(word: int) -> tuple[int, ...]:
    opcode = word >> 26
    if opcode in (2, 3):
        return (opcode,)
    if opcode == 0:
        return (
            opcode, (word >> 21) & 31, (word >> 16) & 31,
            (word >> 11) & 31, (word >> 6) & 31, word & 63,
        )
    return (opcode, (word >> 21) & 31, (word >> 16) & 31)


def words_at(rom: bytes, address: int, size: int) -> tuple[int, ...]:
    offset = address - MAIN_VRAM + MAIN_ROM
    return struct.unpack(f">{size // 4}I", rom[offset:offset + size])


def signatures(repo: Path) -> tuple[dict[str, tuple[int, tuple]], bytes]:
    symbols = code_symbols(repo)
    rom = (repo / "baseroms/us/baserom.z64").read_bytes()
    result = {}
    for index, (address, name) in enumerate(symbols[:-1]):
        size = symbols[index + 1][0] - address
        if 0 < size <= 0x4000:
            result[name] = (
                address,
                tuple(normalized_word(word) for word in words_at(rom, address, size)),
            )
    return result, rom


def normalized_main(rom: bytes) -> tuple[tuple[int, ...], ...]:
    main_end = rom.find(bytes.fromhex("0034350f00060d37")) - 0x70
    if main_end < MAIN_ROM:
        raise ValueError("cannot locate regional executable end")
    words = struct.unpack(f">{main_end // 4}I", rom[:main_end])
    return tuple(normalized_word(word) for word in words)


def signature_index(normalized: tuple, signatures: list[tuple]) -> dict[tuple, list[int]]:
    keys = {signature[:min(8, len(signature))] for signature in signatures}
    lengths = sorted({len(key) for key in keys})
    result = {key: [] for key in keys}
    for index in range(MAIN_ROM // 4, len(normalized)):
        for length in lengths:
            key = normalized[index:index + length]
            if key in result:
                result[key].append(index)
    return result


def find_signature(
    normalized: tuple, index: dict[tuple, list[int]],
    signature: tuple, expected: int,
) -> int:
    first = signature[:min(8, len(signature))]
    candidates = []
    for position in index.get(first, ()):
        if normalized[position:position + len(signature)] == signature:
            candidates.append(MAIN_VRAM + position * 4 - MAIN_ROM)
    if not candidates:
        raise ValueError(f"no regional signature near 0x{expected:08X}")
    return min(candidates, key=lambda address: abs(address - expected))


def fragment_starts(rom: bytes) -> list[int]:
    starts = []
    position = 0
    while True:
        position = rom.find(b"FRAGMENT", position)
        if position < 0:
            break
        if position >= 8 and position % 0x10 == 8:
            starts.append(position - 8)
        position += 1
    if len(starts) < 89:
        raise ValueError(f"only found {len(starts)} fragment headers")
    return starts


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) + kind + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def write_rgba_png(path: Path, width: int, height: int, pixels: bytes) -> None:
    rows = b"".join(
        b"\0" + pixels[y * width * 4:(y + 1) * width * 4]
        for y in range(height)
    )
    data = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(rows, 9))
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(data)


def read_rgba_png(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"{path} is not a PNG")
    position = 8
    compressed = bytearray()
    width = height = 0
    while position < len(data):
        size = struct.unpack_from(">I", data, position)[0]
        kind = data[position + 4:position + 8]
        payload = data[position + 8:position + 8 + size]
        position += 12 + size
        if kind == b"IHDR":
            width, height, depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", payload)
            )
            if (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0):
                raise ValueError(f"{path} must remain 8-bit non-interlaced RGBA")
        elif kind == b"IDAT":
            compressed += payload
        elif kind == b"IEND":
            break
    raw = zlib.decompress(bytes(compressed))
    stride = width * 4
    pixels = bytearray()
    prior = bytearray(stride)
    offset = 0
    for _ in range(height):
        method = raw[offset]
        row = bytearray(raw[offset + 1:offset + 1 + stride])
        offset += 1 + stride
        for index in range(stride):
            left = row[index - 4] if index >= 4 else 0
            up = prior[index]
            upper_left = prior[index - 4] if index >= 4 else 0
            if method == 1:
                row[index] = (row[index] + left) & 0xFF
            elif method == 2:
                row[index] = (row[index] + up) & 0xFF
            elif method == 3:
                row[index] = (row[index] + ((left + up) // 2)) & 0xFF
            elif method == 4:
                estimate = left + up - upper_left
                distances = (
                    abs(estimate - left), abs(estimate - up),
                    abs(estimate - upper_left),
                )
                predictor = (left, up, upper_left)[distances.index(min(distances))]
                row[index] = (row[index] + predictor) & 0xFF
            elif method != 0:
                raise ValueError(f"{path} uses unsupported PNG filter {method}")
        pixels += row
        prior = row
    return width, height, bytes(pixels)


def encode_texture(pixels: bytes, fmt: str) -> bytes:
    output = bytearray()
    if fmt == "rgba32":
        return pixels
    if fmt == "rgba16":
        for position in range(0, len(pixels), 4):
            red, green, blue, alpha = pixels[position:position + 4]
            value = (
                (red >> 3) << 11
                | (green >> 3) << 6
                | (blue >> 3) << 1
                | (1 if alpha >= 128 else 0)
            )
            output += struct.pack(">H", value)
        return bytes(output)
    values = [
        ((pixels[pos] + pixels[pos + 1] + pixels[pos + 2]) // 3,
         pixels[pos + 3])
        for pos in range(0, len(pixels), 4)
    ]
    if fmt == "ia4":
        for index in range(0, len(values), 2):
            first_i, first_a = values[index]
            second_i, second_a = values[index + 1]
            first = ((first_i * 7 + 127) // 255) << 1 | (first_a >= 128)
            second = ((second_i * 7 + 127) // 255) << 1 | (second_a >= 128)
            output.append(first << 4 | second)
    elif fmt == "ia8":
        output += bytes(((intensity >> 4) << 4) | (alpha >> 4)
                        for intensity, alpha in values)
    elif fmt == "ia16":
        for intensity, alpha in values:
            output += bytes((intensity, alpha))
    elif fmt == "i8":
        output += bytes(intensity for intensity, _ in values)
    elif fmt == "i4":
        for index in range(0, len(values), 2):
            first = values[index][0] >> 4
            second = values[index + 1][0] >> 4
            output.append(first << 4 | second)
    else:
        raise ValueError(f"unsupported editable texture format {fmt}")
    return bytes(output)


def n64_odd_row_dword_swap(data: bytes, row_bytes: int, height: int) -> bytes:
    """Convert between linear rows and the N64's odd-row TMEM word order.

    Every 64-bit group on an odd row stores its two 32-bit halves exchanged.
    This operation is its own inverse, so export and import use the same helper.
    """
    if len(data) != row_bytes * height:
        raise ValueError("odd-row dword swap dimensions do not match data")
    output = bytearray(data)
    for y in range(1, height, 2):
        row = y * row_bytes
        for x in range(0, row_bytes - 7, 8):
            output[row + x:row + x + 8] = (
                data[row + x + 4:row + x + 8]
                + data[row + x:row + x + 4]
            )
    return bytes(output)


def encoded_texture_row_bytes(width: int, fmt: str) -> int:
    if fmt in ("i4", "ia4"):
        return (width + 1) // 2
    if fmt in ("ia8", "i8", "ci8"):
        return width
    if fmt in ("rgba16", "ia16"):
        return width * 2
    if fmt == "rgba32":
        return width * 4
    raise ValueError(f"unsupported editable texture format {fmt}")


def decode_ci8(indices: bytes, palette: bytes) -> bytes:
    if len(palette) != 0x200:
        raise ValueError("CI8 palette must contain 256 RGBA16 colours")
    colours = [
        decode_texture(palette[index:index + 2], 0, 2)
        for index in range(0, 0x200, 2)
    ]
    return b"".join(colours[index] for index in indices)


def rgba16_value(red: int, green: int, blue: int, alpha: int) -> int:
    return (
        (red >> 3) << 11 | (green >> 3) << 6 | (blue >> 3) << 1
        | (1 if alpha >= 128 else 0)
    )


def encode_ci8(pixels: bytes, palette: bytes, source_indices: bytes) -> bytes:
    """Map an RGBA edit to CI8 while retaining unchanged source indices."""
    if len(palette) != 0x200 or len(source_indices) * 4 != len(pixels):
        raise ValueError("CI8 pixels, indices, or palette have the wrong size")
    values = [int.from_bytes(palette[pos:pos + 2], "big")
              for pos in range(0, 0x200, 2)]
    exact: dict[int, int] = {}
    for index, value in enumerate(values):
        exact.setdefault(value, index)

    def distance(first: int, second: int) -> int:
        return (
            (((first >> 11) & 0x1F) - ((second >> 11) & 0x1F)) ** 2
            + (((first >> 6) & 0x1F) - ((second >> 6) & 0x1F)) ** 2
            + (((first >> 1) & 0x1F) - ((second >> 1) & 0x1F)) ** 2
            + (64 if (first & 1) != (second & 1) else 0)
        )

    output = bytearray()
    for position, source_index in enumerate(source_indices):
        pixel = position * 4
        target = rgba16_value(*pixels[pixel:pixel + 4])
        if values[source_index] == target:
            output.append(source_index)
        elif target in exact:
            output.append(exact[target])
        else:
            output.append(min(range(256), key=lambda i: distance(target, values[i])))
    return bytes(output)


def presjpeg_resource(image: bytes, width: int, height: int) -> bytes:
    encoded = (
        b"PRESJPEG" + struct.pack(">II", 0x10, width * height * 2) + image
    )
    return encoded + b"\0" * ((-len(encoded)) & 0xF)




def graphics_patches(repo: Path, code: str, source_rom: bytes) -> list[dict]:
    directory = repo / f"assets/localization/mobile/graphics/{ASSET_LANGUAGE.get(code, code)}"
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    patches = []
    archive_edits: dict[tuple[int, int], list[tuple[dict, bytes]]] = {}
    archive_models: dict[tuple[int, int], tuple[dict, bytes]] = {}
    for model in manifest.get("models", []):
        if model.get("source") != "archive":
            raise ValueError(f"unsupported editable model source {model.get('source')}")
        key = (model["archive_slot"], model["resource_index"])
        if key in archive_models:
            raise ValueError(f"duplicate editable model resource {key}")
        archive_models[key] = (model, (directory / model["file"]).read_bytes())
    for texture in manifest["textures"]:
        if texture["format"] == "jpeg":
            replacement = (directory / texture["file"]).read_bytes()
            width, height = jpeg_dimensions(replacement)
            if (width, height) != (texture["width"], texture["height"]):
                raise ValueError(f"{texture['file']} dimensions were changed")
            jpeg_require_stadium_layout(replacement, texture["file"])
        else:
            width, height, pixels = read_rgba_png(directory / texture["file"])
            if (width, height) == (320, 36) and \
                    (texture["width"], texture["height"]) == (320, 37) and \
                    texture["file"].startswith("archive1_099_"):
                # Only 36 rows are uploaded at runtime.  Accept editors/dumps
                # that omit the unused storage row and restore a transparent
                # final row before encoding the fixed-size Archive 1 texture.
                pixels += bytes(width * 4)
                height = 37
            if (width, height) != (texture["width"], texture["height"]):
                raise ValueError(f"{texture['file']} dimensions were changed")
            if texture["format"] == "ci8":
                palette_width, palette_height, palette_pixels = read_rgba_png(
                    directory / texture["palette_file"]
                )
                if (palette_width, palette_height) != (16, 16):
                    raise ValueError(
                        f"{texture['palette_file']} dimensions were changed"
                    )
                palette = encode_texture(palette_pixels, "rgba16")
                replacement = encode_ci8(
                    pixels, palette, bytes.fromhex(texture["source_linear_indices"])
                )
            else:
                replacement = encode_texture(pixels, texture["format"])
            if texture.get("storage_transform") == "n64-odd-row-dword-swap":
                replacement = n64_odd_row_dword_swap(
                    replacement,
                    encoded_texture_row_bytes(width, texture["format"]),
                    height,
                )
            if len(replacement) != texture["byte_count"]:
                raise ValueError(f"{texture['file']} encoded to the wrong size")
        if texture.get("source", "fragment") == "archive":
            key = (texture["archive_slot"], texture["resource_index"])
            archive_edits.setdefault(key, []).append((texture, replacement))
        else:
            offset = int(texture["rom_offset"], 16)
            expected = source_rom[offset:offset + len(replacement)]
            if hashlib.sha256(expected).hexdigest() != texture["source_sha256"]:
                raise ValueError(
                    f"{texture['file']} source bytes do not match profile"
                )
            patches.append({
                "offset": f"0x{offset:X}",
                "expected": expected.hex(),
                "replacement": replacement.hex(),
            })
    edited_archive_slots = {
        key[0] for key in (*archive_edits.keys(), *archive_models.keys())
    }
    for archive_slot in sorted(edited_archive_slots):
        archive_start, archive_size, resource_count = archive_details(
            source_rom, archive_slot
        )
        original_archive = source_rom[archive_start:archive_start + archive_size]
        resources = []
        for resource_index in range(resource_count):
            descriptor = 0x10 + resource_index * 0x10
            relative, allocated = struct.unpack_from(
                ">II", original_archive, descriptor
            )
            resources.append(bytes(original_archive[relative:relative + allocated]))
        slot_edits = {
            resource_index: edits
            for (slot, resource_index), edits in archive_edits.items()
            if slot == archive_slot
        }
        slot_models = {
            resource_index: model
            for (slot, resource_index), model in archive_models.items()
            if slot == archive_slot
        }
        changed = False
        for resource_index in sorted(set(slot_edits) | set(slot_models)):
            edits = slot_edits.get(resource_index, [])
            if not 0 <= resource_index < resource_count:
                raise ValueError(f"invalid archive resource {resource_index}")
            packed = resources[resource_index]
            is_jpeg = bool(edits and edits[0][0]["format"] == "jpeg")
            if is_jpeg:
                if packed[:8] != b"PRESJPEG":
                    raise ValueError(
                        f"archive {archive_slot} resource {resource_index} "
                        "is not PRESJPEG"
                    )
                resource = bytearray(jpeg_payload(packed))
            else:
                if packed[:8] != b"PERS-SZP":
                    raise ValueError(
                        f"archive {archive_slot} resource {resource_index} "
                        "is not PERS-SZP"
                    )
                resource = bytearray(yay0_decode(packed[0x18:]))
            resource_changed = False
            if resource_index in slot_models:
                model, replacement_model = slot_models[resource_index]
                original_resource = bytes(resource)
                if hashlib.sha256(original_resource).hexdigest() != model["source_sha256"]:
                    raise ValueError(
                        f"{model['file']} archive model source bytes do not match"
                    )
                if model.get("format") != "pers-szp-decompressed":
                    raise ValueError(
                        f"unsupported editable model format {model.get('format')}"
                    )
                if replacement_model[8:16] != b"FRAGMENT":
                    raise ValueError(f"{model['file']} is not a relocated model resource")
                if replacement_model != original_resource:
                    resource = bytearray(replacement_model)
                    resource_changed = True
            for texture, replacement in edits:
                offset = 0 if is_jpeg else int(texture["resource_offset"], 16)
                expected_length = len(resource) if is_jpeg else len(replacement)
                original = (
                    jpeg_payload(packed) if is_jpeg
                    else yay0_decode(packed[0x18:])
                )
                expected = bytes(original[offset:offset + expected_length])
                if hashlib.sha256(expected).hexdigest() != texture["source_sha256"]:
                    raise ValueError(
                        f"{texture['file']} archive source bytes do not match"
                    )
                if expected != replacement:
                    if is_jpeg:
                        resource = bytearray(replacement)
                    else:
                        resource[offset:offset + len(replacement)] = replacement
                    resource_changed = True
            if not resource_changed:
                continue
            if is_jpeg:
                texture = edits[0][0]
                image = bytes(resource)
                resources[resource_index] = presjpeg_resource(
                    image, texture["width"], texture["height"]
                )

            else:
                pers_header = bytearray(packed[:0x18])
                # PERS-SZP repeats the decompressed resource length in both
                # header size fields. Texture-only edits preserve it, but an
                # editable model may legitimately change its byte length.
                struct.pack_into(">II", pers_header, 0xC, len(resource), len(resource))
                encoded = bytes(pers_header) + yay0_encode(bytes(resource))
                resources[resource_index] = (
                    encoded + b"\0" * ((-len(encoded)) & 0xF)
                )
            changed = True
        if changed:
            first_resource = 0x10 + resource_count * 0x10
            required_size = first_resource + sum(map(len, resources))
            patch_size = archive_size
            if required_size > archive_size:
                # Master archives are followed by 0xFF ROM padding. Expand only
                # into the verified contiguous padding and update the archive's
                # own size word; never rewrite native JPEG streams merely to fit.
                padding_end = archive_start + archive_size
                while padding_end < len(source_rom) and source_rom[padding_end] == 0xFF:
                    padding_end += 1
                available_size = padding_end - archive_start
                patch_size = (required_size + 0xF) & ~0xF
                if patch_size > available_size:
                    raise ValueError(
                        f"edited archive {archive_slot} needs {patch_size} bytes, "
                        f"but only {available_size} bytes are safely available"
                    )
                print(
                    f"expanded {code} archive {archive_slot} from "
                    f"{archive_size} to {patch_size} bytes in verified ROM padding"
                )
            original_patch_region = source_rom[
                archive_start:archive_start + patch_size
            ]
            cursor = first_resource
            rebuilt = bytearray(original_patch_region[:first_resource])
            rebuilt += b"\0" * (patch_size - len(rebuilt))
            struct.pack_into(">I", rebuilt, 8, patch_size)
            for resource_index, packed in enumerate(resources):
                if cursor + len(packed) > patch_size:
                    raise ValueError(
                        f"edited archive {archive_slot} resources exceed "
                        "the safely available capacity"
                    )
                descriptor = 0x10 + resource_index * 0x10
                struct.pack_into(">II", rebuilt, descriptor, cursor, len(packed))
                rebuilt[cursor:cursor + len(packed)] = packed
                cursor += len(packed)
            replacement_path = (
                repo / f"assets/localization/mobile/{code}/"
                f"mobile-graphics-archive{archive_slot}.bin"
            )
            replacement_path.write_bytes(rebuilt)
            patches.append({
                "offset": f"0x{archive_start:X}",
                "size": patch_size,
                "expected_sha256": hashlib.sha256(
                    original_patch_region
                ).hexdigest(),
                "replacement_file": (
                    f"{code}/mobile-graphics-archive{archive_slot}.bin"
                ),
            })
    return patches

def asset_root(rom: bytes) -> int:
    signature = bytes.fromhex("0034350f00060d37030438393a3b3c3d")
    position = rom.find(signature)
    if position >= 0 and rom.find(signature, position + 1) < 0:
        return position - 0x70
    # Japanese has a slightly different message-header signature, but the
    # first two master archive pointers are shared by every verified release.
    signature = bytes.fromhex("0171800001898000")
    position = rom.find(signature)
    if position < 0 or rom.find(signature, position + 1) >= 0:
        raise ValueError("cannot uniquely locate the asset root")
    return position - 0x10


def text_patch(repo: Path, code: str, source_rom: bytes, layer: Path) -> dict:
    asset_code = ASSET_LANGUAGE.get(code, code)
    document = json.loads(
        (repo / f"assets/localization/mobile/text/{asset_code}.json").read_text(
            encoding="utf-8"
        )
    )
    root = asset_root(source_rom)
    archive = int.from_bytes(source_rom[root + 0x18:root + 0x1C], "big")
    resource_count = int.from_bytes(source_rom[archive + 0xC:archive + 0x10], "big")
    next_archive = int.from_bytes(source_rom[root + 0x1C:root + 0x20], "big")
    archive_capacity = next_archive - archive
    header_size = 0x10 + resource_count * 0x10
    resources = []
    for index in range(resource_count):
        item_descriptor = archive + 0x10 + index * 0x10
        item_offset = int.from_bytes(source_rom[item_descriptor:item_descriptor + 4], "big")
        item_size = int.from_bytes(source_rom[item_descriptor + 4:item_descriptor + 8], "big")
        resources.append(source_rom[archive + item_offset:archive + item_offset + item_size])

    replacements = [{
        "text_file": document["text_file"],
        "first_entry": document["first_entry"],
        "last_entry": document["last_entry"],
        "entries": document["entries"],
    }] + document.get("additional_text_files", [])
    override_code = MESSAGE_OVERRIDE_LANGUAGE.get(code, asset_code)
    override_path = (
        repo / f"assets/localization/mobile/text-overrides/{override_code}.json"
    )
    if override_path.exists():
        override_document = json.loads(
            override_path.read_text(encoding="utf-8")
        )
        for block in override_document.get("text_files", []):
            entries = block.get("entries", [])
            if not entries:
                continue
            indices = [item["index"] for item in entries]
            replacements.append({
                "text_file": block["text_file"],
                "first_entry": min(indices),
                "last_entry": max(indices),
                "entries": entries,
            })
    for replacement in replacements:
        text_file = replacement["text_file"]
        if not 0 <= text_file < resource_count:
            raise ValueError(f"{code} message file {text_file} is unavailable")
        original = resources[text_file]
        count = int.from_bytes(original[:4], "big")
        entries = []
        for index in range(count):
            start = int.from_bytes(original[4 + index * 4:8 + index * 4], "big")
            end = original.find(b"\0", start)
            if end < 0:
                raise ValueError(
                    f"{code} message file {text_file} entry {index} is unterminated"
                )
            entries.append(original[start:end])
        first = replacement["first_entry"]
        last = replacement["last_entry"]
        for item in replacement["entries"]:
            index = item["index"]
            if not first <= index <= last or index >= count:
                raise ValueError(
                    f"Mobile text file {text_file} index {index} is outside "
                    f"{first}-{last}"
                )
            entries[index] = item["text"].replace(r"\n", "\n").encode("cp1252")
        entry_table_size = 4 + count * 4
        rebuilt = bytearray(struct.pack(">I", count))
        body = bytearray()
        for entry in entries:
            rebuilt += struct.pack(">I", entry_table_size + len(body))
            body += entry + b"\0"
        rebuilt += body
        resources[text_file] = bytes(rebuilt) + b"\0" * ((-len(rebuilt)) & 0xF)

    replacement = bytearray(archive_capacity)
    replacement[:header_size] = source_rom[archive:archive + header_size]
    cursor = header_size
    for index, resource in enumerate(resources):
        if cursor + len(resource) > archive_capacity:
            raise ValueError(
                f"{code} expanded Mobile Stadium text exceeds message archive capacity"
            )
        item_descriptor = 0x10 + index * 0x10
        struct.pack_into(">II", replacement, item_descriptor, cursor, len(resource))
        replacement[cursor:cursor + len(resource)] = resource
        cursor += len(resource)
        cursor = (cursor + 0xF) & ~0xF
    struct.pack_into(">I", replacement, 8, cursor)

    destination = layer / "mobile-text-archive.bin"
    destination.write_bytes(replacement)
    original_archive = source_rom[archive:archive + archive_capacity]
    return {
        "offset": f"0x{archive:X}",
        "size": archive_capacity,
        "expected_sha256": hashlib.sha256(original_archive).hexdigest(),
        "replacement_file": f"{code}/{destination.name}",
    }

def texture_commands(fragment: bytes) -> list[dict]:
    textures = {}
    for offset in range(0, len(fragment) - 8, 8):
        word0, pointer = struct.unpack_from(">II", fragment, offset)
        if word0 >> 24 != 0xFD or not 0x84100000 <= pointer < 0x84200000:
            continue
        load_count = None
        render_word = None
        tile_word = None
        for command in range(offset + 8, min(offset + 0x50, len(fragment) - 8), 8):
            first, second = struct.unpack_from(">II", fragment, command)
            opcode = first >> 24
            if opcode == 0xF3:
                load_count = ((second >> 12) & 0xFFF) + 1
            elif opcode == 0xF5 and load_count is not None:
                render_word = first
            elif opcode == 0xF2:
                tile_word = second
                break
        if load_count is None or render_word is None or tile_word is None:
            continue
        load_size = (word0 >> 19) & 3
        render_format = (render_word >> 21) & 7
        render_size = (render_word >> 19) & 3
        byte_count = load_count * (1 << load_size) // 2
        width = ((tile_word >> 12) & 0xFFF) // 4 + 1
        bits = (4, 8, 16, 32)[render_size]
        height = byte_count * 8 // (width * bits)
        key = (pointer, byte_count, width, height, render_format, render_size)
        textures[key] = {
            "display_list_offset": offset,
            "pointer": pointer,
            "byte_count": byte_count,
            "width": width,
            "height": height,
            "format": render_format,
            "size": render_size,
        }
    return list(textures.values())


def decode_texture(raw: bytes, fmt: int, size: int) -> bytes:
    pixels = bytearray()
    if fmt == 0 and size == 3:  # RGBA32
        return raw
    if fmt == 0 and size == 2:  # RGBA16
        for position in range(0, len(raw), 2):
            value = int.from_bytes(raw[position:position + 2], "big")
            red = (value >> 11) & 0x1F
            green = (value >> 6) & 0x1F
            blue = (value >> 1) & 0x1F
            pixels += bytes((
                red << 3 | red >> 2,
                green << 3 | green >> 2,
                blue << 3 | blue >> 2,
                255 if value & 1 else 0,
            ))
    elif fmt == 3 and size == 0:  # IA4
        for value in raw:
            for nibble in (value >> 4, value & 0xF):
                intensity = ((nibble >> 1) * 255 + 3) // 7
                alpha = 255 if nibble & 1 else 0
                pixels += bytes((intensity, intensity, intensity, alpha))
    elif fmt == 3 and size == 1:  # IA8
        for value in raw:
            intensity = (value >> 4) * 17
            alpha = (value & 0xF) * 17
            pixels += bytes((intensity, intensity, intensity, alpha))
    elif fmt == 3 and size == 2:  # IA16
        for intensity, alpha in zip(raw[0::2], raw[1::2]):
            pixels += bytes((intensity, intensity, intensity, alpha))
    elif fmt == 4 and size == 1:  # I8
        for value in raw:
            pixels += bytes((value, value, value, 255))
    elif fmt == 4 and size == 0:  # I4
        for value in raw:
            for nibble in (value >> 4, value & 0xF):
                intensity = nibble * 17
                pixels += bytes((intensity, intensity, intensity, 255))
    else:
        raise ValueError(f"unsupported mobile texture format {fmt}, size {size}")
    return bytes(pixels)


def yay0_decode(data: bytes) -> bytes:
    if data[:4] != b"Yay0":
        raise ValueError("resource does not contain a Yay0 stream")
    size, link_offset, chunk_offset = struct.unpack_from(">III", data, 4)
    output = bytearray()
    mask_offset = 0x10
    mask = bits = 0
    while len(output) < size:
        if bits == 0:
            mask = struct.unpack_from(">I", data, mask_offset)[0]
            mask_offset += 4
            bits = 32
        if mask & 0x80000000:
            output.append(data[chunk_offset])
            chunk_offset += 1
        else:
            value = struct.unpack_from(">H", data, link_offset)[0]
            link_offset += 2
            distance = (value & 0xFFF) + 1
            count = value >> 12
            if count == 0:
                count = data[chunk_offset] + 18
                chunk_offset += 1
            else:
                count += 2
            if distance > len(output):
                raise ValueError("invalid Yay0 back-reference")
            for _ in range(count):
                output.append(output[-distance])
        mask = (mask << 1) & 0xFFFFFFFF
        bits -= 1
    return bytes(output[:size])


def yay0_encode(data: bytes) -> bytes:
    """Encode a deterministic, size-optimized Yay0 stream."""
    positions: dict[bytes, list[int]] = {}
    matches: list[tuple[int, int]] = [(0, 0)] * len(data)
    for cursor in range(len(data)):
        best_length = best_distance = 0
        key = data[cursor:cursor + 3]
        candidates = positions.get(key, ()) if len(key) == 3 else ()
        for previous in reversed(candidates[-96:]):
            distance = cursor - previous
            if distance > 0x1000:
                break
            limit = min(273, len(data) - cursor)
            length = 3
            while (
                length < limit
                and data[previous + length] == data[cursor + length]
            ):
                length += 1
            if length > best_length:
                best_length, best_distance = length, distance
                if length == limit:
                    break
        matches[cursor] = (best_length, best_distance)
        if len(key) == 3:
            bucket = positions.setdefault(key, [])
            bucket.append(cursor)
            while bucket and cursor - bucket[0] > 0x1000:
                bucket.pop(0)

    # Costs are in bits: one mask bit plus one, two, or three payload bytes.
    size = len(data)
    costs = [0] * (size + 1)
    choices = [1] * size
    for cursor in range(size - 1, -1, -1):
        best_cost = 9 + costs[cursor + 1]
        best_advance = 1
        longest = matches[cursor][0]
        for length in range(3, min(longest, 17) + 1):
            cost = 17 + costs[cursor + length]
            if cost < best_cost:
                best_cost, best_advance = cost, length
        for length in range(18, longest + 1):
            cost = 25 + costs[cursor + length]
            if cost < best_cost:
                best_cost, best_advance = cost, length
        costs[cursor] = best_cost
        choices[cursor] = best_advance

    masks: list[int] = []
    links = bytearray()
    chunks = bytearray()
    cursor = 0
    mask = 0
    bits = 0
    while cursor < size:
        advance = choices[cursor]
        use_match = advance >= 3
        mask = (mask << 1) | (0 if use_match else 1)
        bits += 1
        if use_match:
            distance = matches[cursor][1]
            if advance >= 18:
                links += struct.pack(">H", distance - 1)
                chunks.append(advance - 18)
            else:
                links += struct.pack(
                    ">H", (advance - 2) << 12 | (distance - 1)
                )
        else:
            chunks.append(data[cursor])
        cursor += advance
        if bits == 32:
            masks.append(mask)
            mask = bits = 0
    if bits:
        masks.append(mask << (32 - bits))
    mask_data = b"".join(struct.pack(">I", value) for value in masks)
    link_offset = 0x10 + len(mask_data)
    chunk_offset = link_offset + len(links)
    return (
        b"Yay0" + struct.pack(">III", len(data), link_offset, chunk_offset)
        + mask_data + links + chunks
    )


def archive_details(rom: bytes, slot: int) -> tuple[int, int, int]:
    root = asset_root(rom)
    start = int.from_bytes(
        rom[root + 0x10 + slot * 4:root + 0x14 + slot * 4], "big"
    )
    size = int.from_bytes(rom[start + 8:start + 12], "big")
    count = int.from_bytes(rom[start + 12:start + 16], "big")
    if not (0 < start < len(rom) and 0x10 <= size <= len(rom) - start):
        raise ValueError(f"archive slot {slot} has invalid bounds")
    return start, size, count


MOBILE_PANEL_TEXTURES = (
    # The three illustrated connection panels in the Mobile Stadium help.
    # Resource 228 contains three consecutive 248x89 RGBA16 frames.
    (1, 228, 0x18, 248, 89),
    (1, 228, 0xAC90, 248, 89),
    (1, 228, 0x15908, 248, 89),
    # The large illustrated phone/link panel and the welcome panel.
    (1, 229, 0x10, 256, 137),
    (1, 253, 0x10, 160, 142),
    # Yellow Mobile Stadium title banner used by the help/menu presentation.
    (1, 254, 0x18, 152, 44),
)
# Metallic Mobile Stadium battle-intro title. The resource stores one IA8
# 320x37 image; rendering uploads its visible first 36 rows as three 320x12
# TMEM slices. GlideN64 labels those runtime loads as format/size #3/#1.
MOBILE_BATTLE_TITLE_TEXTURE = (1, 99, 0x10, 320, 37)
MOBILE_PRESENTATION_JPEGS = tuple(range(48))
MOBILE_HELP_JPEGS = tuple(range(59))
# International Archive 13 keeps black PRESJPEG placeholders for these three
# Japanese-only Mobile Stadium help screenshots.  Seed the editable Western
# assets from the retail Japanese resources while retaining each destination
# ROM's placeholder bytes as the import precondition.
MOBILE_HELP_REFERENCE_JPEGS = (56, 57, 58)


def clean_opaque_panel(pixels: bytes, width: int, height: int) -> bytes:
    """Remove RGBA5551 ordered dither without smearing panel artwork edges."""
    current = bytearray(pixels)
    neighbours = (
        (-1, -1, 1), (0, -1, 2), (1, -1, 1),
        (-1,  0, 2), (0,  0, 4), (1,  0, 2),
        (-1,  1, 1), (0,  1, 2), (1,  1, 1),
    )
    for _ in range(2):
        source = memoryview(current)
        output = bytearray(current)
        for y in range(height):
            for x in range(width):
                offset = (y * width + x) * 4
                centre = source[offset:offset + 3]
                totals = [0, 0, 0]
                weight_total = 0
                for dx, dy, weight in neighbours:
                    sx, sy = x + dx, y + dy
                    if not (0 <= sx < width and 0 <= sy < height):
                        continue
                    sample_offset = (sy * width + sx) * 4
                    sample = source[sample_offset:sample_offset + 3]
                    if max(abs(sample[c] - centre[c]) for c in range(3)) > 40:
                        continue
                    for channel in range(3):
                        totals[channel] += sample[channel] * weight
                    weight_total += weight
                for channel in range(3):
                    output[offset + channel] = (
                        totals[channel] + weight_total // 2
                    ) // weight_total
                output[offset + 3] = 0xFF
        current = output
    return bytes(current)


def archive_resource(rom: bytes, slot: int, index: int) -> bytes:
    start, _, count = archive_details(rom, slot)
    if not 0 <= index < count:
        raise ValueError(f"invalid archive {slot} resource {index}")
    descriptor = start + 0x10 + index * 0x10
    relative, allocated = struct.unpack_from(">II", rom, descriptor)
    return bytes(rom[start + relative:start + relative + allocated])


def jpeg_payload(resource: bytes) -> bytes:
    if resource[:8] != b"PRESJPEG" or resource[16:18] != b"\xFF\xD8":
        raise ValueError("resource does not contain a PRESJPEG image")
    end = resource.find(b"\xFF\xD9", 18)
    if end < 0:
        raise ValueError("PRESJPEG image has no end marker")
    return bytes(resource[16:end + 2])


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if data[:2] != b"\xFF\xD8":
        raise ValueError("file is not a JPEG image")
    position = 2
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
           0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while position + 4 <= len(data):
        while position < len(data) and data[position] != 0xFF:
            position += 1
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if position + 2 > len(data):
            break
        length = int.from_bytes(data[position:position + 2], "big")
        if length < 2 or position + length > len(data):
            break
        if marker in sof and length >= 7:
            height, width = struct.unpack_from(">HH", data, position + 3)
            return width, height
        position += length
    raise ValueError("cannot find JPEG dimensions")


def jpeg_require_stadium_layout(data: bytes, name: str = "JPEG") -> None:
    """Reject JPEG variants unsupported by Stadium's fixed JPEG decoder.

    Native PRESJPEG resources are baseline, three-component 4:2:0 streams
    without a JFIF APP0 segment.  Pillow and many image editors default to
    4:4:4/JFIF, which is a valid desktop JPEG but stalls the game's decoder.
    """
    position = 2
    sampling = None
    has_jfif = False
    marker_layout = []
    quantization = None
    huffman_order = None
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if position + 2 > len(data):
            break
        length = int.from_bytes(data[position:position + 2], "big")
        if length < 2 or position + length > len(data):
            break
        payload = data[position + 2:position + length]
        if marker == 0xE0 and payload.startswith(b"JFIF\0"):
            has_jfif = True
        if marker in {0xDB, 0xC4, 0xC0}:
            marker_layout.append(marker)
        if marker == 0xDB:
            quantization = payload
        if marker == 0xC4:
            huffman_order = []
            cursor = 0
            while cursor < len(payload):
                if cursor + 17 > len(payload):
                    huffman_order = None
                    break
                huffman_order.append(payload[cursor])
                value_count = sum(payload[cursor + 1:cursor + 17])
                cursor += 17 + value_count
            if cursor != len(payload):
                huffman_order = None
        if marker == 0xC0 and len(payload) >= 15 and payload[5] == 3:
            sampling = (payload[7], payload[10], payload[13])
        if marker == 0xDA:
            break
        position += length
    native_quantization = b"\0" + b"\1" * 64 + b"\1" + b"\1" * 64
    quantization_mismatch = (
        name.startswith(("archive13_056_", "archive13_057_", "archive13_058_"))
        and quantization != native_quantization
    )
    if (sampling != (0x22, 0x11, 0x11) or has_jfif or
            quantization_mismatch or
            huffman_order != [0x00, 0x01, 0x10, 0x11] or
            marker_layout != [0xDB, 0xC4, 0xC0]):
        raise ValueError(
            f"{name} is not Stadium-compatible baseline 4:2:0 JPEG data: "
            "expected combined DQT/DHT markers, DC0/DC1/AC0/AC1 Huffman "
            "ordering before SOF0, and no JFIF header"
        )


def archive_resource_textures(resource: bytes) -> list[dict]:
    """Find editable texture descriptors in a relocated model resource.

    Stadium model materials use a compact descriptor immediately before a
    relocated texture pointer: format/size metadata, width/texel count, then
    the pointer.  Older resources also use a log2-width/log2-height RGBA16
    descriptor, so retain that parser as a compatibility fallback.
    """
    if resource[8:16] != b"FRAGMENT":
        return []
    relocation = int.from_bytes(resource[0x14:0x18], "big")
    if not 0x20 <= relocation <= len(resource) - 4:
        return []
    count = int.from_bytes(resource[relocation:relocation + 4], "big")
    if relocation + 4 + count * 4 > len(resource):
        return []
    formats = {
        (0, 2): "rgba16",
        (3, 1): "ia8",
        (3, 2): "ia16",
        (4, 0): "i4",
        (4, 1): "i8",
    }
    bits_per_pixel = {0: 4, 1: 8, 2: 16}
    textures = {}
    for index in range(count):
        entry = int.from_bytes(
            resource[relocation + 4 + index * 4:
                     relocation + 8 + index * 4], "big"
        )
        if entry >> 24 != 2:
            continue
        pointer_location = entry & 0xFFFFFF
        if not 8 <= pointer_location < relocation:
            continue
        metadata, dimensions, pointer = struct.unpack_from(
            ">III", resource, pointer_location - 8
        )
        fmt = metadata >> 24
        size = (metadata >> 16) & 0xFF
        width = dimensions >> 16
        texel_count = dimensions & 0xFFFF
        canonical = False
        if (
            (fmt, size) in formats
            and width > 0 and texel_count > 0
            and texel_count % width == 0
        ):
            height = texel_count // width
            byte_count = texel_count * bits_per_pixel[size] // 8
            offset = pointer - 0x8FF00000
            if (
                height > 0 and 0x20 <= offset < relocation
                and offset + byte_count <= relocation
            ):
                textures[(offset, byte_count, width, height, fmt, size)] = {
                    "offset": offset,
                    "width": width,
                    "height": height,
                    "byte_count": byte_count,
                    "format": fmt,
                    "size": size,
                    "label": formats[(fmt, size)],
                }
                canonical = True

        if canonical:
            continue

        # Compatibility form used by a subset of archive resources.
        width, height = dimensions >> 16, dimensions & 0xFFFF
        if (
            width == 0 or height == 0
            or width & (width - 1) or height & (height - 1)
            or metadata >> 24 != int(math.log2(width))
            or (metadata >> 16) & 0xFF != int(math.log2(height))
        ):
            continue
        offset = pointer - 0x8FF00000
        byte_count = width * height * 2
        if not 0x20 <= offset < relocation or offset + byte_count > relocation:
            continue
        key = (offset, byte_count, width, height, 0, 2)
        textures.setdefault(key, {
            "offset": offset,
            "width": width,
            "height": height,
            "byte_count": byte_count,
            "format": 0,
            "size": 2,
            "label": "rgba16",
        })
    return list(textures.values())


ARCHIVE1_TEXTURE_FORMATS = {
    (0, 2): "rgba16",
    (0, 3): "rgba32",
    (2, 1): "ci8",
    (3, 0): "ia4",
    (3, 1): "ia8",
    (3, 2): "ia16",
    (4, 0): "i4",
    (4, 1): "i8",
}
ARCHIVE1_TEXTURE_BITS = {0: 4, 1: 8, 2: 16, 3: 32}


def archive1_resource_textures(resource: bytes) -> list[dict]:
    """Read Archive 1's pointer table and per-image N64 texture descriptors."""
    if len(resource) < 4:
        return []
    count = int.from_bytes(resource[:4], "big")
    if count > 0x1000 or 4 + count * 4 > len(resource):
        raise ValueError("Archive 1 texture pointer table is invalid")
    textures = []
    for index in range(count):
        pointer = int.from_bytes(
            resource[4 + index * 4:8 + index * 4], "big"
        )
        if pointer >> 24 != 0x8F:
            raise ValueError("Archive 1 texture pointer has the wrong segment")
        offset = pointer - 0x8FF00000
        if not 8 <= offset <= len(resource):
            raise ValueError("Archive 1 texture pointer is out of range")
        width, height = struct.unpack_from(">HH", resource, offset - 8)
        fmt, size = resource[offset - 4], resource[offset - 3]
        if (fmt, size) not in ARCHIVE1_TEXTURE_FORMATS or not width or not height:
            raise ValueError("Archive 1 texture descriptor is unsupported")
        texels = width * height
        byte_count = (texels * ARCHIVE1_TEXTURE_BITS[size] + 7) // 8
        if offset + byte_count > len(resource):
            raise ValueError("Archive 1 texture data is truncated")
        texture = {
            "offset": offset,
            "width": width,
            "height": height,
            "byte_count": byte_count,
            "format": fmt,
            "size": size,
            "label": ARCHIVE1_TEXTURE_FORMATS[(fmt, size)],
        }
        if (fmt, size) == (2, 1):
            texture["palette_offset"] = offset + byte_count
            if texture["palette_offset"] + 0x200 > len(resource):
                raise ValueError("Archive 1 CI8 palette is truncated")
        textures.append(texture)
    return textures


def export_graphics(repo: Path, input_root: Path, languages: list[str]) -> None:
    root = repo / "assets/localization/mobile/graphics"
    japanese_rom_path = input_root / "Pokemon Stadium Kin Gin (Japan).z64"
    if not japanese_rom_path.is_file():
        raise FileNotFoundError(
            f"{japanese_rom_path} is required for the Mobile help screenshots"
        )
    japanese_rom = normalize_rom(japanese_rom_path.read_bytes())
    help_references = {
        resource_index: jpeg_payload(
            archive_resource(japanese_rom, 13, resource_index)
        )
        for resource_index in MOBILE_HELP_REFERENCE_JPEGS
    }
    for code in languages:
        profile = json.loads(
            (repo / f"assets/localization/mobile/profiles/{code}.json").read_text()
        )
        rom = normalize_rom(
            source_rom_path(
                repo, input_root, code, profile["source_rom"]
            ).read_bytes()
        )
        if (
            profile.get("source_md5")
            and hashlib.md5(rom).hexdigest() != profile["source_md5"]
        ):
            raise ValueError(f"{code} graphics source ROM does not match its profile")
        if (
            profile.get("source_sha256")
            and hashlib.sha256(rom).hexdigest() != profile["source_sha256"]
        ):
            raise ValueError(f"{code} graphics source ROM does not match its profile")
        start = int(profile["fragments"]["79"], 16)
        end = int(profile["fragments"]["80"], 16)
        fragment = rom[start:end]
        directory = root / code
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            "format": 4,
            "language": code,
            "fragment": 79,
            "fragment_rom_start": f"0x{start:X}",
            "textures": [],
            "models": [],
        }
        for texture in texture_commands(fragment):
            pointer = texture["pointer"]
            offset = pointer - 0x84100000
            raw = fragment[offset:offset + texture["byte_count"]]
            label = {
                (3, 1): "ia8", (3, 2): "ia16",
                (4, 0): "i4", (4, 1): "i8",
            }[(texture["format"], texture["size"])]
            name = (
                f"texture_{pointer:08X}_{label}_"
                f"{texture['width']}x{texture['height']}.png"
            )
            write_rgba_png(
                directory / name, texture["width"], texture["height"],
                decode_texture(raw, texture["format"], texture["size"]),
            )
            manifest["textures"].append({
                "file": name,
                "source": "fragment",
                "rom_offset": f"0x{start + offset:X}",
                "fragment_offset": f"0x{offset:X}",
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "byte_count": len(raw),
                "format": label,
                "width": texture["width"],
                "height": texture["height"],
            })
        archive_start, archive_size, resource_count = archive_details(rom, 16)
        for resource_index in range(resource_count):
            descriptor = archive_start + 0x10 + resource_index * 0x10
            relative, allocated = struct.unpack_from(">II", rom, descriptor)
            resource_rom_offset = archive_start + relative
            packed = rom[resource_rom_offset:resource_rom_offset + allocated]
            if packed[:8] != b"PERS-SZP":
                continue
            resource = yay0_decode(packed[0x18:])
            for texture in archive_resource_textures(resource):
                offset = texture["offset"]
                raw = resource[offset:offset + texture["byte_count"]]
                name = (
                    f"archive16_{resource_index:03d}_{offset:06X}_"
                    f"{texture['label']}_"
                    f"{texture['width']}x{texture['height']}.png"
                )
                write_rgba_png(
                    directory / name, texture["width"], texture["height"],
                    decode_texture(raw, texture["format"], texture["size"]),
                )
                manifest["textures"].append({
                    "file": name,
                    "source": "archive",
                    "archive_slot": 16,
                    "resource_index": resource_index,
                    "resource_offset": f"0x{offset:X}",
                    "source_sha256": hashlib.sha256(raw).hexdigest(),
                    "byte_count": len(raw),
                    "format": texture["label"],
                    "width": texture["width"],
                    "height": texture["height"],
                })
        special_archive1 = {
            (resource_index, offset)
            for archive_slot, resource_index, offset, _, _ in MOBILE_PANEL_TEXTURES
            if archive_slot == 1
        }
        special_archive1.add((
            MOBILE_BATTLE_TITLE_TEXTURE[1], MOBILE_BATTLE_TITLE_TEXTURE[2]
        ))
        _, _, archive1_count = archive_details(rom, 1)
        for resource_index in range(archive1_count):
            packed = archive_resource(rom, 1, resource_index)
            if packed[:8] != b"PERS-SZP":
                continue
            resource = yay0_decode(packed[0x18:])
            for texture in archive1_resource_textures(resource):
                offset = texture["offset"]
                if (resource_index, offset) in special_archive1:
                    continue
                width, height = texture["width"], texture["height"]
                raw = resource[offset:offset + texture["byte_count"]]
                linear = n64_odd_row_dword_swap(
                    raw,
                    encoded_texture_row_bytes(width, texture["label"]),
                    height,
                )
                name = (
                    f"archive1_{resource_index:03d}_{offset:06X}_"
                    f"{texture['label']}_{width}x{height}.png"
                )
                entry = {
                    "file": name,
                    "source": "archive",
                    "archive_slot": 1,
                    "resource_index": resource_index,
                    "resource_offset": f"0x{offset:X}",
                    "source_sha256": hashlib.sha256(raw).hexdigest(),
                    "byte_count": len(raw),
                    "format": texture["label"],
                    "storage_transform": "n64-odd-row-dword-swap",
                    "width": width,
                    "height": height,
                }
                if texture["label"] == "ci8":
                    palette_offset = texture["palette_offset"]
                    palette = resource[palette_offset:palette_offset + 0x200]
                    palette_name = (
                        f"archive1_{resource_index:03d}_{palette_offset:06X}_"
                        "rgba16_palette_16x16.png"
                    )
                    pixels = decode_ci8(linear, palette)
                    write_rgba_png(directory / name, width, height, pixels)
                    write_rgba_png(
                        directory / palette_name, 16, 16,
                        decode_texture(palette, 0, 2),
                    )
                    entry["palette_file"] = palette_name
                    entry["source_linear_indices"] = linear.hex()
                    manifest["textures"].append(entry)
                    manifest["textures"].append({
                        "file": palette_name,
                        "source": "archive",
                        "archive_slot": 1,
                        "resource_index": resource_index,
                        "resource_offset": f"0x{palette_offset:X}",
                        "source_sha256": hashlib.sha256(palette).hexdigest(),
                        "byte_count": len(palette),
                        "format": "rgba16",
                        "width": 16,
                        "height": 16,
                        "description": f"Palette for {name}",
                    })
                else:
                    write_rgba_png(
                        directory / name, width, height,
                        decode_texture(linear, texture["format"], texture["size"]),
                    )
                    manifest["textures"].append(entry)
        for archive_slot, resource_index, offset, width, height in MOBILE_PANEL_TEXTURES:
            packed = archive_resource(rom, archive_slot, resource_index)
            if packed[:8] != b"PERS-SZP":
                raise ValueError(
                    f"archive {archive_slot} resource {resource_index} "
                    "is not PERS-SZP"
                )
            resource = yay0_decode(packed[0x18:])
            byte_count = width * height * 2
            raw = resource[offset:offset + byte_count]
            if len(raw) != byte_count:
                raise ValueError("Mobile panel texture is truncated")
            name = (
                f"archive{archive_slot}_{resource_index:03d}_{offset:06X}_"
                f"rgba16_{width}x{height}.png"
            )
            filtered = encode_texture(
                clean_opaque_panel(decode_texture(raw, 0, 2), width, height),
                "rgba16",
            )
            linear = n64_odd_row_dword_swap(filtered, width * 2, height)
            pixels = decode_texture(linear, 0, 2)
            write_rgba_png(directory / name, width, height, pixels)
            manifest["textures"].append({
                "file": name,
                "source": "archive",
                "archive_slot": archive_slot,
                "resource_index": resource_index,
                "resource_offset": f"0x{offset:X}",
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "byte_count": len(raw),
                "format": "rgba16",
                "display_filter": "opaque-bilateral-dedither-v2-before-layout",
                "storage_transform": "n64-odd-row-dword-swap",
                "width": width,
                "height": height,
            })
        archive_slot, resource_index, offset, width, height = (
            MOBILE_BATTLE_TITLE_TEXTURE
        )
        packed = archive_resource(rom, archive_slot, resource_index)
        if packed[:8] != b"PERS-SZP":
            raise ValueError("Mobile battle title texture is not PERS-SZP")
        resource = yay0_decode(packed[0x18:])
        byte_count = width * height
        raw = resource[offset:offset + byte_count]
        if len(raw) != byte_count:
            raise ValueError("Mobile battle title texture is truncated")
        name = (
            f"archive{archive_slot}_{resource_index:03d}_{offset:06X}_"
            f"ia8_{width}x{height}.png"
        )
        linear = n64_odd_row_dword_swap(raw, width, height)
        write_rgba_png(
            directory / name, width, height, decode_texture(linear, 3, 1)
        )
        manifest["textures"].append({
            "file": name,
            "source": "archive",
            "archive_slot": archive_slot,
            "resource_index": resource_index,
            "resource_offset": f"0x{offset:X}",
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "byte_count": len(raw),
            "format": "ia8",
            "storage_transform": "n64-odd-row-dword-swap",
            "width": width,
            "height": height,
            "runtime_slices": [
                {"offset": "0x10", "width": 320, "height": 12},
                {"offset": "0xF10", "width": 320, "height": 12},
                {"offset": "0x1E10", "width": 320, "height": 12},
            ],
            "description": "Metallic Mobile Stadium battle-intro title",
        })
        for resource_index in MOBILE_PRESENTATION_JPEGS:
            image = jpeg_payload(archive_resource(rom, 8, resource_index))
            width, height = jpeg_dimensions(image)
            name = (
                f"archive8_{resource_index:03d}_jpeg_"
                f"{width}x{height}.jpg"
            )
            (directory / name).write_bytes(image)
            manifest["textures"].append({
                "file": name,
                "source": "archive",
                "archive_slot": 8,
                "resource_index": resource_index,
                "source_sha256": hashlib.sha256(image).hexdigest(),
                "byte_count": len(image),
                "format": "jpeg",
                "width": width,
                "height": height,
            })
        for resource_index in MOBILE_HELP_JPEGS:
            source_image = jpeg_payload(
                archive_resource(rom, 13, resource_index)
            )
            width, height = jpeg_dimensions(source_image)
            image = help_references.get(resource_index, source_image)
            if jpeg_dimensions(image) != (width, height):
                raise ValueError(
                    f"Japanese help JPEG {resource_index} dimensions differ "
                    f"from the {code} placeholder"
                )
            name = (
                f"archive13_{resource_index:03d}_jpeg_"
                f"{width}x{height}.jpg"
            )
            (directory / name).write_bytes(image)
            manifest["textures"].append({
                "file": name,
                "source": "archive",
                "archive_slot": 13,
                "resource_index": resource_index,
                "source_sha256": hashlib.sha256(source_image).hexdigest(),
                "byte_count": len(source_image),
                "format": "jpeg",
                "width": width,
                "height": height,
                **({"description": "Mobile Stadium Crystal help screenshot"}
                   if resource_index in MOBILE_HELP_REFERENCE_JPEGS else {}),
            })
        manifest["archive16"] = {
            "rom_offset": f"0x{archive_start:X}",
            "size": archive_size,
            "resource_count": resource_count,
            "source_sha256": hashlib.sha256(
                rom[archive_start:archive_start + archive_size]
            ).hexdigest(),
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        print(f"exported {code}: {len(manifest['textures'])} Mobile Stadium graphics")


def make_profile(repo: Path, path: Path, code: str) -> dict:
    rom = normalize_rom(path.read_bytes())
    reference, _ = signatures(repo)
    normalized = normalized_main(rom)
    wanted_signatures = [
        reference[name][1] for name in set(ALIASES.values())
    ]
    wanted_signatures += [
        reference["func_80069F20"][1], reference["func_80069E38"][1],
        reference["func_80065BB0"][1],
    ]
    index = signature_index(normalized, wanted_signatures)
    # A long unique routine beside the omitted block gives a good local delta.
    anchor_name = "func_80069F20"
    anchor_us, anchor_sig = reference[anchor_name]
    anchor = find_signature(normalized, index, anchor_sig, anchor_us)
    delta = anchor - anchor_us
    symbols = {}
    for alias, reference_name in ALIASES.items():
        us_address, signature = reference[reference_name]
        symbols[alias] = find_signature(
            normalized, index, signature, us_address + delta
        )
    command = symbols["mobile_download_command"]
    command_offset = command - MAIN_VRAM + MAIN_ROM
    lui, _, load = struct.unpack_from(">III", rom, command_offset + 0xA8)
    if lui >> 16 != 0x3C0B or load >> 16 != 0x8D6B:
        raise ValueError(f"{code}: cannot recover Mobile download buffer table")
    low = load & 0xFFFF
    if low & 0x8000:
        low -= 0x10000
    symbols["mobile_download_buffers"] = ((lui & 0xFFFF) << 16) + low
    # Command 0x28's availability query uses a dedicated byte array rather
    # than the DLD0 header used by commands 0x26 and 0x27. Recover it from the
    # command-0x28 jump-table case (func_8005CDDC + 0xD4).
    count_lui, count_addu = struct.unpack_from(
        ">II", rom, command_offset + 0xD4
    )
    count_lbu, = struct.unpack_from(">I", rom, command_offset + 0xE0)
    if (count_lui >> 16 != 0x3C03 or count_addu != 0x00671821 or
            count_lbu >> 16 != 0x9063):
        raise ValueError(f"{code}: cannot recover Mobile Friend Data count array")
    count_low = count_lbu & 0xFFFF
    if count_low & 0x8000:
        count_low -= 0x10000
    symbols["mobile_friend_counts"] = (
        ((count_lui & 0xFFFF) << 16) + count_low
    )
    dispatch = symbols["mobile_download_read_dispatch"]
    dispatch_offset = dispatch - MAIN_VRAM + MAIN_ROM
    table_lui, _, table_load = struct.unpack_from(
        ">III", rom, dispatch_offset + 0x48
    )
    if table_lui >> 16 != 0x3C01 or table_load >> 16 != 0x8C2F:
        raise ValueError(
            f"{code}: cannot recover Mobile download read jump table"
        )
    table_low = table_load & 0xFFFF
    if table_low & 0x8000:
        table_low -= 0x10000
    symbols["mobile_download_copy_table"] = (
        ((table_lui & 0xFFFF) << 16) + table_low
    )
    symbols["mobile_download_copy_continue"] = dispatch + 0xCC
    # func_800570CC begins by comparing the requested SRAM bank with a
    # one-byte-per-controller mapper cache.  Recover that cache directly from
    # the regional routine so the Mobile overlay can invalidate it before
    # touching MBC30 banks 4-7.  A Transfer Pak reset or another subsystem can
    # change the physical mapper without updating this RAM byte; emulators
    # commonly hide that desynchronization by exposing SRAM linearly.
    read = symbols["mobile_unlock_read"]
    read_offset = read - MAIN_VRAM + MAIN_ROM
    cache_lui, cache_lbu = struct.unpack_from(">II", rom, read_offset)
    if cache_lui >> 16 != 0x3C0E or cache_lbu >> 16 != 0x91CE:
        raise ValueError(f"{code}: cannot recover Transfer Pak bank cache")
    cache_low = cache_lbu & 0xFFFF
    if cache_low & 0x8000:
        cache_low -= 0x10000
    # The first byte is the active-controller bit mask.  The mapper routine
    # immediately below func_800570CC uses the four-byte bank-cache array at
    # +7 (one byte per controller).
    symbols["mobile_transfer_active_mask"] = (
        ((cache_lui & 0xFFFF) << 16) + cache_low
    )
    symbols["mobile_transfer_bank_cache"] = (
        symbols["mobile_transfer_active_mask"] + 7
    )
    # The scanner passes the per-controller cartridge descriptor at +0x28 to
    # mobile_gbpak_acquire.  Recover the regional table rather than assuming
    # the US RAM address; every descriptor has a retained 0x70-byte stride.
    scan = symbols["mobile_data_scan"]
    scan_offset = scan - MAIN_VRAM + MAIN_ROM
    active_lui, active_load = struct.unpack_from(">II", rom, scan_offset)
    if active_lui >> 16 != 0x3C18 or active_load >> 16 != 0x8F18:
        raise ValueError(f"{code}: cannot recover scanner active-controller mask")
    active_low = active_load & 0xFFFF
    if active_low & 0x8000:
        active_low -= 0x10000
    symbols["mobile_scanner_active_mask"] = (
        ((active_lui & 0xFFFF) << 16) + active_low
    )
    context_lui, context_addiu = struct.unpack_from(
        ">II", rom, scan_offset + 0x3C
    )
    if context_lui >> 16 != 0x3C09 or context_addiu >> 16 != 0x2529:
        raise ValueError(f"{code}: cannot recover Transfer Pak context table")
    context_low = context_addiu & 0xFFFF
    if context_low & 0x8000:
        context_low -= 0x10000
    symbols["mobile_transfer_contexts"] = (
        ((context_lui & 0xFFFF) << 16) + context_low
    )
    # The tagged controller allocations all use the same regional destructor,
    # but that small callback is not byte-identical across every PAL build and
    # therefore cannot be located safely by the ordinary signature mapper.
    # Recover the function pointer from the retained allocator call sites
    # immediately preceding the scanner instead.
    allocator_jal = (
        3 << 26 | ((symbols["mobile_tagged_alloc"] >> 2) & 0x03FFFFFF)
    )
    destructor_candidates: set[int] = set()
    scan_window_start = max(MAIN_VRAM, scan - 0x1000)
    for address in range(scan_window_start, scan, 4):
        offset = address - MAIN_VRAM + MAIN_ROM
        instruction, = struct.unpack_from(">I", rom, offset)
        if instruction != allocator_jal:
            continue
        for previous in range(max(scan_window_start, address - 0x28), address - 4, 4):
            previous_offset = previous - MAIN_VRAM + MAIN_ROM
            lui, addiu = struct.unpack_from(">II", rom, previous_offset)
            if lui >> 16 == 0x3C07 and addiu >> 16 == 0x24E7:
                low = addiu & 0xFFFF
                if low & 0x8000:
                    low -= 0x10000
                destructor_candidates.add(((lui & 0xFFFF) << 16) + low)
    if len(destructor_candidates) != 1:
        raise ValueError(
            f"{code}: cannot recover the Transfer Pak allocation destructor "
            f"({len(destructor_candidates)} candidates)"
        )
    symbols["mobile_transfer_alloc_destructor"] = destructor_candidates.pop()
    # The retail RMON monitor is not started in production Stadium builds and
    # its code is already used by the Mobile resident trampolines. Reserve a
    # small portion of rmonmisc BSS as persistent storage for four 0x1c0 raw
    # Friend blocks. Unlike main-pool allocations, this survives the menu ->
    # state-18 pool pop. This relative BSS layout is shared by all regions.
    symbols["mobile_friend_raw_cache"] = (
        symbols["mobile_transfer_contexts"] + 0x6A90
    )
    # The retained Saturday/checkpoint overlay has this historical layout.
    # A clean IDO build replaces the value with the linked exported symbol in
    # build_overlay(), so the resident scanner never relies on compiler layout.
    symbols["mobile_overlay_finalize"] = OVERLAY_VRAM + 0x940
    # The dormant state-session cave hosts two resident entries. Entry zero
    # guards only Mobile Stadium's own controller scan.
    # Entry +0x100 replaces the scanner's existing release call: it copies the
    # Mobile SRAM while the Pak is still acquired, then performs that release.
    symbols["mobile_guarded_scan"] = symbols["mobile_state_session"]
    symbols["mobile_scan_release_hook"] = symbols["mobile_state_session"] + 0x140
    symbols["mobile_finish_scan_load"] = (
        symbols["mobile_state_session"] + SCANNER_FINISH_OFFSET
    )
    symbols["mobile_scanner_release_call"] = symbols["mobile_data_scan"] + 0xEC
    # The two tiny retained wrappers immediately following the scanner request
    # masks 8 (E000/PTP0) and 0x10 (F000).  Restore only the E000 call target;
    # the resident pre-release hook already performed the physical SRAM read
    # and cached the original status byte while the Transfer Pak was powered.
    symbols["mobile_e000_wrapper_call"] = symbols["mobile_data_scan"] + 0x184
    stub_us, stub_signature = reference["func_80069E38"]
    stub = find_signature(normalized, index, stub_signature, stub_us + delta)
    router_us, router_signature = reference["func_80065BB0"]
    router = find_signature(
        normalized, index, router_signature, router_us + delta
    )
    route_instruction = router + 0x1C4
    router_offset = router - MAIN_VRAM + MAIN_ROM
    table_lui, _, table_load = struct.unpack_from(">III", rom, router_offset + 0xE0)
    if table_lui >> 16 != 0x3C01 or table_load >> 16 != 0x8C39:
        raise ValueError(f"{code}: cannot recover main-menu result jump table")
    table_low = table_load & 0xFFFF
    if table_low & 0x8000:
        table_low -= 0x10000
    menu_result_table = ((table_lui & 0xFFFF) << 16) + table_low
    # Result 6 is the Japanese Mobile Stadium selection. International ROMs
    # point it at the generic cancel/default route alongside result zero.
    mobile_result_entry = menu_result_table + 5 * 4
    mobile_result_entry_offset = mobile_result_entry - MAIN_VRAM + MAIN_ROM
    expected_mobile_result = struct.pack(">I", route_instruction - 4)
    if rom[mobile_result_entry_offset:mobile_result_entry_offset + 4] != expected_mobile_result:
        raise ValueError(f"{code}: unexpected Mobile menu result-table entry")
    menu_state_pattern = rom.find(MOBILE_MENU_STATE_PATTERN)
    if (
        menu_state_pattern < 0
        or rom.find(MOBILE_MENU_STATE_PATTERN, menu_state_pattern + 1) >= 0
    ):
        raise ValueError(f"{code}: cannot uniquely locate Mobile menu state input")
    menu_state_offset = menu_state_pattern + 8
    menu_graph_pattern = rom.find(MOBILE_MENU_GRAPH_PATTERN)
    if (
        menu_graph_pattern < 0
        or rom.find(MOBILE_MENU_GRAPH_PATTERN, menu_graph_pattern + 1) >= 0
    ):
        raise ValueError(f"{code}: cannot uniquely locate Mobile navigation input")
    menu_graph_flag_offset = menu_graph_pattern + MOBILE_MENU_GRAPH_FLAG_OFFSET
    menu_graph_base_offset = menu_graph_pattern + MOBILE_MENU_GRAPH_BASE_OFFSET
    fragments = fragment_starts(rom)
    return {
        "format": 1,
        "language": code,
        "source_rom": path.name,
        "source_sha256": hashlib.sha256(rom).hexdigest(),
        "cart_id": rom[0x3B:0x3F].decode("ascii"),
        "state18_stub": f"0x{stub:08X}",
        "state18_rom_offset": f"0x{stub - MAIN_VRAM + MAIN_ROM:X}",
        "menu_route_instruction": f"0x{route_instruction:08X}",
        "menu_route_rom_offset": (
            f"0x{route_instruction - MAIN_VRAM + MAIN_ROM:X}"
        ),
        "menu_mobile_result_rom_offset": f"0x{mobile_result_entry_offset:X}",
        "menu_state_rom_offset": f"0x{menu_state_offset:X}",
        "menu_graph_flag_rom_offset": f"0x{menu_graph_flag_offset:X}",
        "menu_graph_base_rom_offset": f"0x{menu_graph_base_offset:X}",
        "symbols": {name: f"0x{address:08X}" for name, address in symbols.items()},
        "fragments": {
            # The first header in the ROM is logical fragment 1.
            str(index): f"0x{fragments[index - 1]:X}" for index in FRAGMENTS
        },
    }


def write_profiles(repo: Path, input_root: Path, languages: list[str]) -> None:
    output = repo / "assets/localization/mobile/profiles"
    output.mkdir(parents=True, exist_ok=True)
    for code in languages:
        if code not in ROM_NAMES:
            raise ValueError(f"unsupported mobile language {code!r}")
        profile = make_profile(
            repo, source_rom_path(repo, input_root, code), code
        )
        path = output / f"{code}.json"
        path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}")


def linker_script(profile: dict) -> str:
    definitions = []
    for name, value in profile["symbols"].items():
        definitions.append(f"{name} = {value};")
    for index, value in profile["fragments"].items():
        definitions.append(f"fragment{index}_ROM_START = {value};")
    for address in (
        0x81100000, 0x81400000, 0x81600000, 0x81800000, 0x81A00000,
        0x82600000, 0x83000000, 0x84100000, 0x84200000, 0x84300000,
    ):
        definitions.append(f"D_{address:08X} = 0x{address:08X};")
    return """OUTPUT_ARCH(mips)
SECTIONS
{
  . = 0x84500020;
  .text : { *(.text) }
  .rodata ALIGN(16) : { *(.rodata) *(.rodata.*) }
  .data ALIGN(16) : { *(.data) *(.data.*) }
  /DISCARD/ : { *(.comment) *(.mdebug*) *(.pdr) *(.reginfo) *(.MIPS.abiflags) }
}
""" + "\n".join(definitions) + "\n"


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def install_package(repo: Path, package: Path, force: bool) -> None:
    """Install the source and editable assets bundled with a portable kit."""
    if not package.is_dir():
        raise ValueError(
            f"{package} is missing; run install from a packaged localization kit"
        )
    copied = 0
    preserved = 0
    for source_name, destination_name in PACKAGE_FILES:
        source = package / source_name
        destination = repo / destination_name
        if not source.is_file():
            raise FileNotFoundError(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not force:
            preserved += 1
            continue
        shutil.copy2(source, destination)
        copied += 1
    asset_source = package / "assets"
    if not asset_source.is_dir():
        raise FileNotFoundError(asset_source)
    asset_destination = repo / "assets/localization/mobile"
    for source in asset_source.rglob("*"):
        if not source.is_file():
            continue
        destination = asset_destination / source.relative_to(asset_source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not force:
            preserved += 1
            continue
        shutil.copy2(source, destination)
        copied += 1
    print(
        f"installed {copied} Mobile Stadium files; "
        f"preserved {preserved} existing files"
    )


def internal_relocations(elf: Path, repo: Path) -> list[tuple[int, int]]:
    output = subprocess.run(
        ["mips-linux-gnu-readelf", "-rW", str(elf)], cwd=repo,
        check=True, capture_output=True, text=True,
    ).stdout
    relocations = [(4, 0)]  # R_MIPS_26 for the fragment header entry jump.
    for line in output.splitlines():
        match = re.match(
            r"\s*([0-9A-Fa-f]+)\s+[0-9A-Fa-f]+\s+"
            r"R_MIPS_(32|26|HI16|LO16)\s+[0-9A-Fa-f]+\s+(\S+)", line
        )
        if not match or match.group(3) not in (".text", ".rodata", ".data"):
            continue
        address = int(match.group(1), 16)
        kind = {"32": 2, "26": 4, "HI16": 5, "LO16": 6}[match.group(2)]
        relocations.append((kind, address - OVERLAY_VRAM))
    return relocations


def elf_symbol_address(elf: Path, repo: Path, symbol: str) -> int:
    output = subprocess.run(
        ["mips-linux-gnu-nm", "-n", str(elf)], cwd=repo,
        check=True, capture_output=True, text=True,
    ).stdout
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-1] == symbol:
            # Newer GNU nm sign-extends 32-bit MIPS addresses to 64 bits.
            return int(fields[0], 16) & 0xFFFFFFFF
    raise ValueError(f"{elf}: symbol {symbol!r} was not found")


def make_fragment(
    code: bytes, relocations: list[tuple[int, int]], entry_address: int
) -> bytes:
    if entry_address & 3:
        raise ValueError("Mobile Stadium overlay entry must be 4-byte aligned")
    if not OVERLAY_CODE <= entry_address < OVERLAY_CODE + len(code):
        raise ValueError("Mobile Stadium overlay entry is outside its code image")
    relocation = (0x20 + len(code) + 0xF) & ~0xF
    relocation_size = 4 + len(relocations) * 4
    file_size = max(
        (relocation + relocation_size + 0xF) & ~0xF,
        OVERLAY_MIN_FILE_SIZE,
    )
    jump = 0x08000000 | ((entry_address >> 2) & 0x03FFFFFF)
    header = struct.pack(
        ">8I", jump, 0, 0x46524147, 0x4D454E54,
        0x20, relocation, file_size, file_size,
    )
    output = bytearray(b"\0" * file_size)
    output[:0x20] = header
    output[0x20:0x20 + len(code)] = code
    struct.pack_into(">I", output, relocation, len(relocations))
    for index, (kind, offset) in enumerate(relocations):
        struct.pack_into(">I", output, relocation + 4 + index * 4,
                         kind << 24 | offset)
    return bytes(output)


def restore_checkpoint_overlay_direct_load(slot_path: Path) -> bool:
    """Restore the checkpoint's internal call after the failed inner session.

    The physical-session experiment redirected offset 0x9e4 to resident code.
    GB Pak acquisition unmaps the calling overlay, so the replacement could
    not jump back to 0x84500940.  The corrected state wrapper acquires before
    mapping the overlay; restore this call and its R_MIPS_26 relocation.
    """
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0x20 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    relocation = struct.unpack_from(">I", slot, 0x14)[0]
    file_size = struct.unpack_from(">I", slot, 0x18)[0]
    count = struct.unpack_from(">I", slot, relocation)[0]
    relocations = [
        ((value >> 24) & 0xFF, value & 0xFFFFFF)
        for value, in struct.iter_unpack(
            ">I", slot[relocation + 4:relocation + 4 + count * 4]
        )
    ]
    target_relocation = (4, 0x9E4)
    call, = struct.unpack_from(">I", slot, 0x9E4)
    if call >> 26 != 3:
        raise ValueError(f"{slot_path}: checkpoint Mobile load call is not JAL")
    old_target = 0x80000000 | ((call & 0x03FFFFFF) << 2)
    if old_target == 0x84500940 and target_relocation in relocations:
        return False
    if target_relocation in relocations:
        raise ValueError(
            f"{slot_path}: unexpected relocated Mobile load target 0x{old_target:08X}"
        )
    code = bytearray(slot[0x20:relocation])
    struct.pack_into(
        ">I", code, 0x9C4, 3 << 26 | ((0x84500940 >> 2) & 0x03FFFFFF)
    )
    relocations.append(target_relocation)
    entry_jump, = struct.unpack_from(">I", slot, 0)
    entry_address = 0x80000000 | ((entry_jump & 0x03FFFFFF) << 2)
    fragment = make_fragment(bytes(code), relocations, entry_address)
    if len(fragment) > file_size:
        raise ValueError(f"{slot_path}: migrated fragment unexpectedly grew")
    slot[:file_size] = b"\0" * file_size
    slot[:len(fragment)] = fragment
    slot_path.write_bytes(slot)
    return True


def restore_checkpoint_overlay_sessionless(
    slot_path: Path, finish_address: int, fragment_loader: int
) -> bool:
    """Restore the last working overlay path with no retained Pak session."""
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0xDB4 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    relocation = struct.unpack_from(">I", slot, 0x14)[0]
    file_size = struct.unpack_from(">I", slot, 0x18)[0]
    count = struct.unpack_from(">I", slot, relocation)[0]
    relocations = [
        ((value >> 24) & 0xFF, value & 0xFFFFFF)
        for value, in struct.iter_unpack(
            ">I", slot[relocation + 4:relocation + 4 + count * 4]
        )
    ]
    loader_relocation = (4, 0xDAC)
    loader_call, delay_slot = struct.unpack_from(">II", slot, 0xDAC)
    loader_target = 0x80000000 | ((loader_call & 0x03FFFFFF) << 2)
    replacement = 3 << 26 | ((finish_address >> 2) & 0x03FFFFFF)
    fragment_call, = struct.unpack_from(">I", slot, 0xDDC)
    fragment_target = 0x80000000 | ((fragment_call & 0x03FFFFFF) << 2)
    fragment_instruction = 3 << 26 | ((fragment_loader >> 2) & 0x03FFFFFF)
    if (loader_target == 0x845009CC and delay_slot == 0 and
            loader_relocation in relocations and
            fragment_call == fragment_instruction):
        return False
    if loader_call >> 26 != 3 or delay_slot != 0:
        raise ValueError(
            f"{slot_path}: unexpected Mobile download-list call "
            f"0x{loader_call:08X}"
        )
    if loader_target not in (0x845009CC, finish_address):
        raise ValueError(
            f"{slot_path}: unexpected Mobile loader target "
            f"0x{loader_target:08X}"
        )
    if (fragment_call >> 26 != 3 or
            fragment_target not in (fragment_loader, finish_address)):
        raise ValueError(
            f"{slot_path}: unexpected first fragment-loader call "
            f"0x{fragment_call:08X}"
        )
    code = bytearray(slot[0x20:relocation])
    struct.pack_into(
        ">II", code, 0xD8C,
        3 << 26 | ((0x845009CC >> 2) & 0x03FFFFFF), 0
    )
    struct.pack_into(">I", code, 0xDBC, fragment_instruction)
    if loader_relocation not in relocations:
        relocations.append(loader_relocation)
    entry_jump, = struct.unpack_from(">I", slot, 0)
    entry_address = 0x80000000 | ((entry_jump & 0x03FFFFFF) << 2)
    fragment = make_fragment(bytes(code), relocations, entry_address)
    if len(fragment) > file_size:
        raise ValueError(f"{slot_path}: session migration unexpectedly grew")
    slot[:file_size] = b"\0" * file_size
    slot[:len(fragment)] = fragment
    slot_path.write_bytes(slot)
    return True


def patch_checkpoint_overlay_guarded_scan(
    slot_path: Path, original_scan: int, guarded_scan: int
) -> bool:
    """Route only Mobile Stadium's internal controller scan through the guard."""
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0xDA0 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    call_offset = 0xD9C
    call, = struct.unpack_from(">I", slot, call_offset)
    if call >> 26 != 3:
        raise ValueError(f"{slot_path}: Mobile setup scan is not JAL")
    target = 0x80000000 | ((call & 0x03FFFFFF) << 2)
    if target == guarded_scan:
        return False
    if target != original_scan:
        raise ValueError(
            f"{slot_path}: unexpected Mobile setup scan target 0x{target:08X}"
        )
    struct.pack_into(
        ">I", slot, call_offset,
        3 << 26 | ((guarded_scan >> 2) & 0x03FFFFFF)
    )
    slot_path.write_bytes(slot)
    return True


def patch_checkpoint_overlay_finish_scan_load(
    slot_path: Path, fragment_loader: int, finish_loader: int
) -> bool:
    """Release retained scanner sessions at the first post-copy fragment load."""
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0xDE0 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    call_offset = 0xDDC
    call, = struct.unpack_from(">I", slot, call_offset)
    if call >> 26 != 3:
        raise ValueError(f"{slot_path}: first Mobile fragment load is not JAL")
    target = 0x80000000 | ((call & 0x03FFFFFF) << 2)
    if target == finish_loader:
        return False
    if target != fragment_loader:
        raise ValueError(
            f"{slot_path}: unexpected first fragment loader 0x{target:08X}"
        )
    struct.pack_into(
        ">I", slot, call_offset,
        3 << 26 | ((finish_loader >> 2) & 0x03FFFFFF)
    )
    slot_path.write_bytes(slot)
    return True


def remove_overlay_guarded_scan(slot_path: Path, guarded_scan: int) -> bool:
    """Remove the obsolete in-overlay scan from an already compiled slot.

    GB Pak scan/acquire operations can replace the 0x84500000 TLB mapping.
    Fresh overlays omit this call in C; this structural migration keeps the
    last compiler output testable when WSL is not available to the caller.
    """
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0x20 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    code_end = struct.unpack_from(">I", slot, 0x14)[0]
    call = struct.pack(
        ">I", 3 << 26 | ((guarded_scan >> 2) & 0x03FFFFFF)
    )
    offsets = [
        offset for offset in range(0x20, code_end, 4)
        if slot[offset:offset + 4] == call
    ]
    if not offsets:
        return False
    if len(offsets) != 1:
        raise ValueError(
            f"{slot_path}: found {len(offsets)} in-overlay scan calls"
        )
    slot[offsets[0]:offsets[0] + 4] = b"\0" * 4
    slot_path.write_bytes(slot)
    return True


def remove_overlay_premap_setup(slot_path: Path, setup: int) -> bool:
    """Move the state-0x41 setup call out of an existing mapped overlay."""
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0x20 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    code_end = struct.unpack_from(">I", slot, 0x14)[0]
    call = struct.pack(
        ">I", 3 << 26 | ((setup >> 2) & 0x03FFFFFF)
    )
    signature = call + struct.pack(">I", 0x24040041)
    battle_signature = call + struct.pack(">I", 0x2404005A)
    battle_offsets = [
        offset for offset in range(0x20, code_end - 4, 4)
        if slot[offset:offset + 8] == battle_signature
    ]
    battle_offset = min(battle_offsets) if battle_offsets else code_end
    offsets = [
        offset for offset in range(0x20, code_end - 4, 4)
        if offset < battle_offset and slot[offset:offset + 8] == signature
    ]
    if not offsets:
        return False
    if len(offsets) != 1:
        raise ValueError(
            f"{slot_path}: found {len(offsets)} state-0x41 setup calls"
        )
    slot[offsets[0]:offsets[0] + 8] = b"\0" * 8
    slot_path.write_bytes(slot)
    return True


def disable_overlay_postmap_pak_fallback(slot_path: Path) -> bool:
    """Make the retained IDO image consume only resident-prefetched bytes.

    Two independent branches must change together.  mobile_load_download()
    must return when its resident buffer is absent, while
    mobile_load_downloads() must call it for every port so a present buffer is
    validated and its western Friend record is transposed.  The previous
    migration changed only the latter branch into an unconditional *skip*;
    that avoided the Pak fallback but also suppressed all finalization.
    Fresh builds express this directly in C and do not match these signatures.
    """
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0x20 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    code_end = struct.unpack_from(">I", slot, 0x14)[0]

    # The Saturday FriendDataReads/checkpoint image has the older 0x16c0
    # layout.  Its mobile_load_download() always allocates a replacement
    # buffer and calls the physical E000/F000 readers from 0x84500000.  Once
    # the hardware-prefetch hook was added, that legacy fallback evicted the
    # very fragment executing it; Project64 consequently faulted on the
    # return instruction at 0x84500934.  Make this historical image consume
    # the resident buffer and turn its two physical reads into successful
    # no-ops.  The remainder of the original routines still validates the P3
    # frame and converts the already-prefetched Friend record.
    if code_end == 0x16C0:
        legacy_entry = bytes.fromhex(
            "27bdffe0afbf001cafa40020afb00018240413e00c000a61"
        )
        patched_entry = bytes.fromhex(
            "27bdffe0afbf001cafa40020afb00018"
            "8fa40020000470803c018013002e08218c308730"
            "12000014000000001000000800000000"
            "000000000000000000000000000000000000000000000000"
            "00000000"
        )
        if slot[0x940:0x940 + len(legacy_entry)] == legacy_entry:
            # Keep the original prologue and replace 0x950..0x98c.  The
            # branch at 0x964 returns through the original epilogue at 0x9b8;
            # the branch at 0x96c enters the original validators at 0x990.
            slot[0x950:0x990] = patched_entry[0x10:]
            for offset in (0x5E8, 0x6DC):
                if slot[offset] >> 2 != 3:
                    raise ValueError(
                        f"{slot_path}: legacy physical read at {offset:#x} "
                        "is not a JAL"
                    )
                struct.pack_into(">I", slot, offset, 0x00001025)

            relocation = code_end
            count = struct.unpack_from(">I", slot, relocation)[0]
            entries = [
                struct.unpack_from(">I", slot, relocation + 4 + i * 4)[0]
                for i in range(count)
            ]
            entries = [
                entry for entry in entries
                if not (entry >> 24 == 4 and
                        (entry & 0x00FFFFFF) in (0x5E8, 0x6DC))
            ]
            struct.pack_into(">I", slot, relocation, len(entries))
            for i, entry in enumerate(entries):
                struct.pack_into(">I", slot, relocation + 4 + i * 4, entry)
            tail = relocation + 4 + len(entries) * 4
            old_tail = relocation + 4 + count * 4
            slot[tail:old_tail] = bytes(old_tail - tail)
            slot_path.write_bytes(slot)
            return True

        patched_lookup = bytes.fromhex(
            "8fa40020000470803c018013002e08218c308730"
            "12000014000000001000000800000000"
        )
        if (slot[0x950:0x950 + len(patched_lookup)] == patched_lookup and
                struct.unpack_from(">I", slot, 0x5E8)[0] == 0x00001025 and
                struct.unpack_from(">I", slot, 0x6DC)[0] == 0x00001025):
            return False
        raise ValueError(
            f"{slot_path}: unrecognized 0x16c0 legacy download loader"
        )
    missing_signature = bytes.fromhex("0329502411400018240413e0")
    loop_signatures = (
        bytes.fromhex("8e2e000055c0000426100001"),
        bytes.fromhex("8e2e00001000000426100001"),
    )
    missing = [
        offset for offset in range(0x20, code_end - len(missing_signature) + 1, 4)
        if slot[offset:offset + len(missing_signature)] == missing_signature
    ]
    loop = [
        offset for offset in range(0x20, code_end - 12 + 1, 4)
        if any(slot[offset:offset + 12] == signature
               for signature in loop_signatures)
    ]
    if not missing and not loop:
        return False
    if len(missing) != 1 or len(loop) != 1:
        raise ValueError(
            f"{slot_path}: ambiguous resident-prefetch finalizer branches "
            f"(missing={len(missing)}, loop={len(loop)})"
        )
    # Missing buffer: branch to mobile_load_download()'s epilogue. Present
    # buffer: always execute its validator/transposer from the four-port loop.
    struct.pack_into(">I", slot, missing[0] + 4, 0x10000018)
    struct.pack_into(">I", slot, loop[0] + 4, 0)
    slot_path.write_bytes(slot)
    return True


def ensure_overlay_two_page_mapping(slot_path: Path) -> bool:
    """Reserve the runtime-confirmed fragment window used by FriendDataReads.

    The fragment's relocation table remains at its linked offset; only the
    file/BSS end and zero-filled tail grow.  This makes the retail loader map
    both pages containing the controller entry and its lower-page helpers.
    """
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < OVERLAY_MIN_FILE_SIZE or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    file_size, bss_end = struct.unpack_from(">II", slot, 0x18)
    if file_size >= OVERLAY_MIN_FILE_SIZE and bss_end >= OVERLAY_MIN_FILE_SIZE:
        return False
    if file_size > OVERLAY_MIN_FILE_SIZE or bss_end > OVERLAY_MIN_FILE_SIZE:
        raise ValueError(f"{slot_path}: inconsistent Mobile overlay size")
    slot[file_size:OVERLAY_MIN_FILE_SIZE] = b"\0" * (
        OVERLAY_MIN_FILE_SIZE - file_size
    )
    struct.pack_into(">II", slot, 0x18, OVERLAY_MIN_FILE_SIZE,
                     OVERLAY_MIN_FILE_SIZE)
    slot_path.write_bytes(slot)
    return True


def retarget_overlay_finish_load(
    slot_path: Path, old_finish: int, new_finish: int
) -> bool:
    """Retarget a previously compiled overlay to the resident prefetch epilogue."""
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0x20 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    code_end = struct.unpack_from(">I", slot, 0x14)[0]
    old_call = struct.pack(
        ">I", 3 << 26 | ((old_finish >> 2) & 0x03FFFFFF)
    )
    new_call = struct.pack(
        ">I", 3 << 26 | ((new_finish >> 2) & 0x03FFFFFF)
    )
    offsets = [
        offset for offset in range(0x20, code_end, 4)
        if slot[offset:offset + 4] == old_call
    ]
    if not offsets:
        return False
    if len(offsets) != 1:
        raise ValueError(
            f"{slot_path}: found {len(offsets)} old finish-helper calls"
        )
    slot[offsets[0]:offsets[0] + 4] = new_call
    slot_path.write_bytes(slot)
    return True


def patch_prefetched_overlay_finalizer(slot_path: Path) -> bool:
    """Adapt the last IDO output to finalize resident-prefetched buffers.

    Fresh builds compile the equivalent C path.  This migration keeps the
    checked-in overlay slots usable on hosts where WSL/IDO cannot run: a
    pre-existing buffer branches through three alignment NOPs to validation,
    and Friend validation consumes the resident E000 copy without rereading
    a released Pak.
    """
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0x1600 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    old_branch = 0x1700001F               # bne t8,zero,0x84500920
    new_branch = 0x17000354               # bne t8,zero,0x845015f4
    branch, = struct.unpack_from(">I", slot, 0x8A0)
    friend_read, = struct.unpack_from(">I", slot, 0x524)
    cave = slot[0x15F4:0x1600]
    expected_cave = struct.pack(
        ">III", 0x03008025,
        2 << 26 | ((0x84500900 >> 2) & 0x03FFFFFF), 0
    )
    if branch == new_branch and friend_read == 0 and cave == expected_cave:
        return False
    if branch != old_branch or friend_read >> 26 != 3 or cave != b"\0" * 12:
        # A fresh compiler output has its finalizer directly in C and does not
        # match this historical layout.
        return False
    struct.pack_into(">I", slot, 0x8A0, new_branch)
    struct.pack_into(">I", slot, 0x524, 0)
    slot[0x15F4:0x1600] = expected_cave
    slot_path.write_bytes(slot)
    return True


def clear_invalid_overlay_p3_frame(slot_path: Path, bzero_target: int) -> bool:
    """Clear all of an invalid prefetched F000 frame in cached overlays.

    The retained international count query trusts DLD0 bytes zero and one.
    Clearing only those bytes was sufficient for valid saves but allowed an
    erased/uninitialized Crystal download area to remain observable through
    later retained paths.  Route the validator's invalid arm through the
    unused final 16 bytes of the cached overlay and zero the complete 0x1000
    P3 frame, matching the source-level implementation.
    """
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0x1790 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    code_end, file_size = struct.unpack_from(">II", slot, 0x14)
    layouts = {
        # Cached NTSC-US compiler layout.
        0x1310: (0x5B4, 0x1300, 0x10000352, 0x1000FCAC, 0),
        # Cached international/PAL compiler layout.
        # Its continuation at 0x918 consumes a0=s1; restore that value in the
        # branch delay slot after _bzero has consumed a0=s0.
        0x1630: (0x910, 0x1620, 0x10000343, 0x1000FCBB, 0x02202025),
        # Sealed NTSC hardware/Friend controller layout.
        0x16C0: (0x9A8, 0x1680, 0x10000335, 0x1000FCC9, 0x02002825),
    }
    if code_end not in layouts:
        # Fresh compiler output contains the source-level _bzero path.
        return False

    (arm_offset, helper_offset, branch_to_helper, branch_to_resume,
     resume_delay) = layouts[code_end]
    old_arm = bytes.fromhex("a2000000a2000001")
    new_arm = struct.pack(">II", branch_to_helper, 0x02002025)
    helper = struct.pack(
        ">IIII",
        3 << 26 | ((bzero_target >> 2) & 0x03FFFFFF),
        0x24051000,
        branch_to_resume,
        resume_delay,
    )
    if (slot[arm_offset:arm_offset + 8] == new_arm and
            slot[helper_offset:helper_offset + 0x10] == helper):
        return False
    if slot[arm_offset:arm_offset + 8] == new_arm:
        # Upgrade the first generalized full-clear migration, whose PAL
        # helper omitted the a0 restoration needed by the retained
        # continuation. The branch and relocation are already correct.
        old_helper = helper[:12] + bytes(4)
        if (resume_delay and
                slot[helper_offset:helper_offset + 0x10] == old_helper):
            slot[helper_offset:helper_offset + 0x10] = helper
            slot_path.write_bytes(slot)
            return True
        raise ValueError(f"{slot_path}: unrecognized migrated P3 clear helper")
    if (slot[arm_offset:arm_offset + 8] != old_arm or
            slot[helper_offset:helper_offset + 0x10] != bytes(0x10)):
        raise ValueError(f"{slot_path}: unrecognized cached P3 invalid arm")

    relocation = code_end
    count = struct.unpack_from(">I", slot, relocation)[0]
    entries = [
        struct.unpack_from(">I", slot, relocation + 4 + i * 4)[0]
        for i in range(count)
    ]
    entry = 4 << 24 | helper_offset
    if entry not in entries:
        entries.append(entry)
        entries.sort(key=lambda value: value & 0xFFFFFF)
    tail = relocation + 4 + len(entries) * 4
    if tail > file_size:
        raise ValueError(f"{slot_path}: no room for P3 clear relocation")
    struct.pack_into(">I", slot, relocation, len(entries))
    for i, value in enumerate(entries):
        struct.pack_into(">I", slot, relocation + 4 + i * 4, value)
    slot[relocation + 4 + len(entries) * 4:file_size] = bytes(
        file_size - (relocation + 4 + len(entries) * 4)
    )
    slot[arm_offset:arm_offset + 8] = new_arm
    slot[helper_offset:helper_offset + 0x10] = helper
    slot_path.write_bytes(slot)
    return True


def restore_pal_overlay_p3_count_clear(
    slot_path: Path, bzero_target: int
) -> bool:
    """Keep PAL's proven invalid-frame handling without erasing team data.

    The sealed PAL checkpoint clears only the two retained record-count bytes
    after failed P3 validation. The later full-0x1000 clear helper was written
    for the NTSC cached-overlay continuation. The PAL continuation carries a
    different live-object schedule, so clearing the complete frame removes
    player zero's converted party after the record header has been accepted.

    Restore the exact checkpoint arm for PAL cached overlays and remove the
    now-unused helper relocation. Fresh saves remain protected by the resident
    scanner's FF/FF/count bounds gate.
    """
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0x1790 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    code_end, file_size = struct.unpack_from(">II", slot, 0x14)
    if code_end != 0x1630:
        # NTSC and freshly compiled layouts are intentionally untouched.
        return False

    arm_offset = 0x910
    helper_offset = 0x1620
    old_arm = bytes.fromhex("a2000000a2000001")
    migrated_arm = struct.pack(">II", 0x10000343, 0x02002025)
    helper = struct.pack(
        ">IIII",
        3 << 26 | ((bzero_target >> 2) & 0x03FFFFFF),
        0x24051000,
        0x1000FCBB,
        0x02202025,
    )
    if (slot[arm_offset:arm_offset + 8] == old_arm and
            slot[helper_offset:helper_offset + 0x10] == bytes(0x10)):
        return False
    if (slot[arm_offset:arm_offset + 8] != migrated_arm or
            slot[helper_offset:helper_offset + 0x10] != helper):
        raise ValueError(f"{slot_path}: unrecognized PAL P3 clear migration")

    relocation = code_end
    count = struct.unpack_from(">I", slot, relocation)[0]
    entries = [
        struct.unpack_from(">I", slot, relocation + 4 + i * 4)[0]
        for i in range(count)
    ]
    helper_entry = 4 << 24 | helper_offset
    if helper_entry not in entries:
        raise ValueError(f"{slot_path}: PAL P3 helper relocation is missing")
    entries.remove(helper_entry)
    struct.pack_into(">I", slot, relocation, len(entries))
    for i, value in enumerate(entries):
        struct.pack_into(">I", slot, relocation + 4 + i * 4, value)
    tail = relocation + 4 + len(entries) * 4
    slot[tail:file_size] = bytes(file_size - tail)
    slot[arm_offset:arm_offset + 8] = old_arm
    slot[helper_offset:helper_offset + 0x10] = bytes(0x10)
    slot_path.write_bytes(slot)
    return True


def move_pal_overlay_finalizer_before_allocators(slot_path: Path) -> bool:
    """Run the PAL resident-buffer finalizer before retained allocations.

    The cached PAL IDO layout places mobile_load_downloads() after four calls
    into the resident allocator/zeroing path.  Those calls can invalidate the
    0x84500000 mapping; Project64 then reports a TLB fetch at 0x84500934 when
    the overlay attempts to enter the finalizer.  NTSC's sealed checkpoint has
    a different layout and is intentionally not touched.

    Fresh compiler output obtains this ordering directly from the C source.
    For the checked-in 0x1630 PAL layout, route the first rules allocation
    through a small trampoline placed over the now-unreachable mapped-Pak
    helper at slot offset 0x170.  It finalizes first, performs the displaced
    allocation, and resumes at its original return address.  All transfers to
    other code in this overlay use PC-relative branches: the cached fragment's
    relocation table cannot describe injected absolute J/JAL instructions,
    which otherwise retain their 0x84500000 link addresses after the PAL
    loader relocates the overlay and cause a TLB fetch fault.
    """
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0x1790 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    code_end, file_size = struct.unpack_from(">II", slot, 0x14)

    call_site = 0xC80
    late_call = 0xD18
    wrapper = 0x170
    original_end = 0x1630
    load_downloads = 0x84500934
    resume = 0x84500C88

    def jal(address: int) -> int:
        return 3 << 26 | ((address >> 2) & 0x03FFFFFF)

    def jump(address: int) -> int:
        return 2 << 26 | ((address >> 2) & 0x03FFFFFF)

    def relative(source: int, target: int, instruction: int) -> int:
        delta = target - (source + 4)
        if delta & 3 or not -0x20000 <= delta < 0x20000:
            raise ValueError("PAL Mobile overlay branch is out of range")
        return instruction | ((delta >> 2) & 0xFFFF)

    def bal(source: int, target: int) -> int:
        # bgezal $zero, target
        return relative(source, target, 0x04110000)

    def branch(source: int, target: int) -> int:
        # beq $zero, $zero, target
        return relative(source, target, 0x10000000)

    expected_call = bal(call_site, wrapper)
    expected_wrapper = struct.pack(
        ">IIIIII", bal(wrapper, load_downloads & 0xFFFF), 0,
        # The region-specific allocator instruction is filled below.
        0, 0, branch(wrapper + 16, resume & 0xFFFF), 0,
    )
    displaced_allocator = None
    was_previously_migrated = False
    if code_end == original_end:
        words = struct.unpack_from(">IIIIII", slot, wrapper)
        call_word = struct.unpack_from(">I", slot, call_site)[0]
        if (call_word == expected_call and
                struct.unpack_from(">I", slot, late_call)[0] == 0 and
                words[0] == bal(wrapper, load_downloads & 0xFFFF) and
                words[1] == 0 and
                words[2] >> 26 == 3 and words[3] == 0 and
                words[4] == branch(wrapper + 16, resume & 0xFFFF) and
                words[5] == 0):
            return False
        # Convert the immediately preceding absolute-address migration.  Its
        # displaced allocator is still available in the wrapper's third word.
        if (call_word == jal(0x84500170) and
                struct.unpack_from(">I", slot, late_call)[0] == 0 and
                words[0] == jal(load_downloads) and words[1] == 0 and
                words[2] >> 26 == 3 and words[3] == 0 and
                words[4] == jump(resume) and words[5] == 0):
            displaced_allocator = words[2]
            was_previously_migrated = True
    elif code_end == 0x1638:
        # The finalizer has already been moved to the lower-page wrapper and
        # the enlarged tail is now the independent post-battle music
        # dispatcher.  Do not mistake that dispatcher for the superseded
        # absolute-address finalizer trampoline.
        words = struct.unpack_from(">IIIIII", slot, wrapper)
        if (struct.unpack_from(">I", slot, call_site)[0] == expected_call and
                struct.unpack_from(">I", slot, late_call)[0] == 0 and
                words[0] == bal(wrapper, load_downloads & 0xFFFF) and
                words[1] == 0 and words[2] >> 26 == 3 and words[3] == 0 and
                words[4] == branch(wrapper + 16, resume & 0xFFFF) and
                words[5] == 0):
            return False
        # Undo the earlier tail trampoline before applying the executable-page
        # migration.  Relocation entries are code-relative and need no edits.
        displaced_allocator = struct.unpack_from(">I", slot, 0x1628)[0]
        if (struct.unpack_from(">I", slot, call_site)[0] != jal(0x84501620) or
                struct.unpack_from(">I", slot, late_call)[0] != 0 or
                displaced_allocator >> 26 != 3):
            raise ValueError(f"{slot_path}: malformed tail PAL finalizer")
        was_previously_migrated = True
        slot[original_end:file_size - 8] = bytes(slot[0x1638:file_size])
        slot[file_size - 8:file_size] = bytes(8)
        slot[0x1620:original_end] = bytes(original_end - 0x1620)
        struct.pack_into(">I", slot, 0x14, original_end)
        code_end = original_end
    else:
        return False

    allocator_call = displaced_allocator
    if allocator_call is None:
        allocator_call = struct.unpack_from(">I", slot, call_site)[0]
    if (allocator_call >> 26 != 3 or
            (not was_previously_migrated and
             struct.unpack_from(">I", slot, late_call)[0] !=
             jal(load_downloads))):
        # A fresh compiler output has the source-level ordering and will not
        # match this historical PAL layout.
        return False

    struct.pack_into(">I", slot, call_site, expected_call)
    struct.pack_into(">I", slot, late_call, 0)
    slot[wrapper:wrapper + 24] = expected_wrapper[:8] + struct.pack(
        ">I", allocator_call
    ) + expected_wrapper[12:]
    slot_path.write_bytes(slot)
    return True


def ensure_overlay_internal_jump_relocations(slot_path: Path) -> bool:
    """Add every missing R_MIPS_26 entry for an overlay-internal J/JAL.

    The cached PAL fragment has accumulated several instruction migrations.
    Three resulting absolute transfers were absent from its relocation table.
    They work at the link address, but jump back to stale 0x84500000 addresses
    after the PAL loader relocates the fragment. Deriving the complete set from
    executable code prevents another address-specific relocation regression.
    """
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0x20 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    code_end, file_size = struct.unpack_from(">II", slot, 0x14)
    if not 0x20 <= code_end < file_size <= len(slot):
        raise ValueError(f"{slot_path}: invalid Mobile overlay bounds")
    count, = struct.unpack_from(">I", slot, code_end)
    table_start = code_end + 4
    repaired_bounds = False
    if table_start + count * 4 > file_size:
        # The first post-battle music migration moved this table by eight
        # bytes but retained the old aligned file-size field.  Its final
        # relocation consequently sat four bytes beyond the declared file.
        # Repair those already-produced cache slots without touching code or
        # relocation contents.
        table_end = table_start + count * 4
        if code_end in (0x16C8, 0x1638) and table_end <= len(slot):
            file_size = (table_end + 0xF) & ~0xF
            struct.pack_into(">II", slot, 0x18, file_size, file_size)
            repaired_bounds = True
        else:
            raise ValueError(f"{slot_path}: invalid Mobile relocation table")
    entries = list(struct.unpack_from(f">{count}I", slot, table_start))
    present_r26 = {
        entry & 0x00FFFFFF for entry in entries if entry >> 24 == 4
    }
    required: set[int] = set()
    overlay_end = OVERLAY_VRAM + code_end
    for offset in range(0x20, code_end, 4):
        word, = struct.unpack_from(">I", slot, offset)
        if word >> 26 not in (2, 3):
            continue
        target = 0x80000000 | ((word & 0x03FFFFFF) << 2)
        if OVERLAY_CODE <= target < overlay_end:
            required.add(offset)
    missing = sorted(required - present_r26)
    if not missing:
        if repaired_bounds:
            slot_path.write_bytes(slot)
            return True
        return False
    new_entries = entries + [(4 << 24) | offset for offset in missing]
    if table_start + len(new_entries) * 4 > file_size:
        raise ValueError(f"{slot_path}: Mobile relocation table has no room")
    struct.pack_into(">I", slot, code_end, len(new_entries))
    struct.pack_into(f">{len(new_entries)}I", slot, table_start, *new_entries)
    slot_path.write_bytes(slot)
    return True


def ensure_overlay_menu_music_restart(
    slot_path: Path, fragment_loader: int, pool_push: int, pool_pop: int,
    sequence_setup: int, music_wrapper: int,
) -> bool:
    """Restore the checkpoint lifecycle and bind menu music to ``btlp``.

    An older migration grew the executable tail and used state -1 to restart
    sequence 0x41 after the battle pool was popped.  The synthetic state also
    damaged the final zero/exit path.  Undo that migration, restore the
    Fragment 7 call and the real main_pool_push_state/main_pool_pop_state
    pair.  The resident wrapper replaces only the ``btlp`` push: it first
    performs the real push and then starts audio mode 7/sequence 0x41.  This
    is the first point at which the menu pool exists, and it is reached again
    after Watch Battle returns through START.  Restarting before this push or
    from either pop makes the following pool operation immediately discard
    the sequence.  Watch Battle and Friend difficulty otherwise retain the
    exact checkpoint push/fragment order.
    """
    slot = bytearray(slot_path.read_bytes())
    if len(slot) < 0x20 or slot[8:16] != b"FRAGMENT":
        raise ValueError(f"{slot_path}: invalid Mobile overlay fragment")
    code_end, file_size = struct.unpack_from(">II", slot, 0x14)
    if not 0x20 <= code_end < file_size <= len(slot):
        raise ValueError(f"{slot_path}: invalid Mobile overlay bounds")

    def jal(address: int) -> int:
        return 3 << 26 | ((address >> 2) & 0x03FFFFFF)

    changed = False

    # Reverse the obsolete expanded-tail dispatcher.  Its first word is the
    # original bnel loop instruction, deliberately saved by the migration.
    if code_end in (0x16C8, 0x1638):
        old_code_end = code_end - 8
        cave = old_code_end - 0x10
        old_common, = struct.unpack_from(">I", slot, cave)
        if old_common >> 26 not in (1, 4, 5, 6, 7, 20, 21, 22, 23):
            raise ValueError(f"{slot_path}: malformed legacy music dispatcher")
        direct = None
        common_branch = None
        for offset in range(0x20, cave - 28, 4):
            words = struct.unpack_from(">7I", slot, offset)
            if (words[0] == 0x24010001 and words[1] >> 26 == 5 and
                    words[2] == 0 and words[3] >> 26 == 4 and
                    words[4] == 0x241EFFFF and words[5] >> 26 == 4 and
                    words[6] == 0x241E0008):
                direct = offset + 12
                sibling_immediate = words[5] & 0xFFFF
                if sibling_immediate & 0x8000:
                    sibling_immediate -= 0x10000
                pool_pop = direct + 12 + sibling_immediate * 4
                common_branch = pool_pop + 12
                break
        if direct is None or common_branch is None:
            raise ValueError(f"{slot_path}: legacy music state route is missing")
        struct.pack_into(">I", slot, direct + 4, 0x241E000B)
        struct.pack_into(">I", slot, common_branch, old_common)
        relocation = bytes(slot[code_end:file_size])
        # Keep the resident loader's required two-page mapping even on PAL,
        # whose pre-migration relocation payload happened to end at 0x1780.
        old_file_size = max(OVERLAY_MIN_FILE_SIZE, file_size - 0x10)
        slot[old_code_end:old_code_end + len(relocation)] = relocation
        slot[old_code_end + len(relocation):file_size] = bytes(
            file_size - old_code_end - len(relocation)
        )
        code_end = old_code_end
        file_size = old_file_size
        struct.pack_into(">III", slot, 0x14, code_end, file_size, file_size)
        changed = True

    loader_call = jal(fragment_loader)
    wrapper_call = jal(music_wrapper)
    candidates = []
    for offset in range(0x20, code_end - 4, 4):
        word, = struct.unpack_from(">I", slot, offset)
        if word not in (loader_call, wrapper_call):
            continue
        # Fragment 7 is the only loader call whose setup materializes the
        # 0x83000000 destination in the preceding instruction window.
        prior = slot[max(0x20, offset - 0x40):offset]
        if any(prior[i + 2:i + 4] == b"\x83\x00"
               for i in range(0, len(prior) - 3, 4)
               if prior[i] == 0x3C):
            candidates.append(offset)
    if len(candidates) != 1:
        raise ValueError(
            f"{slot_path}: expected one Fragment 7 menu call, "
            f"found {len(candidates)}"
        )
    call_offset = candidates[0]
    current, = struct.unpack_from(">I", slot, call_offset)
    if current != loader_call:
        struct.pack_into(">I", slot, call_offset, loader_call)
        changed = True

    push_call = jal(pool_push)
    pop_call = jal(pool_pop)
    pool_candidates = []
    for offset in range(0x24, code_end - 4, 4):
        word, = struct.unpack_from(">I", slot, offset)
        # Match the exact 'btlp' argument window, not the current target.
        # Cached overlays can still call an older resident wrapper address
        # after regional profiles are regenerated.
        if word >> 26 != 3:  # jal
            continue
        before2, = struct.unpack_from(">I", slot, offset - 8)
        before, = struct.unpack_from(">I", slot, offset - 4)
        delay, = struct.unpack_from(">I", slot, offset + 4)
        # IDO emits both LUI/ORI/JAL and LUI/JAL/ORI forms.
        if ((before2 == 0x3C046274 and before == 0x34846C70) or
                (before == 0x3C046274 and delay == 0x34846C70)):
            pool_candidates.append(offset)
    if len(pool_candidates) != 2:
        raise ValueError(
            f"{slot_path}: expected battle-pool push and pop, "
            f"found {len(pool_candidates)}"
        )
    push_offset, pop_offset = pool_candidates
    current_push, = struct.unpack_from(">I", slot, push_offset)
    current_pop, = struct.unpack_from(">I", slot, pop_offset)
    # btlp is not the final teardown.  Keep its ordinary pop, but acquire it
    # through the resident push+music wrapper so the sequence belongs to the
    # pool that remains live while the Mobile menu is displayed.
    if current_pop != pop_call:
        struct.pack_into(">I", slot, pop_offset, pop_call)
        changed = True
    if current_push != wrapper_call:
        struct.pack_into(">I", slot, push_offset, wrapper_call)
        changed = True

    final_candidates = []
    for offset in range(pop_offset + 4, code_end - 4, 4):
        word, = struct.unpack_from(">I", slot, offset)
        if word >> 26 != 3:
            continue
        before2, = struct.unpack_from(">I", slot, offset - 8)
        before, = struct.unpack_from(">I", slot, offset - 4)
        delay, = struct.unpack_from(">I", slot, offset + 4)
        # 'btpc' is the outer Mobile controller pool and is always the last
        # pop on the return path.
        if ((before2 == 0x3C046274 and before == 0x34847063) or
                (before == 0x3C046274 and delay == 0x34847063)):
            final_candidates.append(offset)
    if len(final_candidates) != 1:
        raise ValueError(
            f"{slot_path}: expected one final btpc pop, "
            f"found {len(final_candidates)}"
        )
    final_pop = final_candidates[0]
    current_final, = struct.unpack_from(">I", slot, final_pop)
    if current_final != pop_call:
        struct.pack_into(">I", slot, final_pop, pop_call)
        changed = True
    slot_path.write_bytes(slot)
    return changed


def retarget_overlay_friend_rule_adapter(
    slot_path: Path, direct_target: int, adapter_target: int
) -> bool:
    """Route Friend difficulty selection through the proven western ABI shim."""
    slot = bytearray(slot_path.read_bytes())
    patched, changed = retarget_friend_rule_call(
        bytes(slot), direct_target, adapter_target, slot_path
    )
    if changed:
        slot_path.write_bytes(patched)
    return changed


def initialize_overlay_selection_input(slot_path: Path) -> bool:
    """Restore Fragment 7's checkpoint controller-selection semantics.

    ``selection == 0`` owns the local party with controller 1 and is also the
    male portrait variant consumed by the UI.  Older portrait experiments
    changed it to one and consequently made the local side COM-controlled.
    This migration restores the field itself; the later battle-engine handoff
    preserves that value separately.
    """
    slot = bytearray(slot_path.read_bytes())

    # Fragment 7 owns ``selection`` while its party picker is active.  An
    # earlier portrait experiment preloaded this field with one, which makes
    # the picker assign both sides to COM and ignore controller input.  Restore
    # the exact constructor schedule from the sealed 2026-08-19 checkpoint.
    # Never force this field merely to choose a portrait: doing so changes
    # Fragment 7's controller ownership along with the displayed variant.
    us_call = bytes.fromhex("0411046902002825")
    us_original = bytes.fromhex("02a0202502002825")
    us_helper = bytes.fromhex("24010001aea1001403e0000802a02025")
    if slot[0x508:0x510] == us_call and slot[0x16B0:0x16C0] == us_helper:
        slot[0x508:0x510] = us_original
        slot[0x16B0:0x16C0] = bytes(0x10)
        slot_path.write_bytes(slot)
        return True

    old_tail = bytes.fromhex(
        "afa7000c" "8cae0020" "24050002" "00001025" "00801825"
        "00c03825" "ac8e0000" "8cef0000" "24420001" "24630004"
        "24e70004" "1445fffb" "ac6f0000" "8fb8000c"
    )
    forced_tail = bytes.fromhex(
        "afa7000c" "8cae0020" "ac8e0000" "8ccf0000" "ac8f0004"
        "8cd80004" "ac980008" "ac800010" "240f0001" "ac8f0014"
        "ac800018" "00000000" "00000000" "8fb8000c"
    )
    if forced_tail in slot:
        if slot.count(forced_tail) != 1:
            raise ValueError(f"{slot_path}: ambiguous forced selection initializer")
        offset = slot.index(forced_tail)
        slot[offset:offset + len(forced_tail)] = old_tail
        slot_path.write_bytes(slot)
        return True

    # Already at the checkpoint behavior (or freshly compiled from the
    # corrected C source): leave it untouched and bypass the obsolete
    # pre-initialization migrations below.
    if old_tail in slot or slot[0x508:0x510] == us_original:
        return False
    raise ValueError(
        f"{slot_path}: controller selection does not match the sealed "
        "checkpoint or a recognized obsolete portrait experiment"
    )
    old_tail = bytes.fromhex(
        "afa7000c"  # sw a3, 0x0c(sp)
        "8cae0020"  # lw t6, 0x20(a1)
        "24050002"  # addiu a1, zero, 2
        "00001025"  # or v0, zero, zero
        "00801825"  # or v1, a0, zero
        "00c03825"  # or a3, a2, zero
        "ac8e0000"  # sw t6, 0(a0)
        "8cef0000"  # lw t7, 0(a3)
        "24420001"  # addiu v0, v0, 1
        "24630004"  # addiu v1, v1, 4
        "24e70004"  # addiu a3, a3, 4
        "1445fffb"  # bne v0, a1, loop
        "ac6f0000"  # sw t7, 0(v1)
        "8fb8000c"  # lw t8, 0x0c(sp)
    )
    new_tail = bytes.fromhex(
        "afa7000c"  # preserve rules argument
        "8cae0020"  # data = context->data
        "ac8e0000"
        "8ccf0000"  # teams[0]
        "ac8f0004"
        "8cd80004"  # teams[1]
        "ac980008"
        "ac800010"  # result = 0
        "240f0001"  # selection = 1 (male portrait / human controller 1)
        "ac8f0014"
        "ac800018"  # value = 0
        "00000000"
        "00000000"
        "8fb8000c"
    )
    initialized_cached = bytes.fromhex(
        "ac800010" "ac800014" "ac800018"
        "00000000" "00000000" "00000000"
    )
    wrong_one_cached = bytes.fromhex(
        "ac800010" "240f0001" "ac8f0014" "ac800018"
        "00000000" "00000000"
    )
    us_old_tail = bytes.fromhex(
        "8fae0024"  # context
        "00001025"  # loop index = 0
        "02001825"  # destination cursor = dst
        "8dcf0020"  # data = context->data
        "24050002"  # two team pointers
        "ae0f0000"  # dst->data
        "8fa40028"  # source team pointers
        "8c980000"
        "24420001"
        "24630004"
        "24840004"
        "1445fffb"
        "ac780000"
    )
    us_initialized_tail = bytes.fromhex(
        "8fae0024"
        "8dcf0020"
        "ae0f0000"
        "8fa40028"
        "8c980000"
        "ae180004"
        "8c990004"
        "ae190008"
        "ae000010"
        "24010001"
        "ae010014"
        "ae000018"
        "00000000"
    )
    us_zero_tail = bytes.fromhex(
        "8fae0024" "8dcf0020" "ae0f0000" "8fa40028" "8c980000"
        "ae180004" "8c990004" "ae190008" "ae000010" "ae000014"
        "00000000" "ae000018" "00000000"
    )
    if us_initialized_tail in slot:
        return False
    if us_zero_tail in slot:
        if slot.count(us_zero_tail) != 1:
            raise ValueError(f"{slot_path}: ambiguous zeroed US selection initializer")
        offset = slot.index(us_zero_tail)
        slot[offset:offset + len(us_zero_tail)] = us_initialized_tail
        slot_path.write_bytes(slot)
        return True
    if us_old_tail in slot:
        if slot.count(us_old_tail) != 1:
            raise ValueError(f"{slot_path}: ambiguous US selection initializer")
        offset = slot.index(us_old_tail)
        slot[offset:offset + len(us_old_tail)] = us_initialized_tail
        slot_path.write_bytes(slot)
        return True
    code_end, file_size = struct.unpack_from(">II", slot, 0x14)
    if code_end == 0x16C0:
        # The sealed NTSC hardware/Friend overlay has no room in the
        # constructor itself. Route its first post-bzero argument setup
        # through a four-instruction leaf helper in the unused 0x16B0 cave.
        # The original delay slot still establishes a1; the helper clears
        # selection and restores a0 before returning to the retained call.
        call_site = 0x508
        helper_offset = 0x16B0
        # bgezal zero is an always-taken PC-relative call, so the completely
        # full 0x16C0 relocation table does not need another R_MIPS_26 entry.
        helper_call = (0x04110000 | 0x0469).to_bytes(4, "big")
        original = bytes.fromhex("02a0202502002825")
        patched = helper_call + bytes.fromhex("02002825")
        helper = bytes.fromhex(
            "24010001"  # selection = 1
            "aea10014"
            "03e00008"  # jr ra
            "02a02025"  # delay: restore a0 = dst
        )
        old_zero_helper = bytes.fromhex(
            "aea00014" "00000000" "03e00008" "02a02025"
        )
        if (slot[call_site:call_site + 8] == patched and
                slot[helper_offset:helper_offset + 0x10] == helper):
            return False
        if (slot[call_site:call_site + 8] == patched and
                slot[helper_offset:helper_offset + 0x10] == old_zero_helper):
            slot[helper_offset:helper_offset + 0x10] = helper
            slot_path.write_bytes(slot)
            return True
        if (slot[call_site:call_site + 8] != original or
                slot[helper_offset:helper_offset + 0x10] != bytes(0x10)):
            raise ValueError(f"{slot_path}: unrecognized sealed US selection initializer")
        slot[call_site:call_site + 8] = patched
        slot[helper_offset:helper_offset + 0x10] = helper
        slot_path.write_bytes(slot)
        return True
    if wrong_one_cached in slot:
        return False
    if initialized_cached in slot:
        if slot.count(initialized_cached) != 1:
            raise ValueError(
                f"{slot_path}: ambiguous cached portrait initializer"
            )
        slot[slot.index(initialized_cached):
             slot.index(initialized_cached) + len(initialized_cached)] = wrong_one_cached
        slot_path.write_bytes(slot)
        return True
    old_offsets = []
    cursor = 0
    while True:
        found = slot.find(old_tail, cursor)
        if found < 0:
            break
        old_offsets.append(found)
        cursor = found + 4
    if not old_offsets:
        # A fresh C build calls _bzero(dst, 0x3C) and then performs the
        # source-level local-player initialization. It does not match the compact
        # cached instruction schedule above.
        if bytes.fromhex("2405003c") in slot:
            return False
        raise ValueError(
            f"{slot_path}: selection initializer is neither cached nor fresh"
        )
    if len(old_offsets) != 1:
        raise ValueError(
            f"{slot_path}: found {len(old_offsets)} cached selection initializers"
        )
    offset = old_offsets[0]
    slot[offset:offset + len(old_tail)] = new_tail
    slot_path.write_bytes(slot)
    return True


def preserve_overlay_friend_battle_gender(slot_path: Path) -> bool:
    """Keep Crystal's portrait variant in the retained battle-engine screens.

    Fragment 7 produces ``selection == 0`` for the male local trainer and one
    for female.  The recovered Friend branch used ``selection == 0`` when it
    filled the fourth participant byte, inverting the portrait only after the
    rules screen.  Replace that single comparison with a value-preserving move;
    the controller-selection structure and party-picker ownership are separate
    and remain unchanged.
    """
    slot = bytearray(slot_path.read_bytes())
    original = bytes.fromhex(
        "24020040"  # trainers[i] = 0x40
        "10800003"
        "acc20000"
        "10000004"
        "ac700000"  # opponent portrait variant = 1
        "8e490014"  # local Fragment 7 selection
        "2d2a0001"  # sltiu t2, t1, 1 (incorrect inversion)
        "ac6a0000"
    )
    replacement = original[:-8] + bytes.fromhex(
        "01205025"  # or t2, t1, zero (preserve 0=male, 1=female)
        "ac6a0000"
    )
    # Current IDO output optimizes the temporary away and stores t1 directly.
    # This is semantically identical to the migrated OR/store sequence and is
    # the expected result of compiling the corrected C source from scratch.
    native = bytes.fromhex(
        "24020040"
        "10800003"
        "acc20000"
        "10000003"
        "ac700000"
        "8e490014"
        "ac690000"
    )
    original_count = slot.count(original)
    replacement_count = slot.count(replacement)
    native_count = slot.count(native)
    if original_count == 0 and replacement_count + native_count == 1:
        return False
    if original_count != 1 or replacement_count != 0 or native_count != 0:
        raise ValueError(
            f"{slot_path}: Friend battle portrait handoff is ambiguous "
            f"(original={original_count}, corrected={replacement_count}, "
            f"native={native_count})"
        )
    offset = slot.index(original)
    slot[offset:offset + len(original)] = replacement
    slot_path.write_bytes(slot)
    return True


def retarget_friend_rule_call(
    fragment: bytes, direct_target: int, adapter_target: int,
    label: object = "Mobile overlay",
) -> tuple[bytes, bool]:
    """Retarget the unique Friend-rule call in a compiled fragment.

    The Mobile overlay's direct func_8006A990 call leaves the retained western
    rule state only partially initialized. The Saturday working checkpoint
    calls a resident adapter instead. Keeping this as a structural migration
    gives cached IDO output and freshly compiled fragments the same contract.
    """
    if len(fragment) < 0x20 or fragment[8:16] != b"FRAGMENT":
        raise ValueError(f"{label}: invalid Mobile overlay fragment")
    code_end = struct.unpack_from(">I", fragment, 0x14)[0]
    direct = struct.pack(
        ">I", 3 << 26 | ((direct_target >> 2) & 0x03FFFFFF)
    )
    adapter = struct.pack(
        ">I", 3 << 26 | ((adapter_target >> 2) & 0x03FFFFFF)
    )
    direct_offsets = [
        offset for offset in range(0x20, code_end, 4)
        if fragment[offset:offset + 4] == direct
    ]
    adapter_offsets = [
        offset for offset in range(0x20, code_end, 4)
        if fragment[offset:offset + 4] == adapter
    ]
    if len(adapter_offsets) == 1 and not direct_offsets:
        return fragment, False
    if len(direct_offsets) != 1 or adapter_offsets:
        raise ValueError(
            f"{label}: Friend rule call is ambiguous "
            f"(direct={len(direct_offsets)}, adapter={len(adapter_offsets)})"
        )
    output = bytearray(fragment)
    output[direct_offsets[0]:direct_offsets[0] + 4] = adapter
    return bytes(output), True


def mobile_chunk_wrapper(profile: dict, size: int) -> bytes:
    """Resident Transfer Pak helpers and main-menu result trampoline."""
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    def j(address: int) -> int:
        return 3 << 26 | ((address >> 2) & 0x03FFFFFF)

    # Match Japanese func_80058810's save-pool lifetime. It pushes the
    # dedicated ``dlck`` state before selecting record 3 and pops it only
    # after the record is written, committed, and finalized.
    words = [
        i(9, 29, 29, -0x30), i(43, 29, 31, 0x2C),
        i(43, 29, 16, 0x28), i(43, 29, 17, 0x24),
        i(43, 29, 18, 0x20), i(43, 29, 19, 0x1C),
        i(43, 29, 20, 0x18), r(4, 0, 16, 0x25),
        r(5, 0, 17, 0x25), r(6, 0, 18, 0x25),
        r(7, 0, 19, 0x25), i(6, 19, 0, 17), 0,
        i(9, 0, 20, 0x1000), i(10, 19, 1, 0x1000),
        i(4, 1, 0, 2), 0, r(19, 0, 20, 0x25),
        r(16, 0, 4, 0x25), r(17, 0, 5, 0x25),
        r(18, 0, 6, 0x25), j(symbols["mobile_unlock_read"]),
        r(20, 0, 7, 0x25), i(5, 2, 0, 6),
        r(17, 20, 17, 0x21), r(18, 20, 18, 0x21),
        r(19, 20, 19, 0x23), i(7, 19, 0, -17), 0,
        r(0, 0, 2, 0x25), i(35, 29, 31, 0x2C),
        i(35, 29, 16, 0x28), i(35, 29, 17, 0x24),
        i(35, 29, 18, 0x20), i(35, 29, 19, 0x1C),
        i(35, 29, 20, 0x18), 0x03E00008, i(9, 29, 29, 0x30),
    ]
    data = bytearray(b"".join(struct.pack(">I", word) for word in words))
    # The command is 0x9c bytes. Use its alignment tail for the missing
    # international main-menu result-6 handler, leaving result zero (B/cancel)
    # on the original title-screen route.
    helper_offset = 0xA0
    if len(data) > helper_offset:
        raise ValueError("resident chunked-read trampoline overlaps menu helper")
    data += b"\0" * (helper_offset - len(data))
    route_epilogue = int(profile["menu_route_instruction"], 16) + 4
    helper = [
        j(symbols["func_8006585C"]), i(9, 0, 4, 0x12),
        2 << 26 | ((route_epilogue >> 2) & 0x03FFFFFF), 0,
    ]
    data += b"".join(struct.pack(">I", word) for word in helper)

    # Restore the reverse half of the retail Mobile handshake.  The scanner
    # discovers a valid cart before the localized Mobile Stadium overlay is
    # loaded, so set western Crystal's sMobileStadiumFlag (raw SRAM 0xe000)
    # from resident code as part of that same discovery transaction.
    reverse_unlock_offset = 0xB0
    if len(data) > reverse_unlock_offset:
        raise ValueError("resident menu helper overlaps Crystal unlock helper")
    data += b"\0" * (reverse_unlock_offset - len(data))
    reverse_unlock = [
        i(9, 29, 29, -0x30), i(43, 29, 31, 0x2C),
        i(43, 29, 16, 0x28), r(4, 0, 16, 0x25),
        i(9, 29, 5, 0), i(13, 0, 6, 0xE000),
        j(symbols["mobile_unlock_read"]), i(9, 0, 7, 0x20),
        i(5, 2, 0, 9), i(36, 29, 8, 0),
        i(9, 0, 9, 1), i(4, 8, 9, 6), i(40, 29, 9, 0),
        r(16, 0, 4, 0x25), i(9, 29, 5, 0),
        i(13, 0, 6, 0xE000), j(symbols["mobile_unlock_write_crystal"]),
        i(9, 0, 7, 0x20), i(35, 29, 31, 0x2C),
        i(35, 29, 16, 0x28), 0x03E00008, i(9, 29, 29, 0x30),
    ]
    data += b"".join(struct.pack(">I", word) for word in reverse_unlock)
    if len(data) > size:
        raise ValueError("resident chunked-read/menu trampoline is too large")
    return bytes(data) + b"\0" * (size - len(data))


def mobile_friend_transposer(entry: int, cave: int, size: int = 0x54) -> bytes:
    """Entry trampoline for the resident western Friend-data converter."""
    del entry
    jump = 2 << 26 | ((cave >> 2) & 0x03FFFFFF)
    data = struct.pack(">2I", jump, 0)
    if len(data) > size:
        raise ValueError("resident Friend transposer trampoline is too large")
    return data + b"\0" * (size - len(data))


def mobile_friend_transposer_body(size: int = 0x74) -> bytes:
    """Convert a western Crystal party_struct to Stadium's 0x24-byte core."""
    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    # Crystal:  00..07 identity/moves/TID, 08..0a EXP, 0b..1b training data,
    #           1c Pokerus, 1d..1e caught data, 1f level, 20 status,
    #           21 reserved, 22..23 current HP.
    # Stadium:  00..07 identity/moves/TID, 08..0b EXP, 0c..1c training data,
    #           1d level, 1e egg/validity flags, 1f status,
    #           20..21 caught data, 22..23 current HP.
    # In particular, caught-data byte 0x85 must not land in +0x1e: Fragment 7
    # treats bit 0 there as invalid/egg and used to discard WEATHER.Q and
    # BORSON.W.  Keeping caught data at +0x20/+0x21 also preserves WINTER's
    # female OT bit for the portrait selector.  E092 is halfword aligned, so
    # the leading eight bytes use big-endian unaligned load pairs.
    words = [
        i(0x22, 5, 8, 0), i(0x26, 5, 8, 3), i(0x2B, 4, 8, 0),
        i(0x22, 5, 8, 4), i(0x26, 5, 8, 7), i(0x2B, 4, 8, 4),
        i(0x28, 4, 0, 8),                 # four-byte EXP: clear high byte
        i(9, 4, 4, 9), i(9, 5, 5, 8),
        i(9, 0, 8, 0x14),                 # EXP through happiness
        i(0x24, 5, 9, 0), i(0x28, 4, 9, 0),
        i(9, 5, 5, 1), i(9, 8, 8, -1), i(7, 8, 0, -5),
        i(9, 4, 4, 1),
        i(0x24, 5, 9, 3), i(0x28, 4, 9, 0),  # level: raw +0x1f -> dst +0x1d
        i(0x28, 4, 0, 1),                    # clear Stadium egg/validity flags
        i(0x24, 5, 9, 4), i(0x28, 4, 9, 2),  # status: raw +0x20 -> dst +0x1f
        i(0x24, 5, 9, 1), i(0x28, 4, 9, 3),  # caught data high
        i(0x24, 5, 9, 2), i(0x28, 4, 9, 4),  # caught data low / OT gender
        i(0x25, 5, 9, 6), i(0x29, 4, 9, 5),  # current HP
        0x03E00008, 0,
    ]
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > size:
        raise ValueError("resident Friend party transposer body is too large")
    return data + b"\0" * (size - len(data))


def mobile_download_copy_wrapper(profile: dict, size: int) -> bytes:
    """Resident command 0x26-0x28 copy handler.

    The Mobile Stadium controller itself occupies the 0x84500000 fragment
    window.  Fragment 7 replaces that mapping before it asks the retained
    dispatcher for downloaded records, so a jump-table target in the custom
    overlay is no longer executable at that point.  Keep this small byte-copy
    handler in unused resident libultra development code instead.
    """
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    words: list[int] = []
    labels: dict[str, int] = {}
    branches: list[tuple[int, int, int, int, str]] = []

    def emit(word: int) -> None:
        words.append(word)

    def label(name: str) -> None:
        labels[name] = len(words)

    def branch(opcode: int, rs: int, rt: int, target: str) -> None:
        branches.append((len(words), opcode, rs, rt, target))
        emit(0)

    table = symbols["mobile_download_buffers"]
    table_hi = (table + 0x8000) >> 16
    table_lo = table & 0xFFFF
    continuation = symbols["mobile_download_copy_continue"]
    friend_converter = symbols["mobile_download_copy_cave"] + 0x100

    branch(4, 18, 0, "fail")                 # beqz s2, fail
    emit(i(35, 29, 8, 0x24))                 # delay: lw t0, controller(sp)
    emit(i(11, 8, 1, 4))                     # sltiu at, t0, 4
    branch(4, 1, 0, "fail")                  # beqz at, fail
    emit(r(0, 8, 8, 0x00) | (2 << 6))        # delay: sll t0, t0, 2
    emit(i(15, 0, 9, table_hi))               # lui t1, %hi(buffer table)
    emit(r(9, 8, 9, 0x21))                   # addu t1, t1, t0
    emit(i(35, 9, 10, table_lo))              # lw t2, %lo(table)(t1)
    branch(4, 10, 0, "fail")                 # beqz t2, fail
    emit(i(35, 29, 8, 0x2C))                 # delay: lw t0, index(sp)
    branch(1, 8, 0, "fail")                  # bltz t0, fail
    emit(i(35, 29, 11, 0x20))                # delay: lw t3, command(sp)
    emit(i(9, 0, 1, 0x26))                   # addiu at, zero, 0x26
    branch(5, 11, 1, "replay")               # bne t3, at, replay
    emit(0)
    emit(i(36, 10, 11, 1))                   # lbu t3, 1(t2)
    emit(r(8, 11, 1, 0x2B))                  # sltu at, t0, t3
    branch(4, 1, 0, "fail")                  # beqz at, fail
    emit(r(0, 8, 11, 0x00) | (3 << 6))       # delay: sll t3, t0, 3
    emit(r(0, 8, 12, 0x00) | (6 << 6))       # sll t4, t0, 6
    emit(r(11, 12, 11, 0x21))                # addu t3, t3, t4
    emit(i(9, 10, 10, 0xDB4))                # addiu t2, t2, 0xdb4
    emit(r(10, 11, 5, 0x21))                 # addu a1, t2, t3
    branch(4, 0, 0, "copy")                  # b copy
    emit(i(9, 0, 6, 0x44))                   # delay: addiu a2, zero, 0x44

    label("replay")
    emit(i(9, 0, 1, 0x27))                   # addiu at, zero, 0x27
    branch(5, 11, 1, "friend")               # bne t3, at, friend
    emit(0)
    emit(i(36, 10, 11, 0))                   # lbu t3, 0(t2)
    emit(r(8, 11, 1, 0x2B))                  # sltu at, t0, t3
    branch(4, 1, 0, "fail")                  # beqz at, fail
    emit(r(0, 8, 11, 0x00) | (10 << 6))      # delay: sll t3, t0, 10
    emit(r(0, 8, 12, 0x00) | (7 << 6))       # sll t4, t0, 7
    emit(r(11, 12, 11, 0x21))                # addu t3, t3, t4
    emit(r(0, 8, 12, 0x00) | (4 << 6))       # sll t4, t0, 4
    emit(r(11, 12, 11, 0x21))                # addu t3, t3, t4
    emit(i(9, 10, 10, 4))                    # addiu t2, t2, 4
    emit(r(10, 11, 5, 0x21))                 # addu a1, t2, t3
    emit(i(9, 0, 6, 0x490))                  # addiu a2, zero, 0x490
    branch(4, 0, 0, "copy")                  # b copy
    emit(0)

    label("friend")
    emit(i(9, 0, 1, 0x28))                   # addiu at, zero, 0x28
    branch(5, 11, 1, "fail")                 # bne t3, at, fail
    emit(0)
    branch(5, 8, 0, "fail")                  # only Friend Data index zero exists
    emit(r(18, 0, 4, 0x25))                  # delay: destination
    emit(3 << 26 | ((friend_converter >> 2) & 0x03FFFFFF))
    emit(i(9, 10, 5, 0x1001))                # delay: native western record
    emit(r(2, 0, 3, 0x25))                   # v1 = converter result
    emit(2 << 26 | ((continuation >> 2) & 0x03FFFFFF))
    emit(0)

    label("copy")
    emit(r(18, 0, 4, 0x25))                  # or a0, s2, zero
    label("copy_loop")
    emit(i(36, 5, 8, 0))                     # lbu t0, 0(a1)
    emit(i(9, 5, 5, 1))                      # addiu a1, a1, 1
    emit(i(40, 4, 8, 0))                     # sb t0, 0(a0)
    emit(i(9, 6, 6, -1))                     # addiu a2, a2, -1
    branch(5, 6, 0, "copy_loop")             # bnez a2, copy_loop
    emit(i(9, 4, 4, 1))                      # delay: addiu a0, a0, 1
    emit(i(9, 0, 3, 1))                      # addiu v1, zero, 1
    emit(2 << 26 | ((continuation >> 2) & 0x03FFFFFF))
    emit(0)

    label("fail")
    emit(r(0, 0, 3, 0x25))                   # or v1, zero, zero
    emit(2 << 26 | ((continuation >> 2) & 0x03FFFFFF))
    emit(0)

    for index, opcode, rs, rt, target in branches:
        words[index] = i(opcode, rs, rt, labels[target] - index - 1)
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > size:
        raise ValueError("resident Mobile download copy handler is too large")
    return data + b"\0" * (size - len(data))


def mobile_friend_copy_converter(profile: dict, size: int = 0x180) -> bytes:
    """Validate and convert command 0x28 entirely from resident code.

    Fragment 7 replaces the 0x84500000 Mobile overlay before requesting the
    selected Friend record.  Consequently neither the retained command
    handler nor the scanner release callback may call the overlay converter.
    This helper lives in the unused tail of the KMC debug initializer and
    consumes the E000 image already cached while the physical Pak was live.
    """
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    words: list[int] = []
    labels: dict[str, int] = {}
    branches: list[tuple[int, int, int, int, str]] = []

    def emit(word: int) -> None:
        words.append(word)

    def label(name: str) -> None:
        labels[name] = len(words)

    def branch(opcode: int, rs: int, rt: int, target: str) -> None:
        branches.append((len(words), opcode, rs, rt, target))
        emit(0)

    # Preserve every saved register used as a loop pointer across retained
    # libc/party-conversion calls.
    for word in (
        i(9, 29, 29, -0x70), i(43, 29, 31, 0x6C),
        i(43, 29, 16, 0x68), i(43, 29, 17, 0x64),
        i(43, 29, 18, 0x60), i(43, 29, 19, 0x5C),
        i(43, 29, 20, 0x58), i(43, 29, 21, 0x54),
        i(43, 29, 22, 0x50), i(43, 29, 23, 0x4C),
        r(4, 0, 16, 0x25), r(5, 0, 17, 0x25),
        r(0, 0, 8, 0x25), r(17, 0, 9, 0x25),
        i(9, 0, 10, 0x1B3),
    ):
        emit(word)
    label("sum_loop")
    for word in (
        i(36, 9, 11, 0), r(8, 11, 8, 0x21), i(12, 8, 8, 0xFFFF),
        i(9, 9, 9, 1), i(9, 10, 10, -1),
    ):
        emit(word)
    branch(5, 10, 0, "sum_loop")
    emit(0)
    for word in (
        i(36, 17, 11, 0x1B3), i(36, 17, 12, 0x1B4),
        r(0, 12, 12, 0x00) | (8 << 6), r(11, 12, 11, 0x25),
    ):
        emit(word)
    branch(5, 8, 11, "fail")
    emit(0)

    # Selection-record header.
    for word in (
        r(16, 0, 4, 0x25), i(9, 0, 5, 0x220),
        j(3, symbols["_bzero"]), 0,
        r(16, 0, 4, 0x25), r(17, 0, 5, 0x25),
        j(3, symbols["func_800616AC"]), 0,
        i(36, 17, 8, 0x0B), i(40, 16, 8, 0x0C),
        i(36, 17, 8, 0x0C), i(40, 16, 8, 0x0D),
        i(9, 17, 18, 0x91), i(9, 17, 19, 0x0D),
        i(9, 17, 20, 0x4F), i(9, 16, 21, 0x10),
        i(9, 0, 22, 6), i(9, 29, 23, 0x10),
    ):
        emit(word)

    label("party_loop")
    for word in (
        r(23, 0, 4, 0x25), i(9, 0, 5, 0x3C),
        j(3, symbols["_bzero"]), 0,
        r(23, 0, 4, 0x25), r(18, 0, 5, 0x25),
        j(3, symbols["mobile_friend_transposer"]), 0,
        r(19, 0, 4, 0x25), i(9, 23, 5, 0x24),
        i(9, 0, 6, 0x0B), j(3, symbols["_bcopy"]), 0,
        i(9, 0, 8, 0x50), i(40, 23, 8, 0x2F),
        r(20, 0, 4, 0x25), i(9, 23, 5, 0x30),
        i(9, 0, 6, 0x0B), j(3, symbols["_bcopy"]), 0,
        i(9, 0, 8, 0x50), i(40, 23, 8, 0x3B),
        r(21, 0, 4, 0x25), r(23, 0, 5, 0x25),
        j(3, symbols["func_80051690"]), 0,
        i(9, 18, 18, 0x30), i(9, 19, 19, 0x0B),
        i(9, 20, 20, 0x0B), i(9, 21, 21, 0x58),
        i(9, 22, 22, -1),
    ):
        emit(word)
    branch(5, 22, 0, "party_loop")
    emit(0)
    branch(4, 0, 0, "done")
    emit(i(9, 0, 2, 1))
    label("fail")
    emit(r(0, 0, 2, 0x25))
    label("done")
    for word in (
        i(35, 29, 31, 0x6C), i(35, 29, 16, 0x68),
        i(35, 29, 17, 0x64), i(35, 29, 18, 0x60),
        i(35, 29, 19, 0x5C), i(35, 29, 20, 0x58),
        i(35, 29, 21, 0x54), i(35, 29, 22, 0x50),
        i(35, 29, 23, 0x4C), 0x03E00008, i(9, 29, 29, 0x70),
    ):
        emit(word)

    for index, opcode, rs, rt, target in branches:
        words[index] = i(opcode, rs, rt, labels[target] - index - 1)
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > size:
        raise ValueError(
            f"resident Friend converter is 0x{len(data):X}, exceeds 0x{size:X}"
        )
    return data + b"\0" * (size - len(data))


def mobile_friend_rule_wrapper(profile: dict, size: int = 0x28) -> bytes:
    """Adapt Friend difficulty selection to the retained western rule ABI.

    This is the exact dynamic sequence used by the sealed Saturday checkpoint:
    select the requested rule, obtain its retained rule value, then tail-call
    the western initializer with the required 0x500 class bits. No fixture
    values are embedded.
    """
    symbols = {
        name: int(value, 16) for name, value in profile["symbols"].items()
    }

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    words = [
        i(9, 29, 29, -0x18),
        i(43, 29, 31, 0x14),
        j(3, symbols["func_8006A990"]),
        0,
        j(3, symbols["func_8006BC40"]),
        0,
        i(13, 2, 4, 0x500),
        i(35, 29, 31, 0x14),
        j(2, symbols["func_8006BC18"]),
        i(9, 29, 29, 0x18),
    ]
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > size:
        raise ValueError("resident Friend rule adapter is too large")
    return data + b"\0" * (size - len(data))


def mobile_state_session_wrapper(
    profile: dict, fragment_wrapper: int, size: int
) -> bytes:
    """Copy Mobile SRAM at the retained scanner's release point.

    Entry zero first performs the retail state-18 controller/Pak setup, then
    runs the retained international scanner for each controller and maps the
    Mobile controller overlay.  The scanner's original
    release JAL is redirected to entry +0x140: while that already-acquired
    Pak is still live, the hook copies E000 and F000 into a main-pool buffer
    and finally performs the original release.

    It is essential that this scan happens from state 18, rather than from
    the earlier main-menu scan.  main_pool_pop_state restores the earlier
    scan's arena before the Crystal picker is entered; retaining a pointer to
    an allocation made there produces the near-null/TLB faults seen on Pak
    selection.  Running the scanner here gives the raw buffers exactly the
    same pool lifetime as the Mobile overlay while still completing every
    physical Pak release before fragment 53 is mapped.
    """
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    words: list[int] = []
    labels: dict[str, int] = {}
    branches: list[tuple[int, int, int, int, str]] = []

    def emit(word: int) -> None:
        words.append(word)

    def label(name: str) -> None:
        labels[name] = len(words)

    def branch(opcode: int, rs: int, rt: int, target: str) -> None:
        branches.append((len(words), opcode, rs, rt, target))
        emit(0)

    def hilo(address: int) -> tuple[int, int]:
        return (address + 0x8000) >> 16, address & 0xFFFF

    table_hi, table_lo = hilo(symbols["mobile_download_buffers"])
    count_hi, count_lo = hilo(symbols["mobile_friend_counts"])
    active_hi, active_lo = hilo(symbols["mobile_scanner_active_mask"])
    cache_hi, cache_lo = hilo(symbols["mobile_transfer_bank_cache"])
    session_data = symbols["mobile_state_session"] + size - 8
    data_hi, data_lo = hilo(session_data)

    # Entry zero: preserve the fragment-loader ABI and establish the retail
    # state-18 controller/PIF context *before* calling mobile_data_scan().
    # Calling the scanner without these two setup operations leaves its Pak
    # acquire path waiting forever in func_800092C8/func_80009200.  This was
    # the soft hang observed at PC 0x80009290 after choosing Crystal.
    #
    # After setup, clear stale download slots, arm the release hook, perform
    # one resident scan for all four ports, and then map the overlay.  The
    # Allocate a DLD object only for a scanner-active controller.  Allocation
    # must happen here in the state-18 caller: doing it from the scanner's
    # release callback produced invalid main-pool pointers and passed garbage
    # destinations to osInvalDCache (EPC 0x8007c1a0).
    #
    # The ordinary menu scan leaves descriptor +5 set.  mobile_data_scan()
    # treats that as "already classified" and returns before acquiring the
    # Pak; clear it immediately before this dedicated Mobile scan.  This is
    # still outside the 0x84500000 fragment, so E000/F000 are copied while the
    # physical Pak is powered and the overlay is mapped only after release.
    for word in (
        i(9, 29, 29, -0x50), i(43, 29, 31, 0x4C),
        i(43, 29, 16, 0x48), i(43, 29, 17, 0x44),
        i(43, 29, 4, 0x20), i(43, 29, 5, 0x24),
        i(43, 29, 6, 0x28), i(43, 29, 7, 0x2C),
        i(35, 29, 8, 0x60), i(43, 29, 8, 0x10),
        # Same state-router initialization used by the original Mobile
        # controller before its overlay-local Transfer Pak reads.
        i(9, 0, 4, 7), r(0, 0, 5, 0x25), r(0, 0, 6, 0x25),
        j(3, symbols["func_800353B4"]), 0,
        j(3, symbols["func_800354E4"]), i(9, 0, 4, 0x41),
        i(15, 0, 9, count_hi), i(9, 9, 9, count_lo),
        i(43, 9, 0, 0),
        # Only scanner releases made by this state-18 pass may prefetch.
        i(15, 0, 8, data_hi), i(9, 8, 8, data_lo),
        i(9, 0, 9, 1), i(40, 8, 9, 0),
        i(15, 0, 17, active_hi), i(35, 17, 17, active_lo),
        r(0, 0, 16, 0x25),
    ):
        emit(word)
    label("scan_loop")
    emit(i(10, 16, 1, 4))
    branch(4, 1, 0, "scan_done")
    emit(r(0, 16, 8, 0x00) | (2 << 6))
    # Clear this slot before testing the scanner's actual active mask.
    emit(i(15, 0, 9, table_hi))
    emit(i(9, 9, 9, table_lo))
    emit(r(9, 8, 9, 0x21))
    emit(i(43, 9, 0, 0))
    emit(r(16, 17, 8, 0x06))
    emit(i(12, 8, 8, 1))
    branch(4, 8, 0, "scan_next")
    emit(i(9, 0, 4, 0x13E0))
    # Allocation is deliberately in the state-18 caller, never in the
    # controller worker/release callback.
    emit(j(3, symbols["func_80002974"]))
    emit(r(0, 0, 5, 0x25))
    branch(4, 2, 0, "scan_next")
    # func_80002974 may clobber every t-register.  Recompute the table slot
    # from the saved controller index before publishing the allocation; using
    # the pre-call $t1 here sent the subsequent SRAM DMA/cache operation to a
    # garbage address on both PJ64 and physical hardware.
    emit(r(0, 16, 8, 0x00) | (2 << 6))
    emit(i(15, 0, 9, table_hi))
    emit(i(9, 9, 9, table_lo))
    emit(r(9, 8, 9, 0x21))
    emit(i(43, 9, 2, 0))
    emit(r(2, 0, 4, 0x25))
    emit(j(3, symbols["_bzero"]))
    emit(i(9, 0, 5, 0x13E0))
    # descriptor = contexts + controller * 0x70; clear classified byte +5.
    emit(r(0, 16, 8, 0x00) | (3 << 6))
    emit(r(8, 16, 8, 0x23))
    emit(r(0, 8, 8, 0x00) | (4 << 6))
    context_hi, context_lo = hilo(symbols["mobile_transfer_contexts"])
    emit(i(15, 0, 9, context_hi))
    emit(i(9, 9, 9, context_lo))
    emit(r(9, 8, 9, 0x21))
    emit(i(40, 9, 0, 5))
    emit(r(16, 0, 4, 0x25))
    # The international routine ignores this request mask, but 0x18 matches
    # the Japanese Friend plus Battle/Rule operation pair and documents the
    # intended session.  The release hook performs both reads on all builds.
    emit(j(3, symbols["mobile_data_scan"]))
    emit(i(9, 0, 5, 0x18))
    label("scan_next")
    emit(i(9, 16, 16, 1))
    branch(4, 0, 0, "scan_loop")
    emit(0)

    label("scan_done")
    emit(i(15, 0, 8, data_hi))
    emit(i(9, 8, 8, data_lo))
    emit(i(40, 8, 0, 0))

    label("map_overlay")
    for word in (
        i(35, 29, 4, 0x20), i(35, 29, 5, 0x24),
        i(35, 29, 6, 0x28), i(35, 29, 7, 0x2C),
        i(35, 29, 8, 0x60), i(43, 29, 8, 0x10),
        j(3, fragment_wrapper), 0,
        i(35, 29, 31, 0x4C), i(35, 29, 16, 0x48),
        i(35, 29, 17, 0x44),
        i(9, 29, 29, 0x50), 0x03E00008, 0,
    ):
        emit(word)

    if len(words) * 4 > 0x140:
        raise ValueError(
            f"resident Mobile scanner entry is 0x{len(words) * 4:X} bytes "
            "and overlaps release hook"
        )
    while len(words) * 4 < 0x140:
        emit(0)

    # Entry +0x140 replaces mobile_data_scan's original release call. The
    # caller passes controller in a0 and the release-state byte in a1.  The
    # hook is armed only around the dedicated state-18 scan.  It repeats the
    # Japanese readiness predicate, allocates exactly one object for a ready
    # cart in the current pool, fills it, and leaves the acquire-state byte
    # untouched for the original release routine.
    label("release_hook")
    for word in (
        i(9, 29, 29, -0x30), i(43, 29, 31, 0x2C),
        i(43, 29, 16, 0x28), i(43, 29, 17, 0x24), i(43, 29, 18, 0x20),
        r(4, 0, 16, 0x25), r(5, 0, 17, 0x25),
        i(15, 0, 8, data_hi), i(9, 8, 8, data_lo),
        i(36, 8, 8, 0),
    ):
        emit(word)
    branch(4, 8, 0, "release_only")
    emit(r(16, 0, 4, 0x25))
    emit(j(3, symbols["mobile_gbpak_ready"]))
    emit(0)
    branch(4, 2, 0, "release_only")
    emit(r(0, 16, 8, 0x00) | (2 << 6))
    emit(i(15, 0, 9, table_hi))
    emit(i(9, 9, 9, table_lo))
    emit(r(9, 8, 9, 0x21))
    emit(i(35, 9, 18, 0))
    branch(4, 18, 0, "release_only")
    emit(0)
    for word in (
        i(15, 0, 8, cache_hi), i(9, 8, 8, cache_lo),
        r(8, 16, 8, 0x21), i(9, 0, 9, -1), i(40, 8, 9, 0),
        # Japanese order: Friend Data at E000 first.
        r(16, 0, 4, 0x25), i(9, 18, 5, 0x1000),
        i(13, 0, 6, 0xE000), j(3, symbols["mobile_unlock_read"]),
        i(9, 0, 7, 0x1C0),
    ):
        emit(word)
    branch(5, 2, 0, "release_only")
    # Do not consume Crystal's first-use flag in the state-18 data scan.
    # Japanese Stadium's ordinary E000/F000 scan is read-only.  Fragment 10
    # later calls func_80060EFC, which performs a separate acquire/read/
    # conditional-write/release transaction and returns the successful write
    # as the localized popup trigger.  Writing 01 here made that callback see
    # an already-enabled cartridge, so the popup could never be displayed.
    # The E000 read/write calls may clobber every t-register. Rebuild the
    # per-port mapper-cache address instead of reusing $t0/$t1 across them.
    emit(i(15, 0, 8, cache_hi))
    for word in (
        # Then Battle/Rule Data at F000, still before the original release.
        i(9, 8, 8, cache_lo), r(8, 16, 8, 0x21),
        i(9, 0, 9, -1), i(40, 8, 9, 0), r(16, 0, 4, 0x25),
        r(18, 0, 5, 0x25), i(13, 0, 6, 0xF000),
        j(3, symbols["mobile_unlock_read"]), i(9, 0, 7, 0x1000),
    ):
        emit(word)
    branch(5, 2, 0, "release_only")
    emit(0)

    label("release_only")
    for word in (
        r(16, 0, 4, 0x25), r(17, 0, 5, 0x25),
        j(3, symbols["mobile_gbpak_release"]), 0,
        i(35, 29, 31, 0x2C), i(35, 29, 16, 0x28),
        i(35, 29, 17, 0x24), i(35, 29, 18, 0x20),
        i(9, 29, 29, 0x30), 0x03E00008, 0,
    ):
        emit(word)

    if len(words) * 4 > 0x280:
        raise ValueError("resident Mobile release hook overlaps finish helper")
    while len(words) * 4 < 0x280:
        emit(0)

    for word in (
        i(9, 29, 29, -0x20), i(43, 29, 31, 0x1C),
        i(43, 29, 4, 0x10), i(43, 29, 5, 0x14), i(43, 29, 6, 0x18),
        i(35, 29, 4, 0x10), i(35, 29, 5, 0x14), i(35, 29, 6, 0x18),
        j(3, symbols["func_800047D8"]), 0,
        i(35, 29, 31, 0x1C), i(9, 29, 29, 0x20), 0x03E00008, 0,
    ):
        emit(word)

    for index, opcode, rs, rt, target in branches:
        words[index] = i(opcode, rs, rt, labels[target] - index - 1)
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > size - 8:
        raise ValueError("resident Mobile scanner-prefetch wrapper is too large")
    return data + b"\0" * (size - len(data))


def mobile_reacquire_prefetch_wrapper(
    profile: dict, fragment_wrapper: int, size: int,
    map_overlay: bool = True,
) -> bytes:
    """Prefetch Mobile SRAM resident, release Paks, then map the overlay.

    Transfer Pak I/O and fragment/TLB mapping must never overlap.  The retail
    scanner is allowed to finish its balanced session, then each detected Pak
    is reacquired long enough to copy the native western F000 and E000 blocks
    into a main-pool buffer.  Every Pak is released before func_80065748 maps
    and calls the 0x84500000 Mobile controller.  The overlay only validates
    and converts those prefetched bytes.
    """
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    words: list[int] = []
    labels: dict[str, int] = {}
    branches: list[tuple[int, int, int, int, str]] = []

    def emit(word: int) -> None:
        words.append(word)

    def label(name: str) -> None:
        labels[name] = len(words)

    def branch(opcode: int, rs: int, rt: int, target: str) -> None:
        branches.append((len(words), opcode, rs, rt, target))
        emit(0)

    def hilo(address: int) -> tuple[int, int]:
        return (address + 0x8000) >> 16, address & 0xFFFF

    context = symbols["mobile_transfer_contexts"] + 0x28
    context_hi, context_lo = hilo(context)
    # This is the mask consumed by mobile_data_scan itself.  The byte found
    # beside mobile_unlock_read belongs to the mapper and is not an equivalent
    # controller-presence mask on hardware.
    scanner_active = symbols["mobile_scanner_active_mask"]
    active_hi, active_lo = hilo(scanner_active)
    table = symbols["mobile_download_buffers"]
    table_hi, table_lo = hilo(table)
    friend_counts = symbols["mobile_friend_counts"]
    count_hi, count_lo = hilo(friend_counts)
    bank_cache = symbols["mobile_transfer_bank_cache"]
    cache_hi, cache_lo = hilo(bank_cache)
    session_data = symbols["mobile_state_session"] + size - 8
    data_hi, data_lo = hilo(session_data)

    # Preserve the state-router call exactly, including its stack fifth arg.
    for word in (
        i(9, 29, 29, -0x60), i(43, 29, 31, 0x5C),
        i(43, 29, 16, 0x58), i(43, 29, 17, 0x54),
        i(43, 29, 18, 0x50), i(43, 29, 19, 0x4C),
        i(43, 29, 4, 0x30), i(43, 29, 5, 0x34),
        i(43, 29, 6, 0x38), i(43, 29, 7, 0x3C),
        i(9, 0, 4, 7), r(0, 0, 5, 0x25), r(0, 0, 6, 0x25),
        j(3, symbols["func_800353B4"]), 0,
        j(3, symbols["func_800354E4"]), i(9, 0, 4, 0x41),
        i(15, 0, 17, active_hi), i(36, 17, 17, active_lo),
        i(15, 0, 18, data_hi), i(9, 18, 18, data_lo),
        i(40, 18, 0, 0), i(43, 18, 0, 4),
        i(15, 0, 8, table_hi), i(9, 8, 8, table_lo),
        i(43, 8, 0, 0), i(43, 8, 0, 4),
        i(43, 8, 0, 8), i(43, 8, 0, 12),
        i(15, 0, 9, count_hi), i(9, 9, 9, count_lo),
        i(43, 9, 0, 0),
        i(9, 0, 16, 0),
    ):
        emit(word)

    label("controller_loop")
    emit(i(10, 16, 1, 4))
    branch(4, 1, 0, "call_overlay")
    emit(r(16, 17, 8, 0x06))                 # delay: scanner mask >> port
    emit(i(12, 8, 8, 1))
    branch(4, 8, 0, "controller_next")
    emit(r(0, 16, 8, 0x00) | (3 << 6))
    emit(r(8, 16, 8, 0x23))                  # port * 7
    emit(r(0, 8, 8, 0x00) | (4 << 6))        # port * 0x70
    emit(i(15, 0, 6, context_hi))
    emit(i(9, 6, 6, context_lo))
    emit(r(6, 8, 6, 0x21))
    emit(r(16, 0, 4, 0x25))
    emit(i(9, 18, 5, 4))
    emit(r(5, 16, 5, 0x21))                  # one release-state byte/port
    emit(j(3, symbols["mobile_gbpak_acquire"]))
    emit(0)
    branch(4, 2, 0, "controller_next")
    emit(0)
    # Match the Japanese sequence: an acquired Pak is not read until Crystal
    # reports the retained ready state.
    emit(r(16, 0, 4, 0x25))
    emit(j(3, symbols["mobile_gbpak_ready"]))
    emit(0)
    branch(4, 2, 0, "release_current")
    emit(0)

    # Allocate one combined raw/converted buffer only for an acquired cart.
    emit(i(9, 0, 4, 0x13E0))
    emit(r(0, 0, 5, 0x25))
    emit(j(3, symbols["func_80002974"]))
    emit(0)
    emit(r(2, 0, 19, 0x25))                  # s3 = buffer
    branch(4, 19, 0, "release_current")
    emit(0)
    emit(r(19, 0, 4, 0x25))
    emit(i(9, 0, 5, 0x13E0))
    emit(j(3, symbols["_bzero"]))
    emit(0)

    # Force MBC30 bank 7 selection, then copy the retail-sized F000 request.
    emit(i(15, 0, 8, cache_hi))
    emit(i(9, 8, 8, cache_lo))
    emit(r(8, 16, 8, 0x21))
    emit(i(9, 0, 9, -1))
    emit(i(40, 8, 9, 0))
    emit(r(16, 0, 4, 0x25))
    emit(r(19, 0, 5, 0x25))
    emit(i(13, 0, 6, 0xF000))
    emit(j(3, symbols["mobile_unlock_read"]))
    emit(i(9, 0, 7, 0x1000))
    branch(5, 2, 0, "release_current")
    emit(0)

    # Copy E000 into the raw Friend-data area of the same buffer.  The F000
    # read may clobber all t-registers, so reconstruct the mapper-cache
    # address instead of carrying $t0 across that call.
    emit(i(15, 0, 8, cache_hi))
    emit(i(9, 8, 8, cache_lo))
    emit(r(8, 16, 8, 0x21))
    emit(i(9, 0, 9, -1))
    emit(i(40, 8, 9, 0))
    emit(r(16, 0, 4, 0x25))
    emit(i(9, 19, 5, 0x1000))
    emit(i(13, 0, 6, 0xE000))
    emit(j(3, symbols["mobile_unlock_read"]))
    emit(i(9, 0, 7, 0x1C0))
    branch(5, 2, 0, "release_current")
    emit(r(0, 16, 8, 0x00) | (2 << 6))

    # Publish only a fully prefetched buffer to the overlay.
    emit(i(15, 0, 9, table_hi))
    emit(i(9, 9, 9, table_lo))
    emit(r(9, 8, 9, 0x21))
    emit(i(43, 9, 19, 0))
    emit(i(36, 18, 9, 0))
    emit(i(9, 0, 10, 1))
    emit(r(16, 10, 10, 0x04))
    emit(r(9, 10, 9, 0x25))
    emit(i(40, 18, 9, 0))

    label("release_current")
    emit(r(16, 0, 4, 0x25))
    emit(i(9, 18, 5, 4))
    emit(r(5, 16, 5, 0x21))
    emit(i(36, 5, 5, 0))
    emit(j(3, symbols["mobile_gbpak_release"]))
    emit(0)

    label("controller_next")
    emit(i(9, 16, 16, 1))
    branch(4, 0, 0, "controller_loop")
    emit(0)

    label("call_overlay")
    if map_overlay:
        for word in (
            i(36, 18, 8, 0), i(15, 0, 9, active_hi),
            i(40, 9, 8, active_lo),
            i(35, 29, 4, 0x30), i(35, 29, 5, 0x34),
            i(35, 29, 6, 0x38), i(35, 29, 7, 0x3C),
            i(35, 29, 8, 0x70), i(43, 29, 8, 0x10),
            j(3, fragment_wrapper), 0,
            r(2, 0, 19, 0x25), r(19, 0, 2, 0x25),
            i(35, 29, 31, 0x5C), i(35, 29, 16, 0x58),
            i(35, 29, 17, 0x54), i(35, 29, 18, 0x50),
            i(35, 29, 19, 0x4C), i(9, 29, 29, 0x60),
            0x03E00008, 0,
        ):
            emit(word)
    else:
        # Return to the resident state stub.  It performs the fragment map
        # only after every dedicated Pak session above has been released.
        # Allocating here also keeps the raw buffers in the same scene/pool
        # lifetime as the mapped Mobile controller.
        for word in (
            i(36, 18, 8, 0), i(15, 0, 9, active_hi),
            i(40, 9, 8, active_lo), r(0, 0, 2, 0x25),
            i(35, 29, 31, 0x5C), i(35, 29, 16, 0x58),
            i(35, 29, 17, 0x54), i(35, 29, 18, 0x50),
            i(35, 29, 19, 0x4C), i(9, 29, 29, 0x60),
            0x03E00008, 0,
        ):
            emit(word)

    if len(words) * 4 > 0x280:
        raise ValueError("resident Mobile prefetch entry overlaps finish helper")
    while len(words) * 4 < 0x280:
        emit(0)

    # Post-download fragment loads no longer need Transfer Pak cleanup.
    for word in (
        i(9, 29, 29, -0x20), i(43, 29, 31, 0x1C),
        i(43, 29, 4, 0x10), i(43, 29, 5, 0x14), i(43, 29, 6, 0x18),
        i(35, 29, 4, 0x10), i(35, 29, 5, 0x14), i(35, 29, 6, 0x18),
        j(3, symbols["func_800047D8"]), 0,
        i(35, 29, 31, 0x1C), i(9, 29, 29, 0x20), 0x03E00008, 0,
    ):
        emit(word)

    for index, opcode, rs, rt, target in branches:
        words[index] = i(opcode, rs, rt, labels[target] - index - 1)
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > size - 8:
        raise ValueError("resident Mobile prefetch wrapper is too large")
    return data + b"\0" * (size - len(data))


def mobile_scanner_prefetch_wrapper(
    profile: dict, fragment_wrapper: int, size: int
) -> bytes:
    """Cache Mobile SRAM before Pak release and preserve it for state 18.

    Entry +0x140 replaces the retained main-menu scanner's release JAL.  At
    that point Crystal is still powered, so the hook reads E000 directly into
    persistent resident BSS and F000 into the menu-pool object, then performs
    the original release.  The menu-pool frame is popped before state 18.

    Entry zero therefore reclaims one equally-sized block for every cached
    controller *before* loading the Mobile fragment.  The pool normally
    returns the same address and the bytes remain intact; if it returns a
    different address, the entry copies the cached object before publishing
    the new pointer.  This gives the data the state-18 lifetime without a
    second Transfer Pak scan (which deadlocks in the SI/PIF DMA path on both
    Project64 and physical hardware).

    The scanner passes ``(controller, release_state_byte)`` in a0/a1.  In
    particular, a1 is a byte value, not a pointer; an older hook dereferenced
    it and could fault at address 0 or 1.
    """
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    def hilo(address: int) -> tuple[int, int]:
        return (address + 0x8000) >> 16, address & 0xFFFF

    words: list[int] = []
    labels: dict[str, int] = {}
    branches: list[tuple[int, int, int, int, str]] = []

    def emit(word: int) -> None:
        words.append(word)

    def label(name: str) -> None:
        labels[name] = len(words)

    def branch(opcode: int, rs: int, rt: int, target: str) -> None:
        branches.append((len(words), opcode, rs, rt, target))
        emit(0)

    table_hi, table_lo = hilo(symbols["mobile_download_buffers"])
    count_hi, count_lo = hilo(symbols["mobile_friend_counts"])
    cache_hi, cache_lo = hilo(symbols["mobile_transfer_bank_cache"])
    friend_cache_hi, friend_cache_lo = hilo(
        symbols["mobile_friend_raw_cache"]
    )
    # Reclaim the menu-pool objects in state 18 before fragment allocations
    # can overwrite them.  Allocation does not clear a block, so the normal
    # same-address case deliberately requires no copy.
    for word in (
        i(9, 29, 29, -0x60), i(43, 29, 31, 0x5C),
        i(43, 29, 16, 0x58), i(43, 29, 17, 0x54),
        i(43, 29, 18, 0x50), i(43, 29, 19, 0x4C),
        i(43, 29, 20, 0x48),
        i(43, 29, 4, 0x30), i(43, 29, 5, 0x34),
        i(43, 29, 6, 0x38), i(43, 29, 7, 0x3C),
        i(35, 29, 8, 0x70), i(43, 29, 8, 0x10),
        i(15, 0, 17, table_hi), i(9, 17, 17, table_lo),
        r(0, 0, 16, 0x25),
    ):
        emit(word)
    label("promote_loop")
    emit(i(10, 16, 1, 4))
    branch(4, 1, 0, "promote_done")
    emit(r(0, 16, 8, 0x00) | (2 << 6))
    emit(r(17, 8, 19, 0x21))
    emit(i(35, 19, 18, 0))
    branch(4, 18, 0, "promote_next")
    emit(i(9, 0, 4, 0x13E0))
    emit(j(3, symbols["func_80002974"]))
    emit(r(0, 0, 5, 0x25))
    emit(r(2, 0, 20, 0x25))
    branch(4, 20, 0, "promote_clear")
    emit(0)
    branch(4, 20, 18, "promote_publish")
    emit(r(18, 0, 8, 0x25))
    emit(r(20, 0, 9, 0x25))
    emit(i(9, 0, 10, 0x13E0))
    label("copy_loop")
    emit(i(35, 8, 11, 0))
    emit(i(43, 9, 11, 0))
    emit(i(9, 8, 8, 4))
    emit(i(9, 9, 9, 4))
    emit(i(9, 10, 10, -4))
    branch(5, 10, 0, "copy_loop")
    emit(0)
    label("promote_publish")
    # Restore the persistent Friend block after reclaiming the state-owned
    # buffer. The released menu pool can overwrite bytes at buffer +0x1000
    # even when its first 0x1000-byte Battle block happens to survive.
    emit(r(0, 16, 8, 0x00) | (9 << 6))
    emit(r(0, 16, 9, 0x00) | (6 << 6))
    emit(r(8, 9, 8, 0x23))
    emit(i(15, 0, 4, friend_cache_hi))
    emit(i(9, 4, 4, friend_cache_lo))
    emit(r(4, 8, 4, 0x21))
    emit(i(9, 20, 5, 0x1000))
    # Copy the resident block inline.  Calling the retained libc _bcopy from
    # this state-router trampoline returned without populating buffer+0x1000
    # in live Project64 RDRAM, even though its source cache was byte-perfect.
    # A fixed aligned word loop has no overlay, pool, or libc-state dependency
    # and exactly covers the 0x1c0-byte Friend block.
    emit(i(9, 0, 10, 0x1C0))
    label("friend_copy_loop")
    emit(i(35, 4, 11, 0))
    emit(i(43, 5, 11, 0))
    emit(i(9, 4, 4, 4))
    emit(i(9, 5, 5, 4))
    emit(i(9, 10, 10, -4))
    branch(5, 10, 0, "friend_copy_loop")
    emit(0)
    branch(4, 0, 0, "promote_next")
    emit(i(43, 19, 20, 0))
    label("promote_clear")
    emit(i(43, 19, 0, 0))
    label("promote_next")
    emit(i(9, 16, 16, 1))
    branch(4, 0, 0, "promote_loop")
    emit(0)

    label("promote_done")
    for word in (
        i(35, 29, 4, 0x30), i(35, 29, 5, 0x34),
        i(35, 29, 6, 0x38), i(35, 29, 7, 0x3C),
        i(35, 29, 8, 0x70), i(43, 29, 8, 0x10),
        j(3, fragment_wrapper), 0,
        i(35, 29, 31, 0x5C), i(35, 29, 16, 0x58),
        i(35, 29, 17, 0x54), i(35, 29, 18, 0x50),
        i(35, 29, 19, 0x4C), i(35, 29, 20, 0x48),
        i(9, 29, 29, 0x60), 0x03E00008, 0,
    ):
        emit(word)
    while len(words) * 4 < 0x140:
        emit(0)

    label("release_hook")
    for word in (
        i(9, 29, 29, -0x30), i(43, 29, 31, 0x2C),
        i(43, 29, 16, 0x28), i(43, 29, 17, 0x24),
        i(43, 29, 18, 0x20),
        r(4, 0, 16, 0x25), r(5, 0, 17, 0x25),
        r(16, 0, 4, 0x25), j(3, symbols["mobile_gbpak_ready"]), 0,
    ):
        emit(word)
    branch(4, 2, 0, "release_only")
    emit(i(9, 0, 4, 0x13E0))
    emit(r(0, 0, 5, 0x25))
    emit(j(3, symbols["func_80002974"]))
    emit(0)
    emit(r(2, 0, 18, 0x25))
    branch(4, 18, 0, "release_only")
    emit(r(0, 16, 8, 0x00) | (2 << 6))
    for word in (
        i(15, 0, 9, table_hi), i(9, 9, 9, table_lo),
        r(9, 8, 9, 0x21), i(43, 9, 18, 0),
        r(18, 0, 4, 0x25), i(9, 0, 5, 0x13E0),
        j(3, symbols["_bzero"]), 0,
        # Clear persistent Friend storage before the live read so a failed
        # scan cannot expose a record cached by an earlier cartridge.
        r(0, 16, 8, 0x00) | (9 << 6),
        r(0, 16, 9, 0x00) | (6 << 6), r(8, 9, 8, 0x23),
        i(15, 0, 4, friend_cache_hi), i(9, 4, 4, friend_cache_lo),
        r(4, 8, 4, 0x21), i(9, 0, 5, 0x1C0),
        j(3, symbols["_bzero"]), 0,
        # Japanese order: Friend Data at E000 while the Pak is still live.
        # Populate the current combined object first.  A cartridge scan may
        # run either before state 18 (menu-pool object) or after it
        # (state-owned object); writing only the persistent cache made the
        # latter contain valid F000 Battle Data but an empty Friend region.
        i(15, 0, 8, cache_hi), i(9, 8, 8, cache_lo),
        r(8, 16, 8, 0x21), i(9, 0, 9, -1), i(40, 8, 9, 0),
        i(9, 18, 5, 0x1000), r(16, 0, 4, 0x25),
        i(13, 0, 6, 0xE000), j(3, symbols["mobile_unlock_read"]),
        i(9, 0, 7, 0x1C0),
    ):
        emit(word)
    # Even a failed E000 read must reach the validator: the combined object
    # was zeroed above, so validation publishes count zero and cannot leave
    # stale Friend availability from an earlier cartridge. F000 may still be
    # read independently for Battle/Rule Data.
    branch(5, 2, 0, "validate_friend")
    # Do not consume Crystal's first-use flag here. Japanese Stadium leaves
    # the byte untouched during its ordinary Mobile-data scan. Fragment 10
    # subsequently invokes a dedicated per-controller transaction which
    # reacquires the Pak, writes 01, and returns the successful write status
    # to build the localized popup mask. Writing E000 here made that later
    # callback observe an already-enabled cartridge and permanently
    # suppressed the first-use message.
    # Also retain an arena-independent copy for a pre-state-18 scan.  Entry
    # zero restores this block after the menu pool is popped.  bcopy is safe
    # here because both the freshly read source and resident destination are
    # live in the same scanner callback; the previously failing bcopy was the
    # later promotion call across two different pool lifetimes.
    for word in (
        r(0, 16, 8, 0x00) | (9 << 6),
        r(0, 16, 9, 0x00) | (6 << 6), r(8, 9, 8, 0x23),
        i(15, 0, 5, friend_cache_hi), i(9, 5, 5, friend_cache_lo),
        r(5, 8, 5, 0x21), i(9, 18, 4, 0x1000),
        j(3, symbols["_bcopy"]), i(9, 0, 6, 0x1C0),
    ):
        emit(word)
    label("validate_friend")
    # Validate the raw western P3 record while it is resident and publish
    # command 0x28 availability before Fragment 7 asks for its count.
    friend_validator = symbols["mobile_music_cave"] + 0x40
    # E000 is Crystal's Mobile Stadium status byte.  The checksummed western
    # Friend record begins at E001; including the status byte shifts both the
    # P3 marker and checksum window and makes every valid record look empty.
    emit(i(9, 18, 4, 0x1001))
    emit(j(3, friend_validator))
    emit(0)
    emit(i(15, 0, 8, count_hi))
    emit(i(9, 8, 8, count_lo))
    emit(r(8, 16, 8, 0x21))
    # Store either zero or one, so a missing/corrupt cartridge also clears
    # availability left by an earlier valid scan.
    emit(i(40, 8, 2, 0))
    for word in (
        # Reconstruct all volatile mapper state after the first read.
        i(15, 0, 8, cache_hi), i(9, 8, 8, cache_lo),
        r(8, 16, 8, 0x21),
        i(9, 0, 9, -1), i(40, 8, 9, 0),
        r(16, 0, 4, 0x25), r(18, 0, 5, 0x25),
        i(13, 0, 6, 0xF000), j(3, symbols["mobile_unlock_read"]),
        i(9, 0, 7, 0x1000),
    ):
        emit(word)
    # Do not publish uninitialized SRAM (normally FF/FF on a fresh save) as
    # three corrupt Battle records.  The mapped overlay later validates the
    # complete P3 marker/checksum, but the retained 0x26 count consumer runs
    # first and therefore requires this early bounds gate.
    branch(5, 2, 0, "clear_download_counts")
    emit(i(36, 18, 8, 0))
    emit(i(11, 8, 1, 4))
    branch(5, 1, 0, "release_only")
    emit(0)
    label("clear_download_counts")
    emit(i(41, 18, 0, 0))

    label("release_only")
    for word in (
        r(16, 0, 4, 0x25), r(17, 0, 5, 0x25),
        j(3, symbols["mobile_gbpak_release"]), 0,
        i(35, 29, 31, 0x2C), i(35, 29, 16, 0x28),
        i(35, 29, 17, 0x24), i(35, 29, 18, 0x20),
        i(9, 29, 29, 0x30), 0x03E00008, 0,
    ):
        emit(word)

    if len(words) * 4 > SCANNER_FINISH_OFFSET:
        raise ValueError(
            "resident scanner-prefetch hook overlaps finish helper: "
            f"0x{len(words) * 4:X} > 0x{SCANNER_FINISH_OFFSET:X}"
        )
    while len(words) * 4 < SCANNER_FINISH_OFFSET:
        emit(0)
    for word in (
        i(9, 29, 29, -0x20), i(43, 29, 31, 0x1C),
        i(43, 29, 4, 0x10), i(43, 29, 5, 0x14), i(43, 29, 6, 0x18),
        i(35, 29, 4, 0x10), i(35, 29, 5, 0x14), i(35, 29, 6, 0x18),
        j(3, symbols["func_800047D8"]), 0,
        i(35, 29, 31, 0x1C), i(9, 29, 29, 0x20), 0x03E00008, 0,
    ):
        emit(word)

    for index, opcode, rs, rt, target in branches:
        words[index] = i(opcode, rs, rt, labels[target] - index - 1)
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > size - 8:
        raise ValueError("resident scanner-prefetch wrapper is too large")
    return data + b"\0" * (size - len(data))


def mobile_legacy_state_session_wrapper(
    profile: dict, fragment_wrapper: int, size: int
) -> bytes:
    """Scan/acquire before mapping the overlay; release before Fragment 7.

    Entry 0 runs the retained scanner through its normal balanced lifecycle,
    then reacquires only controllers that both scanned and acquired
    successfully.  Only after those operations (which can alter the TLB) does
    func_80065748 map/call the Mobile overlay.  The overlay copies commands
    0x26-0x28 and calls entry +0x180, which releases the short sessions before
    performing the pending fragment load.  No GB Pak acquire/scan may execute
    from the 0x84500000 overlay window.
    """
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    words: list[int] = []
    labels: dict[str, int] = {}
    branches: list[tuple[int, int, int, int, str]] = []

    def emit(word: int) -> None:
        words.append(word)

    def label(name: str) -> None:
        labels[name] = len(words)

    def branch(opcode: int, rs: int, rt: int, target: str) -> None:
        branches.append((len(words), opcode, rs, rt, target))
        emit(0)

    context = symbols["mobile_transfer_contexts"] + 0x28
    context_hi = (context + 0x8000) >> 16
    context_lo = context & 0xFFFF
    active = symbols["mobile_transfer_active_mask"]
    active_hi = (active + 0x8000) >> 16
    active_lo = active & 0xFFFF
    session_data = symbols["mobile_state_session"] + size - 8
    data_hi = (session_data + 0x8000) >> 16
    data_lo = session_data & 0xFFFF
    # Entry 0: preserve the state-router arguments, run the same setup scan as
    # the working western controller, retain only successful reacquisitions in
    # fixed resident storage, then map/call the overlay.
    for word in (
        i(9, 29, 29, -0x60), i(43, 29, 31, 0x5C),
        i(43, 29, 16, 0x58), i(43, 29, 17, 0x54),
        i(43, 29, 18, 0x50), i(43, 29, 19, 0x4C),
        i(43, 29, 4, 0x30), i(43, 29, 5, 0x34),
        i(43, 29, 6, 0x38), i(43, 29, 7, 0x3C),
        i(9, 0, 4, 7), r(0, 0, 5, 0x25), r(0, 0, 6, 0x25),
        j(3, symbols["func_800353B4"]), 0,
        j(3, symbols["func_800354E4"]), i(9, 0, 4, 0x41),
        i(15, 0, 17, active_hi), i(36, 17, 17, active_lo),
        i(15, 0, 18, data_hi), i(9, 18, 18, data_lo),
        i(40, 18, 0, 0), i(43, 18, 0, 4),
        i(9, 0, 16, 0),
    ):
        emit(word)
    label("acquire_loop")
    emit(i(10, 16, 1, 4))                    # slti at, s0, 4
    branch(4, 1, 0, "call_overlay")
    emit(r(16, 17, 8, 0x06))                 # delay: srlv t0, s1, s0
    emit(i(12, 8, 8, 1))                     # andi t0, t0, 1
    branch(4, 8, 0, "acquire_next")
    emit(r(0, 16, 8, 0x00) | (3 << 6))       # delay: sll t0, s0, 3
    emit(r(8, 16, 8, 0x23))                  # subu t0, t0, s0
    emit(r(0, 8, 8, 0x00) | (4 << 6))        # sll t0, t0, 4
    emit(i(15, 0, 6, context_hi))
    emit(i(9, 6, 6, context_lo))
    emit(r(6, 8, 6, 0x21))                   # a2 = context + s0*0x70
    emit(r(16, 0, 4, 0x25))                  # a0 = controller
    emit(i(9, 18, 5, 4))
    emit(r(5, 16, 5, 0x21))                  # a1 = &releaseState[s0]
    emit(j(3, symbols["mobile_gbpak_acquire"]))
    emit(0)
    branch(4, 2, 0, "acquire_next")         # failed acquire is not retained
    emit(i(36, 18, 9, 0))
    emit(i(9, 0, 10, 1))
    emit(r(16, 10, 10, 0x04))
    emit(r(9, 10, 9, 0x25))
    emit(i(40, 18, 9, 0))
    label("acquire_next")
    emit(i(9, 16, 16, 1))
    branch(4, 0, 0, "acquire_loop")
    emit(0)

    label("call_overlay")
    for word in (
        i(36, 18, 8, 0), i(15, 0, 9, active_hi),
        i(40, 9, 8, active_lo),
        i(35, 29, 4, 0x30), i(35, 29, 5, 0x34),
        i(35, 29, 6, 0x38), i(35, 29, 7, 0x3C),
        i(35, 29, 8, 0x70), i(43, 29, 8, 0x10),
        j(3, fragment_wrapper), 0,
        r(2, 0, 19, 0x25),
        r(19, 0, 2, 0x25), i(35, 29, 31, 0x5C),
        i(35, 29, 16, 0x58), i(35, 29, 17, 0x54),
        i(35, 29, 18, 0x50), i(35, 29, 19, 0x4C),
        i(9, 29, 29, 0x60), 0x03E00008, 0,
    ):
        emit(word)

    if len(words) * 4 > 0x180:
        raise ValueError("Mobile state-session entry overlaps finish helper")
    while len(words) * 4 < 0x180:
        emit(0)

    # Entry +0x180: called in place of the first post-download fragment load.
    # Preserve its arguments, release the Paks, then make the original
    # resident fragment-loader call and return to the overlay.
    label("finish_entry")
    for word in (
        i(9, 29, 29, -0x30), i(43, 29, 31, 0x2C),
        i(43, 29, 16, 0x28), i(43, 29, 17, 0x24),
        i(43, 29, 18, 0x20),
        i(43, 29, 4, 0x10), i(43, 29, 5, 0x14),
        i(43, 29, 6, 0x18),
        i(15, 0, 17, data_hi), i(9, 17, 17, data_lo),
        i(36, 17, 18, 0), i(9, 0, 16, 0),
    ):
        emit(word)
    label("release_loop")
    emit(i(10, 16, 1, 4))
    branch(4, 1, 0, "load_fragment")
    emit(r(16, 18, 8, 0x06))
    emit(i(12, 8, 8, 1))
    branch(4, 8, 0, "release_next")
    emit(r(16, 0, 4, 0x25))
    emit(i(9, 17, 5, 4))
    emit(r(5, 16, 5, 0x21))
    emit(i(36, 5, 5, 0))
    emit(j(3, symbols["mobile_gbpak_release"]))
    emit(0)
    label("release_next")
    emit(i(9, 16, 16, 1))
    branch(4, 0, 0, "release_loop")
    emit(0)
    label("load_fragment")
    emit(i(40, 17, 0, 0))                    # clear retained active mask
    for word in (
        i(35, 29, 4, 0x10), i(35, 29, 5, 0x14),
        i(35, 29, 6, 0x18),
        j(3, symbols["func_800047D8"]), 0,
        i(35, 29, 31, 0x2C), i(35, 29, 16, 0x28),
        i(35, 29, 17, 0x24), i(35, 29, 18, 0x20),
        i(9, 29, 29, 0x30),
        0x03E00008, 0,
    ):
        emit(word)
    for index, opcode, rs, rt, target in branches:
        words[index] = i(opcode, rs, rt, labels[target] - index - 1)
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > size - 8:
        raise ValueError("resident Mobile state session wrapper is too large")
    return data + b"\0" * (size - len(data))


def mobile_scanner_bridge(profile: dict, size: int) -> bytes:
    """Give Mobile SRAM reads their own short, balanced Pak session.

    The retail scanner must finish its acquire/release lifecycle unchanged;
    retaining that internal session leaves SI/controller state inconsistent
    when the battle engine starts polling pads.  Entry zero therefore runs the
    scanner normally, then reacquires only its reported active controllers.
    The overlay copies commands 0x26-0x28, and entry +0x180 releases these
    dedicated sessions before any battle fragment is loaded.  The hook at
    +0x100 is a transparent replacement for the scanner's original release.
    """
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    words: list[int] = []
    labels: dict[str, int] = {}
    branches: list[tuple[int, int, int, int, str]] = []

    def emit(word: int) -> None:
        words.append(word)

    def label(name: str) -> None:
        labels[name] = len(words)

    def branch(opcode: int, rs: int, rt: int, target: str) -> None:
        branches.append((len(words), opcode, rs, rt, target))
        emit(0)

    session_data = symbols["mobile_state_session"] + size - 8
    mask = session_data
    states = session_data + 4
    mask_hi = (mask + 0x8000) >> 16
    mask_lo = mask & 0xFFFF
    states_hi = (states + 0x8000) >> 16
    states_lo = states & 0xFFFF

    context = symbols["mobile_transfer_contexts"] + 0x28
    context_hi = (context + 0x8000) >> 16
    context_lo = context & 0xFFFF
    active = symbols["mobile_transfer_active_mask"]
    active_hi = (active + 0x8000) >> 16
    active_lo = active & 0xFFFF

    # Run the original scan through its normal release, then establish a
    # separate live session for each controller it positively identified.
    for word in (
        i(9, 29, 29, -0x50), i(43, 29, 31, 0x4C),
        i(43, 29, 16, 0x48), i(43, 29, 17, 0x44),
        i(43, 29, 18, 0x40), i(43, 29, 19, 0x3C),
        i(43, 29, 4, 0x20), i(43, 29, 5, 0x24),
        i(43, 29, 6, 0x28), i(43, 29, 7, 0x2C),
        i(35, 29, 4, 0x20), i(35, 29, 5, 0x24),
        i(35, 29, 6, 0x28), i(35, 29, 7, 0x2C),
        i(35, 29, 10, 0x60), i(43, 29, 10, 0x10),
        j(3, symbols["func_800353B4"]), 0,
        r(2, 0, 19, 0x25),
        i(15, 0, 8, mask_hi), i(9, 8, 8, mask_lo), i(40, 8, 0, 0),
        i(15, 0, 17, active_hi), i(36, 17, 17, active_lo),
        r(0, 0, 16, 0x25),
    ):
        emit(word)
    label("acquire_loop")
    emit(i(10, 16, 1, 4))
    branch(4, 1, 0, "acquire_done")
    emit(r(16, 17, 8, 0x06))                # delay: active >> controller
    emit(i(12, 8, 8, 1))
    branch(4, 8, 0, "acquire_next")
    emit(r(0, 16, 8, 0x00) | (3 << 6))      # delay: controller * 8
    emit(r(8, 16, 8, 0x23))                 # * 7
    emit(r(0, 8, 8, 0x00) | (4 << 6))       # * 0x70
    emit(i(15, 0, 6, context_hi))
    emit(i(9, 6, 6, context_lo))
    emit(r(6, 8, 6, 0x21))                  # a2 = controller context
    emit(r(16, 0, 4, 0x25))
    emit(i(15, 0, 5, states_hi))
    emit(i(9, 5, 5, states_lo))
    emit(r(5, 16, 5, 0x21))                 # a1 = release state byte
    emit(j(3, symbols["mobile_gbpak_acquire"]))
    emit(0)
    branch(4, 2, 0, "acquire_next")         # do not release a failed acquire
    emit(i(15, 0, 8, mask_hi))
    emit(i(36, 8, 9, mask_lo))
    emit(i(9, 0, 10, 1))
    emit(r(16, 10, 10, 0x04))
    emit(r(9, 10, 9, 0x25))
    emit(i(40, 8, 9, mask_lo))
    label("acquire_next")
    emit(i(9, 16, 16, 1))
    branch(4, 0, 0, "acquire_loop")
    emit(0)
    label("acquire_done")
    for word in (
        r(19, 0, 2, 0x25), i(35, 29, 31, 0x4C),
        i(35, 29, 16, 0x48), i(35, 29, 17, 0x44),
        i(35, 29, 18, 0x40), i(35, 29, 19, 0x3C),
        i(9, 29, 29, 0x50), 0x03E00008, 0,
    ):
        emit(word)

    if len(words) * 4 > 0x100:
        raise ValueError("Mobile scanner bridge entry overlaps release hook")
    while len(words) * 4 < 0x100:
        emit(0)

    # Transparent scanner release hook: preserve the retail lifecycle for
    # Mobile and non-Mobile scans alike.
    label("release_hook")
    for word in (
        i(9, 29, 29, -0x20), i(43, 29, 31, 0x1C),
        i(43, 29, 16, 0x18), i(43, 29, 17, 0x14),
        r(4, 0, 16, 0x25), r(5, 0, 17, 0x25),
        j(3, symbols["mobile_gbpak_release"]), 0,
        r(2, 0, 16, 0x25), r(16, 0, 2, 0x25),
        i(35, 29, 31, 0x1C), i(35, 29, 16, 0x18),
        i(35, 29, 17, 0x14), i(9, 29, 29, 0x20),
        0x03E00008, 0,
    ):
        emit(word)

    if len(words) * 4 > 0x180:
        raise ValueError("Mobile scanner release hook overlaps finish helper")
    while len(words) * 4 < 0x180:
        emit(0)

    # Release every session retained by the guarded scan, then execute the
    # original first fragment load with its three arguments unchanged.
    label("finish_entry")
    for word in (
        i(9, 29, 29, -0x40), i(43, 29, 31, 0x3C),
        i(43, 29, 16, 0x38), i(43, 29, 17, 0x34),
        i(43, 29, 18, 0x30),
        i(43, 29, 4, 0x20), i(43, 29, 5, 0x24), i(43, 29, 6, 0x28),
        i(15, 0, 17, mask_hi), i(36, 17, 18, mask_lo),
        r(0, 0, 16, 0x25),
    ):
        emit(word)
    label("finish_loop")
    emit(i(10, 16, 1, 4))
    branch(4, 1, 0, "load_fragment")
    emit(r(16, 18, 8, 0x06))                # delay: srlv t0, mask, controller
    emit(i(12, 8, 8, 1))
    branch(4, 8, 0, "finish_next")
    emit(r(16, 0, 4, 0x25))                 # delay: a0 = controller
    emit(i(15, 0, 5, states_hi))
    emit(i(9, 5, 5, states_lo))
    emit(r(5, 16, 5, 0x21))
    emit(i(36, 5, 5, 0))                    # a1 = retained release state
    emit(j(3, symbols["mobile_gbpak_release"]))
    emit(0)
    label("finish_next")
    emit(i(9, 16, 16, 1))
    branch(4, 0, 0, "finish_loop")
    emit(0)
    label("load_fragment")
    emit(i(40, 17, 0, mask_lo))              # clear retained mask
    for word in (
        i(35, 29, 4, 0x20), i(35, 29, 5, 0x24), i(35, 29, 6, 0x28),
        j(3, symbols["func_800047D8"]), 0,
        r(2, 0, 18, 0x25),
        r(18, 0, 2, 0x25), i(35, 29, 31, 0x3C),
        i(35, 29, 16, 0x38), i(35, 29, 17, 0x34), i(35, 29, 18, 0x30),
        i(9, 29, 29, 0x40), 0x03E00008, 0,
    ):
        emit(word)

    for index, opcode, rs, rt, target in branches:
        words[index] = i(opcode, rs, rt, labels[target] - index - 1)
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > size - 4:
        raise ValueError("resident Mobile scanner bridge is too large")
    return data + b"\0" * (size - len(data))


def mobile_menu_unlock_wrapper(
    profile: dict, initializer_delta: int, size: int,
    description_fallback: int, delivery_helper: int,
) -> bytes:
    """Restore Japanese first-unlock and downloaded-description behavior."""
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    if not -0x8000 <= initializer_delta <= 0x7FFF:
        raise ValueError("relocated Mobile menu initializer is out of range")

    words = [
        i(9, 29, 29, -0x20),                 # addiu sp, sp, -0x20
        i(43, 29, 31, 0x1C),                 # sw ra, 0x1c(sp)
        i(43, 29, 16, 0x18),                 # sw s0, 0x18(sp)
        i(43, 29, 17, 0x14),                 # sw s1, 0x14(sp)
        r(4, 0, 16, 0x25),                   # or s0, a0, zero
        r(5, 0, 17, 0x25),                   # or s1, a1, zero
        i(37, 17, 8, 2),                     # lhu t0, 2(s1)
        i(36, 17, 9, 5),                     # lbu t1, 5(s1)
        i(12, 8, 5, 4),                      # andi a1, t0, 4
        r(0, 5, 5, 0x2B),                    # sltu a1, zero, a1
        i(12, 9, 9, 1),                      # andi t1, t1, 1
        i(4, 5, 0, 11),                      # beqz a1, finish
        r(0, 0, 6, 0x25),                    # delay: a2 = false
        i(5, 9, 0, 9),                       # bnez t1, finish
        0,
        i(13, 9, 9, 1),                      # ori t1, t1, 1
        i(40, 17, 9, 5),                     # sb t1, 5(s1)
        j(3, symbols["mobile_unlock_write_record"]),
        r(17, 0, 4, 0x25),                   # delay: a0 = record state
        i(9, 0, 16, 5),                      # new-unlock menu state
        i(43, 29, 16, 0xBC),                 # persist state in caller's 0x9c(sp)
        i(9, 0, 5, 1),                       # availability = true
        i(9, 0, 6, 1),                       # newly unlocked = true
        r(16, 0, 4, 0x25),                   # finish: restore menu state
        i(35, 29, 31, 0x1C),
        i(9, 31, 8, initializer_delta),       # relocated initializer = ra + delta
        i(35, 29, 16, 0x18),
        i(35, 29, 17, 0x14),
        i(9, 29, 29, 0x20),
        r(8, 0, 0, 0x08),                    # jr relocated initializer
        0,
    ]
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > MOBILE_DESCRIPTION_HELPER_OFFSET:
        raise ValueError("resident Mobile first-unlock helper overlaps description")
    data += b"\0" * (MOBILE_DESCRIPTION_HELPER_OFFSET - len(data))

    table = symbols["mobile_download_buffers"]
    table_hi = (table + 0x8000) >> 16
    table_lo = table & 0xFFFF
    fallback_hi = (description_fallback + 0x8000) >> 16
    fallback_lo = description_fallback & 0xFFFF
    # a0 is the selected Transfer Pak/controller index.  Return the western
    # payload message only when that DLD object exists and advertises at
    # least one replay; otherwise preserve the retail regional default.  The
    # valid-data path tail-calls the resident Delibird helper, preserving the
    # original caller's return address and the description pointer in a1.
    description_words = [
        0x00044080,                           # sll t0, a0, 2
        i(15, 0, 9, table_hi),               # lui t1, %hi(DLD table)
        i(9, 9, 9, table_lo),                # addiu t1, t1, %lo(table)
        r(9, 8, 9, 0x21),                    # addu t1, t1, t0
        i(35, 9, 2, 0),                      # lw v0, 0(t1)
        i(4, 2, 0, 6),                       # beqz v0, fallback
        0,
        i(36, 2, 8, 0),                      # lbu t0, 0(v0)
        i(4, 8, 0, 3),                       # beqz t0, fallback
        0,
        j(2, delivery_helper),               # tail-call Delibird helper
        i(9, 2, 5, MOBILE_DESCRIPTION_OFFSET), # delay: a1 = description
        i(15, 0, 2, fallback_hi),            # fallback: lui v0, %hi(default)
        r(31, 0, 0, 0x08),                  # jr ra
        i(9, 2, 2, fallback_lo),             # addiu v0, v0, %lo(default)
    ]
    data += b"".join(struct.pack(">I", word) for word in description_words)
    if len(data) > size:
        raise ValueError("resident Mobile first-unlock helper is too large")
    return data + b"\0" * (size - len(data))


def mobile_delivery_wrapper(profile: dict, size: int) -> bytes:
    """Apply DLDx+0xFE1's two Delibird console bits and return its text.

    The description accessor tail-calls this helper with the DLD base in v0,
    the western description pointer in a1, and the original menu caller's ra.
    The operation is idempotent: an already-updated save only incurs the
    retained record load and returns without another FlashRAM commit.
    """
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    words = [
        i(9, 29, 29, -0x30),                # addiu sp, sp, -0x30
        i(43, 29, 31, 0x2C),                # sw ra, 0x2c(sp)
        i(43, 29, 5, 0x28),                 # preserve description pointer
        i(36, 2, 8, 0xFE1),                 # requested console flags
        i(12, 8, 8, 3),
        i(4, 8, 0, 18),                     # no bits: done
        i(43, 29, 8, 0x24),                 # delay: preserve requested bits
        # Japanese func_80058810 updates the selected save record inside the
        # caller's existing menu pool.  Do not create/pop a nested pool here:
        # popping it immediately before Watch Battle restores an older arena
        # and makes the battle loader hand osInvalDCache an invalid pointer.
        i(9, 0, 4, 3),
        j(3, symbols["mobile_delivery_select_record"]),
        0,
        j(3, symbols["mobile_delivery_load_record"]),
        i(9, 29, 4, 0x10),
        i(35, 29, 9, 0x24),
        # Japanese func_80058810 loads record 3 at sp+0x10 and ORs the DLD
        # bits into its first halfword.  The retained western equivalents are
        # func_8005493C/func_80054974; older patches accidentally used a
        # different record API, which made this correct +0 field look inert.
        i(37, 29, 10, 0x10),
        r(10, 9, 9, 0x25),
        i(41, 29, 9, 0x10),
        j(3, symbols["mobile_delivery_write_record"]),
        i(9, 29, 4, 0x10),
        i(9, 0, 4, 0x14),
        j(3, symbols["mobile_unlock_commit_record"]),
        r(0, 0, 5, 0x25),
        i(9, 0, 4, 3),                      # finalize selected record 3
        j(3, symbols["mobile_delivery_finalize_record"]),
        0,
        i(35, 29, 2, 0x28),                 # done: return description
        i(35, 29, 31, 0x2C),
        r(31, 0, 0, 0x08),
        i(9, 29, 29, 0x30),
    ]
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > size:
        raise ValueError("resident Delibird delivery helper is too large")
    return data + b"\0" * (size - len(data))


def mobile_ptp0_wrapper(profile: dict, size: int) -> bytes:
    """Recreate Japanese Crystal's native ``PTP0`` picker notification.

    State 18 has already copied E000 into persistent resident RAM while the
    physical Transfer Pak was acquired. Fragment 7 later invokes the retail
    mask-8 wrapper from the cartridge picker. International Stadium retained
    that wrapper and the controller context layout, but removed the routine
    which allocated its tagged 0x180-byte PTP0 object. Pointing context+0x6c
    at resident RAM is not safe because the picker owns and frees this object;
    make the same tagged allocation as Japanese Stadium and copy the prefetched
    bytes into it instead.  This object is the picker's fixed 0x180-byte PTP0
    status object, not the western Friend Data record that happens to share the
    E000 SRAM page.  Japanese func_80059D94 performs no P3/footer/checksum test:
    it allocates PTP0 and lets func_8005878C fill it.  Requiring a western P3
    record here suppresses the notification on a fresh save, which is exactly
    when the E000 status byte is zero and the notification must be displayed.
    """
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    contexts = symbols["mobile_transfer_contexts"]
    contexts_hi = (contexts + 0x8000) >> 16
    contexts_lo = contexts & 0xFFFF
    cache = symbols["mobile_friend_raw_cache"]
    cache_hi = (cache + 0x8000) >> 16
    cache_lo = cache & 0xFFFF
    destructor = symbols["mobile_transfer_alloc_destructor"]
    destructor_hi = (destructor + 0x8000) >> 16
    destructor_lo = destructor & 0xFFFF

    words: list[int] = []
    labels: dict[str, int] = {}
    branches: list[tuple[int, int, int, int, str]] = []

    def emit(word: int) -> None:
        words.append(word)

    def label(name: str) -> None:
        labels[name] = len(words)

    def branch(opcode: int, rs: int, rt: int, target: str) -> None:
        branches.append((len(words), opcode, rs, rt, target))
        emit(0)

    emit(i(9, 29, 29, -0x30))              # addiu sp, sp, -0x30
    emit(i(43, 29, 31, 0x2C))              # sw ra, 0x2c(sp)
    emit(i(43, 29, 16, 0x28))              # sw s0, 0x28(sp)
    emit(i(43, 29, 17, 0x24))              # sw s1, 0x24(sp)
    emit(i(43, 29, 18, 0x20))              # sw s2, 0x20(sp)
    emit(r(4, 0, 16, 0x25))                # s0 = controller
    emit(r(0, 16, 8, 0x00) | (3 << 6))     # t0 = controller * 8
    emit(r(8, 16, 8, 0x23))                # t0 = controller * 7
    emit(r(0, 8, 8, 0x00) | (4 << 6))      # t0 = controller * 0x70
    emit(i(15, 0, 17, contexts_hi))
    emit(i(9, 17, 17, contexts_lo))
    emit(r(17, 8, 17, 0x21))               # s1 = controller context
    emit(r(0, 16, 8, 0x00) | (9 << 6))     # controller * 0x200
    emit(r(0, 16, 9, 0x00) | (6 << 6))     # controller * 0x40
    emit(r(8, 9, 8, 0x23))                 # controller * 0x1c0
    emit(i(15, 0, 18, cache_hi))
    emit(i(9, 18, 18, cache_lo))
    emit(r(18, 8, 18, 0x21))               # s2: prefetched raw E000
    emit(i(35, 17, 9, 0))                  # context flags
    emit(i(12, 9, 9, 8))
    branch(4, 9, 0, "allocate")
    emit(i(35, 17, 2, 0x6C))               # delay: existing PTP0 object
    # The international picker can attach its mask-8 object before this
    # Mobile-specific wrapper runs.  Japanese Stadium's scanner fills that
    # object while the Pak session is live; simply returning here leaves the
    # international object stale and never publishes property 0x28.  Reuse
    # the picker-owned allocation, but refresh it from the E000 bytes which
    # state 18 prefetched before powering the physical Transfer Pak off.
    branch(5, 2, 0, "copy_object")
    emit(0)
    label("allocate")
    # Do not validate this page as Battle or Friend Data.  Japanese
    # func_80059D94 always creates the fixed PTP0 picker-status object and
    # delegates the raw 0x180-byte copy to func_8005878C.  In particular, a
    # newly-created Crystal save has an all-zero page with E000=0: that is the
    # state which must create the first-use notification, not be rejected for
    # lacking a P3 replay/footer checksum.
    # Exact Japanese PTP0 allocation contract.  The persistent cache uses a
    # 0x1c0 stride because it also carries the larger western Friend record;
    # only the first 0x180 bytes belong to the picker-owned PTP0 object.
    emit(i(9, 0, 4, 0x180))                # allocation size
    emit(r(0, 0, 5, 0x25))                 # allocation mode 0
    emit(i(15, 0, 6, 0x5054))              # 'PTP0' + controller
    emit(i(13, 6, 6, 0x5030))
    emit(r(6, 16, 6, 0x21))
    emit(i(15, 0, 7, destructor_hi))
    emit(i(9, 7, 7, destructor_lo))
    emit(j(3, symbols["mobile_tagged_alloc"]))
    emit(0)
    branch(4, 2, 0, "failed")
    emit(i(43, 17, 2, 0x6C))               # delay: context object pointer
    emit(i(35, 17, 9, 0))
    emit(i(13, 9, 9, 8))
    emit(i(43, 17, 9, 0))                  # context flags |= 8
    label("copy_object")
    emit(r(18, 0, 4, 0x25))                # source: prefetched raw E000
    emit(r(2, 0, 5, 0x25))                 # destination: PTP0 object
    emit(j(3, symbols["_bcopy"]))
    emit(i(9, 0, 6, 0x180))
    # func_8005878C also sets a byte in the packed notification array after a
    # valid load.  This is *not* a member of the 0x70-byte controller context.
    # Japanese writes 0x80139D84 + controller.  The value represented by
    # ``mobile_transfer_contexts`` is the context-array base 0x80139BB0, not
    # its context+0x6c object-pointer table at 0x80139C1C.  Therefore the
    # notification array is context base + 0x1d4, not +0x168.  Fragment 7
    # tests this byte to display localized message 5226.
    emit(i(15, 0, 8, contexts_hi))
    emit(i(9, 8, 8, contexts_lo))
    emit(r(8, 16, 8, 0x21))                 # packed byte: base + controller
    emit(i(9, 0, 9, 1))
    emit(i(40, 8, 9, 0x1D4))               # notify[controller] = 1
    emit(i(9, 0, 2, 1))                    # first load succeeds
    branch(4, 0, 0, "finish")
    emit(0)
    label("failed")
    emit(r(0, 0, 2, 0x25))                 # invalid/unavailable E000
    branch(4, 0, 0, "finish")
    emit(0)
    label("finish")
    emit(i(35, 29, 31, 0x2C))
    emit(i(35, 29, 16, 0x28))
    emit(i(35, 29, 17, 0x24))
    emit(i(35, 29, 18, 0x20))
    emit(r(31, 0, 0, 0x08))
    emit(i(9, 29, 29, 0x30))

    for index, opcode, rs, rt, target in branches:
        words[index] = i(opcode, rs, rt, labels[target] - index - 1)
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > MOBILE_FIRST_UNLOCK_QUERY_OFFSET:
        raise ValueError("resident PTP0 wrapper overlaps first-unlock query")

    # Fragment 10's Game Pak selector asks this function once for each
    # controller and builds the popup bitmask from nonzero return values.
    # Reproduce Japanese func_80060EFC as one complete transaction:
    # active/context gate, acquire, read 0x20 at E000, conditional write,
    # release, and return the write result.  In particular this must not use
    # the state-18 cache: on hardware that scan has already powered the Pak
    # off, while the Japanese callback explicitly reacquires it here.
    while len(data) < MOBILE_FIRST_UNLOCK_QUERY_OFFSET:
        data += b"\0"
    # Japanese func_80060EFC gates the notification transaction with the
    # scanner's 32-bit controller mask at context_base + 0x1d8
    # (0x80139d88), not the mapper bank-cache byte found beside the raw SRAM
    # read primitive.  The western scanner retains the same relative layout.
    active = symbols["mobile_scanner_active_mask"]
    active_hi = (active + 0x8000) >> 16
    active_lo = active & 0xFFFF
    query_words: list[int] = []
    query_labels: dict[str, int] = {}
    query_branches: list[tuple[int, int, int, int, str]] = []

    def qemit(word: int) -> None:
        query_words.append(word)

    def qlabel(name: str) -> None:
        query_labels[name] = len(query_words)

    def qbranch(opcode: int, rs: int, rt: int, target: str) -> None:
        query_branches.append((len(query_words), opcode, rs, rt, target))
        qemit(0)

    for word in (
        i(9, 29, 29, -0x50), i(43, 29, 31, 0x1C),
        i(43, 29, 16, 0x18), r(4, 0, 16, 0x25),
        i(43, 29, 0, 0x4C),
        # if (!(active_mask & (1 << controller))) return 0
        i(15, 0, 8, active_hi), i(35, 8, 8, active_lo),
        i(9, 0, 9, 1), r(4, 9, 9, 0x04), r(8, 9, 8, 0x24),
    ):
        qemit(word)
    qbranch(4, 8, 0, "finish")
    # Western contexts have a 0x70-byte stride (Japanese uses 0xc0).
    for word in (
        r(0, 16, 8, 0x00) | (3 << 6), r(8, 16, 8, 0x23),
        r(0, 8, 8, 0x00) | (4 << 6),
        i(15, 0, 9, contexts_hi), i(9, 9, 9, contexts_lo),
        r(9, 8, 2, 0x21), i(36, 2, 10, 5),
    ):
        qemit(word)
    qbranch(5, 10, 0, "finish")
    qemit(i(36, 2, 10, 4))
    qemit(i(9, 0, 11, 7))
    qbranch(5, 10, 11, "finish")
    # acquire(controller, &state, context + 0x28)
    for word in (
        r(16, 0, 4, 0x25), i(9, 29, 5, 0x2B), i(9, 2, 6, 0x28),
        j(3, symbols["mobile_gbpak_acquire"]), 0,
    ):
        qemit(word)
    qbranch(5, 2, 0, "read")
    # Japanese retries the SI operation through func_80065718(controller, 2)
    # when acquire/release return zero.
    for word in (
        r(16, 0, 4, 0x25), j(3, symbols["func_80065718"]),
        i(9, 0, 5, 2),
    ):
        qemit(word)
    qlabel("read")
    for word in (
        r(16, 0, 4, 0x25), i(9, 29, 5, 0x2C), i(13, 0, 6, 0xE000),
        j(3, symbols["mobile_unlock_read"]), i(9, 0, 7, 0x20),
    ):
        qemit(word)
    qbranch(5, 2, 0, "release")
    qemit(i(36, 29, 12, 0x2C))
    qemit(i(9, 0, 13, 1))
    qbranch(4, 12, 13, "release")
    for word in (
        i(40, 29, 13, 0x2C), r(16, 0, 4, 0x25),
        i(9, 29, 5, 0x2C), i(13, 0, 6, 0xE000),
        j(3, symbols["mobile_unlock_write_crystal"]), i(9, 0, 7, 0x20),
        i(43, 29, 2, 0x4C),
    ):
        qemit(word)
    qlabel("release")
    for word in (
        r(16, 0, 4, 0x25), j(3, symbols["mobile_gbpak_release"]),
        i(36, 29, 5, 0x2B),
    ):
        qemit(word)
    qbranch(5, 2, 0, "finish")
    for word in (
        r(16, 0, 4, 0x25), j(3, symbols["func_80065718"]),
        i(9, 0, 5, 2),
    ):
        qemit(word)
    qlabel("finish")
    for word in (
        i(35, 29, 31, 0x1C), i(35, 29, 16, 0x18),
        i(35, 29, 2, 0x4C), r(31, 0, 0, 0x08), i(9, 29, 29, 0x50),
    ):
        qemit(word)
    for index, opcode, rs, rt, target in query_branches:
        query_words[index] = i(
            opcode, rs, rt, query_labels[target] - index - 1
        )
    data += b"".join(struct.pack(">I", word) for word in query_words)
    if len(data) > size:
        raise ValueError("resident PTP0 notification helper is too large")
    return data + b"\0" * (size - len(data))


def mobile_first_unlock_query_patch_info(
    source_rom: bytes,
) -> tuple[int, bytes]:
    """Locate Fragment 10's retained per-controller popup query call."""
    prefix = struct.pack(
        ">8I",
        0x27BDFFD8, 0xAFB20020, 0xAFB1001C, 0xAFB00018,
        0xAFBF0024, 0x00008825, 0x00008025, 0x24120004,
    )
    suffix = struct.pack(
        ">8I",
        0x02002025, 0x10400004, 0x240E0001, 0x020E7804,
        0x022F8825, 0x323100FF, 0x26100001, 0x1612FFF7,
    )
    matches: list[int] = []
    start = 0
    while True:
        hit = source_rom.find(prefix, start)
        if hit < 0:
            break
        call_offset = hit + len(prefix)
        if source_rom[call_offset + 4:call_offset + 4 + len(suffix)] == suffix:
            matches.append(call_offset)
        start = hit + 1
    if len(matches) != 1:
        raise ValueError(
            "cannot uniquely locate Fragment 10 first-unlock query call "
            f"(found {len(matches)})"
        )
    call_offset = matches[0]
    call = source_rom[call_offset:call_offset + 4]
    if int.from_bytes(call, "big") >> 26 != 3:
        raise ValueError("Fragment 10 first-unlock query is not a JAL")
    return call_offset, call


def mobile_menu_music_wrapper(profile: dict, size: int) -> bytes:
    """Acquire ``btlp`` and start menu music only for controller state 11.

    The battle controller acquires this pool once per state iteration and
    copies its current state into dispatch register ``s0`` in the call's delay
    slot.  States 4/5/6/8 are battle-transition
    states and must not have their audio mode replaced.  State 11 is the menu
    state, including the first iteration after Watch Battle returns via START.
    Run the retail mode-7/sequence-0x41 pair only there, after acquisition, so
    the menu owns the restarted sequence without disturbing battle startup.
    """
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def r(rs: int, rt: int, rd: int, funct: int) -> int:
        return rs << 21 | rt << 16 | rd << 11 | funct

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    words = [
        i(9, 29, 29, -0x18),
        i(43, 29, 31, 0x14),
        j(3, symbols["func_80002B34"]),
        0,
        i(9, 0, 8, 11),
        i(5, 16, 8, 7),                  # bne s0, 11, done
        0,
        i(9, 0, 4, 7),
        r(0, 0, 5, 0x25),
        j(3, symbols["func_800353B4"]),
        r(0, 0, 6, 0x25),
        j(3, symbols["func_800354E4"]),
        i(9, 0, 4, 0x41),
        i(35, 29, 31, 0x14),
        r(31, 0, 0, 0x08),
        i(9, 29, 29, 0x18),
    ]
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > 0x40:
        raise ValueError("resident Mobile menu-music helper overlaps validator")
    data += b"\0" * (0x40 - len(data))

    # Leaf validator used by the resident pre-release scanner. a0 points at
    # E001 (the western P3 record, excluding Crystal's status byte). Return
    # one only when the marker and little-endian 16-bit checksum both match.
    validator: list[int] = []
    labels: dict[str, int] = {}
    branches: list[tuple[int, int, int, int, str]] = []

    def emit(word: int) -> None:
        validator.append(word)

    def label(name: str) -> None:
        labels[name] = len(validator)

    def branch(opcode: int, rs: int, rt: int, target: str) -> None:
        branches.append((len(validator), opcode, rs, rt, target))
        emit(0)

    emit(r(0, 0, 2, 0x25))
    emit(i(9, 0, 8, 0x1B3))
    label("sum_loop")
    emit(i(36, 4, 9, 0))
    emit(i(9, 4, 4, 1))
    emit(r(2, 9, 2, 0x21))
    emit(i(9, 8, 8, -1))
    branch(5, 8, 0, "sum_loop")
    emit(i(12, 2, 2, 0xFFFF))
    emit(i(36, 4, 9, -2))
    emit(i(9, 0, 10, 0x50))
    branch(5, 9, 10, "invalid")
    emit(i(36, 4, 9, -1))
    emit(i(9, 0, 10, 0x33))
    branch(5, 9, 10, "invalid")
    emit(i(36, 4, 9, 0))
    emit(i(36, 4, 10, 1))
    emit(r(0, 10, 10, 0x00) | (8 << 6))
    emit(r(9, 10, 9, 0x25))
    branch(5, 2, 9, "invalid")
    emit(0)
    emit(r(31, 0, 0, 0x08))
    emit(i(9, 0, 2, 1))
    label("invalid")
    emit(r(31, 0, 0, 0x08))
    emit(r(0, 0, 2, 0x25))
    for index, opcode, rs, rt, target in branches:
        validator[index] = i(opcode, rs, rt, labels[target] - index - 1)
    data += b"".join(struct.pack(">I", word) for word in validator)
    if len(data) > size:
        raise ValueError("resident Mobile menu-music helper is too large")
    return data + b"\0" * (size - len(data))


def mobile_unlock_wrapper(profile: dict, size: int) -> bytes:
    """Restore the Japanese Mobile Crystal recognition side effect.

    International Crystal moved the complementary Mobile Adapter status bytes
    from Japanese raw SRAM offsets 0x9000/0xE800 to 0x8B10/0xE79A.  When they
    form a valid non-FF pair, set bit 2 in Stadium record 0x14.  The main menu
    owns the separate notification-seen marker so a newly discovered cart can
    still play the Japanese first-unlock animation and sound.  A byte in the
    resident cave remembers a successful check for
    the rest of the running session.  This matters after the Lab writes a
    Mystery Gift: its post-save rescan must not start an unrelated bank-7
    transaction and overwrite the successful write result with a Transfer Pak
    error.  Do not load Stadium's FlashRAM record before the two Crystal reads:
    that ordering disrupts the live Transfer Pak scanner.  The reverse Crystal
    handshake belongs to Mobile Stadium entry and remains in the overlay.
    """
    symbols = {name: int(value, 16) for name, value in profile["symbols"].items()}

    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def j(address: int) -> int:
        return 3 << 26 | ((address >> 2) & 0x03FFFFFF)

    words: list[int] = []
    labels: dict[str, int] = {}
    branches: list[tuple[int, int, int, int, str]] = []

    def emit(word: int) -> None:
        words.append(word)

    def branch(opcode: int, rs: int, rt: int, label: str) -> None:
        branches.append((len(words), opcode, rs, rt, label))
        emit(0)

    session_flag = symbols["mobile_unlock_cave"] + size - 4
    session_flag_hi = (session_flag + 0x8000) >> 16
    session_flag_lo = session_flag & 0xFFFF

    emit(i(9, 29, 29, -0x70))              # addiu sp, sp, -0x70
    emit(i(43, 29, 31, 0x6C))              # sw ra, 0x6c(sp)
    emit(0x00808025)                        # or s0, a0, zero
    emit(i(15, 0, 8, session_flag_hi))      # lui t0, %hi(session flag)
    emit(i(36, 8, 9, session_flag_lo))      # lbu t1, %lo(session flag)(t0)
    branch(5, 9, 0, "finish")              # successful earlier scan: skip cart I/O
    emit(0)
    emit(i(9, 29, 5, 0x20))                # addiu a1, sp, 0x20
    emit(i(13, 0, 6, 0x8B00))              # aligned block containing 0x8b10
    emit(j(symbols["mobile_unlock_read"]))
    emit(i(9, 0, 7, 0x20))                 # delay: addiu a3, zero, 0x20
    branch(5, 2, 0, "finish")              # bnez v0, finish
    emit(0x02002025)                        # delay: or a0, s0, zero
    emit(i(9, 29, 5, 0x40))                # addiu a1, sp, 0x40
    emit(i(13, 0, 6, 0xE780))              # aligned block containing status byte at 0xe79a
    emit(j(symbols["mobile_unlock_read"]))
    emit(i(9, 0, 7, 0x20))
    branch(5, 2, 0, "finish")
    emit(i(36, 29, 8, 0x30))               # delay: byte 0x10 of first block
    emit(i(36, 29, 9, 0x5A))               # byte 0x1a of second block
    emit(8 << 21 | 9 << 16 | 10 << 11 | 0x26)  # xor t2, t0, t1
    emit(i(9, 0, 1, 0xFF))                 # addiu at, zero, 0xff
    branch(5, 10, 1, "finish")             # bne t2, at, finish
    emit(i(9, 29, 4, 0x10))                # delay: addiu a0, sp, 0x10
    branch(4, 8, 1, "finish")              # beq t0, at, finish
    emit(0)
    # Do not write sMobileStadiumFlag from the shared main-menu scan. The
    # dedicated state-18 Mobile session performs that write while the Pak is
    # live and preserves the old zero in its prefetched image so Fragment 7
    # can display the localized first-entry explanation exactly once.
    emit(j(symbols["mobile_unlock_load_record"]))
    emit(i(9, 29, 4, 0x10))                # delay: restore record buffer argument
    emit(i(37, 29, 2, 0x12))               # lhu v0, 0x12(sp)
    emit(i(13, 2, 2, 4))                   # ori v0, v0, 4
    emit(i(41, 29, 2, 0x12))               # sh v0, 0x12(sp)
    emit(j(symbols["mobile_unlock_write_record"]))
    emit(i(9, 29, 4, 0x10))                # delay: addiu a0, sp, 0x10
    emit(i(9, 0, 4, 0x14))                 # addiu a0, zero, 0x14
    emit(j(symbols["mobile_unlock_commit_record"]))
    emit(0x00002825)                        # delay: or a1, zero, zero
    emit(i(15, 0, 8, session_flag_hi))      # successful scan: arm session guard
    emit(i(9, 0, 9, 1))                    # addiu t1, zero, 1
    emit(i(40, 8, 9, session_flag_lo))      # sb t1, %lo(session flag)(t0)
    labels["finish"] = len(words)
    emit(i(35, 29, 31, 0x6C))              # lw ra, 0x6c(sp)
    # This is a tail-call back into the scanner. The scanner restores its own
    # saved s0 at its epilogue; restore our stack here and use the jump delay
    # slot to pass the controller index required by func_8005A92C.
    emit(i(9, 29, 29, 0x70))               # restore wrapper stack
    emit(2 << 26 | ((symbols["mobile_unlock_continue"] >> 2) & 0x03FFFFFF))
    emit(0x02002025)                        # delay: or a0, s0, zero

    for index, opcode, rs, rt, label in branches:
        immediate = labels[label] - index - 1
        words[index] = i(opcode, rs, rt, immediate)
    data = b"".join(struct.pack(">I", word) for word in words)
    if len(data) > size:
        raise ValueError("resident Mobile Crystal unlock wrapper is too large")
    return data + b"\0" * (size - len(data))


def mobile_description_patch_info(source_rom: bytes) -> tuple[int, bytes, int]:
    """Locate the regional Battle Data description call and its fallback."""
    context = source_rom.find(MOBILE_DESCRIPTION_CALL_CONTEXT)
    if context < 0 or source_rom.find(
        MOBILE_DESCRIPTION_CALL_CONTEXT, context + 1
    ) >= 0:
        raise ValueError("cannot uniquely locate Mobile description call")
    call_offset = context + len(MOBILE_DESCRIPTION_CALL_CONTEXT)
    call = source_rom[call_offset:call_offset + 4]
    instruction = int.from_bytes(call, "big")
    if instruction >> 26 != 3:
        raise ValueError("Mobile description accessor is not a JAL")
    accessor = 0x80000000 | ((instruction & 0x03FFFFFF) << 2)
    accessor_offset = accessor - MAIN_VRAM + MAIN_ROM
    words = struct.unpack_from(">4I", source_rom, accessor_offset)
    if (words[0] >> 16 != 0x3C02 or words[1] != 0xAFA40000 or
            words[2] != 0x03E00008 or words[3] >> 16 != 0x2442):
        raise ValueError(
            f"unexpected regional Mobile description accessor at "
            f"0x{accessor:08X}"
        )
    low = words[3] & 0xFFFF
    if low & 0x8000:
        low -= 0x10000
    fallback = ((words[0] & 0xFFFF) << 16) + low
    return call_offset, call, fallback

def state_stub(
    scanner_bridge: int, start: int, end: int, size: int,
    premap_setup: int = 0,
) -> bytes:
    def i(opcode: int, rs: int, rt: int, immediate: int) -> int:
        return opcode << 26 | rs << 21 | rt << 16 | (immediate & 0xFFFF)

    def j(opcode: int, address: int) -> int:
        return opcode << 26 | ((address >> 2) & 0x03FFFFFF)

    instructions = [
        i(9, 29, 29, -0x20),       # addiu sp, sp, -0x20
        i(43, 29, 31, 0x1C),       # sw ra, 0x1c(sp)
    ]
    if premap_setup:
        # premap_setup is a resident helper containing the complete retail
        # func_800353B4(7,0,0) + func_800354E4(0x41) pair.  Keeping it in one
        # call fits the regional 0x48-byte state stub and preserves audio-mode
        # ownership across the later battle/menu transition.
        instructions += [
            j(3, premap_setup),      # complete resident Mobile audio setup
            0,
        ]
    instructions += [
        i(9, 0, 4, 0x35),          # addiu a0, zero, fragment id 0x35
        i(15, 0, 5, (start + 0x8000) >> 16),
        i(9, 5, 5, start),         # addiu a1, a1, %lo(start)
        i(15, 0, 6, (end + 0x8000) >> 16),
        i(9, 6, 6, end),           # addiu a2, a2, %lo(end)
        0x00003825,                 # or a3, zero, zero
        i(43, 29, 0, 0x10),        # sw zero, 0x10(sp)
        j(3, scanner_bridge),       # guarded retail load-and-call
        0,
        i(35, 29, 31, 0x1C),       # lw ra, 0x1c(sp)
        i(9, 29, 29, 0x20),
        0x03E00008,
        0,
    ]
    data = b"".join(struct.pack(">I", word) for word in instructions)
    if len(data) > size:
        raise ValueError("regional state-18 stub is too small")
    return data + b"\0" * (size - len(data))


def build_overlay(repo: Path, input_root: Path, code: str) -> None:
    profile_path = repo / f"assets/localization/mobile/profiles/{code}.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    resident_symbols = {
        name: int(value, 16) for name, value in profile["symbols"].items()
    }
    friend_rule_adapter_address = (
        resident_symbols["mobile_download_copy_cave"] - 0x3BF0
    )

    work = repo / f"build/mobile-stadium/tool/{code}"
    work.mkdir(parents=True, exist_ok=True)
    script = work / "mobile.ld"
    script.write_text(linker_script(profile), encoding="utf-8")
    obj = work / "mobile.o"
    command_obj = work / "mobile-command.o"
    # IDO's legacy driver silently drops an output whose path contains spaces.
    # Compile to /tmp under WSL, then copy the finished object into the build.
    compile_obj = (Path("/tmp") / f"stadium2-mobile-{code}.o"
                   if sys.platform != "win32" else obj)
    elf = work / "mobile.elf"
    raw = work / "mobile.raw.bin"
    compiler = str(repo / "tools/ido/linux/7.1/cc")
    build_version = "VERSION_US" if code == "us" else "VERSION_L"
    run([
        sys.executable, "tools/asm-processor/build.py",
        "--input-enc=utf-8", "--output-enc=euc-jp",
        "--convert-statics=global-with-filename", compiler, "--",
        "mips-linux-gnu-as", "-march=vr4300", "-32", "-G0", "--",
        "-c", "-G", "0", "-non_shared", "-Xcpluscomm", "-nostdinc",
        "-Wab,-r4300_mul", "-Iinclude", "-Isrc", "-Ilib/ultralib/include",
        "-Ilib/ultralib/include/PR", "-Ilib/ultralib/include/ido",
        "-fullwarn", "-verbose", "-woff",
        "624,649,838,712,516,513,596,564,594", "-mips2", "-EB",
        "-D_MIPS_SZLONG=32", "-DNDEBUG", "-D_FINALROM", "-DN_MICRO",
        "-DF3DEX_GBI_2", f"-DBUILD_VERSION={build_version}", "-DLANGUAGE_C",
        "-D_LANGUAGE_C", "-O2", "-o", str(compile_obj),
        "src/mobile_stadium_overlay.c",
    ], repo)
    if compile_obj != obj:
        shutil.copy2(compile_obj, obj)
    run([
        "mips-linux-gnu-as", "-march=vr4300", "-32", "-G0", "-EB",
        "-o", str(command_obj), "src/mobile_stadium_command.s",
    ], repo)
    run([
        "mips-linux-gnu-ld", "-EB", "--emit-relocs", "-T", str(script),
        "-o", str(elf), str(obj), str(command_obj)
    ], repo)
    undefined = subprocess.run(
        ["mips-linux-gnu-nm", "-u", str(elf)], cwd=repo,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if undefined:
        raise ValueError(f"undefined overlay symbols:\n{undefined}")
    run(["mips-linux-gnu-objcopy", "-O", "binary", str(elf), str(raw)], repo)
    relocations = internal_relocations(elf, repo)
    entry_address = elf_symbol_address(elf, repo, "mobile_stadium_entry")
    finalizer_address = elf_symbol_address(elf, repo, "mobile_load_download")
    if not OVERLAY_CODE <= finalizer_address < OVERLAY_CODE + len(raw.read_bytes()):
        raise ValueError("Mobile Friend finalizer is outside its overlay image")
    # Profiles are generated inputs, and this value is intentionally refreshed
    # after every link.  The resident pre-release hook must call the finalizer
    # belonging to this exact overlay, not a stale offset from another IDO
    # layout.
    profile["symbols"]["mobile_overlay_finalize"] = (
        f"0x{finalizer_address:08X}"
    )
    profile_path.write_text(
        json.dumps(profile, indent=2) + "\n", encoding="utf-8"
    )
    fragment = make_fragment(raw.read_bytes(), relocations, entry_address)
    fragment, _ = retarget_friend_rule_call(
        fragment, resident_symbols["func_8006A990"],
        friend_rule_adapter_address, f"{code} freshly compiled overlay"
    )
    if len(fragment) > ROM_SIZE - OVERLAY_ROM:
        raise ValueError("Mobile Stadium overlay exceeds reserved ROM tail")
    # The dormant state-18 fragment loader begins corrupting its runtime
    # bookkeeping at 0x1800 bytes.  This manifested as an osInvalDCache/TLB
    # failure while entering Mobile Stadium, long before the command handler
    # ran.  Reject such a build instead of emitting a ROM that white-screens.
    if len(fragment) >= 0x1800:
        raise ValueError(
            f"Mobile Stadium overlay is {len(fragment):#x}; "
            "the runtime loader requires it to remain below 0x1800"
        )

    source_rom = normalize_rom(
        source_rom_path(
            repo, input_root, code, profile["source_rom"]
        ).read_bytes()
    )
    session_cave_address = resident_symbols["mobile_state_session"]
    session_cave_offset = session_cave_address - MAIN_VRAM + MAIN_ROM
    session_cave_size = (
        resident_symbols["mobile_download_copy_cave"] - session_cave_address
    )
    expected_session_cave = source_rom[
        session_cave_offset:session_cave_offset + session_cave_size
    ]
    fragment_wrapper = resident_symbols["func_80065748"]
    # Preserve the checkpoint's state-18 loader.  The retained scanner release
    # call is hooked below so E000/F000 are copied before that existing Pak
    # session powers Crystal off; no second acquire occurs in this transition.
    replacement_session_cave = mobile_scanner_prefetch_wrapper(
        profile, fragment_wrapper, session_cave_size
    )
    scanner_release_address = resident_symbols["mobile_scanner_release_call"]
    scanner_release_offset = scanner_release_address - MAIN_VRAM + MAIN_ROM
    expected_scanner_release = struct.pack(
        ">I", 3 << 26 |
        ((resident_symbols["mobile_gbpak_release"] >> 2) & 0x03FFFFFF)
    )
    actual_scanner_release = source_rom[
        scanner_release_offset:scanner_release_offset + 4
    ]
    if actual_scanner_release != expected_scanner_release:
        raise ValueError(
            f"{code}: scanner release call is {actual_scanner_release.hex()}, "
            f"expected {expected_scanner_release.hex()}"
        )
    friend_transposer_address = resident_symbols["mobile_friend_transposer"]
    friend_transposer_offset = (
        friend_transposer_address - MAIN_VRAM + MAIN_ROM
    )
    friend_transposer_size = 0x54
    expected_friend_transposer = source_rom[
        friend_transposer_offset:
        friend_transposer_offset + friend_transposer_size
    ]
    friend_transposer_cave_address = resident_symbols[
        "mobile_friend_transposer_cave"
    ]
    friend_transposer_cave_offset = (
        friend_transposer_cave_address - MAIN_VRAM + MAIN_ROM
    )
    friend_transposer_cave_size = 0x74
    expected_friend_transposer_cave = source_rom[
        friend_transposer_cave_offset:
        friend_transposer_cave_offset + friend_transposer_cave_size
    ]
    delivery_cave_address = resident_symbols["mobile_delivery_cave"]
    delivery_cave_offset = delivery_cave_address - MAIN_VRAM + MAIN_ROM
    delivery_cave_size = (
        resident_symbols["mobile_delivery_cave_end"] - delivery_cave_address
    )
    expected_delivery_cave = source_rom[
        delivery_cave_offset:delivery_cave_offset + delivery_cave_size
    ]
    ptp0_cave_address = resident_symbols["mobile_ptp0_cave"]
    ptp0_cave_offset = ptp0_cave_address - MAIN_VRAM + MAIN_ROM
    ptp0_cave_size = (
        resident_symbols["mobile_ptp0_cave_end"] - ptp0_cave_address
    )
    expected_ptp0_cave = source_rom[
        ptp0_cave_offset:ptp0_cave_offset + ptp0_cave_size
    ]
    (first_unlock_query_call_offset,
     expected_first_unlock_query_call) = (
        mobile_first_unlock_query_patch_info(source_rom)
    )
    first_unlock_query_address = (
        ptp0_cave_address + MOBILE_FIRST_UNLOCK_QUERY_OFFSET
    )
    ptp0_call_address = resident_symbols["mobile_e000_wrapper_call"]
    ptp0_call_offset = ptp0_call_address - MAIN_VRAM + MAIN_ROM
    expected_ptp0_call = struct.pack(
        ">I", 3 << 26 |
        ((resident_symbols["mobile_data_scan"] >> 2) & 0x03FFFFFF)
    )
    actual_ptp0_call = source_rom[ptp0_call_offset:ptp0_call_offset + 4]
    if actual_ptp0_call != expected_ptp0_call:
        raise ValueError(
            f"{code}: E000 picker wrapper call is {actual_ptp0_call.hex()}, "
            f"expected {expected_ptp0_call.hex()}"
        )
    music_cave_address = resident_symbols["mobile_music_cave"]
    music_cave_offset = music_cave_address - MAIN_VRAM + MAIN_ROM
    music_cave_size = (
        resident_symbols["mobile_music_cave_end"] - music_cave_address
    )
    expected_music_cave = source_rom[
        music_cave_offset:music_cave_offset + music_cave_size
    ]
    replacement_friend_transposer = mobile_friend_transposer(
        friend_transposer_address,
        friend_transposer_cave_address,
        friend_transposer_size,
    )
    replacement_friend_transposer_cave = mobile_friend_transposer_body(
        friend_transposer_cave_size
    )
    copy_cave_address = resident_symbols["mobile_download_copy_cave"]
    copy_cave_offset = copy_cave_address - MAIN_VRAM + MAIN_ROM
    # __osInitialize_kmc, its unused tail, and __checkHardware_kmc form one
    # contiguous dormant 0x280-byte region ending at osEepromLongWrite.
    # Keep the dispatcher in the first 0x100 bytes and place the resident
    # Friend converter in the remaining 0x180 bytes.
    copy_cave_size = 0x280
    expected_copy_cave = source_rom[
        copy_cave_offset:copy_cave_offset + copy_cave_size
    ]
    copy_handler_size = 0x100
    large_read_calls = (
        resident_symbols["mobile_box_read"] + 0x60,
        resident_symbols["mobile_full_save_read"] + 0x24,
    )
    original_read_call = struct.pack(
        ">I", 3 << 26 | ((resident_symbols["mobile_unlock_read"] >> 2) & 0x03FFFFFF)
    )
    chunked_read_call = struct.pack(
        ">I", 3 << 26 | ((resident_symbols["mobile_chunk_cave"] >> 2) & 0x03FFFFFF)
    )
    chunk_cave_address = resident_symbols["mobile_chunk_cave"]
    chunk_cave_offset = chunk_cave_address - MAIN_VRAM + MAIN_ROM
    chunk_cave_size = 0x108
    expected_chunk_cave = source_rom[
        chunk_cave_offset:chunk_cave_offset + chunk_cave_size
    ]
    unlock_hook_address = resident_symbols["mobile_unlock_scan"] + 0x308
    unlock_hook_offset = unlock_hook_address - MAIN_VRAM + MAIN_ROM
    unlock_cave_address = resident_symbols["mobile_unlock_cave"]
    unlock_cave_offset = unlock_cave_address - MAIN_VRAM + MAIN_ROM
    unlock_cave_size = 0xD0
    expected_unlock_hook = struct.pack(
        ">2I",
        3 << 26 | ((resident_symbols["mobile_unlock_continue"] >> 2) & 0x03FFFFFF),
        0x93A40093,
    )
    actual_unlock_hook = source_rom[unlock_hook_offset:unlock_hook_offset + 8]
    if actual_unlock_hook != expected_unlock_hook:
        raise ValueError(
            f"{code} Mobile unlock hook is {actual_unlock_hook.hex()}, "
            f"expected {expected_unlock_hook.hex()}"
        )
    replacement_unlock_hook = struct.pack(
        ">2I",
        3 << 26 | ((unlock_cave_address >> 2) & 0x03FFFFFF),
        0x93A40093,
    )
    expected_unlock_cave = source_rom[
        unlock_cave_offset:unlock_cave_offset + unlock_cave_size
    ]
    replacement_unlock_cave = mobile_unlock_wrapper(profile, unlock_cave_size)
    stub_offset = int(profile["state18_rom_offset"], 16)
    # The next function starts 0x48 bytes after the dormant international stub.
    stub_size = 0x48
    expected_stub = source_rom[stub_offset:stub_offset + stub_size]
    # Route state 18 through the resident session entry.  The retained
    # scanner release hook cached E000 in persistent BSS while the Pak was
    # live, but the main-menu pool frame has since been popped.  Entry zero
    # reclaims the per-controller objects and restores that Friend block
    # before it invokes the retail fragment loader.  Calling the fragment
    # loader directly here bypasses the restore and makes Friend Data appear
    # empty even though Battle Data at the start of the old object may happen
    # to survive the pool transition.
    replacement_stub = state_stub(
        session_cave_address, OVERLAY_ROM, OVERLAY_ROM + len(fragment),
        stub_size
    )
    route_offset = int(profile["menu_route_rom_offset"], 16)
    expected_route = source_rom[route_offset:route_offset + 4]
    mobile_result_offset = int(profile["menu_mobile_result_rom_offset"], 16)
    expected_mobile_result = source_rom[mobile_result_offset:mobile_result_offset + 4]
    replacement_mobile_result = struct.pack(">I", chunk_cave_address + 0xA0)
    menu_state_offset = int(profile["menu_state_rom_offset"], 16)
    expected_menu_state = source_rom[menu_state_offset:menu_state_offset + 12]
    initializer_instruction = int.from_bytes(expected_menu_state[4:8], "big")
    if initializer_instruction >> 26 != 3:
        raise ValueError(f"{code} Mobile menu initializer is not a JAL")
    initializer = 0x80000000 | ((initializer_instruction & 0x03FFFFFF) << 2)
    fragment_magic = source_rom.rfind(
        b"FRAGMENT", max(0, menu_state_offset - 0x20000), menu_state_offset
    )
    if fragment_magic < 8:
        raise ValueError(f"{code} cannot locate main-menu fragment header")
    fragment_start = fragment_magic - 8
    initializer_rom_offset = fragment_start + (initializer - 0x82200000)
    initializer_delta = initializer_rom_offset - (menu_state_offset + 12)
    # rmon's flush/putw pair is also dormant and sits exactly between the
    # retained Friend transposer and the state-session wrapper.  Moving the
    # main-menu helper here leaves the full KMC tail available to command 0x28.
    menu_helper_address = (
        friend_transposer_cave_address + friend_transposer_cave_size
    )
    menu_helper_size = session_cave_address - menu_helper_address
    menu_helper_offset = menu_helper_address - MAIN_VRAM + MAIN_ROM
    expected_menu_helper = source_rom[
        menu_helper_offset:menu_helper_offset + menu_helper_size
    ]
    (description_call_offset, expected_description_call,
     description_fallback) = mobile_description_patch_info(source_rom)
    description_helper_address = (
        menu_helper_address + MOBILE_DESCRIPTION_HELPER_OFFSET
    )
    replacement_menu_helper = mobile_menu_unlock_wrapper(
        profile, initializer_delta, menu_helper_size, description_fallback,
        delivery_cave_address
    )
    replacement_copy_cave = (
        mobile_download_copy_wrapper(profile, copy_handler_size)
        + mobile_friend_copy_converter(
            profile, copy_cave_size - copy_handler_size
        )
    )
    friend_rule_adapter_offset = (
        friend_rule_adapter_address - MAIN_VRAM + MAIN_ROM
    )
    friend_rule_adapter_size = 0x28
    expected_friend_rule_adapter = source_rom[
        friend_rule_adapter_offset:
        friend_rule_adapter_offset + friend_rule_adapter_size
    ]
    replacement_friend_rule_adapter = mobile_friend_rule_wrapper(
        profile, friend_rule_adapter_size
    )
    replacement_menu_state = struct.pack(
        ">3I",
        3 << 26 | ((menu_helper_address >> 2) & 0x03FFFFFF),
        0x02202825,  # delay: or a1, s1, zero
        0,
    )
    menu_graph_flag_offset = int(profile["menu_graph_flag_rom_offset"], 16)
    menu_graph_base_offset = int(profile["menu_graph_base_rom_offset"], 16)
    expected_menu_graph_flag = source_rom[
        menu_graph_flag_offset:menu_graph_flag_offset + 4
    ]
    expected_menu_graph_base = source_rom[
        menu_graph_base_offset:menu_graph_base_offset + 4
    ]
    if expected_menu_graph_flag != bytes.fromhex("3c088221"):
        raise ValueError(
            f"{code} Mobile navigation flag input is "
            f"{expected_menu_graph_flag.hex()}, expected 3c088221"
        )
    if expected_menu_graph_base != bytes.fromhex("250806b0"):
        raise ValueError(
            f"{code} Mobile navigation base is "
            f"{expected_menu_graph_base.hex()}, expected 250806b0"
        )
    if expected_menu_state[:4] != bytes.fromhex("00002825"):
        raise ValueError(
            f"{code} Mobile menu state input is {expected_menu_state.hex()}, "
            "expected false followed by the retained initializer call"
        )
    if expected_route != bytes.fromhex("24040001"):
        raise ValueError(
            f"{code} menu route is {expected_route.hex()}, expected 24040001"
        )
    slot = bytearray(source_rom[OVERLAY_ROM:])
    slot[:len(fragment)] = fragment
    layer = repo / f"assets/localization/mobile/{code}"
    layer.mkdir(parents=True, exist_ok=True)
    slot_path = layer / "mobile-overlay-slot.bin"
    slot_path.write_bytes(slot)
    initialize_overlay_selection_input(slot_path)
    preserve_overlay_friend_battle_gender(slot_path)
    patches = graphics_patches(repo, code, source_rom)
    patches.append(text_patch(repo, code, source_rom, layer))
    patches.extend(mobile_identity_patches(repo, code, source_rom))
    patches.extend(mobile_organize_patches(code, source_rom, profile))
    patches.append({
        "offset": f"0x{session_cave_offset:X}",
        "expected": expected_session_cave.hex(),
        "replacement": replacement_session_cave.hex(),
    })
    patches.append({
        "offset": f"0x{scanner_release_offset:X}",
        "expected": expected_scanner_release.hex(),
        "replacement": struct.pack(
            ">I", 3 << 26 |
            ((resident_symbols["mobile_scan_release_hook"] >> 2) & 0x03FFFFFF)
        ).hex(),
    })
    patches.append({
        "offset": f"0x{friend_transposer_offset:X}",
        "expected": expected_friend_transposer.hex(),
        "replacement": replacement_friend_transposer.hex(),
    })
    patches.append({
        "offset": f"0x{friend_transposer_cave_offset:X}",
        "expected": expected_friend_transposer_cave.hex(),
        "replacement": replacement_friend_transposer_cave.hex(),
    })
    patches.append({
        "offset": f"0x{delivery_cave_offset:X}",
        "expected": expected_delivery_cave.hex(),
        "replacement": mobile_delivery_wrapper(
            profile, delivery_cave_size
        ).hex(),
    })
    patches.append({
        "offset": f"0x{ptp0_cave_offset:X}",
        "expected": expected_ptp0_cave.hex(),
        "replacement": mobile_ptp0_wrapper(profile, ptp0_cave_size).hex(),
    })
    patches.append({
        "offset": f"0x{ptp0_call_offset:X}",
        "expected": expected_ptp0_call.hex(),
        "replacement": struct.pack(
            ">I", 3 << 26 | ((ptp0_cave_address >> 2) & 0x03FFFFFF)
        ).hex(),
    })
    patches.append({
        "offset": f"0x{first_unlock_query_call_offset:X}",
        "expected": expected_first_unlock_query_call.hex(),
        "replacement": struct.pack(
            ">I", 3 << 26 |
            ((first_unlock_query_address >> 2) & 0x03FFFFFF)
        ).hex(),
    })
    patches.append({
        "offset": f"0x{music_cave_offset:X}",
        "expected": expected_music_cave.hex(),
        "replacement": mobile_menu_music_wrapper(
            profile, music_cave_size
        ).hex(),
    })
    patches.append({
        "offset": f"0x{menu_helper_offset:X}",
        "expected": expected_menu_helper.hex(),
        "replacement": replacement_menu_helper.hex(),
    })
    patches.append({
        "offset": f"0x{description_call_offset:X}",
        "expected": expected_description_call.hex(),
        "replacement": struct.pack(
            ">I", 3 << 26 |
            ((description_helper_address >> 2) & 0x03FFFFFF)
        ).hex(),
    })
    copy_table_address = resident_symbols["mobile_download_copy_table"]
    copy_table_offset = copy_table_address - MAIN_VRAM + MAIN_ROM
    default_copy_target = (
        resident_symbols["mobile_download_read_dispatch"] + 0xC8
    )
    for command in (0x26, 0x27, 0x28):
        entry_offset = copy_table_offset + (command - 0x20) * 4
        expected_entry = struct.pack(">I", default_copy_target)
        actual_entry = source_rom[entry_offset:entry_offset + 4]
        if actual_entry != expected_entry:
            raise ValueError(
                f"{code}: Mobile download command 0x{command:02X} entry is "
                f"{actual_entry.hex()}, expected {expected_entry.hex()}"
            )
        patches.append({
            "offset": f"0x{entry_offset:X}",
            "expected": expected_entry.hex(),
            "replacement": struct.pack(">I", copy_cave_address).hex(),
        })
    for call_address in large_read_calls:
        call_offset = call_address - MAIN_VRAM + MAIN_ROM
        actual_call = source_rom[call_offset:call_offset + 4]
        if actual_call != original_read_call:
            raise ValueError(
                f"{code}: large Crystal SRAM read call at 0x{call_address:08X} "
                f"is {actual_call.hex()}, expected {original_read_call.hex()}"
            )
        patches.append({
            "offset": f"0x{call_offset:X}",
            "expected": original_read_call.hex(),
            "replacement": chunked_read_call.hex(),
        })
    patches += [
        {
            "offset": f"0x{chunk_cave_offset:X}",
            "expected": expected_chunk_cave.hex(),
            "replacement": mobile_chunk_wrapper(profile, chunk_cave_size).hex(),
        },
        {
            "offset": f"0x{copy_cave_offset:X}",
            "expected": expected_copy_cave.hex(),
            "replacement": replacement_copy_cave.hex(),
        },
        {
            "offset": f"0x{friend_rule_adapter_offset:X}",
            "expected": expected_friend_rule_adapter.hex(),
            "replacement": replacement_friend_rule_adapter.hex(),
        },
        {
            "offset": f"0x{unlock_hook_offset:X}",
            "expected": actual_unlock_hook.hex(),
            "replacement": replacement_unlock_hook.hex(),
        },
        {
            "offset": f"0x{unlock_cave_offset:X}",
            "expected": expected_unlock_cave.hex(),
            "replacement": replacement_unlock_cave.hex(),
        },
        {
            "offset": f"0x{menu_graph_flag_offset:X}",
            "expected": expected_menu_graph_flag.hex(),
            "replacement": MOBILE_MENU_GRAPH_FLAG_REPLACEMENT.hex(),
        },
        {
            "offset": f"0x{menu_graph_base_offset:X}",
            "expected": expected_menu_graph_base.hex(),
            "replacement": MOBILE_MENU_GRAPH_BASE_REPLACEMENT.hex(),
        },
        {
            "offset": f"0x{menu_state_offset:X}",
            "expected": expected_menu_state.hex(),
            "replacement": replacement_menu_state.hex(),
        },
        {
            "offset": f"0x{mobile_result_offset:X}",
            "expected": expected_mobile_result.hex(),
            "replacement": replacement_mobile_result.hex(),
        },
        {
            "offset": f"0x{stub_offset:X}",
            "expected": expected_stub.hex(),
            "replacement": replacement_stub.hex(),
        },
        {
            "offset": f"0x{OVERLAY_ROM:X}",
            "size": len(slot),
            "expected_sha256": hashlib.sha256(
                source_rom[OVERLAY_ROM:]
            ).hexdigest(),
            "replacement_file": f"{code}/mobile-overlay-slot.bin",
        },
    ]
    spec = {
        "format": 1,
        "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        "patches": patches,
    }
    (repo / f"assets/localization/mobile/{code}.json").write_text(
        json.dumps(spec, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"built {code}: {len(fragment)}-byte overlay, "
        f"{len(relocations)} relocations; "
        f"state stub 0x{stub_offset:X}"
    )



def sync_graphics_mirror(repo: Path, code: str) -> None:
    """Merge the repository and portable-kit editable graphics directories."""
    primary = repo / f"assets/localization/mobile/graphics/{code}"
    portable = repo / f"stadium2-localization-kit/mobile/assets/graphics/{code}"
    primary.mkdir(parents=True, exist_ok=True)
    portable.mkdir(parents=True, exist_ok=True)
    for name in sorted({p.name for p in primary.iterdir() if p.is_file()} |
                       {p.name for p in portable.iterdir() if p.is_file()}):
        # The portable resource-55 JPEG is the high-quality editable master;
        # sync_localization_kit.py creates the separate ROM-ready 4:2:0 copy.
        # Never mirror that derived copy back over its source.
        if name == "archive13_055_jpeg_144x96.jpg":
            continue
        left = primary / name
        right = portable / name
        if not left.exists():
            shutil.copy2(right, left)
        elif not right.exists():
            shutil.copy2(left, right)
        elif left.read_bytes() != right.read_bytes():
            if left.stat().st_mtime_ns == right.stat().st_mtime_ns:
                raise ValueError(
                    f"{code}: conflicting graphics mirror file with identical "
                    f"timestamp: {name}"
                )
            source, target = ((left, right) if left.stat().st_mtime_ns >
                              right.stat().st_mtime_ns else (right, left))
            shutil.copy2(source, target)

def refresh_assets(repo: Path, input_root: Path, code: str) -> None:
    """Refresh editable assets/config without recompiling the MIPS overlay."""
    sync_graphics_mirror(repo, code)
    profile_path = repo / f"assets/localization/mobile/profiles/{code}.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    spec_path = repo / f"assets/localization/mobile/{code}.json"
    if not spec_path.exists():
        raise ValueError(
            f"{code}: missing {spec_path}; run the full Mobile Stadium build first"
        )
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    profile_hash = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    if spec.get("profile_sha256") != profile_hash:
        print(
            f"warning: {code}: profile JSON was regenerated; retaining the "
            "existing compiled overlay after validating its route/stub patches"
        )
        spec["profile_sha256"] = profile_hash

    source_rom = normalize_rom(
        source_rom_path(repo, input_root, code, profile["source_rom"]).read_bytes()
    )
    resident_symbols = {
        name: int(value, 16) for name, value in profile["symbols"].items()
    }
    friend_rule_adapter_address = (
        resident_symbols["mobile_download_copy_cave"] - 0x3BF0
    )

    session_cave_address = resident_symbols["mobile_state_session"]
    session_cave_offset = session_cave_address - MAIN_VRAM + MAIN_ROM
    session_cave_size = (
        resident_symbols["mobile_download_copy_cave"] - session_cave_address
    )
    expected_session_cave = source_rom[
        session_cave_offset:session_cave_offset + session_cave_size
    ]
    checkpoint_slot = (
        repo / f"assets/localization/mobile/{code}/mobile-overlay-slot.bin"
    )
    if ensure_overlay_two_page_mapping(checkpoint_slot):
        print(f"migrated {code}: restored two-page 0x1790 overlay mapping")
    if remove_overlay_guarded_scan(
        checkpoint_slot, resident_symbols["mobile_guarded_scan"]
    ):
        print(f"migrated {code}: scan/acquire moved ahead of overlay mapping")
    if remove_overlay_premap_setup(
        checkpoint_slot, resident_symbols["func_800354E4"]
    ):
        print(f"migrated {code}: state 0x41 setup moved ahead of overlay mapping")
    if disable_overlay_postmap_pak_fallback(checkpoint_slot):
        print(f"migrated {code}: removed post-map Transfer Pak fallback")
    slot_header = checkpoint_slot.read_bytes()[:0x20]
    fragment_file_size = int.from_bytes(slot_header[0x18:0x1C], "big")
    finish_migrated = False
    # The resident scanner originally placed its finish/load entry at +0x180,
    # then +0x280, and the first hardware-prefetch build used +0x2c0.  The
    # western validator expansion moved the live entry to +0x2d0.  Cached PAL
    # controller slots still contain the +0x2c0 call; leaving it untouched
    # enters the preceding scanner function's restore epilogue, so Fragment 3
    # is never mapped and its subsequent 0x81a00020 entry faults.  Migrate all
    # three historical locations rather than relying on the compiler layout.
    for old_finish_offset in (0x180, 0x280, 0x2C0):
        finish_migrated |= retarget_overlay_finish_load(
            checkpoint_slot,
            resident_symbols["mobile_state_session"] + old_finish_offset,
            resident_symbols["mobile_finish_scan_load"],
        )
    if finish_migrated:
        print(f"migrated {code}: post-copy loader uses resident prefetch epilogue")
    if patch_prefetched_overlay_finalizer(checkpoint_slot):
        print(f"migrated {code}: overlay validates resident-prefetched SRAM")
    if move_pal_overlay_finalizer_before_allocators(checkpoint_slot):
        print(f"migrated {code}: finalized resident data before PAL allocations")
    if code == "us":
        if clear_invalid_overlay_p3_frame(
            checkpoint_slot, resident_symbols["_bzero"]
        ):
            print(f"migrated {code}: invalid Crystal P3 frames become empty data")
    elif restore_pal_overlay_p3_count_clear(
        checkpoint_slot, resident_symbols["_bzero"]
    ):
        print(f"migrated {code}: restored PAL checkpoint P3 count clear")
    if ensure_overlay_internal_jump_relocations(checkpoint_slot):
        print(f"migrated {code}: completed overlay-internal jump relocations")
    if ensure_overlay_menu_music_restart(
        checkpoint_slot,
        resident_symbols["func_80065748"],
        resident_symbols["func_80002B34"],
        resident_symbols["func_80002BE8"],
        resident_symbols["func_800354E4"],
        resident_symbols["mobile_music_cave"],
    ):
        print(f"migrated {code}: scoped the Mobile menu music restart")
    if retarget_overlay_friend_rule_adapter(
        checkpoint_slot, resident_symbols["func_8006A990"],
        friend_rule_adapter_address
    ):
        print(f"migrated {code}: Friend difficulty uses western rule adapter")
    if initialize_overlay_selection_input(checkpoint_slot):
        print(f"migrated {code}: restored Fragment 7 controller-1 local slot")
    if preserve_overlay_friend_battle_gender(checkpoint_slot):
        print(f"migrated {code}: preserved Friend battle portrait gender")
    state_stub_offset = int(profile["state18_rom_offset"], 16)
    state_stub_size = 0x48
    expected_state_stub = source_rom[
        state_stub_offset:state_stub_offset + state_stub_size
    ]

    friend_transposer_address = resident_symbols["mobile_friend_transposer"]
    friend_transposer_offset = friend_transposer_address - MAIN_VRAM + MAIN_ROM
    friend_transposer_size = 0x54
    expected_friend_transposer = source_rom[
        friend_transposer_offset:friend_transposer_offset + friend_transposer_size
    ]
    friend_transposer_cave_address = resident_symbols[
        "mobile_friend_transposer_cave"
    ]
    friend_transposer_cave_offset = (
        friend_transposer_cave_address - MAIN_VRAM + MAIN_ROM
    )
    friend_transposer_cave_size = 0x74
    expected_friend_transposer_cave = source_rom[
        friend_transposer_cave_offset:
        friend_transposer_cave_offset + friend_transposer_cave_size
    ]
    delivery_cave_address = resident_symbols["mobile_delivery_cave"]
    delivery_cave_offset = delivery_cave_address - MAIN_VRAM + MAIN_ROM
    delivery_cave_size = (
        resident_symbols["mobile_delivery_cave_end"] - delivery_cave_address
    )
    expected_delivery_cave = source_rom[
        delivery_cave_offset:delivery_cave_offset + delivery_cave_size
    ]
    ptp0_cave_address = resident_symbols["mobile_ptp0_cave"]
    ptp0_cave_offset = ptp0_cave_address - MAIN_VRAM + MAIN_ROM
    ptp0_cave_size = (
        resident_symbols["mobile_ptp0_cave_end"] - ptp0_cave_address
    )
    expected_ptp0_cave = source_rom[
        ptp0_cave_offset:ptp0_cave_offset + ptp0_cave_size
    ]
    (first_unlock_query_call_offset,
     expected_first_unlock_query_call) = (
        mobile_first_unlock_query_patch_info(source_rom)
    )
    first_unlock_query_address = (
        ptp0_cave_address + MOBILE_FIRST_UNLOCK_QUERY_OFFSET
    )
    ptp0_call_address = resident_symbols["mobile_e000_wrapper_call"]
    ptp0_call_offset = ptp0_call_address - MAIN_VRAM + MAIN_ROM
    expected_ptp0_call = struct.pack(
        ">I", 3 << 26 |
        ((resident_symbols["mobile_data_scan"] >> 2) & 0x03FFFFFF)
    )
    actual_ptp0_call = source_rom[ptp0_call_offset:ptp0_call_offset + 4]
    if actual_ptp0_call != expected_ptp0_call:
        raise ValueError(
            f"{code}: E000 picker wrapper call is {actual_ptp0_call.hex()}, "
            f"expected {expected_ptp0_call.hex()}"
        )
    music_cave_address = resident_symbols["mobile_music_cave"]
    music_cave_offset = music_cave_address - MAIN_VRAM + MAIN_ROM
    music_cave_size = (
        resident_symbols["mobile_music_cave_end"] - music_cave_address
    )
    expected_music_cave = source_rom[
        music_cave_offset:music_cave_offset + music_cave_size
    ]

    copy_cave_address = resident_symbols["mobile_download_copy_cave"]
    copy_cave_offset = copy_cave_address - MAIN_VRAM + MAIN_ROM
    copy_cave_size = 0x280
    copy_handler_size = 0x100
    expected_copy_cave = source_rom[
        copy_cave_offset:copy_cave_offset + copy_cave_size
    ]
    friend_rule_adapter_offset = (
        friend_rule_adapter_address - MAIN_VRAM + MAIN_ROM
    )
    friend_rule_adapter_size = 0x28
    expected_friend_rule_adapter = source_rom[
        friend_rule_adapter_offset:
        friend_rule_adapter_offset + friend_rule_adapter_size
    ]
    menu_state_offset = int(profile["menu_state_rom_offset"], 16)
    menu_state = source_rom[menu_state_offset:menu_state_offset + 12]
    initializer_instruction = int.from_bytes(menu_state[4:8], "big")
    if initializer_instruction >> 26 != 3:
        raise ValueError(f"{code} Mobile menu initializer is not a JAL")
    initializer = 0x80000000 | ((initializer_instruction & 0x03FFFFFF) << 2)
    fragment_magic = source_rom.rfind(
        b"FRAGMENT", max(0, menu_state_offset - 0x20000), menu_state_offset
    )
    if fragment_magic < 8:
        raise ValueError(f"{code} cannot locate main-menu fragment header")
    fragment_start = fragment_magic - 8
    initializer_rom_offset = fragment_start + (initializer - 0x82200000)
    initializer_delta = initializer_rom_offset - (menu_state_offset + 12)
    menu_helper_address = (
        friend_transposer_cave_address + friend_transposer_cave_size
    )
    menu_helper_size = session_cave_address - menu_helper_address
    menu_helper_offset = menu_helper_address - MAIN_VRAM + MAIN_ROM
    expected_menu_helper = source_rom[
        menu_helper_offset:menu_helper_offset + menu_helper_size
    ]
    chunk_cave_address = resident_symbols["mobile_chunk_cave"]
    chunk_cave_offset = chunk_cave_address - MAIN_VRAM + MAIN_ROM
    chunk_cave_size = 0x108
    expected_chunk_cave = source_rom[
        chunk_cave_offset:chunk_cave_offset + chunk_cave_size
    ]
    large_read_call_offsets = {
        resident_symbols["mobile_box_read"] + 0x60 - MAIN_VRAM + MAIN_ROM,
        resident_symbols["mobile_full_save_read"] + 0x24 - MAIN_VRAM + MAIN_ROM,
    }
    unlock_hook_address = resident_symbols["mobile_unlock_scan"] + 0x308
    unlock_hook_offset = unlock_hook_address - MAIN_VRAM + MAIN_ROM
    unlock_cave_address = resident_symbols["mobile_unlock_cave"]
    unlock_cave_offset = unlock_cave_address - MAIN_VRAM + MAIN_ROM
    expected_unlock_hook = source_rom[unlock_hook_offset:unlock_hook_offset + 8]
    replacement_unlock_hook = struct.pack(
        ">2I",
        3 << 26 | ((unlock_cave_address >> 2) & 0x03FFFFFF),
        0x93A40093,
    )
    expected_unlock_cave = source_rom[unlock_cave_offset:unlock_cave_offset + 0xD0]
    scanner_release_address = resident_symbols["mobile_scanner_release_call"]
    scanner_release_offset = scanner_release_address - MAIN_VRAM + MAIN_ROM
    expected_scanner_release = struct.pack(
        ">I", 3 << 26 |
        ((resident_symbols["mobile_gbpak_release"] >> 2) & 0x03FFFFFF)
    )
    actual_scanner_release = source_rom[
        scanner_release_offset:scanner_release_offset + 4
    ]
    if actual_scanner_release != expected_scanner_release:
        raise ValueError(
            f"{code}: scanner release call is {actual_scanner_release.hex()}, "
            f"expected {expected_scanner_release.hex()}"
        )
    (description_call_offset, expected_description_call,
     description_fallback) = mobile_description_patch_info(source_rom)
    description_helper_address = (
        menu_helper_address + MOBILE_DESCRIPTION_HELPER_OFFSET
    )
    unlock_patches = [
        {
            "offset": f"0x{friend_transposer_offset:X}",
            "expected": expected_friend_transposer.hex(),
            "replacement": mobile_friend_transposer(
                friend_transposer_address, friend_transposer_cave_address,
                friend_transposer_size
            ).hex(),
        },
        {
            "offset": f"0x{friend_transposer_cave_offset:X}",
            "expected": expected_friend_transposer_cave.hex(),
            "replacement": mobile_friend_transposer_body(
                friend_transposer_cave_size
            ).hex(),
        },
        {
            "offset": f"0x{delivery_cave_offset:X}",
            "expected": expected_delivery_cave.hex(),
            "replacement": mobile_delivery_wrapper(
                profile, delivery_cave_size
            ).hex(),
        },
        {
            "offset": f"0x{ptp0_cave_offset:X}",
            "expected": expected_ptp0_cave.hex(),
            "replacement": mobile_ptp0_wrapper(
                profile, ptp0_cave_size
            ).hex(),
        },
        {
            "offset": f"0x{ptp0_call_offset:X}",
            "expected": expected_ptp0_call.hex(),
            "replacement": struct.pack(
                ">I", 3 << 26 | ((ptp0_cave_address >> 2) & 0x03FFFFFF)
            ).hex(),
        },
        {
            "offset": f"0x{first_unlock_query_call_offset:X}",
            "expected": expected_first_unlock_query_call.hex(),
            "replacement": struct.pack(
                ">I", 3 << 26 |
                ((first_unlock_query_address >> 2) & 0x03FFFFFF)
            ).hex(),
        },
        {
            "offset": f"0x{music_cave_offset:X}",
            "expected": expected_music_cave.hex(),
            "replacement": mobile_menu_music_wrapper(
                profile, music_cave_size
            ).hex(),
        },
        {
            "offset": f"0x{menu_helper_offset:X}",
            "expected": expected_menu_helper.hex(),
            "replacement": mobile_menu_unlock_wrapper(
                profile, initializer_delta, menu_helper_size,
                description_fallback, delivery_cave_address
            ).hex(),
        },
        {
            "offset": f"0x{description_call_offset:X}",
            "expected": expected_description_call.hex(),
            "replacement": struct.pack(
                ">I", 3 << 26 |
                ((description_helper_address >> 2) & 0x03FFFFFF)
            ).hex(),
        },
        {
            "offset": f"0x{session_cave_offset:X}",
            "expected": expected_session_cave.hex(),
            "replacement": mobile_scanner_prefetch_wrapper(
                profile, resident_symbols["func_80065748"], session_cave_size
            ).hex(),
        },
        {
            "offset": f"0x{scanner_release_offset:X}",
            "expected": expected_scanner_release.hex(),
            "replacement": struct.pack(
                ">I", 3 << 26 |
                ((resident_symbols["mobile_scan_release_hook"] >> 2) &
                 0x03FFFFFF)
            ).hex(),
        },
        {
            "offset": f"0x{state_stub_offset:X}",
            "expected": expected_state_stub.hex(),
            "replacement": state_stub(
                resident_symbols["mobile_state_session"], OVERLAY_ROM,
                OVERLAY_ROM + fragment_file_size, state_stub_size
            ).hex(),
        },
        {
            "offset": f"0x{copy_cave_offset:X}",
            "expected": expected_copy_cave.hex(),
            "replacement": (
                mobile_download_copy_wrapper(profile, copy_handler_size)
                + mobile_friend_copy_converter(
                    profile, copy_cave_size - copy_handler_size
                )
            ).hex(),
        },
        {
            "offset": f"0x{friend_rule_adapter_offset:X}",
            "expected": expected_friend_rule_adapter.hex(),
            "replacement": mobile_friend_rule_wrapper(
                profile, friend_rule_adapter_size
            ).hex(),
        },
        {
            "offset": f"0x{menu_state_offset:X}",
            "expected": menu_state.hex(),
            "replacement": struct.pack(
                ">3I",
                3 << 26 | ((menu_helper_address >> 2) & 0x03FFFFFF),
                0x02202825,
                0,
            ).hex(),
        },
        {
            "offset": f"0x{chunk_cave_offset:X}",
            "expected": expected_chunk_cave.hex(),
            "replacement": mobile_chunk_wrapper(profile, chunk_cave_size).hex(),
        },
        {
            "offset": f"0x{unlock_hook_offset:X}",
            "expected": expected_unlock_hook.hex(),
            "replacement": replacement_unlock_hook.hex(),
        },
        {
            "offset": f"0x{unlock_cave_offset:X}",
            "expected": expected_unlock_cave.hex(),
            "replacement": mobile_unlock_wrapper(profile, 0xD0).hex(),
        },
    ]
    copy_table_offset = (
        resident_symbols["mobile_download_copy_table"] - MAIN_VRAM + MAIN_ROM
    )
    copy_table_offsets = {
        copy_table_offset + (command - 0x20) * 4
        for command in (0x26, 0x27, 0x28)
    }
    default_copy_target = (
        resident_symbols["mobile_download_read_dispatch"] + 0xC8
    )
    for entry_offset in sorted(copy_table_offsets):
        expected_entry = struct.pack(">I", default_copy_target)
        actual_entry = source_rom[entry_offset:entry_offset + 4]
        if actual_entry != expected_entry:
            raise ValueError(
                f"{code}: source Mobile command entry at 0x{entry_offset:X} "
                f"is {actual_entry.hex()}, expected {expected_entry.hex()}"
            )
        unlock_patches.append({
            "offset": f"0x{entry_offset:X}",
            "expected": expected_entry.hex(),
            "replacement": struct.pack(
                ">I", resident_symbols["mobile_download_copy_cave"]
            ).hex(),
        })
    static_offsets = {
        int(profile[key], 16)
        for key in (
            "menu_graph_flag_rom_offset",
            "menu_graph_base_rom_offset",
            "menu_state_rom_offset",
            "menu_mobile_result_rom_offset",
            "state18_rom_offset",
        )
    }
    static_offsets.add(OVERLAY_ROM)
    static_offsets.add(session_cave_offset)
    static_offsets.add(scanner_release_offset)
    static_offsets.add(copy_cave_offset)
    static_offsets.add(menu_helper_offset)
    static_offsets.add(friend_rule_adapter_offset)
    static_offsets.add(music_cave_offset)
    static_offsets.add(ptp0_cave_offset)
    static_offsets.add(ptp0_call_offset)
    static_offsets.add(first_unlock_query_call_offset)
    static_offsets.update(copy_table_offsets)
    required_offsets = set(static_offsets)
    # The resident session wrapper is introduced by this migration and is
    # therefore not expected in pre-fix checkpoint specifications.
    required_offsets.discard(session_cave_offset)
    required_offsets.discard(scanner_release_offset)
    required_offsets.discard(menu_helper_offset)
    required_offsets.discard(friend_rule_adapter_offset)
    required_offsets.discard(music_cave_offset)
    required_offsets.discard(ptp0_cave_offset)
    required_offsets.discard(ptp0_call_offset)
    required_offsets.discard(first_unlock_query_call_offset)
    required_offsets.difference_update(copy_table_offsets)
    static_offsets.update(large_read_call_offsets)
    retained = [
        patch for patch in spec.get("patches", [])
        if int(patch["offset"], 16) in static_offsets and
        int(patch["offset"], 16) not in (
            copy_cave_offset, session_cave_offset, scanner_release_offset,
            state_stub_offset, menu_state_offset, menu_helper_offset,
            friend_rule_adapter_offset, music_cave_offset,
            ptp0_cave_offset, ptp0_call_offset,
            first_unlock_query_call_offset,
            *copy_table_offsets
        )
    ]
    retained_offsets = {
        int(patch["offset"], 16) for patch in spec.get("patches", [])
        if int(patch["offset"], 16) in static_offsets
    }
    missing = required_offsets - retained_offsets
    if missing:
        raise ValueError(
            f"{code}: existing layer is missing retained patches at "
            + ", ".join(f"0x{offset:X}" for offset in sorted(missing))
        )

    layer = repo / f"assets/localization/mobile/{code}"
    patches = graphics_patches(repo, code, source_rom)
    patches.append(text_patch(repo, code, source_rom, layer))
    patches.extend(mobile_identity_patches(repo, code, source_rom))
    patches.extend(mobile_organize_patches(code, source_rom, profile))
    patches.extend(unlock_patches)
    patches.extend(retained)
    spec["patches"] = patches
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    print(f"refreshed {code}: text, graphics, Crystal ID, and service URL patches")
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("install", "profiles", "export-graphics", "build", "refresh-assets", "configure-project64"),
    )
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--input-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--languages", nargs="+", default=list(ROM_NAMES))
    parser.add_argument(
        "--project64-config", type=Path,
        help="directory containing Project64.rdb and Video.rdb",
    )
    parser.add_argument(
        "--additional-mobile-rom-root", type=Path, action="append", default=[],
        help="also install profiles for a separate Mobile ROM directory",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="replace existing source and editable assets during install",
    )
    args = parser.parse_args()
    try:
        repo = args.repo.resolve()
        if args.command == "install":
            install_package(
                repo, Path(__file__).resolve().parent / "mobile", args.force
            )
        elif args.command == "profiles":
            write_profiles(repo, args.input_root.resolve(), args.languages)
        elif args.command == "export-graphics":
            export_graphics(repo, args.input_root.resolve(), args.languages)
        elif args.command == "configure-project64":
            if args.project64_config is None:
                raise ValueError("--project64-config is required")
            configure_project64(
                repo, args.input_root.resolve(), args.project64_config.resolve(),
                args.languages, args.additional_mobile_rom_root,
            )
        elif args.command == "refresh-assets":
            for code in args.languages:
                refresh_assets(repo, args.input_root.resolve(), code)
        else:
            for code in args.languages:
                build_overlay(repo, args.input_root.resolve(), code)
    except (FileNotFoundError, KeyError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
