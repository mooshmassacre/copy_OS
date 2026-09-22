# SPDX-License-Identifier: MIT
# Copyright (c) 2026 @moosmassacre <mooshmassacre@mail.com>

# COPY_OS 1.8 — local test build.
# Fast/secure copying, keyboard controls, responsive scanning, configuration,
# transfer statistics and detailed error reports.
import os
import sys
import shutil
import time
import hashlib
from dataclasses import dataclass, field
from functools import partial
import select
import threading
import queue
import json
import errno
from pathlib import Path
from datetime import datetime


# Classify known system codes; preserve the original exception for diagnosis.
def error_category(error):
    code = getattr(error, "errno", None)
    win = getattr(error, "winerror", None)
    text = str(error).casefold()
    if code == errno.ENOSPC or win in (39, 112):
        return "Disk full"
    if isinstance(error, PermissionError) or code in (errno.EACCES, errno.EPERM) or win == 5:
        return "Access denied"
    if win in (32, 33):
        return "File in use"
    if "source" in text and ("modified" in text or "changed" in text):
        return "Source changed"
    if "sha-256" in text or "copied size" in text or "temporary file size" in text:
        return "Integrity check failed"
    if isinstance(error, (TimeoutError, ConnectionError)) or code in (
        errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNABORTED,
        errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN,
    ) or win in (53, 64, 67, 121, 1231):
        return "Network error or timeout"
    if isinstance(error, FileNotFoundError):
        return "File or directory not found"
    return "Read/write or scan error"


def record_failure(e, path, stage, error, attempts=0):
    e.failure_details.append(dict(
        path=str(path), stage=stage, category=error_category(error),
        reason=str(error), attempts=attempts,
        errno=getattr(error, "errno", None), winerror=getattr(error, "winerror", None)))


def save_report(e, log_file):
    lines = [
        "COPY_OS 1.8 — FINAL REPORT", f"Result: {e.result}",
        f"Processed: {e.processed}/{e.total_files}",
        f"Copied: {e.copied} | Skipped: {e.skipped} | Simulated: {e.simulated} | Copy failures: {e.failures}",
        f"Failed attempts: {e.failed_attempts}",
        f"Last stage: {e.current_stage}", f"Last path: {e.current_path or '--'}",
        "", "FAILURES AND INCIDENTS:",
    ]
    if not e.failure_details:
        lines.append("No failures recorded.")
    for item in e.failure_details:
        lines += [f"- {item['path']}",
                   f"  {item['category']} | Stage: {item['stage']} | Attempts: {item['attempts']}",
                   f"  Reason: {item['reason']}",
                   f"  errno={item['errno']} | winerror={item['winerror']}"]
    if e.result == "CANCELLED":
        lines += ["", "The operation was cancelled; completed files were preserved."]
    report = log_file.with_name(log_file.stem + "_report.txt")
    text = "\n".join(lines) + "\n"
    print("\n" + text, flush=True)
    try:
        report.write_text(text, encoding="utf-8")
        write_log(log_file, f"RESULT: {e.result} | Report: {report.resolve()}")
        print(f"Report: {report.resolve()}", flush=True)
    except OSError as error:
        print(f"Could not save the report: {error}. See the summary above.", flush=True)


class CopyCancelled(KeyboardInterrupt):
    """Cancellation requested from the keyboard."""


class CopyControl:
    def __init__(self):
        self.active = False
        self.paused = False
        self.paused_time = 0.0
        self.redraw = None
        self._original_terminal = None
        self._extended_key = False

    def __enter__(self):
        return self

    def begin(self):
        
        if self.active or not sys.stdin.isatty():
            return
        if sys.platform != "win32":
            import termios
            import tty
            self._original_terminal = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        self.active = True

    def __exit__(self, *args):
        self.suspend()

    def suspend(self):
        if self._original_terminal is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._original_terminal)
        self._original_terminal = None
        self.active = False

    def read_key(self):
        if not self.active:
            return None
        if sys.platform == "win32":
            import msvcrt
            if msvcrt.kbhit():
                key = msvcrt.getwch()
                if self._extended_key:
                    self._extended_key = False
                    return None
                if key in ("\x00", "\xe0"):
                    self._extended_key = True
                    return None
                return key.lower()
        elif select.select([sys.stdin], [], [], 0)[0]:
            return os.read(sys.stdin.fileno(), 1).decode("ascii", errors="ignore").lower()
        return None

    def checkpoint(self):
        self.begin()
        pause_start = None
        try:
            while True:
                key = self.read_key()
                if key in ("c", "\x03"):
                    raise CopyCancelled()
                if key == "p":
                    self.paused = not self.paused
                    if self.paused:
                        pause_start = time.perf_counter()
                        print("\nPAUSED — [P] Resume | [C] Cancel", flush=True)
                    elif self.redraw:
                        self.redraw()
                if not self.paused:
                    return
                time.sleep(0.05)
        finally:
            if pause_start is not None:
                self.paused_time += time.perf_counter() - pause_start

    def wait_seconds(self, seconds):
        remaining = seconds
        while remaining > 0:
            self.checkpoint()
            started = time.perf_counter()
            time.sleep(min(0.1, remaining))
            remaining -= time.perf_counter() - started


