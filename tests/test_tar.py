import io
import subprocess
import tarfile
from pathlib import Path

import pytest
from tuxman.cli import ConversionError, convert, package_name


def archive(path, executable):
    with tarfile.open(path, 'w:gz' if path.name.endswith('.gz') else 'w') as stream:
        data = b'#!/bin/sh\necho hello\n'
        entry = tarfile.TarInfo(executable)
        entry.mode = 0o755
        entry.size = len(data)
        stream.addfile(entry, io.BytesIO(data))


@pytest.mark.parametrize('extension', ['.tar', '.tar.gz'])
@pytest.mark.parametrize('entry', ['usr/bin/hello', 'hello', 'MyApp/hello', 'MyApp/bin/hello'])
def test_binary_tarballs(tmp_path, extension, entry):
    package = tmp_path / ('hello' + extension)
    archive(package, entry)
    output = convert(package, tmp_path / 'Hello.AppDir', None, False)
    assert subprocess.check_output([str(output / 'AppRun')], text=True).strip() == 'hello'
    assert package_name(package) == 'hello'


def test_explicit_quoted_path(tmp_path):
    package = tmp_path / 'app.tar'
    archive(package, 'My App/hello $USER')
    output = convert(package, tmp_path / 'out', 'My App/hello $USER', False)
    assert subprocess.check_output([str(output / 'AppRun')], text=True).strip() == 'hello'


@pytest.mark.parametrize('kind', ['path', 'symlink', 'device'])
def test_unsafe_archives(tmp_path, kind):
    package = tmp_path / 'unsafe.tar'
    with tarfile.open(package, 'w') as stream:
        entry = tarfile.TarInfo('../escape' if kind == 'path' else 'unsafe')
        if kind == 'symlink':
            entry.type = tarfile.SYMTYPE
            entry.linkname = '../escape'
        if kind == 'device':
            entry.type = tarfile.CHRTYPE
        stream.addfile(entry)
    output = tmp_path / 'out'
    with pytest.raises(ConversionError):
        convert(package, output, None, False)
    assert not output.exists()
    assert not (tmp_path / 'escape').exists()


def test_source_archive_needs_executable(tmp_path):
    package = tmp_path / 'source.tar.gz'
    with tarfile.open(package, 'w:gz') as stream:
        stream.addfile(tarfile.TarInfo('source/main.c'))
    with pytest.raises(ConversionError, match='cannot choose an entrypoint'):
        convert(package, tmp_path / 'out', None, False)


def test_entrypoint_cannot_escape(tmp_path):
    package = tmp_path / 'app.tar'
    archive(package, 'hello')
    with pytest.raises(ConversionError, match='inside the package'):
        convert(package, tmp_path / 'out', '../app.tar', False)
