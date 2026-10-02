from pathlib import Path
from unittest.mock import patch
import subprocess

import pytest
from tuxman.cli import ConversionError, _link_appimage_metadata, convert
from test_cli import make_deb


def test_pixmap_and_main_launcher(tmp_path):
    folder=tmp_path/'usr/share/applications'; folder.mkdir(parents=True)
    (folder/'app-url.desktop').write_text('[Desktop Entry]\nNoDisplay=true\nExec=/usr/share/app/app --open-url %U\nIcon=app\n')
    (folder/'app.desktop').write_text('[Desktop Entry]\nExec=/usr/share/app/app %F\nIcon=app\n')
    icon=tmp_path/'usr/share/pixmaps/app.png'; icon.parent.mkdir(parents=True); icon.write_bytes(b'png')
    _link_appimage_metadata(tmp_path)
    assert 'Exec=AppRun %F' in (tmp_path/'app.desktop').read_text()
    assert not (tmp_path/'app-url.desktop').exists()
    assert (tmp_path/'app.png').resolve()==icon


def test_appimage_reports_tool_stderr(tmp_path):
    package=tmp_path/'hello.deb'; make_deb(package)
    with patch('shutil.which',return_value='/tool'), patch('tuxman.cli._link_appimage_metadata'), patch('subprocess.run',return_value=subprocess.CompletedProcess([],1,'','Missing runtime download')):
        with pytest.raises(ConversionError,match='Missing runtime download'):
            convert(package,tmp_path/'out',None,True)
