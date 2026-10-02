# TuxMan

TuxMan extracts the application payload from a Debian (`.deb`) or RPM (`.rpm`)
package and arranges it as an [AppDir](https://docs.appimage.org/reference/appdir.html).
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
tuxman vendor-package.deb --entrypoint usr/bin/vendor-app
./vendor-package.AppDir/AppRun
```

If exactly one executable exists under `usr/bin`, `usr/local/bin`, or `bin`, TuxMan
selects it automatically. Otherwise, pass its package-relative path explicitly:

```sh
tuxman package.rpm -o MyApp.AppDir -e usr/bin/my-app
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

