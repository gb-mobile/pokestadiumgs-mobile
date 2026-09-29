#!/usr/bin/env python3
"""Synchronize editable localization-kit inputs into Mobile Stadium builds."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import shutil
from pathlib import Path

from PIL import Image, ImageFilter

from localization import parse_script


LANGUAGES = ("en", "fr", "de", "it", "es")
TEXT_LANGUAGES = ("us", "en", "fr", "de", "it", "es")
GRAPHIC_LANGUAGES = ("us", "en", "au", "fr", "de", "it", "es")
BATTLE_DEMO = "archive13_055_jpeg_144x96.jpg"
OBSOLETE_BATTLE_DEMO = "archive1_033_000A70_ia8_24x26_1.jpg"
TURBOJPEG_DLL = Path(__file__).resolve().parent / "bin/turbojpeg.dll"


def same_file(first: Path, second: Path) -> bool:
    if not second.exists():
        return False
    source = first.stat()
    target = second.stat()
    # Editors update size and/or modification time. Avoid hashing the many
    # thousands of unchanged exported textures on every build.
    return source.st_size == target.st_size and source.st_mtime_ns <= target.st_mtime_ns


def copy_changed(source: Path, destination: Path) -> bool:
    if same_file(source, destination):
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return True


def copy_content_changed(source: Path, destination: Path) -> bool:
    """Copy small authoritative inputs based on bytes, independent of mtimes."""
    data = source.read_bytes()
    if destination.exists() and destination.read_bytes() == data:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return True


def jpeg_segment(marker: int, payload: bytes) -> bytes:
    return b"\xFF" + bytes((marker,)) + (len(payload) + 2).to_bytes(2, "big") + payload


def split_huffman_tables(payload: bytes) -> dict[int, bytes]:
    tables = {}
    cursor = 0
    while cursor < len(payload):
        start = cursor
        if cursor + 17 > len(payload):
            raise ValueError("truncated JPEG Huffman table")
        identifier = payload[cursor]
        value_count = sum(payload[cursor + 1:cursor + 17])
        cursor += 17 + value_count
        if cursor > len(payload):
            raise ValueError("truncated JPEG Huffman values")
        tables[identifier] = payload[start:cursor]
    return tables


def normalize_jpeg_layout(data: bytes, source: Path) -> bytes:
    """Reorder baseline JPEG metadata without touching compressed scan data."""
    if not data.startswith(b"\xFF\xD8"):
        raise ValueError(f"{source} is not JPEG data")
    cursor = 2
    quantization = []
    huffman = {}
    frame = scan = None
    restart = None
    while cursor + 4 <= len(data):
        marker_start = cursor
        if data[cursor] != 0xFF:
            raise ValueError(f"invalid JPEG marker in {source}")
        while cursor < len(data) and data[cursor] == 0xFF:
            cursor += 1
        marker = data[cursor]
        cursor += 1
        if marker == 0xDA:
            scan = data[marker_start:]
            break
        if marker in range(0xD0, 0xD9) or marker == 0x01:
            continue
        length = int.from_bytes(data[cursor:cursor + 2], "big")
        end = cursor + length
        payload = data[cursor + 2:end]
        if marker == 0xDB:
            quantization.append(payload)
        elif marker == 0xC4:
            huffman.update(split_huffman_tables(payload))
        elif marker == 0xC0:
            frame = payload
        elif marker == 0xDD:
            restart = payload
        cursor = end
    order = (0x00, 0x01, 0x10, 0x11)
    if not quantization or frame is None or scan is None or any(
        identifier not in huffman for identifier in order
    ):
        raise ValueError(f"cannot normalize baseline JPEG layout from {source}")
    result = (
        b"\xFF\xD8"
        + jpeg_segment(0xDB, b"".join(quantization))
        + jpeg_segment(0xC4, b"".join(huffman[x] for x in order))
        + jpeg_segment(0xC0, frame)
    )
    if restart is not None:
        result += jpeg_segment(0xDD, restart)
    return result + scan


class _TJRegion(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int),
                ("w", ctypes.c_int), ("h", ctypes.c_int)]


class _TJTransform(ctypes.Structure):
    _fields_ = [("r", _TJRegion), ("op", ctypes.c_int),
                ("options", ctypes.c_int), ("data", ctypes.c_void_p),
                ("custom_filter", ctypes.c_void_p)]


def lossless_baseline_jpeg(source: Path) -> bytes:
    """Convert progressive 4:2:0 JPEG coefficients to baseline losslessly."""
    if not TURBOJPEG_DLL.exists():
        raise FileNotFoundError(
            f"missing bundled lossless JPEG runtime: {TURBOJPEG_DLL}"
        )
    library = ctypes.CDLL(str(TURBOJPEG_DLL))
    library.tj3Init.argtypes = [ctypes.c_int]
    library.tj3Init.restype = ctypes.c_void_p
    library.tj3Transform.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
        ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(_TJTransform),
    ]
    library.tj3Transform.restype = ctypes.c_int
    library.tj3GetErrorStr.argtypes = [ctypes.c_void_p]
    library.tj3GetErrorStr.restype = ctypes.c_char_p
    library.tj3Free.argtypes = [ctypes.c_void_p]
    library.tj3Destroy.argtypes = [ctypes.c_void_p]
    data = source.read_bytes()
    incoming = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    outputs = (ctypes.c_void_p * 1)()
    sizes = (ctypes.c_size_t * 1)()
    transforms = (_TJTransform * 1)()
    handle = library.tj3Init(2)  # TJINIT_TRANSFORM
    if not handle:
        raise ValueError("cannot initialize bundled TurboJPEG transform")
    try:
        if library.tj3Transform(
            handle, incoming, len(data), 1, outputs, sizes, transforms
        ):
            raise ValueError(library.tj3GetErrorStr(handle).decode("utf-8"))
        transformed = ctypes.string_at(outputs[0], sizes[0])
        library.tj3Free(outputs[0])
    finally:
        library.tj3Destroy(handle)
    return normalize_jpeg_layout(transformed, source)


def jpeg_is_420(source: Path, expected_size: tuple[int, int]) -> bool:
    with Image.open(source) as image:
        return (
            image.size == expected_size
            and getattr(image, "layer", None) == [
                (1, 2, 2, 0), (2, 1, 1, 1), (3, 1, 1, 1)
            ]
        )


def stadium_jpeg(source: Path, expected_size: tuple[int, int]) -> bytes:
    """Encode the restricted JPEG layout assumed by Stadium's decoder."""
    with Image.open(source) as image:
        image = image.convert("RGB")
        if image.size != expected_size:
            raise ValueError(
                f"{source} is {image.size[0]}x{image.size[1]}, expected "
                f"{expected_size[0]}x{expected_size[1]}"
            )
        # Do not apply any filtering here. User-edited 4:2:0 sources already
        # contain the intended image; quality 100 makes the unavoidable
        # progressive-to-baseline transcode as close to lossless as possible.
        buffer = io.BytesIO()
        image.save(
            buffer, "JPEG", quality=100, subsampling=2,
            optimize=True, progressive=False,
        )
    data = buffer.getvalue()
    cursor = 2
    quantization = []
    huffman = {}
    frame = scan = None
    while cursor + 4 <= len(data):
        if data[cursor] != 0xFF:
            raise ValueError(f"invalid JPEG marker in {source}")
        while data[cursor] == 0xFF:
            cursor += 1
        marker = data[cursor]
        cursor += 1
        length = int.from_bytes(data[cursor:cursor + 2], "big")
        end = cursor + length
        payload = data[cursor + 2:end]
        if marker == 0xDB:
            quantization.append(payload)
        elif marker == 0xC4:
            huffman.update(split_huffman_tables(payload))
        elif marker == 0xC0:
            frame = payload
        elif marker == 0xDA:
            scan = data[cursor - 2:]
            break
        cursor = end
    order = (0x00, 0x01, 0x10, 0x11)
    if not quantization or frame is None or scan is None or any(
        identifier not in huffman for identifier in order
    ):
        raise ValueError(f"cannot produce Stadium JPEG from {source}")
    return (
        b"\xFF\xD8"
        + jpeg_segment(0xDB, b"".join(quantization))
        + jpeg_segment(0xC4, b"".join(huffman[x] for x in order))
        + jpeg_segment(0xC0, frame)
        + scan
    )


