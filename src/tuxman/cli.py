from __future__ import annotations

import argparse
import configparser
import os
import posixpath
import shutil
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


TAR_SUFFIXES = (".pkg.tar.zst", ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tgz", ".tar")
SUPPORTED_SUFFIXES = TAR_SUFFIXES + (".deb", ".rpm", ".zip", ".7z", ".sh")


def required_tools(package: Path) -> list[str]:
    name = package.name.lower()
    if name.endswith(".rpm"):
        return ["rpm2cpio", "cpio"]
    if name.endswith(".zst"):
        return ["zstd"]
    if name.endswith(".7z"):
        return [] if shutil.which("7z") or shutil.which("7zz") else ["7z or 7zz"]
    return []


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
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ConversionError(f"unsupported special file in package: {member.name}")
            if member.issym():
                link = posixpath.normpath(str(member_path.parent / member.linkname))
                if PurePosixPath(member.linkname).is_absolute() or link == ".." or link.startswith("../"):
                    raise ConversionError(f"unsafe link in package: {member.name}")
            elif member.islnk():
                link = posixpath.normpath(member.linkname)
                if PurePosixPath(member.linkname).is_absolute() or link == ".." or link.startswith("../"):
                    raise ConversionError(f"unsafe link in package: {member.name}")
            if member.islnk():
                linked = (destination / member.linkname).resolve()
                if destination not in linked.parents:
                    raise ConversionError(f"unsafe link in package: {member.name}")
            # Validate against already extracted links before each write.
            stream.extract(member, destination, set_attrs=True)


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


def _archive_target(destination: Path, name: str) -> Path:
    path = PurePosixPath(name)
    target = (destination / path).resolve()
    if path.is_absolute() or ".." in path.parts or "\\" in name or (
        target != destination.resolve() and destination.resolve() not in target.parents
    ):
        raise ConversionError(f"unsafe path in archive: {name}")
    return target


def _extract_zip(package: Path, destination: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        for member in archive.infolist():
            target = _archive_target(destination, member.filename)
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ConversionError(f"unsupported link or special file: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod((mode & 0o777) or 0o644)


def _extract_7z(package: Path, destination: Path) -> None:
    tool = shutil.which("7z") or shutil.which("7zz")
    if not tool:
        raise ConversionError("7z archives require 7z or 7zz")
    listing = subprocess.run([tool, "l", "-slt", "--", str(package.resolve())],
                             capture_output=True, text=True, check=True)
    if "----------" not in listing.stdout:
        raise ConversionError("cannot read 7z archive listing")
    entries = []
    names = set()
    for block in listing.stdout.split("----------", 1)[1].strip().split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        name = fields.get("Path")
        if not name:
            continue
        target = _archive_target(destination, name)
        attributes = fields.get("Attributes", "")
        if any("Link" in key for key in fields) or "l" in attributes or "L" in attributes:
            raise ConversionError(f"unsupported link in 7z archive: {name}")
        if name in names or any(char in name for char in "*?[]"):
            raise ConversionError(f"ambiguous 7z member: {name}")
        names.add(name)
        entries.append((name, target, fields.get("Folder") == "+" or attributes.startswith("D"), attributes))
    # Stream members to validated files rather than letting 7z write paths or links.
    for name, target, directory, attributes in entries:
        if directory:
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as output:
            subprocess.run([tool, "x", "-so", "-spd", "--", str(package.resolve()), name],
                           stdout=output, stderr=subprocess.PIPE, check=True)
        target.chmod(0o755 if "x" in attributes else 0o644)


def _extract_zstd(package: Path, destination: Path) -> None:
    tool = shutil.which("zstd")
    if not tool:
        raise ConversionError("Zstandard archives require zstd")
    with tempfile.NamedTemporaryFile(suffix=".tar") as decompressed:
        subprocess.run([tool, "-d", "-q", "-c", "--", str(package)],
                       stdout=decompressed, stderr=subprocess.PIPE, check=True)
        decompressed.flush()
        _safe_extract_tar(Path(decompressed.name), destination)


def _extract_script(package: Path, destination: Path) -> None:
    """Package a standalone script without executing it."""
    script_dir = destination / "usr/bin"
    script_dir.mkdir(parents=True, exist_ok=True)
    target = script_dir / package.name
    content = package.read_bytes()
    if b"\x00" in content:
        raise ConversionError("The .sh input contains binary data; choose a shell script")
    if not content.startswith(b"#!"):
        content = b"#!/bin/sh\n" + content
    target.write_bytes(content)
    target.chmod(0o755)
    desktop = destination / "usr/share/applications/tuxman-script.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text("[Desktop Entry]\nType=Application\nName=" + package.stem.replace("\n", " ").replace("\r", " ") +
                       "\nExec=AppRun\nIcon=tuxman-script\nTerminal=true\nCategories=Utility;\n")
    icon = destination / "usr/share/pixmaps/tuxman-script.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).parent / "assets/io.github.TridentSpoon.TuxMan.png", icon)


def extract_package(package: Path, destination: Path) -> None:
    suffix = package.suffix.lower()
    if suffix == ".sh":
        _extract_script(package, destination)
    elif suffix == ".deb":
        _extract_deb(package, destination)
    elif suffix == ".rpm":
        _extract_rpm(package, destination)
    elif package.name.lower().endswith(".zst"):
        _extract_zstd(package, destination)
    elif suffix == ".zip":
        _extract_zip(package, destination)
    elif suffix == ".7z":
        _extract_7z(package, destination)
    elif package.name.lower().endswith(TAR_SUFFIXES):
        _safe_extract_tar(package, destination)
    else:
        raise ConversionError("supported formats: " + ", ".join(SUPPORTED_SUFFIXES))


def _desktop_entrypoints(root: Path) -> list[Path]:
    """Resolve visible app launchers inside the payload, never on the host."""
    candidates = []
    for desktop in sorted((root / "usr/share/applications").glob("*.desktop")):
        config = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            config.read_string(desktop.read_text())
            section = config["Desktop Entry"]
            if section.get("Type", "Application") != "Application":
                continue
            if section.get("Hidden", "false").lower() == "true" or section.get("NoDisplay", "false").lower() == "true":
                continue
            words = shlex.split(section.get("Exec", ""))
        except (configparser.Error, KeyError, UnicodeError, ValueError):
            continue
        if not words:
            continue
        executable = words[0]
        if executable.startswith("/"):
            paths = [root / executable.lstrip("/")]
        elif "/" not in executable:
            paths = [root / folder / executable for folder in ("usr/bin", "usr/local/bin", "bin")]
        else:
            paths = [root / executable]
        for path in paths:
            resolved = path.resolve()
            if root.resolve() in resolved.parents and path.is_file() and os.access(path, os.X_OK):
                if path not in candidates:
                    candidates.append(path)
                break
    return candidates


def _entrypoint(root: Path, requested: str | None, archive: bool = False) -> str:
    if requested:
        candidate = requested.lstrip("/")
        resolved = (root / candidate).resolve()
        if root.resolve() not in resolved.parents:
            raise ConversionError("entrypoint must stay inside the package")
        if not resolved.is_file():
            raise ConversionError(f"entrypoint not found in package: {requested}")
        return candidate
    candidates = _desktop_entrypoints(root)
    if candidates:
        if len(candidates) == 1:
            return str(candidates[0].relative_to(root))
        names = ", ".join(str(path.relative_to(root)) for path in candidates)
        raise ConversionError(f"multiple application launchers ({names}); set the Executable path field or use --entrypoint")
    for folder in ("usr/bin", "usr/local/bin", "bin"):
        directory = root / folder
        if directory.is_dir():
            candidates.extend(p for p in directory.iterdir() if p.is_file() and os.access(p, os.X_OK))
    if archive and not candidates:
        # Binary tarballs often contain an executable at the root or in one
        # enclosing application directory. Leave that layout intact.
        folders = [root]
        children = list(root.iterdir())
        if len(children) == 1 and children[0].is_dir():
            base = children[0]
            folders.extend([base, base / "bin", base / "usr/bin"])
        for folder in folders:
            if folder.is_dir():
                candidates.extend(p for p in folder.iterdir()
                                  if p.is_file() and os.access(p, os.X_OK))
    if len(candidates) != 1:
        names = ", ".join(str(p.relative_to(root)) for p in candidates) or "none"
        raise ConversionError(f"cannot choose an entrypoint ({names}); set the Executable path field or use --entrypoint")
    return str(candidates[0].relative_to(root))


def _write_apprun(appdir: Path, entrypoint: str) -> None:
    script = f"""#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PATH="$HERE/usr/bin:$HERE/usr/sbin:$HERE/bin:${{PATH:-}}"
export LD_LIBRARY_PATH="$HERE/usr/lib:$HERE/usr/lib64:$HERE/lib:$HERE/lib64:${{LD_LIBRARY_PATH:-}}"
export XDG_DATA_DIRS="$HERE/usr/share:${{XDG_DATA_DIRS:-/usr/local/share:/usr/share}}"
exec "$HERE/"{shlex.quote(entrypoint)} "$@"
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
    visible = []
    for candidate in desktops:
        config = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            config.read_string(candidate.read_text())
            entry = config["Desktop Entry"]
            if entry.get("Hidden", "false").lower() != "true" and entry.get("NoDisplay", "false").lower() != "true":
                visible.append(candidate)
        except (configparser.Error, KeyError, UnicodeError):
            continue
    desktop = (visible or desktops)[0]
    # AppImage desktop integration must call the relocatable launcher.
    content = desktop.read_text(errors="replace")
    lines = []
    main_section = False
    icon_name = ""
    for line in content.splitlines():
        if line.startswith("["):
            main_section = line == "[Desktop Entry]"
        if main_section and line.startswith("Icon="):
            icon_name = line.partition("=")[2].strip()
        if line.startswith("Exec="):
            words = shlex.split(line.partition("=")[2])
            args = " ".join(words[1:])
            line = "Exec=AppRun" + (" " + args if args else "")
        lines.append(line)
    (appdir / desktop.name).write_text("\n".join(lines) + "\n")
    if not icon_name or "/" in icon_name:
        raise ConversionError("AppImage creation requires a named packaged icon in the desktop launcher")
    icons = []
    for folder in ("usr/share/icons", "usr/share/pixmaps"):
        directory = appdir / folder
        if directory.is_dir():
            icons.extend(p for p in directory.rglob("*")
                         if p.is_file() and p.name in {icon_name + ext for ext in (".png", ".svg", ".xpm")})
    if not icons:
        raise ConversionError(f"AppImage icon not found in package: {icon_name}")
    icon = sorted(icons)[-1]
    (appdir / f"{icon_name}{icon.suffix}").symlink_to(icon.relative_to(appdir))


def convert(package: Path, output: Path, entrypoint: str | None, appimage: bool) -> Path:
    if not package.is_file():
        raise ConversionError(f"package does not exist: {package}")
    if output.exists():
        raise ConversionError(f"output already exists: {output}")
    output.mkdir(parents=True)
    try:
        extract_package(package, output)
        chosen = _entrypoint(output, entrypoint, package.name.lower().endswith(TAR_SUFFIXES + (".zip", ".7z")))
        _write_apprun(output, chosen)
        if appimage:
            tool = shutil.which("appimagetool")
            if not tool:
                raise ConversionError("--appimage requires appimagetool on PATH")
            _link_appimage_metadata(output)
            image = output.with_suffix(".AppImage")
            environment = os.environ.copy()
            if package.suffix.lower() == ".sh":
                from .dependencies import architecture
                environment["ARCH"] = architecture()
            result = subprocess.run([tool, str(output), str(image)], capture_output=True, text=True, env=environment)
            if result.returncode:
                details = (result.stderr + "\n" + result.stdout).strip()
                raise ConversionError("appimagetool failed:\n" + (details[-6000:] or f"exit status {result.returncode}"))
            return image
        return output
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def package_name(package: Path) -> str:
    """Remove the full supported archive extension for output naming."""
    for suffix in SUPPORTED_SUFFIXES:
        if package.name.lower().endswith(suffix):
            return package.name[:-len(suffix)]
    return package.stem


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert a Linux package or application archive into an AppDir")
    parser.add_argument("package", type=Path, help="input package/archive (" + ", ".join(SUPPORTED_SUFFIXES) + ")")
    parser.add_argument("-o", "--output", type=Path, help="output AppDir (default: NAME.AppDir)")
    parser.add_argument("-e", "--entrypoint", help="executable path inside the package")
    formats = parser.add_mutually_exclusive_group()
    formats.add_argument("--appimage", dest="appimage", action="store_true", help="create an AppImage (default)")
    formats.add_argument("--appdir", dest="appimage", action="store_false", help="create only an AppDir")
    parser.set_defaults(appimage=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output or Path(f"{package_name(args.package)}.AppDir")
    try:
        result = convert(args.package, output, args.entrypoint, args.appimage)
    except (ConversionError, OSError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"tuxman: error: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
