#!/usr/bin/env python3
"""quickkey - a keystroke-launched menu of commands whose output lands in the clipboard.

Bind this to a global hotkey.  A small window pops up with the list of Names
from the config file.  Pick one with the mouse or the keyboard, the Command
runs, and its stdout is placed on the system clipboard.

Speed matters here, so the module keeps its top-level imports to the three
cheapest ones.  Everything else -- tkinter, subprocess, argparse -- is pulled
in only on the path that actually needs it.  Run `quickkey --daemon` once at
login and later invocations hand off to that warm process over a socket,
which skips interpreter and Tk startup entirely.
"""

from __future__ import annotations

import os
import socket
import sys

APP_NAME = "quickkey"

tk = None       # filled in by load_tk()
tkfont = None


def load_tk() -> None:
    global tk, tkfont
    if tk is None:
        import tkinter
        import tkinter.font
        tk = tkinter
        tkfont = tkinter.font


def die(message: str):
    sys.stderr.write(f"{APP_NAME}: {message}\n")
    raise SystemExit(2)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def config_candidates() -> list[str]:
    env = os.environ.get("QUICKKEY_CONFIG")
    if env:
        return [os.path.expanduser(env)]
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return [
        os.path.join(xdg, APP_NAME, "config"),
        os.path.join(xdg, APP_NAME, "config.ini"),
        os.path.expanduser("~/.quickkeyrc"),
        # a git checkout keeps its example next to the script
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "quickkey.conf"),
        # ...and a packaged install puts the system ones here, so a fresh
        # install comes up with something in the menu instead of an error
        os.path.join("/etc", APP_NAME, "config"),
        os.path.join("/usr/share", APP_NAME, "quickkey.conf"),
    ]


def find_config(explicit: str | None) -> str:
    if explicit:
        path = os.path.expanduser(explicit)
        if not os.path.exists(path):
            die(f"config file not found: {path}")
        return path
    for path in config_candidates():
        if os.path.exists(path):
            return path
    die(
        "no config file found.  Looked in:\n  "
        + "\n  ".join(config_candidates())
        + "\n\nCreate one like:\n\n"
        "  [My IP]\n"
        "  command = curl -s ifconfig.me\n"
    )