UI_INTERVAL = 0.2  
BAR_WIDTH = 50
COPY_BLOCK_SIZE = 8 * 1024 * 1024          
MAX_ATTEMPTS = 5
RETRY_DELAY = 10


def enable_ansi():
    """Enable ANSI escape sequences on Windows."""
    if sys.platform == "win32":
        os.system("")  


ASCII_ART = """
   ██████╗ ██████╗ ██████╗ ██╗   ██╗     ██████╗ ███████╗   
  ██╔════╝██╔═══██╗██╔══██╗╚██╗ ██╔╝    ██╔═══██╗██╔════╝   
  ██║     ██║   ██║██████╔╝ ╚████╔╝     ██║   ██║███████╗   
  ██║     ██║   ██║██╔═══╝   ╚██╔╝      ██║   ██║╚════██║   
  ╚██████╗╚██████╔╝██║        ██║       ╚██████╔╝███████║   
   ╚═════╝ ╚═════╝ ╚═╝        ╚═╝        ╚═════╝ ╚══════╝   

┌──────────────────────────────────────────────────────────┐
│               SECURE FILE TRANSFER SYSTEM                │
│                 NAS BACKUP 1.8 by MOOSH                  │
└──────────────────────────────────────────────────────────┘
"""


def format_bytes(value: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(value)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024

    return f"{value} B"


def format_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0

    seconds = int(seconds)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)

    return f"{h:02d}:{m:02d}:{s:02d}"


def create_log() -> Path:
    log_folder = Path("logs")
    log_folder.mkdir(exist_ok=True)

    now = datetime.now()
    name = f"backup_{now.strftime('%Y-%m-%d_%H-%M-%S_%f')}.log"
    return log_folder / name


