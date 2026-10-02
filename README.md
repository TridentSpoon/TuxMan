# TuxMan

TuxMan extracts the application payload from a Debian (`.deb`) or RPM (`.rpm`)
package or tar archive (`.tar`, `.tar.gz`) and arranges it as an [AppDir](https://docs.appimage.org/reference/appdir.html).
It can optionally invoke `appimagetool` to produce an AppImage.

## Is universal package conversion possible?

Not completely. DEB and RPM files are archives, not compiled formats that can be
“decompiled” and recompiled for another distribution. A graphical, self-contained
application can often be repackaged, but conversion cannot fix:

- a binary built for the wrong CPU or an incompatible libc/kernel;
- libraries that the original package declared as external dependencies;
- install scripts, systemd services, kernel modules, drivers, or privileged setup;
- hard-coded paths, distro-specific integrations, licensing, or signature trust.

TuxMan deliberately extracts only the package payload. It **does not run maintainer
scripts as root**. The generated launcher searches bundled binaries, libraries, and
data first. Validate licensing and test the result on every target distribution.
For complex applications, rebuilding from source or using Flatpak is usually more
reliable.

## Install and use

Python 3.10 or newer is required. RPM inputs additionally require `rpm2cpio` and
`cpio`; DEB extraction is implemented directly in Python.

```sh
python -m pip install .
tuxman vendor-package.deb --entrypoint usr/bin/vendor-app --appdir
./vendor-package.AppDir/AppRun
```

If exactly one executable exists under `usr/bin`, `usr/local/bin`, or `bin`, TuxMan
selects it automatically. Otherwise, pass its package-relative path explicitly:

```sh
tuxman package.rpm -o MyApp.AppDir -e usr/bin/my-app --appdir
```

To build an AppImage, install `appimagetool` and run:

```sh
tuxman package.deb -e usr/bin/my-app --appimage
```

`appimagetool` may also require desktop metadata and an icon for a production-grade
AppImage; those assets should normally already be present in the source package.

## Development

```sh
python -m pip install -e '.[test]'
pytest
```


## Desktop app

TuxMan includes a native GTK4/libadwaita interface, following the same toolkit
as Set the Table and N-Able Tux Control. Choose your package and destination,
name the bundle, select AppDir or AppImage, and click **Create bundle**.
Conversion runs in the background. Errors appear in the window; existing
outputs are preserved. **Open output folder** reveals a successful result.
The GUI does not launch converted applications automatically.

Install the GUI dependencies:

- Arch/CachyOS: `sudo pacman -S python-gobject gtk4 libadwaita`
- Debian/Ubuntu: `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`
- Fedora: `sudo dnf install python3-gobject gtk4 libadwaita`

From this checkout, run `./install.sh` to add TuxMan to your application menu.
Re-run it after updating the checkout. Alternatively, after installing the
Python package, run `tuxman-gui`, or use `PYTHONPATH=src python3 -m tuxman.gui`
directly from the checkout. PyGObject comes from your distro; a virtual
environment must have access to those system packages to run the GUI.

The existing `tuxman` command remains available for terminal workflows.

The header menu includes **About TuxMan**, with the version, full MIT license,
project link, and a **Check** button for updates. Checks run in the background
only on request. When a newer GitHub release exists, open its download page
and run the new checkout’s `install.sh` to update. No automatic installation
is performed.

## Tar archives

Select `.tar` or `.tar.gz` in the desktop app, or run:

```sh
tuxman MyApp.tar.gz
tuxman MyApp.tar -e MyApp/bin/my-app --appimage
```

Binary archives can keep their enclosing app folder. TuxMan checks the usual
`usr/bin`, `usr/local/bin`, and `bin` locations first, then executables at the
archive root or in a single enclosing folder (including its `bin` and `usr/bin`).
If there are multiple candidates, enter the executable path relative to the
archive root. Source archives must be built separately before conversion.
Tar extraction rejects escaping paths/links and special device files.
AppImage creation still needs the desktop metadata under
`usr/share/applications` and appimagetool; tar archives without that metadata
can be converted to AppDir instead.

## Supported input formats

| Format | Typical use | Requirements |
| --- | --- | --- |
| `.deb` | Debian/Ubuntu packages | Python |
| `.rpm` | Fedora/RHEL/openSUSE packages | `rpm2cpio`, `cpio` |
| `.tar`, `.tar.gz`, `.tgz`, `.tar.xz`, `.tar.bz2` | Binary or source archives | Python |
| `.tar.zst` | Zstandard-compressed tar archives | `zstd` |
| `.pkg.tar.zst` | Arch/CachyOS package payloads | `zstd` |
| `.zip` | General archives and portable apps | Python |
| `.7z` | 7-Zip archives | `7z` or `7zz` |

All formats share the AppDir/AppImage workflow and optional executable path.
ZIP archives preserve stored Unix executable permissions; if those permissions
are absent, automatic discovery cannot identify the executable. 7z links and
ambiguous member names are rejected; regular members are streamed into validated
paths. Extraction does not execute Arch install hooks or compile source code.

These capabilities broaden input support, but do not guarantee compatibility
on every distro. Missing libraries are not downloaded or bundled automatically,
and CPU/libc requirements still apply. AppImage output continues to require
packaged desktop metadata and `appimagetool`.

## Conversion tool installation

The **Dependencies** window in the top menu checks appimagetool, rpm2cpio, cpio, zstd, and
7z/7zz on startup and with **Check again**. Each missing tool’s **Click here to install** button shows
the proposed package-manager command before starting. System packages are
installed using a graphical polkit password prompt on Arch/CachyOS, Debian/Ubuntu,
Fedora/RHEL, and openSUSE families. Other distros receive manual-install guidance.
Package availability depends on the distro release and enabled repositories.

appimagetool is downloaded from the official AppImage/appimagetool release for
the machine architecture, checked against the release's SHA-256 digest, and
installed under `~/.local/share/tuxman/tools` with a launcher in `~/.local/bin`.
Its launcher uses extract-and-run mode so generating images does not require FUSE.
Installation runs in the background; tools are checked again afterwards.
No packages are installed or downloads started until you confirm Install.

Executable discovery first reads visible packaged desktop launchers under
`usr/share/applications`, including absolute paths such as
`/usr/share/cursor/cursor` or `/opt/app/app`. Paths are resolved inside the
bundle, never against installed host applications. Hidden URL handlers are
ignored. Multiple distinct launchers require an explicit executable choice.

## Shell scripts and default output

AppImage is now the default in the desktop app and CLI. Choose AppDir in the
output selector or pass `--appdir` for an unpacked bundle.

Standalone `.sh` files are supported. TuxMan copies the script, makes it
executable, and generates desktop metadata and an icon. Scripts without a
shebang receive `#!/bin/sh`; existing shebangs are preserved. Conversion never
runs the script. Launching the resulting bundle runs it normally, including
any installation actions the original script contains. Script interpreters,
external commands, companion files, and downloaded resources are not bundled
or installed automatically, so select a self-contained script.

Open **☰ → Dependencies** to check tools. Installed tools show a green tick;
missing tools show a red cross and **Click here to install**. The **Check**
button refreshes the list, and installation checks it again automatically.
