# COPY_OS

### Secure File Transfer System — NAS Backup

A lightweight Python-based file synchronization and backup utility designed for reliable transfers between local storage and NAS/network locations.

COPY_OS provides two copy modes: a fast metadata-based mode and a secure SHA-256 verification mode.

---

## ✨ Features

- 🚀 Fast incremental file copying
- 🔐 Optional SHA-256 integrity verification
- 📊 Real-time terminal progress interface
- 📁 Incremental synchronization
- 🧪 Dry Run mode
- 🔄 Automatic retry system
- 💾 Destination free-space verification
- 📝 Detailed backup logs
- 🛡️ Temporary `.copying` files to prevent incomplete destination files
- ⚛️ Atomic file replacement using `os.replace()`
- 💽 `fsync()` to flush written data in secure mode
- 🔍 Detection of source files modified during transfer
- 🖥️ Windows long-path support
- 🌐 NAS / SMB / network path support
- 🚫 Never deletes files from the destination
- 🧹 Built-in exclusions for temporary and system files

---

## 🔒 Copy Modes

COPY_OS offers two transfer modes.

### 1. Fast Copy

```text
[1] Cópia rápida
```

Uses file metadata to determine whether a file needs to be copied:

- File size
- Modification date

No SHA-256 calculation or second hash-reading pass is performed. Fast mode also skips per-file `fsync()`.

This mode is recommended for normal backups where maximum transfer performance is desired.

### 2. Secure Copy

```text
[2] Cópia segura
```

Uses SHA-256 to verify file integrity.

During the transfer, COPY_OS calculates the SHA-256 hash of the source while writing the destination temporary file.

The temporary file is flushed with `fsync()`, then hashed and compared with the source hash before it replaces the destination.

This mode provides stronger integrity verification at the cost of additional processing time.

---

## 🧠 How It Works

COPY_OS does not blindly copy every file.

For each source file, it checks whether the destination already contains an equivalent up-to-date file.

A file is copied when:

- The destination does not exist
- The file size is different
- The source modification date is newer

Otherwise, the existing destination file is skipped in both modes. Secure mode verifies newly copied files; it does not hash files skipped by the metadata check.

---

## 🛡️ Safe Transfer Process

Files are never written directly over the destination file.

COPY_OS uses a temporary file:

```text
source/file.mkv
        ↓
destination/file.mkv.copying
        ↓
verification
        ↓
destination/file.mkv
```

After the transfer and verification are successful, the temporary file replaces the destination using an atomic operation.

If the transfer fails, the original destination file is preserved.

---

## 🔄 Automatic Retry

Network storage can occasionally experience interruptions or temporary I/O failures.

COPY_OS automatically retries failed transfers.

Current configuration:

```python
MAX_TENTATIVAS = 5
ESPERA_RETRY = 10
```

This means the application can retry a failed file transfer with up to 5 total attempts, waiting 10 seconds between attempts. After the fifth failed attempt, the file is recorded as failed and processing continues. The screen and log show the failing operation, exception, errno and Windows error code when available.

Both modes re-read the source size at the start of every attempt. A change after the initial scan no longer causes retries against an obsolete size. Changes detected during the transfer still cause rejection and retry.

---

## 🧪 Dry Run

Before performing a real backup, COPY_OS can run in **Dry Run** mode.

Dry Run analyzes the source and destination and reports which files would be copied without modifying the destination.

Simulated copies now advance the processed-byte count correctly. Logs are still written in the current working directory. The display updates for each simulated file, with separate simulated file and byte totals. No SHA-256 verification is performed in Dry Run.

This is useful for validating a backup operation before executing it.

---

## 📊 Terminal Interface

COPY_OS provides a fixed terminal interface displaying:

- Current file
- Current file progress
- Overall progress
- Processed files / total files
- Separate copied, skipped, failed and simulated counters
- Transfer speed
- Estimated remaining time
- Current operation
- SHA-256 verification status when enabled

The 1.6 interface clears and redraws the screen with `ESC[2J` and `ESC[H` on each update. It no longer moves the cursor up a fixed number of lines, correcting repeated progress bars caused by wrapped file paths. Use an ANSI-compatible terminal.

Illustrative example:

```text
┌─────────────────────────────────────────┐
│       SECURE FILE TRANSFER SYSTEM       │
│          NAS BACKUP 1.7 by MOOSH        │
└─────────────────────────────────────────┘

MODE: FAST COPY

████████████████████████████░░░░░░░░░░░░  68%

Files: 184 / 267
Speed: 185.4 MB/s
ETA:   00:03:21

Current file:
Movies/Example Movie/example.mkv

COPIANDO — SEM HASH
```

---

## 📝 Logging

Each backup operation generates a timestamped log inside:

```text
logs/
```

Example:

