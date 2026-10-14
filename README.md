# Pokemon Stadium 2 (US)
A WIP decomp of Pokemon Stadium 2 (US/JP).

Parts of this fork's Mobile Stadium modifications were developed with assistance from AI tools, including code generation. This note applies to our modifications, not to the upstream project.

## Upstream decomp reference

The following retail reconstruction instructions are inherited from upstream.
They are not the Mobile Stadium workflow; do not run `make init` in a populated
Mobile Stadium workspace. See [Mobile Stadium maintenance](#mobile-stadium-maintenance).

It builds the following ROMs:

* pokestadiumgs-us.z64: `md5: 1561c75d11cedf356a8ddb1a4a5f9d5d`
* pokestadiumgs-jp.z64: `md5: a17aadcc962393d476edc321e59c504b`

Note: To use this repository, you must already have a rom for the game.

# Prerequisites

Under Debian / Ubuntu (which we recommend using), you can install them with the following commands:

```bash
sudo apt update
sudo apt install make git build-essential binutils-mips-linux-gnu python3 python3-pip python3-venv
```

**Please also ensure that the Python version installed is >3.7.**

The build process has a few python packages required that are located in `requirements.txt`.

To install them simply run in a terminal:

```bash
python3 -m pip install -r requirements.txt
```
### libjpeg-turbo 3.1.2 (`turbojpeg` library)

`tools/sync_localization_kit.py` uses libjpeg-turbo 3.x to convert the Battle Data
help screenshot (`archive13_055_jpeg_144x96.jpg`) to the baseline JPEG layout
Stadium expects. It looks for the library at `tools/bin/turbojpeg.dll` (create the
`tools/bin` folder if it does not exist). The path is the same on Windows and Linux.

**Windows**
1. Download `libjpeg-turbo-3.1.2-vc-x64.exe` from
   https://github.com/libjpeg-turbo/libjpeg-turbo/releases/tag/3.1.2 and run it
   (default folder: `C:\libjpeg-turbo64`).
2. Copy `C:\libjpeg-turbo64\bin\turbojpeg.dll` to `tools\bin\turbojpeg.dll`.

**Linux x86-64 (official .deb, no root needed)**
```sh
mkdir -p tools/bin /tmp/ljt
wget https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.1.2/libjpeg-turbo-official_3.1.2_amd64.deb
dpkg-deb -x libjpeg-turbo-official_3.1.2_amd64.deb /tmp/ljt
cp /tmp/ljt/opt/libjpeg-turbo/lib64/libturbojpeg.so.0.4.0 tools/bin/turbojpeg.dll
```
The file keeps the `.dll` name on Linux on purpose; the loader ignores the extension.
Other CPUs or distributions: build libjpeg-turbo 3.1.2 from source and copy its
`libturbojpeg.so` the same way.

# To use
1. Place the US Pokemon Stadium 2 (US/JP) rom into the repository's "/baseroms/VERSION/" folder as "baserom.z64". `VERSION` can be `us` or `jp`
2. Set up tools and extract the rom: `VERSION=us make init`
3. Re-assemble the rom: `make`

For contacts and other pret projects, see [pret.github.io](https://pret.github.io/).

## Mobile Stadium maintenance

This fork restores the Japanese Mobile Stadium controller in seven international
variants: US English, PAL English, Australian English, French, German, Italian
and Spanish. REON consumes these builds; its service/deployment is a separate
project. Builds and static checks do not establish execution on a real N64.
The final Italian/German Organize Data layout and main-menu cursor cadence still
require recorded console validation. Only Italian/German use the corrected bar;
the other variants retain retail geometry.

### Repository map and architecture

| Responsibility | Existing location |
| --- | --- |
| Agent entry point | [AGENTS.md](AGENTS.md); shared project-base/skills remain in OMM |
| Human and technical documentation | This README; existing upstream library/tool READMEs remain in place |
| Controller and command code | [src/mobile_stadium_overlay.c](src/mobile_stadium_overlay.c), [src/mobile_stadium_command.s](src/mobile_stadium_command.s), [include/mobile_stadium.h](include/mobile_stadium.h) |
| Regional signatures, profiles and patches | [tools/mobile_stadium.py](tools/mobile_stadium.py) |
| Localization, synchronization and packaging | [tools/localization.py](tools/localization.py), [tools/sync_localization_kit.py](tools/sync_localization_kit.py), [tools/package_mobile_stadium.py](tools/package_mobile_stadium.py) |
| Build and existing validations | [Makefile](Makefile), [Build Mobile Stadium ROMs.bat](Build%20Mobile%20Stadium%20ROMs.bat), [tools/build_mobile_overlays_wsl.sh](tools/build_mobile_overlays_wsl.sh), [tools/verify_mobile_interop.py](tools/verify_mobile_interop.py) |
| Authored inputs and generated outputs | `stadium2-localization-kit/` is editable input; `assets/localization/` and graphics mirrors are generated; `build/` also holds accepted/reference outputs |
| Planning and continuity | Canonical OMM scope `mobile-stadium`, with version-checked handoff; any `OMM.md`/`memory/` is legacy |

The international executables retain support routines/fragments but omit the
Japanese state-18 controller. The C/assembly overlay restores that controller;
regional profiles resolve retained routine addresses, and signature-checked
patches connect the overlay, cartridge/save handling, text and graphics. Do not
substitute guessed offsets. No source or upstream directories are relocated.

### Build inputs and workflow

Use legally supplied, immutable regional ROM inputs. `--input-root` defaults to
the repository's parent directory; the exact filenames are:

| Variant | Source filename |
| --- | --- |
| US | `Pokemon Stadium 2 (English).n64` |
| PAL English / Australian | `Pokemon Stadium 2 (Europe).z64` |
| French / German / Italian / Spanish | `Pokemon Stadium 2 (France).n64`, `Pokemon Stadium 2 (Germany).n64`, `Pokemon Stadium 2 (Italy).n64`, `Pokemon Stadium 2 (Spain).n64` |
| Japanese graphics reference | `Pokemon Stadium Kin Gin (Japan).z64` |

Byte order is detected from the header, not the extension. The Japanese ROM is
an input to graphics export; this pipeline does not produce a Japanese build.
Preserve the regional text/graphics kit: ignored assets can be authored inputs,
and a source-only checkout does not prove that every required input is present.

The Windows entry point is `Build Mobile Stadium ROMs.bat`, run in the repository
with Python, the listed requirements, WSL/MIPS build tools and libjpeg-turbo 3.x
available (`tools/bin/turbojpeg.dll` is also the loader filename on Linux).
Its pipeline extracts localization, prepares regional profiles, synchronizes the
kit, compiles overlays, refreshes patch specifications and composes the seven
ROMs under `build/mobile-stadium/`; the final stage packages the minimal overlay.
On a fresh workspace, graphics exports must exist before kit synchronization and
composition. The local Gitea batch-script fix for that ordering remains a separate
pending change; this documentation does not publish it or certify a fresh build.
Do not use the upstream `make init` recipe for this workflow: it invokes
`distclean` and removes extracted assets.

Existing verification entry points, for an authorized validation task:

```sh
python3 tools/verify_mobile_interop.py --repo . --source-only
python3 tools/verify_mobile_interop.py --repo . --crystal-save /path/to/crystal.sav --n64-save /path/to/stadium.sav
```

The first checks the source contract. The second also needs the selected saves,
composed ROMs and synchronized metadata/mirrors; it is not a hardware test.
Record revision, region, input/output hashes and environment for reproducibility.
N64 observations must name the tested ROM/hash and report each checklist result;
for cursor cadence, capture at 60 fps and count each cycle separately (24 recorded
frames slow, 12 fast). These checks do not substitute for console observations.

### Artifact retention

At task/checkpoint/pause closure, inspect exact paths and active processes before
cleanup. Ignored/untracked status does not establish dispensability.

| Category | Retention / cleanup condition |
| --- | --- |
| Build objects, overlay intermediates and caches | Generated by Makefile/tools; remove only known inactive task intermediates after preserving commands, environment, hashes and relevant results. Do not purge all of `build/`. |
| Extracted localization and generated mirrors | Reproduce only with retained regional ROMs, authored kit and matching tools. Check actual readers/inputs first; keep while validation or decisions remain pending. |
| Logs and test captures/results | Retain summaries and cases required to reproduce/review results. Keep unexplained failures and unique console/hardware evidence; remove only known duplicates after durable retention. |
| Original/base ROMs, saves, fixtures, authored text/graphics and accepted releases | Excluded from automatic cleanup, including operator files outside the checkout. Never store their only reference in `/tmp`. |
| Git preparation/verification clones | Remove only clean, inactive task-owned duplicates after verifying complete original/published recovery bundles. Keep bundles, commit maps, patches, inventories and checksums while old clones/pins or recovery still need them; no automatic expiry. |

`make clean` removes the whole configured build directory. `make distclean` and
`make init` also reach extracted assets and are unsuitable for automatic Mobile
Stadium cleanup. No generic cleanup command is added. Use an inspected explicit
path list, never `git clean -xfd`, broad globs or age-based deletion. Preserve
unknown paths, another session's files and active outputs until classified.
Record removals, retained locations, size when easy to measure, pending questions
and resumption conditions in the OMM handoff.
