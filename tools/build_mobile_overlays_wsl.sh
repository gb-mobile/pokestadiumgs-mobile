#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
input_root="$(dirname "$repo")"
tool_root="$repo/build/mobile-stadium/tool"
success_marker="$tool_root/.overlay-build-ok"
toolchain="$tool_root/mips-binutils"
libdir="$toolchain/usr/lib/x86_64-linux-gnu"

mkdir -p "$tool_root"
rm -f "$success_marker"
if [[ ! -x "$toolchain/usr/bin/mips-linux-gnu-ld.bfd" ]]; then
    cd "$tool_root"
    apt download binutils-mips-linux-gnu
    mkdir -p "$toolchain"
    dpkg-deb -x binutils-mips-linux-gnu_*.deb "$toolchain"
fi

export PATH="$toolchain/usr/bin:$PATH"
export LD_LIBRARY_PATH="$libdir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cd "$repo"
for code in "$@"; do
    # AU shares the PAL English compiled controller; only profile data differs.
    if [[ "$code" == "au" ]]; then
        mkdir -p "$tool_root/au"
        cp "$tool_root/en/mobile.o" "$tool_root/au/mobile.o"
    fi
    python3 tools/mobile_stadium.py build \
        --repo . --input-root "$input_root" --languages "$code"
    test "$tool_root/$code/mobile.o" -nt src/mobile_stadium_overlay.c
    test "$tool_root/$code/mobile.raw.bin" -nt src/mobile_stadium_overlay.c
done

# The Windows wrapper checks this marker because some WSL launch failures have
# been observed to return a successful process status while producing no build.
# Record the authoritative source hashes as well, so a later compose cannot
# mistake manually patched or stale objects for a coherent overlay build.
{
    printf 'mobile overlay build completed\n'
    sha256sum src/mobile_stadium_overlay.c src/mobile_stadium_command.s tools/mobile_stadium.py
} > "$success_marker"