```text
logs/
└── backup_2026-09-08_14-30-52.log
```

Logs contain information about successful transfers, failures, retries and integrity verification.

---

## 🚫 File Exclusions

COPY_OS automatically ignores common temporary and system files and directories. Matching is case-insensitive; every name beginning with `._` is excluded.

### Extensions

```text
.tmp
.part
.crdownload
.download
.partial
.!ut
.bc!
```

### Files and directories

```text
Thumbs.db
.DS_Store
._*
desktop.ini
.AppleDouble
.Spotlight-V100
.Trashes
.fseventsd
.DocumentRevisions-V100
.TemporaryItems
.VolumeIcon.icns
@eaDir
#recycle
$RECYCLE.BIN
System Volume Information
```

---

## 💻 Requirements

- Python 3.9+
- No external Python packages required
- Read access to the source
- Write access to the destination
- Sufficient free disk space

COPY_OS uses only Python's standard library.

---

## 🚀 Usage

Clone the repository:

```bash
git clone https://github.com/mooshmassacre/copy_OS.git
```

Enter the project directory:

```bash
cd copy_OS
```

Run:

```bash
python3 copy_OS_1.7.py
```

On Windows:

```powershell
python copy_OS_1.7.py
```

---

The prompts ask for source, destination, normal backup or Dry Run, and (for normal backups) fast or secure copy.

`copy_OS_1.7.py` is the current release. Both `copy_OS_1.6.py` and the original `copy_OS.py` are retained as previous versions.

## 🌐 NAS Usage

COPY_OS can be used with NAS storage accessed through SMB/network paths.

For reliable network operation, using the NAS path directly is recommended instead of relying on mapped drive letters when possible.

Example:

```text
Windows:
\\NAS\Backup\Media

macOS / Linux:
/Volumes/Backup/Media
```

---

## ⚙️ Configuration

Main configuration values are located near the beginning of the Python script.

```python
BAR_WIDTH = 50
BLOCO_COPIA = 8 * 1024 * 1024

MAX_TENTATIVAS = 5
ESPERA_RETRY = 10
```

### Transfer block size

```python
BLOCO_COPIA = 8 * 1024 * 1024
```

The default transfer block is 8 MB.

---

## ⚠️ Fast Mode vs Secure Mode

| Feature | Fast | Secure |
|---|:---:|:---:|
| Incremental copy | ✅ | ✅ |
| Size verification | ✅ | ✅ |
| Modification date | ✅ | ✅ |
| SHA-256 | ❌ | ✅ |
| Integrity verification | Basic | Strong |
| Performance | 🚀 Faster | 🔐 Slower |
| Recommended for | Normal backups | Critical data |

Fast mode intentionally trades content hashing for performance.

Secure mode should be preferred when verifying the exact content of transferred files is important.

---

## 🔐 Data Safety

COPY_OS is designed with a **non-destructive destination policy**.

The application:

- Does not delete destination files
- Does not automatically mirror deletions
- Does not remove files that exist only on the destination
- Uses temporary files during transfers
- Verifies the copied data in secure mode before replacement

This makes COPY_OS suitable for backup-oriented workflows rather than destructive synchronization.

---

## 📌 Project Status

**Current version: 1.7.0**

Speed uses transferred bytes and active copy time (including source hashing and flush), excluding skipped files, retry waits and temporary-file verification. Transferred bytes include retry traffic; successfully copied bytes count completed files once. System/NAS caching can still affect the measurement. ETA estimates pending copy time only, excluding future retries and SHA-256 verification. The overall bar measures processing, not success; check the failure count.

The final summary and log separate scanned, copied, skipped, simulated and failed bytes, failed attempts, verified files, copy time and temporary-file verification time. Free-space checking compares free space against the entire scanned source size and may warn even for incremental backups.

COPY_OS is actively developed.

Future releases may introduce additional improvements to performance, verification, reporting and synchronization capabilities.

---

## Release 1.7

- Expanded transfer statistics and per-file Dry Run updates.
- Refreshed source size on every attempt in both modes.
- At most five attempts per file, with explicit failure reporting.
- Secure mode retains source-change detection, `fsync()`, SHA-256 comparison and replacement only after successful verification.
- Restored large block-letter title with aligned frame.

## 🗺️ Roadmap

Planned improvements may include:

- [x] Corrected Dry Run processed-byte calculation (1.6)
- [x] Additional transfer statistics (1.7)
- [ ] Configuration file
- [ ] More detailed error reporting
- [ ] Additional NAS/network optimizations
- [ ] Improved recovery of interrupted transfers
- [ ] Expanded verification options
- [ ] GUI interface

---

## 📄 License

License information will be added in a future release.

---

## 👤 Author

**MOOSH**

COPY_OS — Secure File Transfer System

Built with Python.
