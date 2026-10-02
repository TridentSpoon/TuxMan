"""Native GTK4/libadwaita front end for the shared converter."""
from pathlib import Path
import shutil
import threading
import sys
import os
import shlex

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from .cli import convert, package_name, required_tools, SUPPORTED_SUFFIXES
from . import __version__, updates, dependencies


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
        box.append(Gtk.Label(label="Turn a Linux package or application archive into an AppDir or AppImage.",
                             xalign=0, wrap=True))
        self.settings = Adw.PreferencesGroup(title="Package and output",
            description="Supported: " + ", ".join(SUPPORTED_SUFFIXES))
        box.append(self.settings)
        self.package_row = Adw.ActionRow(title="Package", subtitle="Choose a Linux package or application archive")
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
            "AppImages require appimagetool; RPM requires rpm2cpio and cpio; .zst requires zstd; .7z requires 7z or 7zz. "
            "External dependencies, services and incompatible binaries can prevent an app from running on another distro.",
            xalign=0, wrap=True))
        self.dependency_group = Adw.PreferencesGroup(title="Conversion tools")
        box.append(self.dependency_group)
        self.dependency_rows = {}
        for tool in dependencies.TOOLS:
            row = Adw.ActionRow(title=tool.label)
            row.set_use_markup(False)
            self.dependency_rows[tool.key] = row
            self.dependency_group.add(row)
        dependency_actions = Gtk.Box(spacing=12)
        self.recheck = Gtk.Button(label="Check again")
        self.recheck.connect("clicked", self.check_dependencies)
        dependency_actions.append(self.recheck)
        self.install_tools = Gtk.Button(label="Install missing tools")
        self.install_tools.connect("clicked", self.confirm_install)
        dependency_actions.append(self.install_tools)
        self.dependency_group.add(dependency_actions)
        self.dependency_message = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.dependency_group.add(self.dependency_message)
        self.check_dependencies()
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

    def check_dependencies(self, *_):
        self.missing_tools = []
        for tool in dependencies.TOOLS:
            present = tool.present()
            self.dependency_rows[tool.key].set_subtitle(
                ("Installed · " if present else "Missing · ") + tool.purpose)
            if not present:
                self.missing_tools.append(tool)
        self.install_tools.set_sensitive(bool(self.missing_tools))
        self.dependency_message.set_text("All conversion tools are available." if not self.missing_tools
                                         else "Install missing tools to enable their formats.")

    def confirm_install(self, *_):
        self.check_dependencies()
        tools = list(self.missing_tools)
        if not tools:
            return
        try:
            command = dependencies.install_command(dependencies.family(), tools)
            if any(tool.key == "appimagetool" for tool in tools):
                dependencies.architecture()
        except (ValueError, OSError) as exc:
            self.dependency_message.set_text(str(exc))
            return
        description = ""
        if command:
            description += "Install distro packages using a graphical password prompt:\n" + shlex.join(command)
        if any(tool.key == "appimagetool" for tool in tools):
            description += "\n\nDownload appimagetool from its official GitHub release, verify SHA-256, and install it in your user folder."
        dialog = Adw.MessageDialog(transient_for=self, heading="Install missing tools?", body=description.strip())
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", "Install")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda dialog, response: self.begin_install(tools, command) if response == "install" else None)
        dialog.present()

    def begin_install(self, tools, command):
        self.busy = True
        self.dependency_group.set_sensitive(False)
        self.settings.set_sensitive(False)
        self.start.set_sensitive(False)
        self.spinner.start()
        self.dependency_message.set_text("Installing tools… Complete the password prompt if requested.")
        def work():
            try:
                dependencies.install(tools, command)
            except Exception as exc:
                GLib.idle_add(self.install_finished, str(exc))
            else:
                GLib.idle_add(self.install_finished, None)
        threading.Thread(target=work, daemon=True).start()

    def install_finished(self, error):
        self.busy = False
        self.spinner.stop()
        self.dependency_group.set_sensitive(True)
        self.settings.set_sensitive(True)
        self.start.set_sensitive(self.package is not None)
        self.check_dependencies()
        if error:
            self.dependency_message.set_text("Installation failed: " + error)
        elif self.missing_tools:
            self.dependency_message.set_text("Installation finished, but some tools are still missing. Check the statuses above.")
        return GLib.SOURCE_REMOVE

    def choose_package(self, *_):
        dialog = Gtk.FileChooserNative(title="Choose a package", transient_for=self,
            action=Gtk.FileChooserAction.OPEN, accept_label="Choose", cancel_label="Cancel")
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Packages and tar archives")
        for suffix in SUPPORTED_SUFFIXES:
            file_filter.add_pattern("*" + suffix)
        dialog.add_filter(file_filter)
        dialog.connect("response", self.package_selected)
        dialog.show()

    def package_selected(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT and dialog.get_file().get_path():
            self.package = Path(dialog.get_file().get_path())
            self.package_row.set_subtitle(str(self.package))
            self.name.set_text(package_name(self.package))
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
        needed = required_tools(self.package)
        if appimage:
            needed.append("appimagetool")
        missing = [tool for tool in needed if tool == "7z or 7zz" or not shutil.which(tool)]
        if missing:
            self.status.set_text("Install these tools first: " + ", ".join(missing))
            return
        output = self.destination / (name + ".AppDir")
        if output.exists() or (appimage and output.with_suffix(".AppImage").exists()):
            self.status.set_text("That output already exists. Choose another bundle name.")
            return
        entrypoint = self.entry.get_text().strip() or None
        self.busy = True
        self.dependency_group.set_sensitive(False)
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
        self.dependency_group.set_sensitive(True)
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
            self.status.set_text("Please wait for the current operation to finish before closing.")
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
        box.append(Gtk.Label(label="Portable bundles from Packages and tar archives", wrap=True))
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
    os.environ["PATH"] = str(Path.home() / ".local/bin") + os.pathsep + os.environ.get("PATH", "")
    return Application().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
