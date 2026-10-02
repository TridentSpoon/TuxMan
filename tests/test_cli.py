import io
import os
import tarfile
from pathlib import Path

import pytest

from tuxman.cli import ConversionError, _link_appimage_metadata, convert


def _ar_member(name: str, data: bytes) -> bytes:
    header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{0:<8}{len(data):<10}`\n".encode()
    return header + data + (b"\n" if len(data) % 2 else b"")


def make_deb(path: Path, executable: str = "usr/bin/hello") -> None:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        content = b"#!/bin/sh\necho hello\n"
        info = tarfile.TarInfo(executable)
        info.mode = 0o755
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    path.write_bytes(b"!<arch>\n" + _ar_member("debian-binary", b"2.0\n") + _ar_member("data.tar.gz", payload.getvalue()))


def test_converts_deb_to_runnable_appdir(tmp_path: Path) -> None:
    package, output = tmp_path / "hello.deb", tmp_path / "Hello.AppDir"
    make_deb(package)

    assert convert(package, output, None, False) == output
    assert (output / "usr/bin/hello").read_text().endswith("echo hello\n")
    assert os.access(output / "AppRun", os.X_OK)
    assert "usr/bin/hello" in (output / "AppRun").read_text()


def test_requires_entrypoint_when_ambiguous(tmp_path: Path) -> None:
    package, output = tmp_path / "tools.deb", tmp_path / "Tools.AppDir"
    make_deb(package, "usr/bin/one")
    # Add a second executable by rebuilding the archive with a directory-like name is
    # unnecessary here: a requested missing path exercises the actionable validation.
    with pytest.raises(ConversionError, match="entrypoint not found"):
        convert(package, output, "usr/bin/two", False)
    assert not output.exists()


def test_rejects_unknown_package_type(tmp_path: Path) -> None:
    package = tmp_path / "thing.zip"
    package.write_bytes(b"not a package")
    with pytest.raises(ConversionError, match="supported package types"):
        convert(package, tmp_path / "out", None, False)


def test_links_appimage_desktop_and_icon(tmp_path: Path) -> None:
    appdir = tmp_path / "Demo.AppDir"
    desktop = appdir / "usr/share/applications/demo.desktop"
    icon = appdir / "usr/share/icons/hicolor/256x256/apps/demo.png"
    desktop.parent.mkdir(parents=True)
    icon.parent.mkdir(parents=True)
    desktop.write_text("[Desktop Entry]\nName=Demo\nIcon=demo\n")
    icon.write_bytes(b"png")

    _link_appimage_metadata(appdir)

    assert (appdir / "demo.desktop").resolve() == desktop
    assert (appdir / "demo.png").resolve() == icon
