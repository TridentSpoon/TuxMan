import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from tuxman import dependencies as deps


def test_distro_detection():
    assert deps.family('ID=cachyos\nID_LIKE=arch') == 'arch'
    assert deps.family('ID=mint\nID_LIKE="ubuntu debian"') == 'debian'
    assert deps.family('ID=unknown') == 'unknown'


@pytest.mark.parametrize('family,manager', [('arch','pacman'),('debian','apt-get'),('fedora','dnf'),('suse','zypper')])
def test_package_plan(family, manager):
    command = deps.install_command(family, list(deps.TOOLS))
    assert command[0] == manager
    assert 'appimagetool' not in command
    assert deps.PACKAGES[family]['rpm2cpio'] in command


def test_unsupported_distro_and_arch():
    with pytest.raises(ValueError): deps.install_command('unknown', [deps.TOOLS[1]])
    with pytest.raises(ValueError): deps.architecture('unknown')
    assert deps.install_command('unknown', [deps.TOOLS[0]]) == []


def test_7zip_alternative():
    with patch('shutil.which', side_effect=lambda name: '/bin/7zz' if name == '7zz' else None):
        assert deps.TOOLS[-1].present()


def test_auth_cancelled():
    with patch('shutil.which', side_effect=lambda name: '/usr/bin/' + name), patch('subprocess.run') as run:
        run.return_value.returncode = 126
        with pytest.raises(ValueError, match='cancelled'):
            deps.install([deps.TOOLS[1]], ['pacman','-S','rpm-tools'])
        assert run.call_args.args[0] == ['/usr/bin/pkexec','/usr/bin/pacman','-S','rpm-tools']


@pytest.mark.parametrize('valid', [True, False])
def test_download_checksum(tmp_path, valid):
    binary = b'test appimage bytes'
    checksum = hashlib.sha256(binary if valid else b'wrong').hexdigest()
    release = {'assets': [{'name':'appimagetool-x86_64.AppImage',
        'browser_download_url':'https://github.com/AppImage/appimagetool/releases/download/test/appimagetool-x86_64.AppImage',
        'digest':'sha256:' + checksum}]}
    with patch.object(Path,'home',return_value=tmp_path), patch('platform.machine',return_value='x86_64'), patch('urllib.request.urlopen',side_effect=[io.BytesIO(json.dumps(release).encode()),io.BytesIO(binary)]):
        if valid:
            deps.download_appimagetool()
            wrapper = tmp_path/'.local/bin/appimagetool'
            assert 'APPIMAGE_EXTRACT_AND_RUN=1' in wrapper.read_text()
            assert (tmp_path/'.local/share/tuxman/tools/appimagetool-x86_64.AppImage').read_bytes() == binary
        else:
            with pytest.raises(ValueError,match='checksum mismatch'): deps.download_appimagetool()
            assert not (tmp_path/'.local/bin/appimagetool').exists()
            assert not list((tmp_path/'.local/share/tuxman/tools').iterdir())
