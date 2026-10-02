#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Gtk, Adw' || {
    echo "Install GTK4, libadwaita and Python GObject bindings first (see README)." >&2
    exit 1
}
app_dir="$HOME/.local/share/tuxman"
mkdir -p "$app_dir" "$HOME/.local/bin" "$HOME/.local/share/applications"
cp -R src/tuxman "$app_dir/"
cat > "$HOME/.local/bin/tuxman-gui" <<'LAUNCHER'
#!/bin/sh
export PYTHONPATH="$HOME/.local/share/tuxman${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m tuxman.gui "$@"
LAUNCHER
chmod +x "$HOME/.local/bin/tuxman-gui"
cat > "$HOME/.local/share/applications/io.github.TridentSpoon.TuxMan.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=TuxMan
Comment=Convert Debian and RPM packages into portable bundles
Exec="${HOME}/.local/bin/tuxman-gui"
Icon=${app_dir}/tuxman/assets/io.github.TridentSpoon.TuxMan.png
Terminal=false
Categories=Utility;
DESKTOP
echo "Installed TuxMan. Open it from your application menu."