def write_log(log_file: Path, message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def clear_line():
    print("\033[2K", end="")


def safe_path(path: Path) -> Path:
    """
    Add the Windows prefix required for long paths.
    """
    if sys.platform != "win32":
        return path

    path_string = str(path.resolve())

    if path_string.startswith("\\\\?\\"):
        return Path(path_string)

    if path_string.startswith("\\\\"):
        
        return Path("\\\\?\\UNC\\" + path_string[2:])

    return Path("\\\\?\\" + path_string)


# Read-only daemon queries keep startup responsive when SMB calls block.
def free_space(destination: Path, control=None, timeout=5.0) -> int:
    """Bound the query wait so a blocked SMB call does not freeze the interface."""
    result = queue.Queue(maxsize=1)

    def query():
        try:
            
            target = destination if destination.exists() else destination.parent
            result.put(shutil.disk_usage(target).free)
        except Exception:
            result.put(-1)

    threading.Thread(target=query, daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        if control is not None:
            pause = control.paused_time
            control.checkpoint()
            deadline += control.paused_time - pause
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return -1
        try:
            return result.get(timeout=min(0.05, remaining))
        except queue.Empty:
            pass


def calculate_sha256(file: Path, callback=None, control=None):
    sha = hashlib.sha256()
    bytes_read = 0

    with open(file, "rb") as f:
        while True:
            if control is not None:
                control.checkpoint()
            block = f.read(COPY_BLOCK_SIZE)
            if not block:
                break

            sha.update(block)
            bytes_read += len(block)

            if callback is not None:
                callback(bytes_read)

    return sha.hexdigest(), bytes_read


def needs_copy(source: Path, destination: Path):
    try:
        destination_stat = destination.stat()
    except FileNotFoundError:
        return True, "new"
    except OSError:
        return True, "metadata read error"
    try:
        source_stat = source.stat()
    except OSError:
        return True, "metadata read error"

    if source_stat.st_size != destination_stat.st_size:
        return True, "different size"

    if source_stat.st_mtime_ns > destination_stat.st_mtime_ns:
        return True, "source is newer"

    return False, "already exists and is up to date"


EXCLUDED_EXTENSIONS = {
    ".tmp", ".part", ".crdownload", ".download",
    ".partial", ".!ut", ".bc!",
}

EXCLUDED_NAMES = {
    "thumbs.db", ".ds_store", "desktop.ini", ".appledouble",
    ".spotlight-v100", ".trashes", ".fseventsd",
    ".documentrevisions-v100", ".temporaryitems", ".volumeicon.icns",
    "@eadir", "#recycle", "$recycle.bin", "system volume information",
}

DEFAULT_EXTENSIONS = frozenset(EXCLUDED_EXTENSIONS)
DEFAULT_NAMES = frozenset(EXCLUDED_NAMES)


# Configuration is applied only after every value has passed validation.
def load_configuration(path=None):
    """Validate the entire configuration before applying additional exclusions."""
    global MAX_ATTEMPTS, RETRY_DELAY, EXCLUDED_EXTENSIONS, EXCLUDED_NAMES
    path = Path(path) if path is not None else Path(__file__).resolve().with_name("config.json")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        data = {}
        found = False
    except (OSError, UnicodeError) as error:
        raise ValueError(f"Could not read {path}: {error}") from error
    else:
        found = True
        def no_duplicates(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise ValueError(f"Duplicate key: {key}")
                obj[key] = value
            return obj
        try:
            data = json.loads(text, object_pairs_hook=no_duplicates)
        except ValueError as error:
            raise ValueError(f"Invalid configuration in {path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("config.json must contain a JSON object.")
    allowed = {"max_attempts", "retry_delay_seconds", "additional_extensions", "additional_names"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown options: {', '.join(sorted(unknown))}")
    attempts = data.get("max_attempts", 5)
    delay = data.get("retry_delay_seconds", 10)
    for name, value, minimum, maximum in [
        ("max_attempts", attempts, 1, 1000),
        ("retry_delay_seconds", delay, 0, 3600),
    ]:
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    lists = {}
    for key in ("additional_extensions", "additional_names"):
        items = data.get(key, [])
        if not isinstance(items, list):
            raise ValueError(f"{key} must be a list of strings.")
        normalized = set()
        for item in items:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{key} contains an empty name or a non-string value.")
            value = item.strip().casefold()
            if any(c in value for c in '/\\*?[]') or any(ord(c) < 32 for c in value):
                raise ValueError(f"{key}: use simple names without paths or wildcards: {item!r}")
            if key == "additional_extensions" and (not value.startswith('.') or len(value) < 2 or '.' in value[1:]):
                raise ValueError(f"Invalid extension: {item!r}. Example: .bak")
            if key == "additional_names" and value in ('.', '..'):
                raise ValueError("Invalid exclusion name.")
            normalized.add(value)
        lists[key] = normalized
    
    MAX_ATTEMPTS, RETRY_DELAY = attempts, delay
    EXCLUDED_EXTENSIONS = set(DEFAULT_EXTENSIONS) | lists["additional_extensions"]
    EXCLUDED_NAMES = set(DEFAULT_NAMES) | lists["additional_names"]
    return path if found else None


def should_exclude(name: str) -> bool:
    normalized_name = name.casefold()
    if normalized_name.startswith("._"):
        return True
    if normalized_name in EXCLUDED_NAMES:
        return True
    return Path(name).suffix.casefold() in EXCLUDED_EXTENSIONS


@dataclass
class Statistics:
    total_files: int = 0
    total_bytes: int = 0
    planned_bytes: int = 0
    processed: int = 0
    copied: int = 0
    skipped: int = 0
    failures: int = 0
    simulated: int = 0
    verified: int = 0
    failed_attempts: int = 0
    copied_bytes: int = 0
    skipped_bytes: int = 0
    simulated_bytes: int = 0
    failed_bytes: int = 0
    transferred_bytes: int = 0
    verified_bytes: int = 0
    current_bytes: int = 0
    copy_time: float = 0.0
    hash_time: float = 0.0
    dry_run: bool = False
    result: str = "STOPPED WITH ERROR"
    current_stage: str = "Initialization"
    current_path: str = ""
    failure_details: list = field(default_factory=list)

    @property
    def speed(self):
        return self.transferred_bytes / self.copy_time if self.copy_time > 0 else 0.0

    @property
    def copy_eta(self):
        remaining = max(0, self.planned_bytes - self.copied_bytes - self.failed_bytes - self.current_bytes)
        return remaining / self.speed if self.speed > 0 else None


def calculate_speed_eta(processed, total_bytes, started):
    if started is None or processed <= 0:
        return 0.0, 0.0

    elapsed = time.time() - started
    if elapsed <= 0:
        return 0.0, 0.0

    speed = processed / elapsed
    remaining = max(total_bytes - processed, 0)
    eta = remaining / speed if speed > 0 else 0

    return speed, eta


# Keep the latest frame for resume, but throttle normal terminal redraws.
def update_interface(
    processed,
    total_bytes,
    total_files,
    current_file="",
    file_bytes=0,
    file_size=0,
    started=None,
    status="",
    hash_progress=None,
    statistics=None,
    control=None,
    force=False,
):
    if control is not None:
        control.redraw = partial(
            update_interface, processed, total_bytes, total_files,
            current_file, file_bytes, file_size, started, status,
            hash_progress, statistics, control, True)
    ui_state = statistics if statistics is not None else control
    urgent = force or status in {"COMPLETED", "COMPLETED WITH FAILURES", "DRY RUN COMPLETED", "FINISHED", "STARTING"} and not current_file
    urgent = urgent or any(word in status for word in ("ERROR", "FAILURE", "CANCELLED"))
    ui_now = time.monotonic()
    if ui_state is not None:
        if not urgent and ui_now - getattr(ui_state, "_last_redraw", float('-inf')) < UI_INTERVAL:
            return
        ui_state._last_redraw = ui_now
    if total_bytes > 0:
        percentage = (processed / total_bytes) * 100
    else:
        percentage = 100.0 if total_files == 0 else 0.0

    percentage = min(max(percentage, 0), 100)
    filled = int(BAR_WIDTH * percentage / 100)
    bar = "█" * filled + "░" * (BAR_WIDTH - filled)

    speed, eta = calculate_speed_eta(processed, total_bytes, started)

    if hash_progress is not None:
        hash_text = f"DESTINATION HASH: {hash_progress:6.2f}%"
    else:
        hash_text = "DESTINATION HASH: --"

    if file_size > 0:
        file_percentage = min((file_bytes / file_size) * 100, 100)
    else:
        file_percentage = 0.0

    lines = [
        f"OVERALL PROGRESS  [{bar}] {percentage:6.2f}%",
        f"TOTAL: {format_bytes(processed)} / {format_bytes(total_bytes)}    |    FILES: {total_files}",
        f"SPEED: {format_bytes(speed)}/s    |    TIME REMAINING: {format_time(eta)}",
        f"STATUS: {status or '--'}",
        f"FILE: {current_file or '--'}",
        f"CURRENT FILE: {format_bytes(file_bytes)} / {format_bytes(file_size)} ({file_percentage:6.2f}%)",
        hash_text,
    ]

    if statistics is not None:
        e = statistics
        lines[1] = f"SCANNED: {format_bytes(total_bytes)} | FILES: {e.processed} / {e.total_files}"
        eta = e.copy_eta
        lines[2] = ("DRY RUN — NO TRANSFER" if e.dry_run else
                     f"COPY: {format_bytes(e.speed)}/s | ETA COPY: {format_time(eta) if eta is not None else '--'} (estimate; excludes hash/retry)")
        lines += [
            f"COPIED: {e.copied} | SKIPPED: {e.skipped} | FAILURES: {e.failures} | SIMULATED: {e.simulated}",
            f"TRANSFERRED: {format_bytes(e.transferred_bytes)} (includes retries) | SHA-256 OK: {e.verified}",
        ]


    print("\033[2J\033[H", end="")
    print(ASCII_ART)
    print()

    lines.append("[P] Pause / Resume | [C] Cancel | Ctrl+C Cancel")
    for line in lines:
        print(line)
    sys.stdout.flush()


class ScanStopped(Exception):
    pass


# Scan in a worker; only the main thread handles input and renders progress.
def collect_files(source: Path, log_file: Path, control=None, timeout=30.0):
    """Scan in the background; never start copying after a blocked scan."""
    files, errors = [], []
    incidents = []
    stop, done = threading.Event(), threading.Event()
    lock = threading.Lock()
    state = dict(path=str(source), count=0, bytes=0,
                  activity=time.monotonic(), fatal=None)

    def refresh(**values):
        with lock:
            state.update(values)
            state['activity'] = time.monotonic()

    def proceed():
        while control is not None and control.paused and not stop.is_set():
            stop.wait(0.05)
        return not stop.is_set()

    def scan():
        try:
            folders = [source]
            while folders and proceed():
                folder = folders.pop()
                refresh(path=str(folder))
                try:
                    with os.scandir(folder) as entries:
                        for entry in entries:
                            if not proceed():
                                return
                            refresh(path=entry.path)
                            if should_exclude(entry.name):
                                continue
                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    info = entry.stat(follow_symlinks=False)
                                    if getattr(info, 'st_file_attributes', 0) & 0x400:
                                        errors.append(f"Reparse/junction directory not traversed: {entry.path}")
                                    else:
                                        folders.append(Path(entry.path))
                                elif entry.is_symlink() and entry.is_dir():
                                    continue  
                                else:
                                    size = entry.stat().st_size
                                    file = Path(entry.path)
                                    files.append((file, file.relative_to(source), size))
                                    with lock:
                                        state['count'] += 1
                                        state['bytes'] += size
                            except OSError as error:
                                errors.append(f"SCAN ERROR: {entry.path} | {error}")
                                incidents.append((entry.path, error))
                            refresh()
                except OSError as error:
                    if folder == source:
                        raise
                    errors.append(f"SCAN ERROR: {folder} | {error}")
                    incidents.append((str(folder), error))
                refresh()
        except Exception as error:
            incidents.append((state['path'], error))
            refresh(fatal=f"{type(error).__name__}: {error}")
        finally:
            done.set()

    def display():
        with lock:
            current = state.copy()
        delay = max(0, time.monotonic() - current['activity'])
        print("\033[2J\033[H", end="")
        print(ASCII_ART)
        print("SCANNING SOURCE — [P] Pause/Resume | [C] Cancel")
        print(f"Files: {current['count']} | Total: {format_bytes(current['bytes'])}")
        print(f"Path: {current['path']}")
        print(f"Waiting for a response for {delay:.1f}s (response timeout: {timeout:g}s)", flush=True)

    if control is not None:
        control.redraw = display
    threading.Thread(target=scan, daemon=True).start()
    last_screen = 0.0
    try:
        while True:
            if control is not None:
                pause = control.paused_time
                control.checkpoint()
                if control.paused_time != pause:
                    refresh()  
            now = time.monotonic()
            if now - last_screen >= 0.25:
                display()
                last_screen = now
            if done.is_set():
                break
            with lock:
                current = state.copy()
            if now - current['activity'] >= timeout:
                raise ScanStopped(f"Source not responding for {timeout:g}s: {current['path']}. No files were copied.")
            done.wait(0.05)
        if control is not None and hasattr(control, 'statistics'):
            for path, original_error in incidents:
                record_failure(control.statistics, path, 'Source scan', original_error)
        if state['fatal']:
            raise ScanStopped(f"Could not scan the source: {state['fatal']}")
        for error in errors:
            write_log(log_file, error)
            if control is not None and hasattr(control, 'statistics'):
                record_failure(control.statistics, str(source), "Source scan", OSError(error))
        if errors:
            raise ScanStopped(
                f"{len(errors)} source read error(s). Fix the paths listed in the log before copying.")
        display()
        return files, state['bytes'], errors
    except (KeyboardInterrupt, ScanStopped) as error:
        write_log(log_file, str(error) or "SCAN CANCELLED BY USER. No files were copied.")
        raise
    finally:
        stop.set()
        if control is not None:
            control.redraw = None
            control.suspend()


# Write and verify a temporary file before replacing the destination.
def copy_with_retry(
    source_file: Path, destination_file: Path, processed: int,
    total_bytes: int, total_files: int, started: float, relative: Path,
    file_size: int, log_file: Path, dry_run: bool = False,
    secure_mode: bool = False,
    statistics=None,
    control=None,
):
    e = statistics if statistics is not None else Statistics()
    update_interface = partial(globals()['update_interface'], statistics=e, control=control)
    attempt = 0
    scanned_size = file_size
    source_file = safe_path(source_file)
    destination_file = safe_path(destination_file)

    while attempt < MAX_ATTEMPTS:
        temp = destination_file.with_name(destination_file.name + ".copying")
        stage = "prepare destination"
        e.current_stage = stage
        e.current_path = str(relative)
        try:
            if control is not None:
                control.checkpoint()
            if dry_run:
                write_log(log_file, f"DRY RUN - would be copied: {relative}")
                e.simulated += 1
                e.simulated_bytes += file_size
                return True, processed + file_size

            destination_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass

            stage = "read source metadata"
            e.current_stage = stage
            e.current_path = str(relative)
            source_stat_start = source_file.stat()
            file_size = source_stat_start.st_size
            file_bytes = 0
            e.current_bytes = 0
            source_sha = hashlib.sha256() if secure_mode else None
            copy_status = ("COPYING + CALCULATING SHA-256" if secure_mode
                            else "COPYING — FAST MODE (NO HASH)")

            update_interface(processed, total_bytes, total_files,
                                 str(relative), 0, file_size, started,
                                 copy_status, None)

            stage = "open source/temporary file and transfer data"
            e.current_stage = stage
            e.current_path = str(relative)
            with open(source_file, "rb") as source, open(temp, "wb") as destination:
                while True:
                    if control is not None:
                        control.checkpoint()
                    block_start = time.perf_counter()
                    block = source.read(COPY_BLOCK_SIZE)
                    if not block:
                        break
                    destination.write(block)
                    if source_sha is not None:
                        source_sha.update(block)
                    e.copy_time += time.perf_counter() - block_start
                    e.transferred_bytes += len(block)
                    file_bytes += len(block)
                    e.current_bytes = file_bytes
                    update_interface(processed + file_bytes, total_bytes,
                                         total_files, str(relative),
                                         file_bytes, file_size, started,
                                         copy_status, None)
                stage = "flush temporary file"
                e.current_stage = stage
                e.current_path = str(relative)
                flush_start = time.perf_counter()
                destination.flush()


                if secure_mode:
                    os.fsync(destination.fileno())
                e.copy_time += time.perf_counter() - flush_start

            stage = "check source stability and size"
            e.current_stage = stage
            e.current_path = str(relative)
            source_stat_end = source_file.stat()
            if (source_stat_start.st_size != source_stat_end.st_size or
                    source_stat_start.st_mtime_ns != source_stat_end.st_mtime_ns):
                raise IOError("The source was modified during copying. The file will be retried.")

            if file_bytes != file_size:
                raise IOError(f"Copied size differs from expected size: {file_bytes} != {file_size}")

            stage = "preserve metadata on temporary file"
            e.current_stage = stage
            e.current_path = str(relative)
            shutil.copystat(source_file, temp)

            if secure_mode:
                def hash_progress(bytes_read):
                    hash_percentage = (bytes_read / file_size * 100) if file_size > 0 else 100.0
                    update_interface(processed, total_bytes, total_files, str(relative),
                                         bytes_read, file_size, started,
                                         "VERIFYING DESTINATION SHA-256", hash_percentage)

                stage = "verify temporary file SHA-256"
                e.current_stage = stage
                e.current_path = str(relative)
                previous_pause = control.paused_time if control else 0.0
                hash_start = time.perf_counter()
                try:
                    destination_hash, bytes_hash = calculate_sha256(temp, callback=hash_progress, control=control)
                finally:
                    e.hash_time += time.perf_counter() - hash_start - ((control.paused_time - previous_pause) if control else 0.0)
                e.verified_bytes += bytes_hash
                source_hash = source_sha.hexdigest()
                if bytes_hash != file_bytes:
                    raise IOError("The temporary file size changed during verification.")
                if source_hash != destination_hash:
                    write_log(log_file, f"INTEGRITY FAILURE: {relative} | Source={source_hash} | Temporary={destination_hash}")
                    raise IOError("The copied file SHA-256 does not match the SHA-256 computed during copying.")
                final_status = "COMPLETED / SHA-256 OK"
                hash_log = f" | SHA-256={source_hash}"
            else:
                final_status = "COMPLETED / FAST COPY"
                hash_log = ""

            if control is not None:
                control.checkpoint()
            
            source_stat_final = source_file.stat()
            if (source_stat_final.st_size != source_stat_start.st_size or
                    source_stat_final.st_mtime_ns != source_stat_start.st_mtime_ns):
                raise IOError("The source changed before replacement; retrying the copy.")
            stage = "replace destination file"
            e.current_stage = stage
            e.current_path = str(relative)
            os.replace(temp, destination_file)
            difference = file_bytes - scanned_size
            e.total_bytes += difference
            e.planned_bytes += difference
            total_bytes += difference
            e.copied += 1
            e.copied_bytes += file_bytes
            e.current_bytes = 0
            if secure_mode:
                e.verified += 1
            processed += file_bytes
            write_log(log_file, f"OK: {relative} | {format_bytes(file_bytes)}{hash_log}")
            update_interface(processed, total_bytes, total_files, str(relative),
                                 file_size, file_size, started, final_status,
                                 100.0 if secure_mode else None)
            return True, processed

        except KeyboardInterrupt:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError as cleanup_error:
                record_failure(e, temp, "Temporary file cleanup", cleanup_error)
                write_log(log_file, f"Temporary file not removed: {temp} | {cleanup_error}")
            write_log(log_file, "BACKUP CANCELLED BY USER.")
            raise
        except (OSError, IOError, ConnectionError, TimeoutError) as error:
            attempt += 1
            e.failed_attempts += 1
            e.current_bytes = 0
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
            detail = f"{stage}: {type(error).__name__}: {error} | errno={getattr(error, 'errno', None)} | winerror={getattr(error, 'winerror', None)}"
            write_log(log_file, f"ATTEMPT {attempt} FAILED: {relative} | {detail}")
            if attempt >= MAX_ATTEMPTS:
                record_failure(e, relative, stage, error, attempt)
                update_interface(processed, total_bytes, total_files, str(relative),
                                     0, file_size, started, f"FAILED AFTER {attempt} ATTEMPTS — {detail}", None)
                return False, processed
            update_interface(processed, total_bytes, total_files, str(relative),
                                 0, file_size, started,
                                 f"ERROR — ATTEMPT {attempt}/{MAX_ATTEMPTS} — RETRY IN {RETRY_DELAY}s | {detail}", None)
            if control is not None:
                control.wait_seconds(RETRY_DELAY)
            else:
                time.sleep(RETRY_DELAY)
        except Exception as error:
            record_failure(e, relative, stage, error, attempt + 1)
            write_log(log_file, f"UNRECOVERABLE ERROR: {relative} | {error}")
            update_interface(processed, total_bytes, total_files, str(relative),
                                 0, file_size, started, "UNRECOVERABLE ERROR", None)
            return False, processed

    return False, processed


# Always restore terminal settings and emit the final report.
def copy_folders(source: str, destination: str, dry_run: bool = False, secure_mode: bool = False):
    with CopyControl() as control:
        control.statistics = Statistics(dry_run=dry_run)
        control.log_file = None
        try:
            return _copy_folders(source, destination, dry_run, secure_mode, control)
        except KeyboardInterrupt:
            control.statistics.result = "CANCELLED"
            return None
        except Exception as error:
            e = control.statistics
            record_failure(e, e.current_path or source, e.current_stage, error)
            e.result = "STOPPED WITH ERROR"
            return None
        finally:
            control.suspend()
            if control.log_file is not None:
                save_report(control.statistics, control.log_file)
            else:
                print(f"Result: {control.statistics.result}; could not create the log.")


def _copy_folders(source, destination, dry_run, secure_mode, control):
    e = control.statistics
    update_interface = partial(globals()['update_interface'], statistics=e, control=control)
    source = Path(source)
    destination = Path(destination)

    log_file = create_log()
    control.log_file = log_file
    e.current_stage = "Source scan"
    e.current_path = str(source)

    write_log(log_file, "=" * 60)
    write_log(log_file, "COPY_OS BACKUP START V1.8")
    write_log(log_file, f"Source: {source}")
    write_log(log_file, f"Destination: {destination}")
    write_log(log_file, f"Dry Run: {dry_run}")
    write_log(log_file, f"Secure mode (SHA-256): {secure_mode}")
    write_log(log_file, f"Effective configuration: attempts={MAX_ATTEMPTS} | interval={RETRY_DELAY}s | extensions={sorted(EXCLUDED_EXTENSIONS)} | names={sorted(EXCLUDED_NAMES)} | prefix=._")


    try:
        files, total_bytes, scan_errors = collect_files(source, log_file, control)
    except ScanStopped as error:
        record_failure(e, source, "Source scan", error)
        print(f"\nSCAN STOPPED: {error}\nLog: {log_file.resolve()}", flush=True)
        return None
    total_files = len(files)
    e.total_files = total_files
    e.total_bytes = total_bytes


    plan = {relative: True for _, relative, _ in files}
    e.planned_bytes = total_bytes

    if scan_errors:
        print(f"\n[WARNING] {len(scan_errors)} error(s) during the scan. See the log.")

    print(f"\nFiles found : {total_files}")
    print(f"Total size        : {format_bytes(total_bytes)}")


    if not dry_run:
        e.current_stage = "Free-space query"
        e.current_path = str(destination)
        print("Checking destination free space (timeout: 5s)... [P] Pause | [C] Cancel", flush=True)
        try:
            available = free_space(destination, control=control)
        except KeyboardInterrupt:
            write_log(log_file, "CANCELLED DURING FREE-SPACE QUERY. No files were copied.")
            raise
        finally:
            
            control.suspend()

        if available >= 0:
            print(f"Destination free space: {format_bytes(available)}")

            if available < total_bytes:
                print("\n[WARNING] Free space appears to be smaller than the total backup size.")
                print("        The script can continue, but may fail before finishing.")
                answer = input("Continue anyway? [y/N]: ").strip().lower()
                if answer != "y":
                    e.result = "CANCELLED"
                    print("Operation cancelled by user.")
                    write_log(log_file, "Cancelled because of insufficient free space.")
                    return
        else:
            print("The destination did not respond within 5s or the query failed. Continuing without a free-space estimate.", flush=True)
            write_log(log_file, "WARNING: free space unavailable or query exceeded 5s; continuing without an estimate.")

    print(f"\nStarting backup from:\n  {source}\nto:\n  {destination}\n")

    if dry_run:
        print("*** DRY RUN — NO FILES WILL BE COPIED ***\n")

    print("-" * 70)


    processed = 0
    new_files = 0
    updated_files = 0
    skipped = 0
    failures = 0
    verified = 0

    started = time.time()

    update_interface(
        0, total_bytes, total_files, "", 0, 0, started, "STARTING", None
    )

    for source_file, relative, size in files:
        destination_file = destination / relative

        try:
            e.current_stage = "Process file"
            e.current_path = str(relative)
            control.checkpoint()
            must_copy, reason = needs_copy(source_file, destination_file)

            if must_copy != plan[relative]:
                e.planned_bytes += size if must_copy else -size
            if not must_copy:
                processed += size
                skipped += 1
                e.skipped += 1
                e.skipped_bytes += size
                e.processed += 1

                write_log(log_file, f"SKIPPED: {relative} | {reason}")

                update_interface(
                    processed,
                    total_bytes,
                    total_files,
                    str(relative),
                    size,
                    size,
                    started,
                    "UP TO DATE — SKIPPED",
                    None,
                )
                continue

            if reason == "new":
                new_files += 1
                status = "NEW"
            else:
                updated_files += 1
                status = "UPDATING"

            write_log(log_file, f"{status}: {relative} | {reason}")

            success, processed = copy_with_retry(
                source_file,
                destination_file,
                processed,
                total_bytes,
                total_files,
                started,
                relative,
                size,
                log_file,
                dry_run,
                secure_mode,
                e,
                control,
            )

            total_bytes = e.total_bytes
            e.processed += 1
            if success:
                verified += int(secure_mode and not dry_run)
                update_interface(processed, total_bytes, total_files, str(relative),
                                     size, size, started,
                                     "WOULD BE COPIED" if dry_run else "COMPLETED", None)
            else:
                failures += 1
                e.failures += 1
                e.failed_bytes += size
                e.current_bytes = 0
                processed += size

                update_interface(
                    processed,
                    total_bytes,
                    total_files,
                    str(relative),
                    0,
                    size,
                    started,
                    "FAILURE — CONTINUING",
                    None,
                )

        except KeyboardInterrupt:
            write_log(log_file, "BACKUP CANCELLED BY USER.")
            print("\n\nBackup interrupted.")
            raise

        except (OSError, IOError, ConnectionError, TimeoutError) as error:
            failures += 1
            e.failures += 1
            e.processed += 1
            e.failed_bytes += size
            e.current_bytes = 0
            processed += size

            record_failure(e, relative, "Metadata read/copy decision", error)
            write_log(log_file, f"PROCESSING FAILURE: {relative} | {error}")

            update_interface(
                processed,
                total_bytes,
                total_files,
                str(relative),
                0,
                size,
                started,
                "ERROR — CONTINUING",
                None,
            )

    e.result = "COMPLETED WITH FAILURES" if e.failures else ("DRY RUN COMPLETED" if dry_run else "COMPLETED")
    e.current_stage = "Finishing"
    duration = time.time() - started

    update_interface(
        total_bytes, total_bytes, total_files, "", 0, 0, started, e.result, None
    )

    print("\n" + "-" * 70)
    summary = [
        "DRY RUN SUMMARY:" if dry_run else "BACKUP SUMMARY:",
        f"Files processed: {e.processed} / {e.total_files}",
        f"Scanned: {format_bytes(e.total_bytes)}",
        f"Successfully copied: {e.copied} | {format_bytes(e.copied_bytes)}",
        f"Skipped (up to date): {e.skipped} | {format_bytes(e.skipped_bytes)}",
        f"Would be copied: {e.simulated} | {format_bytes(e.simulated_bytes)}",
        f"Failures: {e.failures} | {format_bytes(e.failed_bytes)}",
        f"Scan errors: {len(scan_errors)}",
        f"Failed attempts: {e.failed_attempts}",
        f"Transferred bytes (includes retries): {format_bytes(e.transferred_bytes)}",
        f"SHA-256 verified files: {e.verified}",
        f"Bytes read during temporary file verification: {format_bytes(e.verified_bytes)}",
        f"Copy time (includes source hashing and flush): {e.copy_time:.2f}s",
        f"Temporary file verification time: {e.hash_time:.2f}s",
        f"Average active copy speed: {format_bytes(e.speed)}/s",
        f"Total time after scan: {format_time(duration)}",
    ]
    for line in summary:
        print("  " + line)
        write_log(log_file, line)
    print(f"\n  LOG: {log_file.resolve()}")
    return e


if __name__ == "__main__":
    enable_ansi()

    print("\033[2J\033[H", end="")
    print(ASCII_ART)
    print("\nThis program does NOT delete destination files.")
    print("Fast backup uses size + date; secure backup uses SHA-256.")

    try:
        config_used = load_configuration()
    except ValueError as error:
        print(f"\nCONFIGURATION ERROR: {error}\nNo files were copied.")
        sys.exit(2)
    print(f"Configuration: {config_used if config_used else 'built-in defaults (no config.json)'}")
    print(f"Attempts per file: {MAX_ATTEMPTS} | Interval: {RETRY_DELAY}s")

    source = input("\nEnter the SOURCE FOLDER path:\n> ").strip().strip('"')
    destination = input("\nEnter the DESTINATION FOLDER path:\n> ").strip().strip('"')

    print("\nExecution mode:")
    print("  [1] Normal backup")
    print("  [2] Dry Run (simulation, copies nothing)")

    mode = input("\nChoose [1/2] (default: 1): ").strip()
    dry_run = mode == "2"

    secure_mode = False
    if not dry_run:
        print("\nBackup type:")
        print("  [1] Fast copy (size + date, no SHA-256)")
        print("  [2] Secure copy (size + date + SHA-256)")
        kind = input("\nChoose [1/2] (default: 1): ").strip()
        secure_mode = kind == "2"

    if not source or not destination:
        print("\nError: You must specify a source and destination.")
    else:
        try:
            copy_folders(source, destination, dry_run, secure_mode)
        except KeyboardInterrupt:
            print("\n\nOperation interrupted by user.")

    input("\nPress Enter to exit...")