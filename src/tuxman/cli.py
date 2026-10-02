from __future__ import annotations

import argparse
import os
import posixpath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


class ConversionError(RuntimeError):
    """An input package cannot be converted safely."""


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    """Extract a package tarball while rejecting paths outside destination."""
    destination = destination.resolve()
    with tarfile.open(archive, "r:*") as stream:
        for member in stream.getmembers():
            member_path = PurePosixPath(member.name)
            target = (destination / member_path).resolve()
            if member_path.is_absolute() or ".." in member_path.parts or (
                target != destination and destination not in target.parents
            ):
                raise ConversionError(f"unsafe path in package: {member.name}")
            if member.issym():
                link = posixpath.normpath(str(member_path.parent / member.linkname))
                if PurePosixPath(member.linkname).is_absolute() or link == ".." or link.startswith("../"):
                    raise ConversionError(f"unsafe link in package: {member.name}")
            elif member.islnk():
                link = posixpath.normpath(member.linkname)
                if PurePosixPath(member.linkname).is_absolute() or link == ".." or link.startswith("../"):
                    raise ConversionError(f"unsafe link in package: {member.name}")
        stream.extractall(destination)


def _extract_deb(package: Path, destination: Path) -> None:
    """Extract the data member from an ar-format Debian package."""
    with package.open("rb") as source:
        if source.read(8) != b"!<arch>\n":
            raise ConversionError(f"{package} is not a Debian ar archive")
        while header := source.read(60):
            if len(header) != 60 or header[58:60] != b"`\n":
                raise ConversionError("invalid ar member header")
            name = header[:16].decode("ascii", "replace").strip().rstrip("/")
            try:
                size = int(header[48:58].decode("ascii").strip())
            except ValueError as exc:
                raise ConversionError("invalid ar member size") from exc
            payload = source.read(size)
            if size % 2:
                source.read(1)
            if name.startswith("data.tar"):
                suffix = name.removeprefix("data.tar") or ".tar"
                with tempfile.NamedTemporaryFile(suffix=suffix) as data_file:
                    data_file.write(payload)
                    data_file.flush()
                    _safe_extract_tar(Path(data_file.name), destination)
                return
    raise ConversionError("Debian package has no data.tar member")


def _extract_rpm(package: Path, destination: Path) -> None:
    rpm2cpio, cpio = shutil.which("rpm2cpio"), shutil.which("cpio")
    if not rpm2cpio or not cpio:
        raise ConversionError("RPM conversion requires both rpm2cpio and cpio")
    first = subprocess.Popen([rpm2cpio, str(package)], stdout=subprocess.PIPE)
    assert first.stdout is not None
    second = subprocess.run(
        [cpio, "-idm", "--quiet", "--no-absolute-filenames"],
        cwd=destination,
        stdin=first.stdout,
        check=False,
    )
    first.stdout.close()
    first_status = first.wait()
    if first_status or second.returncode:
        raise ConversionError("failed to extract RPM payload")


def extract_package(package: Path, destination: Path) -> None:
    suffix = package.suffix.lower()
    if suffix == ".deb":
        _extract_deb(package, destination)
    elif suffix == ".rpm":
        _extract_rpm(package, destination)
    else:
        raise ConversionError("supported package types are .deb and .rpm")


def _entrypoint(root: Path, requested: str | None) -> str:
    if requested:
        candidate = requested.lstrip("/")
        if not (root / candidate).is_file():
            raise ConversionError(f"entrypoint not found in package: {requested}")
        return candidate
    candidates: list[Path] = []
    for folder in ("usr/bin", "usr/local/bin", "bin"):
        directory = root / folder
        if directory.is_dir():
            candidates.extend(p for p in directory.iterdir() if p.is_file() and os.access(p, os.X_OK))
    if len(candidates) != 1:
        names = ", ".join(str(p.relative_to(root)) for p in candidates) or "none"
        raise ConversionError(f"cannot choose an entrypoint ({names}); pass --entrypoint")
    return str(candidates[0].relative_to(root))


def _write_apprun(appdir: Path, entrypoint: str) -> None:
    script = f"""#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PATH="$HERE/usr/bin:$HERE/usr/sbin:$HERE/bin:${{PATH:-}}"
export LD_LIBRARY_PATH="$HERE/usr/lib:$HERE/usr/lib64:$HERE/lib:$HERE/lib64:${{LD_LIBRARY_PATH:-}}"
export XDG_DATA_DIRS="$HERE/usr/share:${{XDG_DATA_DIRS:-/usr/local/share:/usr/share}}"
exec "$HERE/{entrypoint}" "$@"
"""
    target = appdir / "AppRun"
    target.write_text(script)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _link_appimage_metadata(appdir: Path) -> None:
    """Expose packaged desktop metadata at the AppDir root for appimagetool."""
    desktop_dir = appdir / "usr/share/applications"
    desktops = sorted(desktop_dir.glob("*.desktop")) if desktop_dir.is_dir() else []
    if not desktops:
        raise ConversionError("AppImage creation requires a packaged .desktop file")
    desktop = desktops[0]
    (appdir / desktop.name).symlink_to(desktop.relative_to(appdir))

    icon_name = ""
    for line in desktop.read_text(errors="replace").splitlines():
        if line.startswith("Icon="):
            icon_name = line.partition("=")[2].strip()
            break
    if not icon_name or "/" in icon_name:
        return
    icon_root = appdir / "usr/share/icons"
    icons = sorted(icon_root.glob(f"**/{icon_name}.*")) if icon_root.is_dir() else []
    icons = [p for p in icons if p.suffix.lower() in {".png", ".svg", ".xpm"}]
    if icons:
        icon = icons[-1]
        (appdir / f"{icon_name}{icon.suffix}").symlink_to(icon.relative_to(appdir))


def convert(package: Path, output: Path, entrypoint: str | None, appimage: bool) -> Path:
    if not package.is_file():
        raise ConversionError(f"package does not exist: {package}")
    if output.exists():
        raise ConversionError(f"output already exists: {output}")
    output.mkdir(parents=True)
    try:
        extract_package(package, output)
        chosen = _entrypoint(output, entrypoint)
        _write_apprun(output, chosen)
        if appimage:
            tool = shutil.which("appimagetool")
            if not tool:
                raise ConversionError("--appimage requires appimagetool on PATH")
            _link_appimage_metadata(output)
            image = output.with_suffix(".AppImage")
            subprocess.run([tool, str(output), str(image)], check=True)
            return image
        return output
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert a DEB or RPM payload into an AppDir")
    parser.add_argument("package", type=Path, help="input .deb or .rpm package")
    parser.add_argument("-o", "--output", type=Path, help="output AppDir (default: NAME.AppDir)")
    parser.add_argument("-e", "--entrypoint", help="executable path inside the package")
    parser.add_argument("--appimage", action="store_true", help="also run appimagetool")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output or Path(f"{args.package.stem}.AppDir")
    try:
        result = convert(args.package, output, args.entrypoint, args.appimage)
    except (ConversionError, OSError, subprocess.SubprocessError) as exc:
        print(f"tuxman: error: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
