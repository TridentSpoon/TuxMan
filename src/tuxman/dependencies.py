"""Manual dependency checks and installations; no network activity on startup."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import tempfile
import urllib.request

RELEASE_API = 'https://api.github.com/repos/AppImage/appimagetool/releases/latest'


@dataclass(frozen=True)
class Tool:
    key: str
    label: str
    binaries: tuple[str, ...]
    purpose: str

    def present(self):
        return any(shutil.which(name) for name in self.binaries)


TOOLS = (
    Tool('appimagetool', 'appimagetool', ('appimagetool',), 'Create AppImages'),
    Tool('rpm2cpio', 'rpm2cpio', ('rpm2cpio',), 'Extract RPM packages'),
    Tool('cpio', 'cpio', ('cpio',), 'Extract RPM payloads'),
    Tool('zstd', 'Zstandard', ('zstd',), 'Open .tar.zst and Arch packages'),
    Tool('7zip', '7-Zip', ('7z', '7zz'), 'Open .7z archives'),
)
PACKAGES = {
    'arch': {'rpm2cpio': 'rpm-tools', 'cpio': 'cpio', 'zstd': 'zstd', '7zip': '7zip'},
    'debian': {'rpm2cpio': 'rpm2cpio', 'cpio': 'cpio', 'zstd': 'zstd', '7zip': 'p7zip-full'},
    'fedora': {'rpm2cpio': 'rpm', 'cpio': 'cpio', 'zstd': 'zstd', '7zip': '7zip'},
    'suse': {'rpm2cpio': 'rpm', 'cpio': 'cpio', 'zstd': 'zstd', '7zip': '7zip'},
}


def family(text=None):
    if text is None:
        text = Path('/etc/os-release').read_text()
    data = {}
    for line in text.splitlines():
        key, sep, value = line.partition('=')
        if sep:
            data[key] = value.strip().strip('\"\'')
    aliases = {'arch': 'arch', 'cachyos': 'arch', 'manjaro': 'arch',
               'debian': 'debian', 'ubuntu': 'debian', 'fedora': 'fedora',
               'rhel': 'fedora', 'centos': 'fedora', 'opensuse-tumbleweed': 'suse',
               'opensuse-leap': 'suse', 'opensuse': 'suse', 'suse': 'suse'}
    for item in [data.get('ID', ''), *data.get('ID_LIKE', '').split()]:
        if item in aliases:
            return aliases[item]
    return 'unknown'


def install_command(distro, tools):
    keys = [tool.key for tool in tools if tool.key != 'appimagetool']
    if not keys:
        return []
    if distro not in PACKAGES:
        raise ValueError('Automatic system-tool installation is unavailable on this distro. Install ' + ', '.join(keys) + ' using your package manager.')
    packages = list(dict.fromkeys(PACKAGES[distro][key] for key in keys))
    commands = {'arch': ['pacman', '-S', '--needed', '--noconfirm'],
                'debian': ['apt-get', 'install', '-y'],
                'fedora': ['dnf', 'install', '-y'],
                'suse': ['zypper', '--non-interactive', 'install']}
    return commands[distro] + packages


def architecture(machine=None):
    machine = machine or platform.machine()
    mapping = {'x86_64': 'x86_64', 'amd64': 'x86_64', 'aarch64': 'aarch64',
               'arm64': 'aarch64', 'i386': 'i686', 'i686': 'i686', 'armv7l': 'armhf'}
    if machine not in mapping:
        raise ValueError('No appimagetool download is configured for ' + machine)
    return mapping[machine]


def download_appimagetool():
    request = urllib.request.Request(RELEASE_API, headers={'User-Agent': 'TuxMan', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    name = 'appimagetool-' + architecture() + '.AppImage'
    asset = next((asset for asset in release['assets'] if asset['name'] == name), None)
    if not asset:
        raise ValueError('The official appimagetool release has no download for this architecture.')
    url = asset['browser_download_url']
    digest = asset.get('digest', '')
    if not url.startswith('https://github.com/AppImage/appimagetool/releases/download/') or not digest.startswith('sha256:'):
        raise ValueError('The release has no trusted download URL or SHA-256 checksum.')
    directory = Path.home() / '.local/share/tuxman/tools'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
        temporary = Path(output.name)
        checksum = hashlib.sha256()
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    checksum.update(chunk)
                    output.write(chunk)
            output.flush()
            if checksum.hexdigest() != digest.removeprefix('sha256:'):
                raise ValueError('appimagetool checksum mismatch; download discarded.')
            temporary.chmod(0o755)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    bindir = Path.home() / '.local/bin'
    bindir.mkdir(parents=True, exist_ok=True)
    wrapper = bindir / 'appimagetool'
    if wrapper.exists() or wrapper.is_symlink():
        raise ValueError('An appimagetool launcher already exists in ~/.local/bin; it was preserved.')
    # Extract-and-run avoids requiring FUSE just to generate an AppImage.
    with wrapper.open('x') as stream:
        stream.write('#!/bin/sh\nexport APPIMAGE_EXTRACT_AND_RUN=1\nexec ' + shlex.quote(str(target)) + ' "$@"\n')
    wrapper.chmod(0o755)


def install(tools, command):
    if command:
        manager = shutil.which(command[0])
        pkexec = shutil.which('pkexec')
        if not manager or not pkexec:
            raise ValueError('The package manager or pkexec is missing. Install the tools manually.')
        result = subprocess.run([pkexec, manager, *command[1:]], capture_output=True, text=True, timeout=1800)
        if result.returncode in (126, 127):
            raise ValueError('Authentication was cancelled or refused.')
        if result.returncode:
            raise ValueError((result.stderr or result.stdout or 'Package installation failed.')[-2000:])
    if any(tool.key == 'appimagetool' for tool in tools):
        download_appimagetool()
