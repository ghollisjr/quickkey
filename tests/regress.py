import os, sys, time, subprocess, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import quickkey as qk
qk.load_tk()
CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "quickkey.conf")
fails = []
def check(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok: fails.append(f"{label}: got {got!r} want {want!r}")

def clip(sel="clipboard"):
    return subprocess.run(["xclip","-o","-selection",sel],
                          capture_output=True, text=True).stdout.strip()

print("1. one-shot copy + exit")
app = qk.QuickKey(qk.load_entries(CONF), timeout=10, keep_open=False, strip=True, title="t")
app.filter_var.set("uuid"); app.root.update()
check("filtered", [qk.load_entries(CONF)[i][0] for i in app.visible], ["UUID"])
app.activate(); app.root.mainloop()
check("exit code", app.exit_code, 0)
check("uuid length", len(clip()), 36)

print("2. failure path copies nothing")
app = qk.QuickKey([("Bad","echo nope >&2; exit 3")], timeout=5, keep_open=True, strip=True, title="t")
before = clip(); app.activate()
while app.busy: app.root.update()
check("status", app.status.cget("text"), "“Bad” failed: nope")
check("clipboard untouched", clip(), before)
check("exit code", app.exit_code, 1)
app.root.destroy()

print("3. fixed numbering + Alt-N")
app = qk.QuickKey([("One","echo one"),("Two","echo two"),("Three","echo three")],
                  timeout=5, keep_open=True, strip=True, title="t")
app.filter_var.set("e"); app.root.update()
check("rows", [app.listbox.get(i).strip() for i in range(app.listbox.size())],
      ["1. One", "3. Three"])
app.activate_number(2); app.root.update()
check("hidden Alt-2 inert", app.busy, False)
app.activate_number(3)
while app.busy: app.root.update()
check("Alt-3 ran Three", clip(), "three")
app.root.destroy()

print("4. both selections (the urxvt fix)")
app = qk.QuickKey([("T","echo sel-test")], timeout=5, keep_open=True, strip=True, title="t")
app.activate()
while app.busy: app.root.update()
check("CLIPBOARD", clip("clipboard"), "sel-test")
check("PRIMARY", clip("primary"), "sel-test")
app.root.destroy()

print("5. daemon cycle")
C2, S2 = "/tmp/qk-regress.conf", "/tmp/qk-regress.sock"
open(C2,"w").write("[Alpha]\ncommand = echo alpha\n\n[Beta]\ncommand = echo beta\n")
if os.path.exists(S2): os.unlink(S2)
app = qk.QuickKey(qk.load_entries(C2), timeout=5, keep_open=False, strip=True,
                  title="t", config_path=C2, daemon=True)
app.serve(qk.listen(S2))
def pump(sec=0.4):
    end = time.time()+sec
    while time.time() < end: app.root.update(); time.sleep(0.005)
check("starts hidden", app.root.state(), "withdrawn")
threading.Thread(target=lambda: qk.wake_daemon(S2), daemon=True).start(); pump()
check("wakes", app.root.state(), "normal")
app.activate_number(2)
while app.busy: app.root.update()
pump(0.1)
check("hides after copy", app.root.state(), "withdrawn")
check("copied beta", clip(), "beta")
time.sleep(0.05)
open(C2,"w").write("[Alpha]\ncommand = echo alpha\n\n[Gamma]\ncommand = echo gamma\n")
threading.Thread(target=lambda: qk.wake_daemon(S2), daemon=True).start(); pump()
check("config reloaded", [app.listbox.get(i).strip() for i in range(app.listbox.size())],
      ["1. Alpha", "2. Gamma"])
app.root.destroy(); os.unlink(S2); os.unlink(C2)

print("6. multi-monitor positioning")
# a 2560x1440 monitor at the origin, a 1920x1080 primary to its right
LAYOUT = [{"x": 0, "y": 0, "w": 2560, "h": 1440, "primary": False},
          {"x": 2560, "y": 0, "w": 1920, "h": 1080, "primary": True}]
check("parses xrandr --listmonitors", qk.parse_monitors(
    "Monitors: 2\n"
    " 0: +DP-2 2560/697x1440/392+0+0  DP-2\n"
    " 1: +*HDMI-1 1920/598x1080/336+2560+0  HDMI-1"), LAYOUT)

qk._monitors = LAYOUT                       # pretend the cache is warm
check("pointer on left monitor",  qk.monitor_for(100, 100, "active"), LAYOUT[0])
check("pointer on right monitor", qk.monitor_for(3000, 500, "active"), LAYOUT[1])
check("exact seam goes right",    qk.monitor_for(2560, 0, "active"), LAYOUT[1])
check("last pixel stays left",    qk.monitor_for(2559, 0, "active"), LAYOUT[0])
check("primary mode",             qk.monitor_for(100, 100, "primary"), LAYOUT[1])
check("all mode centres on display", qk.monitor_for(100, 100, "all"), None)
check("pointer off every monitor", qk.monitor_for(9999, 9999, "active"), None)

# the geometry the window actually asks for, per mode
def placed(mode, px, py):
    app = qk.QuickKey([("A", "true")], timeout=5, keep_open=True, strip=True,
                      title="t", monitor_mode=mode)
    app.root.winfo_pointerx = lambda: px
    app.root.winfo_pointery = lambda: py
    qk._monitors = LAYOUT
    app.place_window(); app.root.update_idletasks()
    geom = app.root.geometry(); app.root.destroy()
    return geom

w, h = 460, 96 + 24*3
check("placed on left monitor",  placed("active", 100, 100),
      f"{w}x{h}+{(2560-w)//2}+{(1440-h)//3}")
check("placed on right monitor", placed("active", 3000, 500),
      f"{w}x{h}+{2560+(1920-w)//2}+{(1080-h)//3}")
check("placed on primary",       placed("primary", 100, 100),
      f"{w}x{h}+{2560+(1920-w)//2}+{(1080-h)//3}")
qk._monitors = None

print("7. trailing & launches in the background")
for raw, want in [("libreoffice &", ("libreoffice", True)),
                  ("libreoffice", ("libreoffice", False)),
                  ("echo a && echo b", ("echo a && echo b", False)),
                  ("firefox  &  ", ("firefox", True))]:
    check(f"split {raw!r}", qk.split_background(raw), want)

MARK = "/tmp/qk-bg-marker"
if os.path.exists(MARK): os.unlink(MARK)
subprocess.run(["xclip","-selection","clipboard"], input=b"untouched")
time.sleep(0.2)

# a command that outlives the picker by a wide margin
app = qk.QuickKey([("Slow", f"sleep 1.5; touch {MARK} &")], timeout=30,
                  keep_open=True, strip=True, title="t")
t0 = time.time()
app.activate()
elapsed = time.time() - t0
check("returns immediately, no 30s wait", elapsed < 0.5, True)
check("never goes busy", app.busy, False)
check("status says launched", app.status.cget("text"), "launched “Slow”")
check("clipboard untouched", clip(), "untouched")
check("marker not written yet", os.path.exists(MARK), False)
app.root.destroy()

# the child must survive the picker being gone
time.sleep(2.0)
check("child ran after window closed", os.path.exists(MARK), True)
if os.path.exists(MARK): os.unlink(MARK)

print()
print("ALL PASS" if not fails else "FAILURES:\n  " + "\n  ".join(fails))
sys.exit(1 if fails else 0)
