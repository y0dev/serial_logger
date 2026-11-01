"""Shared helpers for serial_logger.py and tcp_logger.py."""
import csv
import datetime
import json
import re
import sys
import threading
from pathlib import Path

from ansi import (
    ANSI_BRIGHT_GREEN,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_RED,
    ANSI_RESET,
    ANSI_YELLOW,
)
from keyword_parser import KEYWORDS, KeywordParser

EOL_MAP = {"lf": "\n", "cr": "\r", "crlf": "\r\n", "none": ""}

# Console-only colorization of common log levels (the log file is never colored).
LEVEL_PATTERN = re.compile(r"\b(ERROR|ERR|FATAL|CRITICAL|CRIT|WARNING|WARN|INFO|DEBUG|TRACE|SUCCESS|PASS|FAIL)\b", re.IGNORECASE)
LEVEL_COLORS = {
    "ERROR": ANSI_RED, "ERR": ANSI_RED, "FATAL": ANSI_RED, "FAIL": ANSI_RED,
    "CRITICAL": ANSI_RED, "CRIT": ANSI_RED,
    "WARNING": ANSI_YELLOW, "WARN": ANSI_YELLOW,
    "INFO": ANSI_GREEN,
    "DEBUG": ANSI_DIM, "TRACE": ANSI_DIM,
    "SUCCESS": ANSI_BRIGHT_GREEN, "PASS": ANSI_BRIGHT_GREEN, 
}


def level_color(line):
    """Return an ANSI color for the first log level found in line, or None."""
    m = LEVEL_PATTERN.search(line)
    return LEVEL_COLORS[m.group(1).upper()] if m else None


_EOL_RE = re.compile(rb"\r+\n|\r|\n")
_LEADING_LF_RE = re.compile(rb"\r*\n")


def timestamp(date=False):
    fmt = "%Y-%m-%d %H:%M:%S.%f" if date else "%H:%M:%S.%f"
    return datetime.datetime.now().strftime(fmt)[:-3]


FORMAT_EXT = {"text": "log", "csv": "csv", "json": "jsonl"}


