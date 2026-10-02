import io
import os
import shutil
import subprocess
import tarfile
import zipfile
from unittest.mock import patch

import pytest
from tuxman.cli import ConversionError, convert, package_name


@pytest.mark.parametrize('suffix,mode', [('.tar.xz','w:xz'),('.tar.bz2','w:bz2'),('.tgz','w:gz'),('.tar.zst','w'),('.pkg.tar.zst','w')])
def test_compressed_formats(tmp_path, suffix, mode):
    package = tmp_path / ('hello' + suffix)
    raw = tmp_path / 'input.tar' if suffix.endswith('zst') else package
    with tarfile.open(raw, mode) as stream:
        data = b'#!/bin/sh\necho hello\n'
        info = tarfile.TarInfo('usr/bin/hello'); info.size=len(data); info.mode=0o755
        stream.addfile(info, io.BytesIO(data))
        if suffix.startswith('.pkg'):
            info = tarfile.TarInfo('.PKGINFO'); info.mode=0o644
            stream.addfile(info)
    if suffix.endswith('zst'):
        if not shutil.which('zstd'): pytest.skip('zstd not installed')
        subprocess.run(['zstd','-q',str(raw),'-o',str(package)],check=True)
    out=convert(package,tmp_path/'out',None,False)
    assert subprocess.check_output([str(out/'AppRun')],text=True).strip()=='hello'
    assert package_name(package)=='hello'


def test_zip_permissions_and_launch(tmp_path):
    package=tmp_path/'hello.zip'
    with zipfile.ZipFile(package,'w') as stream:
        member=zipfile.ZipInfo('MyApp/hello'); member.create_system=3
        member.external_attr=0o100755 << 16
        stream.writestr(member,b'#!/bin/sh\necho hello\n')
    out=convert(package,tmp_path/'out',None,False)
    assert subprocess.check_output([str(out/'AppRun')],text=True).strip()=='hello'


@pytest.mark.parametrize('name,mode', [('../escape',0o100644),('/escape',0o100644),('link',0o120777)])
def test_unsafe_zip(tmp_path,name,mode):
    package=tmp_path/'bad.zip'
    with zipfile.ZipFile(package,'w') as stream:
        member=zipfile.ZipInfo(name); member.external_attr=mode << 16
        stream.writestr(member,'../escape')
    with pytest.raises(ConversionError): convert(package,tmp_path/'out',None,False)
    assert not (tmp_path/'out').exists()


def test_7z_real_archive(tmp_path):
    tool=shutil.which('7z') or shutil.which('7zz')
    if not tool: pytest.skip('7z not installed')
    source=tmp_path/'hello'; source.write_text('#!/bin/sh\necho hello\n'); source.chmod(0o755)
    package=tmp_path/'app.7z'
    subprocess.run([tool,'a',str(package),str(source)],stdout=subprocess.DEVNULL,check=True)
    out=convert(package,tmp_path/'out',None,False)
    assert subprocess.check_output([str(out/'AppRun')],text=True).strip()=='hello'


@pytest.mark.parametrize('suffix,message',[('.tar.zst','zstd'),('.7z','7z')])
def test_missing_tools(tmp_path,suffix,message):
    package=tmp_path/('app'+suffix); package.touch()
    with patch('shutil.which',return_value=None):
        with pytest.raises(ConversionError,match=message): convert(package,tmp_path/'out',None,False)
    assert not (tmp_path/'out').exists()
