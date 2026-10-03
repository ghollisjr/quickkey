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

print()
print("ALL PASS" if not fails else "FAILURES:\n  " + "\n  ".join(fails))
sys.exit(1 if fails else 0)
