# quickkey

*(said like "quickie")*

A keystroke-launched menu of commands.  Hit your hotkey, a small window pops
up, you pick an entry by clicking or typing, the command runs, and its stdout
is waiting on your clipboard.

Pure standard library — Python 3 with tkinter.  The only outside dependency is
a clipboard helper (`xclip`, `xsel`, `wl-copy`, or `pbcopy`), which you almost
certainly already have.

## Install

### Arch Linux

It is on the AUR as [`quickkey`](https://aur.archlinux.org/packages/quickkey):

```sh
paru -S quickkey        # or: yay -S quickkey
```

Or without a helper:

```sh
git clone https://aur.archlinux.org/quickkey.git
cd quickkey && makepkg -si
```

Either way you get `/usr/bin/quickkey`, a systemd **user** unit, and a
fallback config at `/usr/share/quickkey/quickkey.conf`.

```sh
systemctl --user enable --now quickkey.service   # keep it warm (see Speed)
```

The `PKGBUILD` in this repo is the same one the AUR carries: it builds the
tagged release tarball from GitHub, not your working tree.  To package an
unreleased change, tag it first.  Releasing:

```sh
git tag -a v1.2.3 -m 'quickkey 1.2.3' && git push origin v1.2.3
rm -f *.tar.gz && updpkgsums          # a cached tarball will be re-hashed
makepkg --printsrcinfo > .SRCINFO     # the AUR rejects a stale .SRCINFO
```

then commit `PKGBUILD` and `.SRCINFO` to the AUR repo.

### Anywhere else

There is nothing to build -- drop `quickkey.py` somewhere on your `PATH`.

## Config

INI format.  Each section name is what shows up in the menu; `command` is run
through the shell.

```ini
[Public IP]
command = curl -s https://ifconfig.me

[UUID]
command = cat /proc/sys/kernel/random/uuid

[Password (24 chars)]
command = tr -dc 'A-Za-z0-9!@#$%^&*' < /dev/urandom | head -c 24
```

quickkey looks for the config in this order:

1. `$QUICKKEY_CONFIG`
2. `~/.config/quickkey/config` (then `config.ini`)
3. `~/.quickkeyrc`
4. `quickkey.conf` next to the script
5. `/etc/quickkey/config`
6. `/usr/share/quickkey/quickkey.conf` (shipped by the package)

`quickkey.conf` in this repo is a starter — copy it into place:

```sh
mkdir -p ~/.config/quickkey
cp quickkey.conf ~/.config/quickkey/config
```

## Keys

| Key | Action |
| --- | --- |
| type anything | filter the list |
| type a number | jump to that entry (`4` puts item 4 on top) |
| `Up` / `Down`, `Ctrl-p` / `Ctrl-n` | move |
| `PgUp` / `PgDn`, `Home` / `End` | jump |
| `Enter` | run the selection and copy its output |
| `Alt-1` … `Alt-9` | run that numbered entry directly |
| `Esc`, `Ctrl-q` | quit without doing anything |

Single click selects, double click runs.

The window closes itself once something is copied.  Pass `-k` to keep it open
for copying several things in a row.

## Speed

Cold, it is about 60 ms from exec to a window on screen -- roughly 45 ms of
that is Tk building itself, which no amount of tuning inside the script will
recover.  To get out of that entirely, leave one resident:

```sh
quickkey --daemon &
```

It builds the window, hides it, and waits on a unix socket.  A later bare
`quickkey` notices the socket, hands off, and exits -- about **20 ms**, and
the already-built window just un-hides.  Bind the hotkey to plain `quickkey`
either way: with no daemon running it draws its own window as usual, so the
same binding works whether or not the daemon is up.

The daemon re-reads the config whenever the file's mtime changes, so editing
your commands does not mean restarting it.

Only a bare `quickkey` hands off.  Anything with flags (`quickkey -k`) draws
its own window, since the resident one was built with its own options and
silently ignoring yours would be worse.

The package ships `quickkey.service` for this:

```sh
systemctl --user enable --now quickkey.service
```

It is a *user* unit -- `--user` is not optional.  A plain
`systemctl enable quickkey` talks to the system manager and reports "Unit
quickkey.service could not be found" even though the file is installed; so
does `systemctl status quickkey`.  Always pass `--user`.

It installs into `default.target`, not `graphical-session.target`.  The
latter reads better, but many X setups (startx, bare window managers, some
display managers) never activate it, and a unit wanted by an inactive target
silently never starts.  Check yours with:

```sh
systemctl --user is-active graphical-session.target
```

If it says `active`, either target works.  The unit is still *ordered* after
it, and retries for a minute if the display is not up yet.

If the service starts but the hotkey never reaches it, your session probably
did not export `DISPLAY` into the systemd user manager.  Check with
`systemctl --user show-environment`; most desktops populate it, and
otherwise:

```sh
dbus-update-activation-environment --systemd DISPLAY WAYLAND_DISPLAY XAUTHORITY
```

Running from a checkout instead of the package? Copy the unit to
`~/.config/systemd/user/` and point `ExecStart` at your `quickkey.py`.

## Bind it to a hotkey

The script is the whole program — point your window manager at it.

**i3 / sway**

```
bindsym $mod+c exec --no-startup-id /home/ghollisjr/myprogs/quickkey/quickkey.py
```

**xbindkeys** (`~/.xbindkeysrc`, works under most X11 setups)

```
"/home/ghollisjr/myprogs/quickkey/quickkey.py"
  Mod4 + c
```

**GNOME**: Settings → Keyboard → Custom Shortcuts → add the script's path.

**KDE**: System Settings → Shortcuts → Custom Shortcuts → new global shortcut
running the script.

## Options

```
-c, --config PATH   use this config file
    --daemon        stay resident; later bare runs pop this window up
    --no-daemon     draw our own window even if a daemon is running
-k, --keep-open     stay open after copying instead of exiting
-t, --timeout SECS  give up on a command after this long (default 30, 0 = never)
    --no-strip      keep leading/trailing newlines in the copied text
    --list          print the configured names and commands, then exit
    --title TEXT    window title
```

## Notes

Nothing is printed on the way out -- on a successful copy the window just
goes, since a confirmation you cannot read before it disappears is only a
flash.  Run with `-k` if you want to see what was copied.

Trailing newlines are stripped from the output before copying, so
`date`-style one-liners paste cleanly.  Use `--no-strip` if you want the bytes
verbatim.

If a command exits non-zero, nothing is copied — the last line of its stderr
shows in the status bar instead and the window stays put.

Commands run in a background thread, so a slow one doesn't freeze the window.