def pal_display_jpeg(source: Path, expected_size: tuple[int, int]) -> bytes:
    """Prepare a JPEG for the one-line PAL presentation stretch.

    Stadium presents this 144x96 help image one output line taller in the PAL
    builds.  The resulting bilinear interpolation is especially visible in
    the tiny localized PLAY-icon labels.  Apply a mild vertical-only
    pre-emphasis to the derived ROM copy, while retaining the editable source
    untouched.  Reusing the source quantization tables keeps the archive size
    stable and avoids introducing a second, unrelated quality change.
    """
    with Image.open(source) as incoming:
        if incoming.size != expected_size:
            raise ValueError(
                f"{source} is {incoming.size[0]}x{incoming.size[1]}, expected "
                f"{expected_size[0]}x{expected_size[1]}"
            )
        quantization = incoming.quantization
        if not quantization or 0 not in quantization or 1 not in quantization:
            raise ValueError(f"{source} has no two-table JPEG quantization data")
        image = incoming.convert("RGB").filter(ImageFilter.Kernel(
            (3, 3),
            (0, -0.5, 0, 0, 2.0, 0, 0, -0.5, 0),
            scale=1,
        ))
        buffer = io.BytesIO()
        image.save(
            buffer, "JPEG", qtables=quantization, subsampling=2,
            optimize=True, progressive=False,
        )
    return normalize_jpeg_layout(buffer.getvalue(), source)