def load_entries(path: str) -> list[tuple[str, str]]:
    """Parse the config into an ordered list of (name, command) pairs.

    INI format: the section name is the Name, and it holds one `command` key.
    """
    import configparser

    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    parser.optionxform = str  # keep key case
    try:
        with open(path, encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        die(f"could not read config {path}:\n{exc}")

    entries: list[tuple[str, str]] = []
    for name in parser.sections():
        section = parser[name]
        command = section.get("command") or section.get("cmd")
        if not command:
            die(f"config section [{name}] in {path} has no `command =` line")
        entries.append((name, command.strip()))

    if not entries:
        die(f"config {path} contains no entries")
    return entries


# --------------------------------------------------------------------------
# clipboard
# --------------------------------------------------------------------------

# X11 has two independent selections and applications disagree about which
# one "paste" means: CLIPBOARD is Ctrl-V, PRIMARY is middle-click and the
# Shift-Insert that urxvt, xterm and friends use.  Writing only CLIPBOARD
# leaves those terminals pasting whatever stale text PRIMARY still held, so
# by default we set both.  Wayland mirrors the same split; macOS and Windows
# have only the one clipboard.
SELECTIONS = ("clipboard", "primary")

CLIPBOARD_TOOLS = [
    ("wl-copy", {"clipboard": ["wl-copy"],
                 "primary": ["wl-copy", "--primary"]}),
    ("xclip", {"clipboard": ["xclip", "-selection", "clipboard"],
               "primary": ["xclip", "-selection", "primary"]}),
    ("xsel", {"clipboard": ["xsel", "--clipboard", "--input"],
              "primary": ["xsel", "--primary", "--input"]}),
    ("pbcopy", {"clipboard": ["pbcopy"]}),
    ("clip.exe", {"clipboard": ["clip.exe"]}),
]

_clipboard_cache: list[str] | None | bool = False


def which(program: str) -> str | None:
    """A minimal shutil.which -- importing shutil costs more than this does."""
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(directory, program)
        if os.access(candidate, os.X_OK) and os.path.isfile(candidate):
            return candidate
    return None


def clipboard_command() -> dict[str, list[str]] | None:
    """The per-selection argv table for the first helper we can find."""
    global _clipboard_cache
    if _clipboard_cache is False:
        _clipboard_cache = None
        for program, commands in CLIPBOARD_TOOLS:
            if which(program):
                _clipboard_cache = commands
                break
    return _clipboard_cache


def copy_to_clipboard(text: str, selections=SELECTIONS) -> list[str]:
    """Put text on the given X selections so it survives after we exit.

    Tk's own clipboard is dropped when the process dies under X11/Wayland,
    so we hand off to a helper that owns the selection instead.  Returns the
    selections actually written -- pbcopy and clip.exe have only one.
    """
    import subprocess

    commands = clipboard_command()
    if commands is None:
        raise RuntimeError(
            "no clipboard helper found; install one of: "
            + ", ".join(program for program, _ in CLIPBOARD_TOOLS)
        )

    payload = text.encode("utf-8")
    written = []
    for selection in selections:
        command = commands.get(selection)
        if command is None:
            continue        # this platform has no such selection
        # Note: helpers like xclip fork a daemon to own the selection.  That
        # daemon inherits our pipes, so capturing its output would block
        # until the selection is replaced -- send the streams to /dev/null.
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.stdin.write(payload)
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(
                f"{command[0]} exited with status {proc.returncode} "
                f"writing the {selection} selection"
            )
        written.append(selection)

    if not written:
        raise RuntimeError("none of the requested selections are supported here")
    return written


# --------------------------------------------------------------------------
# running commands
# --------------------------------------------------------------------------

class Result:
    def __init__(self, name: str, stdout: str, stderr: str, returncode: int):
        self.name = name
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def split_background(command: str) -> tuple[str, bool]:
    """A trailing `&` means fire-and-forget, as it does in a shell.

    `foo &` launches foo detached and we stop caring: no waiting, no output,
    no clipboard.  `a && b` is untouched -- that is two commands, not a
    background one, and a command genuinely ending in `&&` is a shell syntax
    error either way.

    The `&` is stripped here; launch_detached puts the whole command in a
    backgrounded subshell, so every part of it is detached.
    """
    stripped = command.rstrip()
    if stripped.endswith("&") and not stripped.endswith("&&"):
        return stripped[:-1].rstrip(), True
    return command, False


def launch_detached(command: str) -> None:
    """Start a command and walk away.

    The whole command goes into a backgrounded subshell, `( ... ) &`, rather
    than relying on a trailing `&` in the command itself: in `a; b &` the
    shell backgrounds only `b`, and waiting on `a` would block the Tk thread
    and freeze the window.  Wrapped this way the outer shell forks and exits
    at once, so wait() costs nothing and the real process is reparented to
    init -- no zombie, which matters since the daemon can outlive hundreds
    of these.

    start_new_session puts it in its own session so it survives quickkey
    exiting moments later in one-shot mode.  Every stream goes to /dev/null:
    nothing reads them, and an inherited pipe is exactly what made xclip hang.
    """
    import subprocess

    subprocess.Popen(
        f"({command}) &",
        shell=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    ).wait()


def run_command(name: str, command: str, timeout: float | None) -> Result:
    import subprocess

    try:
        proc = subprocess.run(command, shell=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(name, "", f"timed out after {timeout:g}s", 124)
    except OSError as exc:
        return Result(name, "", str(exc), 126)
    return Result(
        name,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
        proc.returncode,
    )


# --------------------------------------------------------------------------
# monitors
# --------------------------------------------------------------------------

# Tk reports the whole X display, so centring in it lands near the seam
# between monitors on a multi-head setup -- usually not where you are
# working.  The pointer tells us which monitor that is; xrandr tells us
# where the monitors are.
#
# The catch is cost: winfo_pointerx() is ~0.05ms, but `xrandr
# --listmonitors` measures ~110ms on a real display, which is five times the
# daemon's whole wake time.  So xrandr NEVER runs on the path to showing the
# window.  The layout is cached (in memory, and in a file so one-shot runs
# benefit too) and refreshed on a worker thread; an invocation that finds no
# usable cache just centres the old way and leaves a good cache behind.

_monitors: list[dict] | None = None
_refreshing = False


def monitor_cache_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return os.path.join(runtime, f"{APP_NAME}-monitors")
    return os.path.join("/tmp", f"{APP_NAME}-monitors-{os.getuid()}")


def parse_monitors(text: str) -> list[dict]:
    """Parse `xrandr --listmonitors` output.

    Lines look like:  0: +*HDMI-1 1920/598x1080/336+1920+0  HDMI-1
    where the * marks the primary monitor.
    """
    import re

    rects = []
    for line in text.splitlines()[1:]:
        match = re.search(
            r"^\s*\d+:\s+\+(\*)?\S+\s+(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)", line
        )
        if match:
            star, w, h, x, y = match.groups()
            rects.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                          "primary": star == "*"})
    return rects


