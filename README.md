# Pokemon Stadium 2 (US)
A WIP decomp of Pokemon Stadium 2 (US/JP).

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