def log_base_path(log_dir, prefix, name, ext):
    """Resolve the base log path (before any rotation suffix)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    if name:
        name = name if "." in name else f"{name}.{ext}"
    else:
        # Port names like /dev/ttyUSB0 or \\.\COM12 must not become path parts.
        name = f"{re.sub(r'[^\w.-]+', '_', str(prefix)).strip('_.')}.{ext}"
    return log_dir / name


class LogWriter:
    """Writes log records to the file as plain text, CSV, or JSON Lines, with
    optional size-based rotation for long unattended runs. The console is
    handled separately (always human-readable), so the format affects only the
    file.
    """

    def __init__(self, base_path, fmt, timestamps, date=False,
                 overwrite=False, max_bytes=0):
        self.base_path = base_path
        self.fmt = fmt
        self.timestamps = timestamps
        self.date = date
        self.mode = "w" if overwrite else "a"
        self.max_bytes = max_bytes
        self._lock = threading.Lock()  # markers/rotation write from threads
        self._part = 1
        self._open()

    def _part_path(self, part):
        # No rotation -> use the plain base name; otherwise insert .partNNNN
        # before the extension so all parts of a run sort together.
        if not self.max_bytes:
            return self.base_path
        p = self.base_path
        return p.with_name(f"{p.stem}.part{part:04d}{p.suffix}")

    def _open(self, anchor=False):
        self.path = self._part_path(self._part)
        # newline="" so the csv module controls line endings (avoids blank rows).
        self.file = open(self.path, self.mode, encoding="utf-8", newline="")
        self._csv = csv.writer(self.file) if self.fmt == "csv" else None
        if self._csv is not None and self.file.tell() == 0:
            self._csv.writerow(["timestamp", "matched", "line"])
            self.file.flush()
        if anchor:  # date-stamp the start of each rotated part
            self._record(self.stamp(), f"==== CONTINUED {timestamp(date=True)} (part {self._part}) ====",False)

    def _record(self, ts, line, matched):
        if self.fmt == "csv":
            self._csv.writerow([ts, int(bool(matched)), line])
        elif self.fmt == "json":
            record = {"timestamp": ts, "matched": bool(matched), "line": line}
            self.file.write(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            self.file.write((f"[{ts}] {line}" if self.timestamps else line) + "\n")
        self.file.flush()

    def stamp(self):
        """Timestamp for a record, dated per the --date preference."""
        return timestamp(self.date)

    def write(self, ts, line, matched):
        with self._lock:
            self._record(ts, line, matched)
            if self.max_bytes and self.file.tell() >= self.max_bytes:
                self.file.close()
                self._part += 1
                self._open(anchor=True)

    def close(self):
        self.file.close()



def build_session_log_dir(base_dir: str, now: datetime) -> str:
    """
    Construct a structured log directory path rooted at base_dir:

        <base_dir>/YYYY/MM_MMM/HH_mm_ss

    Component breakdown:
        YYYY     — four-digit year                    e.g. 2024
        MM_MMM   — two-digit month number + underscore + three-letter month name
                   e.g. 03_MAR  (zero-padded month, uppercase abbreviation)
        HH_mm_ss — session start time:  hour_minute_second
                   e.g. 14_22_01

    Example full path:
        ./logs/2024/03_MAR/14_22_01

    Each session gets its own leaf directory so every run is isolated and
    log files never collide, even across restarts within the same second.
    The directory is created by the callers (setup_session_logger /
    setup_port_logger) via Path.mkdir(parents=True, exist_ok=True).

    Args:
        base_dir — root log directory from config (e.g. "./logs")
        now      — datetime object representing session start time

    Returns:
        Full structured path string ready for Path() or os.makedirs().
    """
    year           = now.strftime("%Y")           # 2024
    month          = now.strftime("%m_%b").upper()  # 03_MAR  (strftime %b = Jan/Feb/Mar…)
    month_day_year = now.strftime("%m_%d_%Y")
    time_dir       = now.strftime("%H_%M_%S")     # 14_22_01

    return str(Path(base_dir) / year / month / month_day_year / time_dir)

def open_log_writer(args, prefix):
    """Open the log file(s) per args and wrap in a LogWriter. Returns (writer, path)."""
    ext = FORMAT_EXT[args.format]
    session_dir = build_session_log_dir(args.log_dir, datetime.datetime.now())
    base = log_base_path(Path(session_dir), prefix, args.name, ext)
    max_bytes = int(args.max_mb * 1024 * 1024) if args.max_mb else 0
    writer = LogWriter(base, args.format, args.timestamps, args.date,
                       args.overwrite, max_bytes)
    return writer, writer.path


def iter_lines(read_chunk, stop_on_empty=True):
    """Yield decoded lines from repeated read_chunk() calls.

    read_chunk() returns bytes, or None for a read timeout (keep polling).
    When it returns b'':
      - stop_on_empty=True  (TCP): the peer closed the connection, so stop.
      - stop_on_empty=False (serial): just a read timeout with no data yet,
        so keep polling; a real disconnect surfaces as an exception instead.

    Lines may end in \\n, \\r\\n, or a lone \\r (and \\r\\r\\n from doubly
    translated output); all count as a single line break.
    """
    buf = b""
    skip_lf = False  # previous chunk ended in \r; swallow a \n that completes \r\n
    while True:
        chunk = read_chunk()
        if chunk is None:
            continue
        if not chunk:
            if stop_on_empty:
                break
            continue
        buf += chunk
        if skip_lf:
            skip_lf = False
            m = _LEADING_LF_RE.match(buf)  # rest of a \r\n (or \r\r\n) split across reads
            if m:
                buf = buf[m.end():]
        parts = _EOL_RE.split(buf)
        buf = parts.pop()  # incomplete trailing line
        skip_lf = not buf and parts and chunk.endswith(b"\r")
        for raw in parts:
            yield raw.decode("utf-8", errors="replace")
    if buf:  # flush a trailing line that had no newline before disconnect
        yield buf.decode("utf-8", errors="replace")


def process_and_log(line, pattern, filter_only, writer, color=True, kw_parser: KeywordParser=None):
    matched = pattern.search(line) if pattern else None
    if pattern and filter_only and not matched:
        return
    ts = writer.stamp()
    writer.write(ts, line, matched)
    stamped = f"[{ts}] {line}" if writer.timestamps else line
    if not color:
        c = None
    elif matched:                     # an explicit --filter hit wins
        c = ANSI_RED
    else:
        c = level_color(line)
    print(f"{c}{stamped}{ANSI_RESET}" if c else stamped)

    if kw_parser:
        result = kw_parser.feed(line)
        if result:
            print(f"[{ts}] Parsed keyword '{result['keyword']}': {result['fields']}")


def write_marker(writer, label, color=True):
    """Write a visual divider to the log (and console) to break up the capture."""
    text = f"==== MARKER: {label} ====" if label else "==== MARKER ===="
    ts = writer.stamp()
    writer.write(ts, text, False)
    out = f"[{ts}] {text}" if writer.timestamps else text
    print(f"{ANSI_CYAN}{out}{ANSI_RESET}" if color else out)


def write_session_banner(writer, color=True):
    """Record the full session start date/time so the log is anchored even when
    line timestamps are time-only (and to separate sessions when appending).
    """
    text = f"==== SESSION STARTED {timestamp(date=True)} ===="
    writer.write(writer.stamp(), text, False)
    print(f"{ANSI_DIM}{text}{ANSI_RESET}" if color else text)


class StdinReader(threading.Thread):
    """Daemon thread reading stdin lines and dispatching each one:

      - a line equal to (or starting with) mark_cmd inserts a marker, even
        in interactive mode, so it never reaches the device;
      - any other line is forwarded to the connection when interactive;
      - otherwise (capture mode) it is ignored.

    The send target is set per connection so input is dropped while
    disconnected instead of leaking a thread per reconnect.
    """

    def __init__(self, interactive, eol, mark_cmd, on_marker):
        super().__init__(daemon=True)
        self.interactive = interactive
        self.suffix = EOL_MAP[eol]
        self.mark_cmd = mark_cmd      # None disables marker interception
        self.on_marker = on_marker
        self._send = None
        self._lock = threading.Lock()

    def set_target(self, send):
        with self._lock:
            self._send = send

    def clear_target(self):
        with self._lock:
            self._send = None

    def _marker_label(self, line):
        """Return the marker label if line is the mark command, else None."""
        c = self.mark_cmd
        if not c:
            return None
        if line == c:
            return ""
        if line.startswith(c + " "):
            return line[len(c):].strip()
        return None

    def run(self):
        for raw in sys.stdin:
            line = raw.rstrip("\n").rstrip("\r")
            label = self._marker_label(line)
            if label is not None:
                self.on_marker(label)
                continue
            if not self.interactive:
                continue  # capture mode: nothing to forward to
            data = (line + self.suffix).encode("utf-8", errors="replace")
            with self._lock:
                send = self._send
            if send is None:
                print(f"[{timestamp()}] not connected; input dropped", file=sys.stderr)
                continue
            try:
                send(data)
            except OSError as exc:
                print(f"[{timestamp()}] send failed: {exc}", file=sys.stderr)


def run_guarded(run, args):
    """Run run(args), exiting quietly with status 130 on Ctrl+C (including
    during port selection or log setup, before run()'s own cleanup is armed).
    """
    # Undecodable bytes become U+FFFD, which a legacy console/redirect codec
    # (e.g. cp1252) can't encode; don't let print() crash the logger over it.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        run(args)
    except (KeyboardInterrupt, EOFError):  # EOFError: Ctrl+Z/Ctrl+D at a prompt
        print("\nStopped.")
        sys.exit(130)


def start_stdin_reader(interactive, eol, mark_cmd, on_marker):
    """Start a StdinReader if there's anything for it to do, else return None.

    Skipped when stdin isn't a terminal (e.g. piped) so we don't consume
    redirected input. Returns the reader (needed to set/clear the send target
    in interactive mode) or None.
    """
    if not sys.stdin.isatty():
        return None
    if not interactive and not mark_cmd:
        return None
    reader = StdinReader(interactive, eol, mark_cmd, on_marker)
    reader.start()
    return reader


def build_keyword_parser(args=None, session_dir=None):
    """Build the KeywordParser from keyword_parser.KEYWORDS.

    Args:
        args: Parsed command-line arguments. Unused for now; accepted so
            options can later control keyword parsing without changing callers.
        session_dir: Session log directory; parsed results are saved under
            ``<session_dir>/parsed_keyword/`` when given, unless
            --no-save-keywords was passed.

    Returns:
        A KeywordParser, or None if KEYWORDS is empty (parsing disabled).
    """
    if args and getattr(args, "no_keywords", False):
        return None
    if args and getattr(args, "no_save_keywords", False):
        session_dir = None
    return KeywordParser(session_dir, KEYWORDS) if KEYWORDS else None


def add_common_args(parser):
    parser.add_argument("--log-dir", type=Path, default=Path("logs"), help="Directory for log files (default: ./logs)")
    parser.add_argument("--name", help="Custom log filename (a .log extension is added if none given). Default: <type>_<timestamp>.log")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite the log file instead of appending to it")
    parser.add_argument("--max-mb", type=float, default=0, help="Roll over to a new log file once it exceeds this many MB (default: off). Parts are named <name>.partNNNN.<ext>")
    parser.add_argument("--filter", help="Regex; matching lines are highlighted in the console")
    parser.add_argument("--no-color", dest="color", action="store_false", help="Disable all console color (log-level coloring and --filter highlight); color is also auto-disabled when output isn't a terminal")
    parser.add_argument("--mark-cmd", default="/mark", help="Type this (optionally followed by a label) then Enter to insert a marker in the log; works while interacting (default: /mark)")
    parser.add_argument("--no-markers", dest="markers", action="store_false", help="Disable the marker command")
    parser.add_argument("--filter-only", action="store_true", help="Only log/print lines matching --filter")
    parser.add_argument("--no-timestamps", dest="timestamps", action="store_false", help="Don't prefix lines with a timestamp (text format/console only; CSV always has a timestamp column)")
    parser.add_argument("--date", action="store_true", help="Include the date in every line's timestamp (default: time only; the session banner always records the full start date)")
    parser.add_argument("--format", choices=["text", "csv", "json"], default="text", help="Log file format (default: text). csv -> .csv with timestamp,matched,line columns; json -> JSON Lines (.jsonl), one object per line")
    parser.add_argument("--no-keywords", action="store_true", help="Disable keyword parsing (see KEYWORDS in keyword_parser.py)")
    parser.add_argument("--no-save-keywords", action="store_true", help="Parse keywords but don't save results to <session>/parsed_keyword/*.jsonl")
    parser.add_argument("--no-interactive", dest="interactive", action="store_false",
                        help="Receive-only; don't forward your stdin to the connection")
    parser.add_argument("--eol", choices=EOL_MAP, default="lf",
                        help="Line ending appended to each line you type when interactive (default: lf)")