def query_monitors() -> list[dict]:
    """Ask X for the monitor layout.  Slow -- never call this inline."""
    import subprocess

    try:
        out = subprocess.run(["xrandr", "--listmonitors"], capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_monitors(out.stdout) if out.returncode == 0 else []


def load_monitors() -> list[dict] | None:
    """Cached layout, or None when we have never managed to read one."""
    global _monitors
    if _monitors is None:
        try:
            with open(monitor_cache_path(), encoding="utf-8") as handle:
                rects = []
                for line in handle:
                    x, y, w, h, primary = line.split()
                    rects.append({"x": int(x), "y": int(y), "w": int(w),
                                  "h": int(h), "primary": primary == "1"})
            _monitors = rects or None
        except (OSError, ValueError):
            _monitors = None
    return _monitors


def store_monitors(rects: list[dict]) -> None:
    global _monitors
    _monitors = rects or None
    if not rects:
        return
    try:
        path = monitor_cache_path()
        with open(path, "w", encoding="utf-8") as handle:
            for r in rects:
                handle.write(f"{r['x']} {r['y']} {r['w']} {r['h']} "
                             f"{1 if r['primary'] else 0}\n")
        os.chmod(path, 0o600)
    except OSError:
        pass


def refresh_monitors() -> None:
    """Re-read the layout on a worker thread, for the *next* invocation.

    Not a daemon thread: a one-shot run exits moments after copying, and we
    want the cache written even then.  It costs nothing visible, since by
    then the window is already gone.
    """
    global _refreshing
    if _refreshing:
        return
    import threading

    _refreshing = True

    def work():
        global _refreshing
        try:
            store_monitors(query_monitors())
        finally:
            _refreshing = False

    threading.Thread(target=work, daemon=False).start()


def monitor_for(px: int, py: int, mode: str) -> dict | None:
    """The monitor to place the window on, or None to centre on the display."""
    if mode == "all":
        return None
    rects = load_monitors()
    if not rects:
        refresh_monitors()      # nothing cached yet; be right next time
        return None
    if mode == "primary":
        for rect in rects:
            if rect["primary"]:
                return rect
        return rects[0]
    for rect in rects:
        if (rect["x"] <= px < rect["x"] + rect["w"]
                and rect["y"] <= py < rect["y"] + rect["h"]):
            return rect
    # the pointer is somewhere we do not know about, so the layout changed
    refresh_monitors()
    return None


# --------------------------------------------------------------------------
# daemon socket
# --------------------------------------------------------------------------

# the kernel caps AF_UNIX paths at ~108 bytes, so a long XDG_RUNTIME_DIR has
# to fall back to /tmp rather than blow up at bind() time
SOCKET_PATH_MAX = 100


def socket_path() -> str:
    fallback = os.path.join("/tmp", f"{APP_NAME}-{os.getuid()}.sock")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        path = os.path.join(runtime, f"{APP_NAME}.sock")
        if len(path.encode("utf-8")) <= SOCKET_PATH_MAX:
            return path
    return fallback


def wake_daemon(path: str) -> bool:
    """Ask a running daemon to show its window.  True if one answered."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1.0)
    try:
        client.connect(path)
        client.sendall(b"show")
        return client.recv(8) == b"ok"
    except OSError:
        return False
    finally:
        client.close()


def listen(path: str) -> socket.socket:
    if os.path.exists(path) and not wake_daemon(path):
        os.unlink(path)          # stale socket from a dead daemon
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    os.chmod(path, 0o600)
    server.listen(4)
    server.setblocking(False)
    return server


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

BG = "#1e1e2a"
FG = "#e6e6ef"
DIM = "#8a8aa0"
SEL_BG = "#4b6bdf"
SEL_FG = "#ffffff"
ERR = "#ff8080"
OK = "#7ddc8a"

POLL_MS = 5       # only runs while a command is in flight


class QuickKey:
    def __init__(self, entries, *, timeout, keep_open, strip, title,
                 config_path=None, daemon=False, selections=SELECTIONS,
                 monitor_mode="active"):
        self.entries = entries
        self.timeout = timeout
        self.keep_open = keep_open
        self.strip = strip
        self.selections = selections
        self.monitor_mode = monitor_mode
        self.config_path = config_path
        self.daemon = daemon
        self.config_mtime = self.read_mtime()
        # indices into self.entries -- an entry's displayed number is its
        # position in the config and never changes as you filter
        self.visible: list[int] = list(range(len(entries)))
        self.result: Result | None = None
        self.busy = False
        self.poll_job = None
        self.server: socket.socket | None = None
        self.exit_code = 0

        self.root = tk.Tk()
        self.root.title(title)
        self.root.configure(bg=BG)
        self.root.minsize(360, 160)

        ui_font = ("TkDefaultFont", 11)
        small_font = ("TkDefaultFont", 9)

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.refresh())

        entry = tk.Entry(
            self.root,
            textvariable=self.filter_var,
            bg="#2a2a3a", fg=FG, insertbackground=FG,
            relief="flat", font=ui_font,
        )
        entry.pack(fill="x", padx=8, pady=(8, 4), ipady=4)
        self.entry = entry

        # the status bar claims its strip first so a short window can never
        # squeeze it off the bottom
        self.status = tk.Label(
            self.root, text="", anchor="w", bg=BG, fg=DIM, font=small_font,
        )
        self.status.pack(side="bottom", fill="x", padx=10, pady=(4, 8))

        frame = tk.Frame(self.root, bg=BG)
        frame.pack(fill="both", expand=True, padx=8)

        self.listbox = tk.Listbox(
            frame,
            activestyle="none",
            bg="#23232f", fg=FG,
            selectbackground=SEL_BG, selectforeground=SEL_FG,
            highlightthickness=0, relief="flat",
            font=ui_font, exportselection=False,
        )
        scroll = tk.Scrollbar(frame, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.bind_keys()
        self.refresh()
        self.place_window()

        if daemon:
            self.root.withdraw()
            self.root.protocol("WM_DELETE_WINDOW", self.dismiss)
        else:
            self.present()

    # -- wiring ------------------------------------------------------------

    def bind_keys(self) -> None:
        root = self.root
        root.bind("<Escape>", lambda _e: self.dismiss())
        root.bind("<Control-c>", lambda _e: self.dismiss())
        root.bind("<Control-q>", lambda _e: self.quit())
        root.bind("<Return>", lambda _e: self.activate())
        root.bind("<KP_Enter>", lambda _e: self.activate())
        root.bind("<Down>", lambda _e: self.move(1))
        root.bind("<Up>", lambda _e: self.move(-1))
        root.bind("<Control-n>", lambda _e: self.move(1))
        root.bind("<Control-p>", lambda _e: self.move(-1))
        root.bind("<Next>", lambda _e: self.move(10))
        root.bind("<Prior>", lambda _e: self.move(-10))
        root.bind("<Home>", lambda _e: self.select(0))
        root.bind("<End>", lambda _e: self.select(len(self.visible) - 1))
        for digit in range(1, 10):
            root.bind(f"<Alt-Key-{digit}>", lambda _e, d=digit: self.activate_number(d))

        self.listbox.bind("<Button-1>", self.on_click)
        self.listbox.bind("<Double-Button-1>", self.on_click_activate)
        self.listbox.bind("<Return>", lambda _e: self.activate())

        # keep typing going to the filter box no matter where focus lands
        self.listbox.bind("<Key>", lambda _e: self.entry.focus_set())

    def place_window(self) -> None:
        """Centre on the monitor under the pointer, falling back to the display.

        Called on every show, not just at construction: in daemon mode the
        window is built once and the pointer may be on a different monitor
        each time the hotkey fires.
        """
        width = 460
        height = 96 + 24 * max(3, min(len(self.entries), 14))

        # ~0.05ms, unlike the xdotool call this replaces
        rect = monitor_for(self.root.winfo_pointerx(),
                           self.root.winfo_pointery(),
                           self.monitor_mode)
        if rect is None:
            origin_x, origin_y = 0, 0
            area_w = self.root.winfo_screenwidth()
            area_h = self.root.winfo_screenheight()
        else:
            origin_x, origin_y = rect["x"], rect["y"]
            area_w, area_h = rect["w"], rect["h"]

        x = origin_x + max(0, (area_w - width) // 2)
        y = origin_y + max(0, (area_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def present(self) -> None:
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.focus_force()
        self.entry.focus_set()

    # -- config reloading (daemon mode) ------------------------------------

    def read_mtime(self) -> float:
        try:
            return os.stat(self.config_path).st_mtime if self.config_path else 0.0
        except OSError:
            return 0.0

    def reload_if_changed(self) -> None:
        mtime = self.read_mtime()
        if mtime and mtime != self.config_mtime:
            self.config_mtime = mtime
            try:
                self.entries = load_entries(self.config_path)
            except SystemExit:
                return          # keep the old list rather than dying
            self.place_window()

    # -- list state --------------------------------------------------------

    def refresh(self) -> None:
        needle = self.filter_var.get().strip()
        if needle:
            hits = [i for i, (name, _c) in enumerate(self.entries)
                    if needle.lower() in name.lower()]
            # a bare number is the menu position shown next to the entry, so
            # typing it should put that entry on top -- but keep the text
            # matches, since names have digits in them too
            if needle.isdigit():
                position = int(needle) - 1
                if 0 <= position < len(self.entries):
                    hits = [position] + [i for i in hits if i != position]
            self.visible = hits
        else:
            self.visible = list(range(len(self.entries)))

        self.listbox.delete(0, "end")
        for entry_index in self.visible:
            name = self.entries[entry_index][0]
            prefix = f"{entry_index + 1}. " if entry_index < 9 else "   "
            self.listbox.insert("end", f" {prefix}{name}")

        if self.visible:
            self.select(0)
        if not self.busy:
            self.set_status(self.hint())

    def hint(self) -> str:
        count = len(self.visible)
        total = len(self.entries)
        shown = f"{count}/{total}" if count != total else f"{total}"
        return f"{shown} commands  ·  Enter run  ·  Alt+N quick pick  ·  Esc quit"

    def select(self, index: int) -> None:
        if not self.visible:
            return
        index = max(0, min(index, len(self.visible) - 1))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.listbox.see(index)

    def current(self) -> int | None:
        selection = self.listbox.curselection()
        return selection[0] if selection else None

    def move(self, delta: int) -> str:
        index = self.current()
        self.select(0 if index is None else index + delta)
        return "break"

    def on_click(self, event) -> None:
        index = self.listbox.nearest(event.y)
        self.select(index)
        self.entry.focus_set()

    def on_click_activate(self, event) -> str:
        self.on_click(event)
        self.activate()
        return "break"

    # -- action ------------------------------------------------------------

    def activate(self, index: int | None = None) -> str:
        if self.busy:
            return "break"
        if index is None:
            index = self.current()
        if index is None or not (0 <= index < len(self.visible)):
            return "break"

        import threading

        name, command = self.entries[self.visible[index]]

        command, background = split_background(command)
        if background:
            return self.launch(name, command)

        self.busy = True
        self.result = None
        self.set_status(f"running “{name}” …", DIM)
        threading.Thread(target=self.worker, args=(name, command), daemon=True).start()
        self.poll_job = self.root.after(POLL_MS, self.poll)
        return "break"

    def launch(self, name: str, command: str) -> str:
        """Fire-and-forget: no output, no clipboard, window straight away."""
        try:
            launch_detached(command)
        except OSError as exc:
            self.set_status(f"could not launch “{name}”: {exc}", ERR)
            self.exit_code = 1
            return "break"

        if not self.keep_open:
            self.dismiss()
            return "break"
        self.filter_var.set("")
        self.set_status(f"launched “{name}”", OK)
        return "break"

    def worker(self, name: str, command: str) -> None:
        self.result = run_command(name, command, self.timeout)

    def poll(self) -> None:
        """Only alive while a command runs, so an idle window costs nothing."""
        result = self.result
        if result is None:
            self.poll_job = self.root.after(POLL_MS, self.poll)
            return
        self.poll_job = None
        self.busy = False
        self.result = None
        self.finish(result)

    def activate_number(self, number: int) -> str:
        """Run the entry labelled `number`, if the filter still shows it."""
        try:
            row = self.visible.index(number - 1)
        except ValueError:
            return "break"
        return self.activate(row)

    def finish(self, result: Result) -> None:
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()
            tail = detail[-1] if detail else f"exit status {result.returncode}"
            self.set_status(f"“{result.name}” failed: {tail}", ERR)
            self.exit_code = 1
            return

        text = result.stdout
        if self.strip:
            text = text.strip("\n")

        try:
            written = copy_to_clipboard(text, self.selections)
        except RuntimeError as exc:
            self.set_status(str(exc), ERR)
            self.exit_code = 1
            return

        if not self.keep_open:
            self.dismiss()      # no farewell message: it would only be a flash
            return

        chars = len(text)
        lines = text.count("\n") + 1 if text else 0
        # clear the filter first: that refresh resets the status line, so the
        # confirmation has to be written after it
        self.filter_var.set("")
        self.set_status(
            f"copied {chars} char{'s' if chars != 1 else ''} "
            f"({lines} line{'s' if lines != 1 else ''}) from “{result.name}”"
            f" to {' + '.join(written)}",
            OK,
        )

    def set_status(self, text: str, color: str = DIM) -> None:
        self.status.configure(text=text, fg=color)

    # -- lifecycle ---------------------------------------------------------

    def dismiss(self) -> str:
        """Esc or a finished copy: hide if we are warm, exit if we are not."""
        if self.daemon:
            self.root.withdraw()
            return "break"
        return self.quit()

    def show(self) -> None:
        """A hotkey woke the daemon."""
        self.reload_if_changed()
        self.filter_var.set("")
        self.refresh()
        # before deiconify: the pointer may be on a different monitor than it
        # was when this window was built, or the last time it was shown
        self.place_window()
        self.root.deiconify()
        self.present()
        if self.daemon:
            refresh_monitors()      # keep the cache warm for the next press

    def serve(self, server: socket.socket) -> None:
        """Hook the listening socket straight into Tk's event loop.

        createfilehandler is a Unix-only tkinter extra and is missing from
        some builds; fall back to a slow poll when it is not there.
        """
        self.server = server
        try:
            self.root.tk.createfilehandler(server, tk.READABLE,
                                           lambda *_: self.accept())
        except (AttributeError, tk.TclError):
            self.poll_socket()

    def poll_socket(self) -> None:
        self.accept()
        self.root.after(25, self.poll_socket)

    def accept(self) -> None:
        try:
            conn, _ = self.server.accept()
        except (BlockingIOError, OSError):
            return
        try:
            if conn.recv(16).startswith(b"show"):
                conn.sendall(b"ok")
                self.show()
        except OSError:
            pass
        finally:
            conn.close()

    def quit(self) -> str:
        if self.poll_job is not None:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        self.root.destroy()
        return "break"

    def run(self) -> int:
        self.root.mainloop()
        return self.exit_code


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

class Options:
    config = None
    keep_open = False
    timeout = 30.0
    strip = True
    title = APP_NAME
    selections = SELECTIONS
    monitor_mode = "active"
    daemon = False
    no_daemon = False
    list_only = False


def parse_args(argv: list[str]) -> Options:
    """Only pay for argparse when there is actually something to parse."""
    if not argv:
        return Options()

    import argparse

    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Pop up a menu of commands; copy the chosen one's stdout to the clipboard.",
    )
    parser.add_argument("-c", "--config", help="path to the config file")
    parser.add_argument("-k", "--keep-open", action="store_true",
                        help="stay open after copying instead of exiting")
    parser.add_argument("-t", "--timeout", type=float, default=30.0,
                        help="seconds to let a command run (default: 30, 0 for none)")
    parser.add_argument("--no-strip", action="store_true",
                        help="keep leading/trailing newlines in the copied output")
    parser.add_argument("--selection", choices=("both", "clipboard", "primary"),
                        default="both",
                        help="which X selection to write (default: both; "
                             "PRIMARY is what middle-click and Shift-Insert paste)")
    parser.add_argument("--monitor", choices=("active", "primary", "all"),
                        default="active", dest="monitor_mode",
                        help="which monitor to appear on: active follows the "
                             "mouse pointer (default), primary is the monitor "
                             "X marks primary, all centres across the whole "
                             "display (the old behaviour)")
    parser.add_argument("--daemon", action="store_true",
                        help="stay resident and pop up when a later run wakes it")
    parser.add_argument("--no-daemon", action="store_true",
                        help="draw our own window even if a daemon is running")
    parser.add_argument("--list", action="store_true", dest="list_only",
                        help="print the configured names and exit")
    parser.add_argument("--title", default=APP_NAME, help="window title")
    args = parser.parse_args(argv)

    options = Options()
    options.config = args.config
    options.keep_open = args.keep_open
    options.timeout = args.timeout
    options.strip = not args.no_strip
    options.title = args.title
    options.selections = (SELECTIONS if args.selection == "both"
                          else (args.selection,))
    options.monitor_mode = args.monitor_mode
    options.daemon = args.daemon
    options.no_daemon = args.no_daemon
    options.list_only = args.list_only
    return options


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    options = parse_args(arguments)
    path = socket_path()

    # The fast path: a warm daemon already has Tk up, so hand off and get out
    # without importing tkinter at all.  Only a bare invocation qualifies --
    # the daemon was built with its own options, so honouring flags here would
    # mean silently ignoring them.
    if not arguments:
        if os.path.exists(path) and wake_daemon(path):
            return 0

    config = find_config(options.config)
    entries = load_entries(config)

    if options.list_only:
        width = max(len(name) for name, _ in entries)
        for name, command in entries:
            print(f"{name.ljust(width)}  {command}")
        return 0

    if clipboard_command() is None:
        sys.stderr.write(
            f"{APP_NAME}: warning: no clipboard helper found "
            f"({', '.join(program for program, _ in CLIPBOARD_TOOLS)})\n"
        )

    server = listen(path) if options.daemon else None
    if server is not None:
        # a plain SIGTERM would skip the finally block and strand the socket
        import signal

        def stop(_signum, _frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

    load_tk()
    app = QuickKey(
        entries,
        timeout=options.timeout or None,
        keep_open=options.keep_open,
        strip=options.strip,
        title=options.title,
        config_path=config,
        daemon=options.daemon,
        selections=options.selections,
        monitor_mode=options.monitor_mode,
    )
    if server is not None:
        app.serve(server)
        sys.stderr.write(f"{APP_NAME}: listening on {path}\n")
    try:
        return app.run()
    except SystemExit:
        return 0
    finally:
        if server is not None:
            server.close()
            try:
                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