def battle_demo_jpeg(source: Path, language: str) -> bytes:
    """Create the deterministic ROM-ready Archive 13 resource 55 stream."""
    expected_size = (144, 96)
    if language in {"fr", "de", "it", "es"}:
        return pal_display_jpeg(source, expected_size)
    if is_stadium_jpeg(source, expected_size):
        return source.read_bytes()
    if jpeg_is_420(source, expected_size):
        return lossless_baseline_jpeg(source)
    return stadium_jpeg(source, expected_size)


def is_stadium_jpeg(path: Path, expected_size: tuple[int, int]) -> bool:
    """Return whether a JPEG already has the restricted Stadium layout."""
    data = path.read_bytes()
    if not data.startswith(b"\xFF\xD8"):
        return False
    cursor = 2
    marker_order: list[int] = []
    huffman_order: list[int] = []
    dimensions = None
    while cursor + 4 <= len(data):
        if data[cursor] != 0xFF:
            return False
        while cursor < len(data) and data[cursor] == 0xFF:
            cursor += 1
        if cursor >= len(data):
            return False
        marker = data[cursor]
        cursor += 1
        if marker == 0xDA:
            marker_order.append(marker)
            break
        if marker in range(0xD0, 0xD9) or marker == 0x01:
            continue
        if cursor + 2 > len(data):
            return False
        length = int.from_bytes(data[cursor:cursor + 2], "big")
        end = cursor + length
        if length < 2 or end > len(data):
            return False
        payload = data[cursor + 2:end]
        marker_order.append(marker)
        if marker in (0xE0, 0xEE):
            return False
        if marker == 0xC0:
            if len(payload) < 5:
                return False
            dimensions = (
                int.from_bytes(payload[3:5], "big"),
                int.from_bytes(payload[1:3], "big"),
            )
        elif marker == 0xC4:
            try:
                huffman_order.extend(split_huffman_tables(payload))
            except ValueError:
                return False
        cursor = end
    return (
        dimensions == expected_size
        and all(marker in marker_order for marker in (0xDB, 0xC4, 0xC0, 0xDA))
        and huffman_order == [0x00, 0x01, 0x10, 0x11]
    )


def entry_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_overrides(path: Path) -> dict[tuple[int, int], str]:
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        (block["text_file"], item["index"]): item["text"]
        for block in document.get("text_files", [])
        for item in block["entries"]
    }


