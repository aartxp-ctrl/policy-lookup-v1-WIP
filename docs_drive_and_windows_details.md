# Policy Lookup on Windows — install and use

Works on Windows 10/11, 64-bit Intel or AMD PCs (not ARM/Snapdragon).
Nothing is installed into Windows: everything lives in this one folder.
Needs about 8 GB of free space and 8 GB of RAM (16 GB is smoother).

## Option A — from the prepared drive (no internet at all)

1. Plug in the drive. (It must be formatted **exFAT** — Mac-only formats are invisible to Windows.)
2. Open the drive in File Explorer → `policy-lookup-v2` → double-click **`Run.bat`**.
3. **First time only:** a black window sets things up for 3–5 minutes. Leave it.
4. Your browser opens the app at `http://127.0.0.1:8501`.

## Option B — from GitHub (internet needed once)

1. On the GitHub page: **Code → Download ZIP**. Right-click the ZIP → **Extract All…**
   to a folder you choose (or straight onto a USB drive).
2. Double-click **`Setup.bat`** and wait (downloads ~3–4 GB: Python, packages,
   the local AI and its model). If the internet drops, run it again — it continues.
3. Disconnect from the internet if you like, then double-click **`Run.bat`**.

## Warnings you may see (all expected)

| Message | What to do |
|---|---|
| "Windows protected your PC" (SmartScreen) | **More info → Run anyway** |
| Windows Firewall asks about `python.exe` or `ollama.exe` | **Cancel / don't allow** is fine — the app only talks to this computer |
| Antivirus scans the folder on first run | Let it finish; the first start is slower |

## Using it

1. **Add documents:** drag PDFs, scans or Word files into the left sidebar
   (or put many files in `reference_docs\` and they can be added in bulk).
   Scanned pages take longer — a few seconds per page.
2. **Ask a question** in plain English or Hinglish, e.g. *"maximum quantity for a repeat order"*,
   *"retirement age of a Brigadier"*, *"accused ko defending officer milta hai kya"*.
3. **Read the answer in this order:**
   - ⚠️ the warning — this is an offline AI tool and can be wrong;
   - **Short answer** with its source (file · clause · page);
   - the **exact sentence** from the document right under it;
   - the full matching clauses below.
   **Before signing anything, read the exact text, not just the short answer.**
4. To search only some documents, untick the others in the sidebar.
5. **To stop:** close the black window.

## If something goes wrong

| Problem | Fix |
|---|---|
| Browser didn't open | Open `http://127.0.0.1:8501` yourself |
| "Local AI not running" in the sidebar | Search still works. Close the window and start `Run.bat` again; details are in `data\ollama.log` |
| Very slow short answers | Normal on older PCs (10–30 s). Search results appear first regardless |
| Port already in use | Another copy is running — close the other black window |
| Start over completely | Delete `runtime\venv-windows-amd64` and run `Run.bat` again |
