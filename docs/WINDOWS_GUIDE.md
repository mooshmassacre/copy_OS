# Windows user guide — COPY_OS 1.8

## Download and install

1. Install Python 3.9 or newer from [python.org](https://www.python.org/downloads/windows/). Enable the Python launcher or add Python to PATH when the installer offers those options.
2. [Download the current project ZIP](https://github.com/mooshmassacre/copy_OS/archive/refs/heads/main.zip).
3. Right-click the ZIP and select **Extract All**. Open the extracted project folder; do not run the launcher from inside the ZIP.
4. Keep `start_copy_OS.bat`, `copy_OS.py` and `config.json` together.
5. Double-click **start_copy_OS.bat**.

The launcher looks for a working Python 3.9+ installation through `py -3`, `python`, then `python3`. No extra Python packages are required. It does not install Python or request administrator privileges.

The original v1.8.0 release ZIP predates this launcher. Use the current project ZIP above to get it.

## Start a backup

1. Enter the source folder, for example `D:\Projects`.
2. Enter the destination folder, for example `\\NAS\Backups\Projects` or `E:\Backups`.
3. Choose **2 — Dry Run** for an initial simulation. It creates logs and a report, but no destination files.
4. For a real copy, choose **1 — Normal backup**.
5. Choose **1 — Fast copy** or **2 — Secure copy**.

Both modes skip files considered up to date using size and modification time. Secure mode verifies transferred files using SHA-256 before replacement; it does not hash skipped files. Files that exist only at the destination are not deleted.

Open the NAS share in File Explorer first and confirm that you can access it using your normal Windows account. Run one COPY_OS instance per destination at a time.

## Pause, resume and cancel

Keep the terminal focused:

- **P:** pause or resume.
- **C:** cancel.
- **Ctrl+C:** also cancels.

These controls do not require Enter. During copying or hashing, a blocked operating-system read/write must return before input can be handled. Cancellation preserves completed copies and attempts to remove the active temporary file. Use the cancellation controls rather than closing the terminal window.

At completion, review the summary and press Enter to exit.

## Configuration and reports

Edit `config.json` beside the script to adjust retry limits and additional exclusions. Restart COPY_OS after editing it. See the [configuration reference](../README.md#configuration) for valid options.

When started with the BAT file, logs and reports are written into the project's `logs` folder. Keep the extracted project in a location where your account can write files. The launcher also supports folder names containing spaces and UNC network locations.

## Troubleshooting

| Message or symptom | What to do |
|---|---|
| Python 3.9 or newer was not found | Install a supported Python version with its launcher or PATH option, then open the BAT file again. |
| Could not find copy_OS.py | Extract the full project and keep the BAT file beside the script. |
| Could not open the folder | Check that the drive or network share containing the launcher is available. |
| Microsoft Store opens during Python detection | A Windows app execution alias may be handling `python`. Install Python and its launcher, or adjust the Python aliases in Windows Settings to use your installed interpreter. |
| CONFIGURATION ERROR | Check `config.json` for valid JSON and supported English option names. |
| Access denied or network error | Check access to both folders in File Explorer and review the reported path and error. |
| COPY_OS exited with code ... | Read the error shown above the message before closing the window. |
| Broken logo or escape characters | Use an ANSI-capable terminal with a Unicode monospace font, such as Windows Terminal. |

The BAT file has been reviewed for interpreter detection, paths and Windows line endings. It has not yet been executed on a real Windows system. Windows/NAS validation remains pending.

Copyright (c) 2026 @moosmassacre <mooshmassacre@mail.com>. MIT License.