def write_overrides(path: Path, code: str,
                    overrides: dict[tuple[int, int], str]) -> None:
    by_file: dict[int, list[dict]] = {}
    for (text_file, index), text in sorted(overrides.items()):
        by_file.setdefault(text_file, []).append({"index": index, "text": text})
    document = {
        "format": 1,
        "language": code,
        "source": f"{code}_msg.txt",
        "text_files": [
            {"text_file": text_file, "entries": entries}
            for text_file, entries in sorted(by_file.items())
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sync_messages(repo: Path, kit: Path) -> int:
    state_path = kit / "mobile/assets/text/msg-sync-state.json"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists() else {"format": 1, "entries": {}}
    )
    initialized = not state_path.exists()
    changed = 0
    for code in LANGUAGES:
        groups = parse_script(kit / f"{code}_msg.txt")
        prior = state["entries"].setdefault(code, {})
        override_path = kit / f"mobile/assets/text/{code}.msg-overrides.json"
        overrides = load_overrides(override_path)
        for text_file, entries in enumerate(groups):
            for index, text in enumerate(entries):
                key = f"{text_file}:{index}"
                current = entry_hash(text)
                if not initialized and prior.get(key) != current:
                    overrides[(text_file, index)] = text
                    changed += 1
                prior[key] = current
        write_overrides(override_path, code, overrides)
        copy_changed(
            override_path,
            repo / f"assets/localization/mobile/text-overrides/{code}.json",
        )
    state_path.write_text(
        json.dumps(state, indent=2) + "\n", encoding="utf-8"
    )
    return changed


def sync_graphics(repo: Path, kit: Path) -> tuple[int, int]:
    source_root = kit / "mobile/assets/graphics"
    targets = (
        repo / "assets/localization/mobile/graphics",
        repo / "EUR_Language_Build/mobile/assets/graphics",
    )
    copied = converted = 0
    # This old name describes an unrelated 24x26 Archive 1 texture. Remove
    # stale copies from every mirror so refresh-assets cannot resurrect them.
    for root in (source_root, *targets):
        for language in GRAPHIC_LANGUAGES:
            obsolete = root / language / OBSOLETE_BATTLE_DEMO
            if obsolete.exists():
                obsolete.unlink()
    # Resource 55 remains a high-quality editable JPEG in the kit. Generate a
    # separate ROM-ready 4:2:0 copy for each build mirror; never overwrite and
    # repeatedly recompress the editable source.
    for language in GRAPHIC_LANGUAGES:
        source = source_root / language / BATTLE_DEMO
        if not source.exists():
            continue
        encoded = battle_demo_jpeg(source, language)
        destinations = [
            *(target / language / BATTLE_DEMO for target in targets),
            repo.parent / f"NewGraphics/mobile/assets/graphics/{language}/{BATTLE_DEMO}",
        ]
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists() or destination.read_bytes() != encoded:
                destination.write_bytes(encoded)
                converted += 1
    for source in source_root.rglob("*"):
        if not source.is_file() or source.name == BATTLE_DEMO:
            continue
        relative = source.relative_to(source_root)
        for target_root in targets:
            copied += copy_changed(source, target_root / relative)
    return copied, converted


def sync_mobile_json(repo: Path, kit: Path) -> int:
    """Make the localization kit's editable Mobile JSON build-authoritative."""
    copied = 0
    for code in TEXT_LANGUAGES:
        source = kit / f"mobile/assets/text/{code}.json"
        if source.exists():
            # Parse before copying so a malformed editor save cannot silently
            # replace the last buildable localization.
            json.loads(source.read_text(encoding="utf-8"))
            copied += copy_content_changed(
                source, repo / f"assets/localization/mobile/text/{code}.json"
            )
    return copied


def sync_mobile_controller_sources(repo: Path, kit: Path) -> int:
    """Keep every portable build mirror on the authoritative controller."""
    copied = 0
    files = (
        "include/mobile_stadium.h",
        "src/mobile_stadium_overlay.c",
        "src/mobile_stadium_command.s",
    )
    for relative in files:
        source = repo / relative
        for root in (kit, repo / "EUR_Language_Build/mobile"):
            copied += copy_content_changed(source, root / relative)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    kit = repo / "stadium2-localization-kit"
    # Older portable kits stored assets directly below ``assets``; current
    # kits use ``mobile/assets``.  Accept the former and normalize it so the
    # rest of the build has one repeatable layout.
    if not (kit / "mobile/assets").is_dir() and (kit / "assets").is_dir():
        (kit / "mobile").mkdir(parents=True, exist_ok=True)
        shutil.copytree(kit / "assets", kit / "mobile/assets", dirs_exist_ok=True)
    copied, converted = sync_graphics(repo, kit)
    copied += sync_mobile_controller_sources(repo, kit)
    copied += sync_mobile_json(repo, kit)
    message_changes = sync_messages(repo, kit)
    builds = kit / "mobile/assets/builds.json"
    if builds.exists():
        copied += copy_changed(
            builds, repo / "assets/localization/mobile/builds.json"
        )
    print(
        f"Localization-kit sync: {copied} files copied, "
        f"{converted} localized JPEG outputs regenerated, "
        f"{message_changes} message overrides updated"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
