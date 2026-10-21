# Handoff: quickkey multi-monitor positioning fix

## Problem

On multi-monitor X11 setups, quickkey's picker window always appears on the
primary monitor, regardless of which monitor currently has focus / the mouse
pointer. User reports: active window, mouse, and keyboard are all on monitor A,
but quickkey pops up on monitor B every time.

## Root cause

Likely in `quickkey.py`'s window-positioning code. tkinter's
`winfo_screenwidth()` / `winfo_screenheight()` return the dimensions of the
entire X display spanning all monitors (the full Xinerama rectangle), not of
the current monitor. Centering math like `(sw - w) // 2` then lands near the
seam between monitors — in a two-monitor horizontal setup, that's typically on
the primary (HDMI) monitor, not where the user is working.

Not an IceWM-specific bug; same behavior would occur under any X11 WM.

## Desired behavior

Window appears on the monitor containing the mouse pointer at the moment
quickkey is invoked. Falls back to current behavior if monitor detection fails
(e.g. xdotool / xrandr unavailable).

## Suggested approach

Add a helper function that:
1. Gets the pointer position (xdotool getmouselocation, or python-xlib)
2. Parses `xrandr --listmonitors` for monitor rectangles
3. Returns the rectangle containing the pointer

Then use that rectangle (not the full screen) in the existing centering math.

### Reference implementation sketch

```python
import re
import subprocess

def active_monitor_geometry():
    """(x, y, w, h) of the monitor containing the mouse pointer, or None."""
    try:
        out = subprocess.check_output(
            ["xdotool", "getmouselocation", "--shell"],
            text=True, timeout=1,
        )
        env = dict(line.split("=", 1) for line in out.strip().splitlines())
        px, py = int(env["X"]), int(env["Y"])
    except (subprocess.SubprocessError, FileNotFoundError, KeyError, ValueError):
        return None

    try:
        out = subprocess.check_output(
            ["xrandr", "--listmonitors"], text=True, timeout=1,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

    # Lines like: " 0: +*HDMI-1 1920/598x1080/336+1920+0  HDMI-1"
    for line in out.splitlines()[1:]:
        m = re.search(r'(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)', line)
        if not m:
            continue
        w, h, x, y = (int(g) for g in m.groups())
        if x <= px < x + w and y <= py < y + h:
            return (x, y, w, h)
    return None
```

Where the window is positioned (search for `winfo_screenwidth` or `geometry`),
prefer this over the full-screen fallback:

```python
geom = active_monitor_geometry()
if geom:
    mx, my, mw, mh = geom
    x = mx + (mw - w) // 2
    y = my + (mh - h) // 3
else:
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 3
root.geometry(f"{w}x{h}+{x}+{y}")
```

## Daemon mode wrinkle

Important: quickkey has a `--daemon` resident mode where the window is built
once and un-hidden on each hotkey press. The position computation currently
likely runs at window creation, not at un-hide time. For the fix to work in
daemon mode, repositioning must happen inside the hotkey/socket handoff
handler, right before the window is deiconified / shown.

Look for the socket-receive path (whatever handles incoming client connections
in the daemon). The reposition call needs to go there, not in the one-shot
window setup.

## Suggested flag

Consider adding `--monitor` with choices:
- `active` (new default) — follow the pointer
- `primary` — explicit primary monitor
- `all` — current behavior (center across the whole X display)

Keeps the behavior change discoverable and reversible for anyone who preferred
the old behavior.

## Dependencies

- `xdotool` is already a common dependency; many quickkey users will have it
- `xrandr` is standard with X11, no issue
- Alternative: `python-xlib` for pointer position without a subprocess (one
  less fork per invocation, ~5ms savings). Optional dep is probably not worth
  it given quickkey's speed is already dominated by Tk startup.

Prefer the subprocess approach to keep stdlib purity consistent with the rest
of the project.

## Testing

- Single-monitor case: active_monitor_geometry should return that monitor's
  rect, which equals the full screen rect, so behavior is identical to before.
- Multi-monitor case with pointer on secondary: window should appear on
  secondary.
- Pointer at exact monitor boundary: deterministic (first match wins, which is
  what the `x <= px < x+w` condition gives).
- xdotool/xrandr missing: falls back to full-screen centering (current
  behavior), no regression.
- Daemon mode: reposition must happen on each un-hide, not just at daemon
  start.

Add a test in `tests/regress.py` that mocks `xdotool`/`xrandr` subprocess
output and verifies the positioning math.

## Related

The user also notes similar multi-monitor awareness would be useful for
IceWM's taskbar following focus, but that's a WM-level concern separate from
quickkey. Scope here is just the picker window position.
