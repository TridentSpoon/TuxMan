import os
from pathlib import Path

import pytest
from tuxman.cli import ConversionError, _entrypoint


def executable(root, name):
    path=root/name; path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('#!/bin/sh\n'); path.chmod(0o755)
    return path


def desktop(root,name,body):
    path=root/'usr/share/applications'/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('[Desktop Entry]\nType=Application\n'+body)


def test_cursor_layout(tmp_path):
    executable(tmp_path,'usr/share/cursor/cursor')
    executable(tmp_path,'usr/share/cursor/chrome_crashpad_handler')
    desktop(tmp_path,'cursor.desktop','Exec=/usr/share/cursor/cursor %F\n[Desktop Action new-window]\nExec=/missing/helper\n')
    desktop(tmp_path,'cursor-url.desktop','NoDisplay=true\nExec=/usr/share/cursor/helper %U\n')
    assert _entrypoint(tmp_path,None)=='usr/share/cursor/cursor'


def test_quoted_opt_path(tmp_path):
    executable(tmp_path,'opt/My App/app')
    desktop(tmp_path,'app.desktop','Exec="/opt/My App/app" %U\n')
    assert _entrypoint(tmp_path,None)=='opt/My App/app'


def test_bare_command_and_deduplication(tmp_path):
    executable(tmp_path,'usr/bin/app')
    executable(tmp_path,'usr/bin/helper')
    desktop(tmp_path,'one.desktop','Exec=app %F\n')
    desktop(tmp_path,'two.desktop','Exec=app %U\n')
    assert _entrypoint(tmp_path,None)=='usr/bin/app'


def test_multiple_apps_need_choice(tmp_path):
    for name in ('one','two'):
        executable(tmp_path,'opt/'+name)
        desktop(tmp_path,name+'.desktop','Exec=/opt/'+name+'\n')
    with pytest.raises(ConversionError,match='multiple application launchers'):
        _entrypoint(tmp_path,None)


def test_host_path_is_not_selected(tmp_path):
    desktop(tmp_path,'app.desktop','Exec=/bin/sh\n')
    with pytest.raises(ConversionError,match='none'):
        _entrypoint(tmp_path,None)
