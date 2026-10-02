import subprocess
from pathlib import Path

import pytest
from tuxman.cli import convert, build_parser, ConversionError


@pytest.mark.parametrize('shebang', ['#!/bin/sh\n','#!/usr/bin/env bash\n',''])
def test_script_bundles_without_running(tmp_path,shebang):
    source=tmp_path/'hello.sh'
    marker=tmp_path/'marker'
    source.write_text(shebang+'echo hello\n'+'touch "'+str(marker)+'"\n')
    result=convert(source,tmp_path/'out',None,False)
    assert not marker.exists()
    assert source.stat().st_mode & 0o111 == 0
    assert (result/'usr/bin/hello.sh').read_text().startswith(shebang or '#!/bin/sh\n')
    assert (result/'usr/share/applications/tuxman-script.desktop').is_file()
    assert subprocess.check_output([str(result/'AppRun')],text=True).strip()=='hello'
    assert marker.exists()


def test_script_appimage_metadata(tmp_path):
    from tuxman.cli import _link_appimage_metadata
    source=tmp_path/'hello.sh'; source.write_text('echo hello\n')
    result=convert(source,tmp_path/'out',None,False)
    _link_appimage_metadata(result)
    assert (result/'tuxman-script.png').is_file()
    assert 'Terminal=true' in (result/'tuxman-script.desktop').read_text()


def test_binary_sh_is_rejected(tmp_path):
    source=tmp_path/'binary.sh'; source.write_bytes(b'\x00binary')
    with pytest.raises(ConversionError,match='binary data'): convert(source,tmp_path/'out',None,False)
    assert not (tmp_path/'out').exists()


def test_default_output_and_opt_out():
    assert build_parser().parse_args(['script.sh']).appimage is True
    assert build_parser().parse_args(['script.sh','--appdir']).appimage is False
