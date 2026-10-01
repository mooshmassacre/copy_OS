# macOS user guide — COPY_OS 1.8

## Start with two clicks

1. Install Python 3.9 or newer if it is not already available.
2. [Download the current project ZIP](https://github.com/mooshmassacre/copy_OS/archive/refs/heads/main.zip) and extract it with Archive Utility.
3. Keep `start_copy_OS.command`, `copy_OS.py` and `config.json` in the same writable folder.
4. Double-click **start_copy_OS.command**. COPY_OS opens in Terminal.
5. Enter the source and destination folder paths and select your copy mode. Try Dry Run first.

The launcher searches PATH and common Homebrew and python.org installation locations. It does not install Python and is not a standalone application with Python bundled.

Mount your NAS share in Finder before starting. Mounted shares are usually under `/Volumes`. Logs and reports are written into the `logs` directory beside the script.

You can drag folders from Finder into the source/destination prompts: escaped spaces and quoted paths are accepted. Plain paths containing spaces also work.

## Controls

Press **Spacebar** to pause/resume or **Esc** to cancel. P and C are inactive; Ctrl+C is ignored during the backup operation. See the [keyboard controls section](../README.md#keyboard-controls). Press Enter at the final prompt to finish.

## Troubleshooting

- **Python not found:** install Python 3.9+ from [python.org](https://www.python.org/downloads/macos/), then open the launcher again.
- **Missing copy_OS.py:** extract the complete project and keep its files together.
- **Opens in an editor:** choose **Open With > Terminal**.
- **Permission denied:** if extraction removed executable permissions, open Terminal in the extracted folder and run once:

```sh
chmod +x start_copy_OS.command
```

- **macOS blocks the downloaded file:** review the source and use macOS's normal Open/Privacy & Security workflow. The launcher does not bypass system protections.
- **Folder access denied:** check the selected paths and any macOS folder/network-volume access prompts.
- **Nonzero exit code:** read the error displayed above the message; the launcher waits for Enter.

The launcher was tested locally with Dry Run, paths containing spaces, missing-script handling and error exit codes. The user confirmed successful double-click execution on macOS.

The original v1.8.0 release archive predates this launcher; use the current project ZIP linked above.

Copyright (c) 2026 @moosmassacre <mooshmassacre@mail.com>. MIT License.

