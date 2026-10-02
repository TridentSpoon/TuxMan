"""Native GTK4/libadwaita front end for the shared converter."""
from pathlib import Path
import shutil
import threading
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from .cli import convert
from . import __version__, updates


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="TuxMan", default_width=680, default_height=660)
        self.package = None
        self.destination = Path.home() / "Downloads"
        self.result = None
        self.busy = False
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("About TuxMan", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *_: AboutWindow(self).present())
        self.add_action(about)
        view.add_top_bar(header)
        self.set_content(view)
        scroll = Gtk.ScrolledWindow()
        view.set_content(scroll)
        clamp = Adw.Clamp(maximum_size=620)
        scroll.set_child(clamp)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                      margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        clamp.set_child(box)
        title = Gtk.Label(label="Make your app portable", xalign=0)
        title.add_css_class("title-1")
        box.append(title)
        box.append(Gtk.Label(label="Turn a Debian or RPM package into an AppDir or AppImage.",
                             xalign=0, wrap=True))
        self.settings = Adw.PreferencesGroup(title="Package and output")
        box.append(self.settings)
        self.package_row = Adw.ActionRow(title="Package", subtitle="Choose a .deb or .rpm file")
        self.package_row.set_use_markup(False)
        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", self.choose_package)
        self.package_row.add_suffix(choose)
        self.settings.add(self.package_row)
        self.destination_row = Adw.ActionRow(title="Save in", subtitle=str(self.destination))
        self.destination_row.set_use_markup(False)
        folder = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        folder.connect("clicked", self.choose_destination)
        self.destination_row.add_suffix(folder)
        self.settings.add(self.destination_row)
        self.name = Adw.EntryRow(title="Bundle name (without extension)")
        self.settings.add(self.name)
        self.format = Adw.ComboRow(title="Output format",
            model=Gtk.StringList.new(["AppDir", "AppImage"]))
        self.settings.add(self.format)
        self.entry = Adw.EntryRow(title="Executable path (optional, e.g. usr/bin/my-app)")
        self.settings.add(self.entry)
        box.append(Gtk.Label(label="The executable is chosen automatically when the package contains one candidate. "
            "AppImages require appimagetool; RPM files require rpm2cpio and cpio. "
            "External dependencies, services and incompatible binaries can prevent an app from running on another distro.",
            xalign=0, wrap=True))
        actions = Gtk.Box(spacing=12)
        self.start = Gtk.Button(label="Create bundle", sensitive=False)
        self.start.add_css_class("suggested-action")
        self.start.connect("clicked", self.create_bundle)
        actions.append(self.start)
        self.spinner = Gtk.Spinner()
        actions.append(self.spinner)
        box.append(actions)
        self.status = Gtk.Label(label="Choose a package to get started.", xalign=0, wrap=True, selectable=True)
        box.append(self.status)
        self.open = Gtk.Button(label="Open output folder", visible=False, halign=Gtk.Align.START)
        self.open.connect("clicked", self.open_output)
        box.append(self.open)
        self.connect("close-request", self.on_close)

    def choose_package(self, *_):
        dialog = Gtk.FileChooserNative(title="Choose a package", transient_for=self,
            action=Gtk.FileChooserAction.OPEN, accept_label="Choose", cancel_label="Cancel")
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Debian and RPM packages")
        file_filter.add_pattern("*.deb")
        file_filter.add_pattern("*.rpm")
        dialog.add_filter(file_filter)
        dialog.connect("response", self.package_selected)
        dialog.show()

    def package_selected(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT and dialog.get_file().get_path():
            self.package = Path(dialog.get_file().get_path())
            self.package_row.set_subtitle(str(self.package))
            self.name.set_text(self.package.stem)
            self.start.set_sensitive(True)
            self.open.set_visible(False)
            self.status.set_text("Ready to create a bundle.")
        dialog.destroy()

    def choose_destination(self, *_):
        dialog = Gtk.FileChooserNative(title="Choose output folder", transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER, accept_label="Choose", cancel_label="Cancel")
        dialog.connect("response", self.destination_selected)
        dialog.show()

    def destination_selected(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT and dialog.get_file().get_path():
            self.destination = Path(dialog.get_file().get_path())
            self.destination_row.set_subtitle(str(self.destination))
        dialog.destroy()

    def create_bundle(self, *_):
        name = self.name.get_text().strip()
        if not name or name in {".", ".."} or "/" in name:
            self.status.set_text("Enter a bundle name without folder separators.")
            return
        appimage = self.format.get_selected() == 1
        needed = (["rpm2cpio", "cpio"] if self.package.suffix.lower() == ".rpm" else [])
        if appimage:
            needed.append("appimagetool")
        missing = [tool for tool in needed if not shutil.which(tool)]
        if missing:
            self.status.set_text("Install these tools first: " + ", ".join(missing))
            return
        output = self.destination / (name + ".AppDir")
        if output.exists() or (appimage and output.with_suffix(".AppImage").exists()):
            self.status.set_text("That output already exists. Choose another bundle name.")
            return
        entrypoint = self.entry.get_text().strip() or None
        self.busy = True
        self.settings.set_sensitive(False)
        self.start.set_sensitive(False)
        self.open.set_visible(False)
        self.spinner.start()
        self.status.set_text("Creating your bundle…")
        threading.Thread(target=self.worker, args=(self.package, output, entrypoint, appimage), daemon=True).start()

    def worker(self, package, output, entrypoint, appimage):
        try:
            result = convert(package, output, entrypoint, appimage)
        except Exception as exc:
            GLib.idle_add(self.finished, None, str(exc))
        else:
            GLib.idle_add(self.finished, result, None)

    def finished(self, result, error):
        self.busy = False
        self.spinner.stop()
        self.settings.set_sensitive(True)
        self.start.set_sensitive(True)
        self.result = result
        self.status.set_text("Conversion failed: " + error if error else f"Bundle created: {result}")
        self.open.set_visible(result is not None)
        return GLib.SOURCE_REMOVE

    def open_output(self, *_):
        try:
            Gio.AppInfo.launch_default_for_uri(self.result.parent.as_uri(), None)
        except GLib.Error as exc:
            self.status.set_text(str(exc))

    def on_close(self, *_):
        if self.busy:
            self.status.set_text("Please wait for conversion to finish before closing.")
        return self.busy


class AboutWindow(Adw.Window):
    """About details and a manual release check, like Set the Table."""
    def __init__(self, parent):
        super().__init__(title="About TuxMan", transient_for=parent, modal=True,
                         default_width=480, default_height=580)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        page = Adw.PreferencesPage()
        view.set_content(page)
        heading = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        icon = Gtk.Image.new_from_file(str(Path(__file__).parent / "assets/io.github.TridentSpoon.TuxMan.png"))
        icon.set_pixel_size(96)
        box.append(icon)
        title = Gtk.Label(label="TuxMan")
        title.add_css_class("title-1")
        box.append(title)
        box.append(Gtk.Label(label=f"Version {__version__}"))
        box.append(Gtk.Label(label="Portable bundles from Debian and RPM packages", wrap=True))
        heading.add(box)
        page.add(heading)
        details = Adw.PreferencesGroup(title="About")
        details.add(Adw.ActionRow(title="License", subtitle="MIT License · © 2026 TuxMan contributors"))
        license_button = Gtk.Button(label="Read license")
        license_button.connect("clicked", self.show_license)
        details.add(license_button)
        website = Gtk.LinkButton(uri="https://github.com/TridentSpoon/TuxMan", label="Project on GitHub")
        details.add(website)
        page.add(details)
        group = Adw.PreferencesGroup(title="Updates")
        self.update_row = Adw.ActionRow(title="Check for updates", subtitle="Check GitHub for a newer release.")
        self.update_row.set_use_markup(False)
        self.update_row.set_subtitle_lines(0)
        self.check_button = Gtk.Button(label="Check", valign=Gtk.Align.CENTER)
        self.check_button.connect("clicked", self.check_updates)
        self.update_row.add_suffix(self.check_button)
        self.update_spinner = Gtk.Spinner()
        self.update_row.add_suffix(self.update_spinner)
        group.add(self.update_row)
        self.download = Gtk.LinkButton(uri=updates.RELEASES_URL, label="Open releases page", visible=False)
        group.add(self.download)
        group.set_description("Checks run only when you click. Download a release, then run its install.sh to update.")
        page.add(group)

    def show_license(self, *_):
        window = Adw.Window(title="MIT License", transient_for=self, modal=True,
                            default_width=520, default_height=480)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        scroll = Gtk.ScrolledWindow()
        text = (Path(__file__).parent / "assets/LICENSE.txt").read_text()
        scroll.set_child(Gtk.Label(label=text, wrap=True, selectable=True, xalign=0,
                                  margin_top=20, margin_bottom=20, margin_start=20, margin_end=20))
        view.set_content(scroll)
        window.set_content(view)
        window.present()

    def check_updates(self, *_):
        self.check_button.set_sensitive(False)
        self.update_spinner.start()
        self.download.set_visible(False)
        self.update_row.set_subtitle("Checking…")
        def work():
            result = updates.check_for_update(__version__)
            GLib.idle_add(self.update_finished, result)
        threading.Thread(target=work, daemon=True).start()

    def update_finished(self, result):
        self.check_button.set_sensitive(True)
        self.update_spinner.stop()
        self.update_row.set_subtitle(result.message)
        self.download.set_visible(result.status in {updates.UPDATE_AVAILABLE, updates.NO_RELEASES})
        return GLib.SOURCE_REMOVE


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.TridentSpoon.TuxMan")

    def do_activate(self):
        window = self.get_active_window() or Window(self)
        window.present()


def main():
    return Application().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
