# Running this on Windows

> **`python3` and `python` may be two different Pythons.** On Windows,
> `python3` is often the Microsoft Store's Python (3.14 on the team laptop)
> with none of the backend's packages or Playwright, while `python` is the one
> you installed them into. `mingw32-make` now picks the first of the two that
> can import `fastapi` and `playwright` (and says which, before the backend
> tests run). If it picks wrong, say so: `mingw32-make test PY=python`.

Two ways. The first needs one install and takes five minutes; the second needs
nothing but does not run the laptop demo.

---

## A. Run the demo on your laptop (do this first)

You need a C++ compiler. You already have Chocolatey, so in **PowerShell as
Administrator**:

```powershell
choco install mingw -y
```

Close that window, open a **normal PowerShell** in the project folder, and check:

```powershell
g++ --version
```

If that prints a version, you are ready. Now:

```powershell
g++ -std=gnu++17 -DAC_LOG_CAPACITY=4096 -Ilib/ac `
    lib/ac/ac_sha256.cpp lib/ac/ac_record.cpp lib/ac/ac_node.cpp lib/ac/ac_gateway.cpp lib/ac/ac_sim.cpp `
    native/main.cpp -o demo.exe
.\demo.exe
```

and the tests:

```powershell
g++ -std=gnu++17 -DAC_LOG_CAPACITY=4096 -Ilib/ac `
    lib/ac/ac_sha256.cpp lib/ac/ac_record.cpp lib/ac/ac_node.cpp lib/ac/ac_gateway.cpp lib/ac/ac_sim.cpp `
    tools/selftest.cpp -o selftest.exe
.\selftest.exe
```

Use **Windows Terminal** or **PowerShell 7**, not the old `cmd.exe` — the output
is coloured and legacy `cmd` shows the colour codes as stray characters.

---

## B. Set up for the board (do this while the parts ship)

1. Open **VS Code**.
2. Extensions → search **PlatformIO IDE** → Install. It downloads its own
   compiler for the ESP32, so you do not need MinGW for this part.
3. File → Open Folder → this `annachain` folder.
4. Wait for PlatformIO to finish indexing (bottom status bar goes quiet).

Nothing to build yet — you have no board. When it arrives:

```powershell
pio run -e node_mock -t upload
python tools/server.py COM5
```

Find the right COM port in Device Manager under **Ports (COM & LPT)** after you
plug the board in. If `python` is not found, use `py` instead, and install
pyserial once:

```powershell
py -m pip install pyserial
```

---

## If something goes wrong

**`g++ is not recognized`** — the Chocolatey install did not put it on PATH.
Close and reopen PowerShell first. If it still fails, run
`choco install mingw -y --force` and reopen again.

**Garbled characters like `←[32m`** — you are in legacy `cmd.exe`. Open Windows
Terminal instead.

**`pio is not recognized`** — use PlatformIO from inside VS Code (the alien-head
icon in the left bar → Project Tasks), rather than from PowerShell.
