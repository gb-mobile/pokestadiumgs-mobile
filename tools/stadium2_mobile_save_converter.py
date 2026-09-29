#!/usr/bin/env python3
"""Convert retail Pokemon Stadium 2 FlashRAM saves to Mobile Stadium saves."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

import mobile_save


REGIONS = {
    "us": ("English (USA / NTSC)", "USA_Mobile.fla"),
    "en": ("English (Europe / PAL)", "ENG_PAL_Mobile.fla"),
    "au": ("English (Australia / PAL)", "AUS_Mobile.fla"),
    "fr": ("French (PAL)", "FRA_Mobile.fla"),
    "de": ("German (PAL)", "GER_Mobile.fla"),
    "it": ("Italian (PAL)", "ITA_Mobile.fla"),
    "es": ("Spanish (PAL)", "SPA_Mobile.fla"),
}

OUTPUT_FORMATS = {
    "Project64 FlashRAM (.fla)": ("FLA", ".fla"),
    "N64 FlashRAM (.sav)": ("SAV", ".sav"),
    "Libretro save container (.srm)": ("SRM", ".srm"),
}

# N64 cores commonly store every possible cartridge/controller save device in
# one .srm container. Pokemon Stadium 2's 128 KiB FlashRAM is the final region
# in this layout; preceding regions may contain unrelated user progress.
SRM_SIZE = 0x48800
SRM_FLASH_OFFSET = 0x28800


def read_flashram(input_path: Path) -> tuple[bytes, bytearray | None, str]:
    """Return raw FlashRAM plus an optional containing .srm image."""
    raw = input_path.read_bytes()
    if len(raw) in (mobile_save.FLASH_SIZE, mobile_save.FLASH_SIZE - 0x100):
        kind = "SAV" if input_path.suffix.lower() == ".sav" else "FLA"
        return raw, None, kind
    if len(raw) == SRM_SIZE:
        flash = raw[SRM_FLASH_OFFSET:SRM_FLASH_OFFSET + mobile_save.FLASH_SIZE]
        mobile_save.decode_save(flash, f"{input_path} embedded FlashRAM")
        return flash, bytearray(raw), "SRM"
    raise ValueError(
        f"{input_path}: expected a 0x1FF00/0x20000-byte .fla/.sav or "
        f"0x{SRM_SIZE:X}-byte .srm, got 0x{len(raw):X}"
    )


def output_kind(output_path: Path) -> str:
    suffix = output_path.suffix.lower()
    if suffix == ".fla":
        return "FLA"
    if suffix == ".sav":
        return "SAV"
    if suffix == ".srm":
        return "SRM"
    raise ValueError("Output filename must end in .fla, .sav, or .srm")


def convert_save(input_path: Path, output_path: Path) -> dict[str, object]:
    """Set the persistent Mobile Stadium appearance bit and repair checksum.

    Stadium 2's FlashRAM structure is shared by all western languages.  The
    language selection controls output naming only; no localized save data is
    replaced.  The first-time notification bit is deliberately left alone so
    a newly converted save still plays the proper unlock animation.
    """
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if input_path == output_path:
        raise ValueError("Output must be different from the input save")

    raw_flash, srm_container, input_kind = read_flashram(input_path)
    decoded, byte_order = mobile_save.decode_save(raw_flash, str(input_path))
    original_bank = decoded[:mobile_save.BANK_SIZE]
    patched_bank = bytearray(original_bank)

    appearance_offset = mobile_save.MOBILE_APPEARANCE_OFFSET
    appearance = int.from_bytes(
        patched_bank[appearance_offset:appearance_offset + 2], "big"
    )
    patched_bank[appearance_offset:appearance_offset + 2] = (
        appearance | mobile_save.MOBILE_APPEARANCE_MASK
    ).to_bytes(2, "big")

    record_offset, record_size = mobile_save.RECORDS[0x14]
    record = patched_bank[record_offset:record_offset + record_size]
    record[-2:] = (sum(record[:-2]) & 0xFFFF).to_bytes(2, "big")
    patched_bank[record_offset:record_offset + record_size] = record
    mobile_save.validate_bank(patched_bank, str(output_path))

    changed = [
        index for index, (before, after) in enumerate(zip(original_bank, patched_bank))
        if before != after
    ]
    allowed = {
        appearance_offset,
        appearance_offset + 1,
        record_offset + record_size - 2,
        record_offset + record_size - 1,
    }
    if any(index not in allowed for index in changed):
        raise AssertionError("Conversion changed data outside record 0x14")

    target_kind = output_kind(output_path)
    if target_kind == "FLA":
        # Preserve standalone FLA byte order/length. Cross-format extraction
        # emits Project64's conventional word-swapped full FlashRAM image.
        fla_order = byte_order if input_kind == "FLA" else "Project64 word-swapped"
        output = mobile_save.encode_save(bytes(patched_bank) * 2, fla_order)
        if input_kind == "FLA" and len(raw_flash) == mobile_save.FLASH_SIZE - 0x100:
            output = output[:-0x100]
    elif target_kind == "SAV":
        # N64 .sav files are canonical big-endian, as used by the supplied
        # pokestadiumgs-ntsc-en.sav fixture.
        output = mobile_save.encode_save(bytes(patched_bank) * 2, "big-endian")
    else:
        if srm_container is None:
            srm_container = bytearray(b"\xFF" * SRM_SIZE)
        embedded_flash = mobile_save.encode_save(
            bytes(patched_bank) * 2, "Project64 word-swapped"
        )
        srm_container[
            SRM_FLASH_OFFSET:SRM_FLASH_OFFSET + mobile_save.FLASH_SIZE
        ] = embedded_flash
        output = bytes(srm_container)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=output_path.name + ".", suffix=".tmp", dir=output_path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(output)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, output_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    return {
        "byte_order": byte_order,
        "input_kind": input_kind,
        "output_kind": target_kind,
        "size": len(output),
        "sha256": hashlib.sha256(output).hexdigest().upper(),
        "changed_offsets": changed,
    }


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Pokemon Stadium 2 Mobile Save Converter")
    root.resizable(False, False)

    region_by_label = {label: code for code, (label, _) in REGIONS.items()}
    region_var = tk.StringVar(value=REGIONS["us"][0])
    format_var = tk.StringVar(value=next(iter(OUTPUT_FORMATS)))
    input_var = tk.StringVar()
    output_var = tk.StringVar()
    status_var = tk.StringVar(value="Select a retail Stadium 2 .fla, .sav, or .srm save.")

    frame = ttk.Frame(root, padding=14)
    frame.grid(sticky="nsew")
    ttk.Label(frame, text="Language / build:").grid(row=0, column=0, sticky="w")
    region_box = ttk.Combobox(
        frame, textvariable=region_var, values=list(region_by_label),
        state="readonly", width=34,
    )
    region_box.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0))

    ttk.Label(frame, text="Output format:").grid(row=1, column=0, sticky="w", pady=(8, 0))
    format_box = ttk.Combobox(
        frame, textvariable=format_var, values=list(OUTPUT_FORMATS),
        state="readonly", width=34,
    )
    format_box.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0))

    ttk.Label(frame, text="Retail save:").grid(row=2, column=0, sticky="w", pady=(10, 0))
    ttk.Entry(frame, textvariable=input_var, width=58).grid(
        row=2, column=1, sticky="ew", padx=8, pady=(10, 0)
    )
    ttk.Label(frame, text="Mobile save:").grid(row=3, column=0, sticky="w", pady=(8, 0))
    ttk.Entry(frame, textvariable=output_var, width=58).grid(
        row=3, column=1, sticky="ew", padx=8, pady=(8, 0)
    )

    def selected_suffix() -> str:
        return OUTPUT_FORMATS[format_var.get()][1]

    def suggested_name(suffix: str | None = None) -> str:
        code = region_by_label[region_var.get()]
        if suffix is None:
            suffix = selected_suffix()
        return str(Path(REGIONS[code][1]).with_suffix(suffix))

    def browse_input() -> None:
        selected = filedialog.askopenfilename(
            title="Select retail Pokemon Stadium 2 save",
            filetypes=(("Stadium 2 saves", "*.fla *.sav *.srm"),
                       ("Project64 FlashRAM save", "*.fla"),
                       ("N64 FlashRAM save", "*.sav"),
                       ("N64 SRM container", "*.srm"), ("All files", "*.*")),
        )
        if not selected:
            return
        input_var.set(selected)
        suffix = Path(selected).suffix.lower()
        if suffix not in (".fla", ".sav", ".srm"):
            suffix = ".fla"
        for label, (_kind, extension) in OUTPUT_FORMATS.items():
            if extension == suffix:
                format_var.set(label)
                break
        output_var.set(str(Path(selected).with_name(suggested_name(suffix))))

    def browse_output() -> None:
        current = Path(output_var.get()) if output_var.get() else Path(suggested_name())
        suffix = current.suffix.lower()
        suffix = selected_suffix()
        current = current.with_suffix(suffix)
        kind = OUTPUT_FORMATS[format_var.get()][0]
        description = {
            "FLA": "Project64 FlashRAM save",
            "SAV": "N64 FlashRAM save",
            "SRM": "Libretro save container",
        }[kind]
        selected = filedialog.asksaveasfilename(
            title="Save converted Mobile Stadium save",
            initialfile=current.name, defaultextension=suffix,
            filetypes=((description, f"*{suffix}"), ("All files", "*.*")),
        )
        if selected:
            output_var.set(selected)

    def region_changed(_event: object = None) -> None:
        if output_var.get():
            current = Path(output_var.get())
            suffix = current.suffix.lower()
            if suffix not in (".fla", ".sav", ".srm"):
                suffix = selected_suffix()
            output_var.set(str(current.with_name(suggested_name(suffix))))

    def format_changed(_event: object = None) -> None:
        if output_var.get():
            output_var.set(str(Path(output_var.get()).with_suffix(selected_suffix())))

    def convert_clicked() -> None:
        try:
            if not input_var.get() or not output_var.get():
                raise ValueError("Select both an input and output save")
            # The format selector is authoritative even if a filename was
            # pasted or manually edited with a different extension.
            output_path = Path(output_var.get()).with_suffix(selected_suffix())
            output_var.set(str(output_path))
            result = convert_save(Path(input_var.get()), output_path)
        except (OSError, ValueError, AssertionError) as error:
            messagebox.showerror("Conversion failed", str(error), parent=root)
            status_var.set("Conversion failed; the input save was not modified.")
            return
        status_var.set(
            f"Created {Path(output_var.get()).name} ({result['size']} bytes, "
            f"{result['input_kind']} to {result['output_kind']})."
        )
        messagebox.showinfo(
            "Conversion complete",
            "Mobile Stadium is enabled. All unrelated save progress was preserved.\n\n"
            f"SHA-256: {result['sha256']}", parent=root,
        )

    ttk.Button(frame, text="Browse...", command=browse_input).grid(row=2, column=2, pady=(10, 0))
    ttk.Button(frame, text="Browse...", command=browse_output).grid(row=3, column=2, pady=(8, 0))
    ttk.Separator(frame).grid(row=4, column=0, columnspan=3, sticky="ew", pady=12)
    ttk.Label(
        frame,
        text=("PAL English and Australian saves use the same internal format.\n"
              ".fla, big-endian .sav, and 0x48800-byte .srm are supported.\n"
              "The original file is never overwritten."),
    ).grid(row=5, column=0, columnspan=2, sticky="w")
    ttk.Button(frame, text="Convert Save", command=convert_clicked).grid(
        row=5, column=2, sticky="e"
    )
    ttk.Label(frame, textvariable=status_var, foreground="#444444").grid(
        row=6, column=0, columnspan=3, sticky="w", pady=(12, 0)
    )
    region_box.bind("<<ComboboxSelected>>", region_changed)
    format_box.bind("<<ComboboxSelected>>", format_changed)
    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--region", choices=tuple(REGIONS), default="us")
    parser.add_argument("--gui", action="store_true")
    args = parser.parse_args()
    if args.gui or (args.input is None and args.output is None):
        run_gui()
        return 0
    if args.input is None or args.output is None:
        parser.error("--input and --output must be supplied together")
    result = convert_save(args.input, args.output)
    print(f"Region: {REGIONS[args.region][0]}")
    print(f"Byte order: {result['byte_order']}")
    print(f"Wrote {args.output} ({result['size']} bytes)")
    print(f"SHA-256: {result['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
