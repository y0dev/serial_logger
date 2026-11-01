# Serial Logger

Captures text from an embedded target and writes it to the console and a
timestamped log file, with optional highlighting, markers, log rotation and
keyword parsing. Two front ends share the same core:

| Script | Source |
| --- | --- |
| `main.py` | A real or virtual COM port (e.g. a board's debug UART) |
| `tcp_logger.py` | A TCP socket (e.g. Xilinx's JTAG UART terminal on localhost) |

Both reconnect automatically and give up after `MAX_RETRIES` consecutive
failures. `Ctrl+C` stops cleanly.

## Install

```
pip install -r requirements.txt
```

Requires Python 3.8+ and `pyserial`.

## Usage

```
python main.py --list-ports
python main.py --port COM5 --baud 115200
python main.py                       # prompts for a port (q to quit)

python tcp_logger.py --port 3121
python tcp_logger.py --host 192.168.1.50 --port 3121
```

### Batch file templates

`run_serial.bat` and `run_tcp.bat` start the logger with fixed settings. Copy
one, edit the settings block at the top (`COM_PORT`/`BAUD` or
`TCP_HOST`/`TCP_PORT`, `LOG_DIR`, `EOL`), then double-click it. Extra options
can be added after the file name, e.g. `run_serial.bat --filter ERROR`.

### Options (both scripts)

| Option | Description |
| --- | --- |
| `--log-dir DIR` | Root directory for logs (default `./logs`) |
| `--name NAME` | Log filename; `.log`/`.csv`/`.jsonl` is added if there is no extension |
| `--overwrite` | Overwrite the log instead of appending |
| `--max-mb N` | Start a new file after N MB (`<name>.part0001.log`, `.part0002.log`, ...) |
| `--format text\|csv\|json` | File format; csv has `timestamp,matched,line` columns, json is one object per line |
| `--filter REGEX` | Highlight matching lines (red) |
| `--filter-only` | Only log and print lines matching `--filter` |
| `--no-color` | Disable console colors (also off when output is redirected) |
| `--no-timestamps` | Don't prefix lines with a timestamp (text format and console) |
| `--date` | Include the date in every timestamp |
| `--no-keywords` | Disable keyword parsing |
| `--no-save-keywords` | Parse keywords but don't write `parsed_keyword/*.jsonl` |
| `--no-interactive` | Receive only; don't send what you type |
| `--eol lf\|cr\|crlf\|none` | Line ending added to what you type (default `lf`) |
| `--mark-cmd CMD` | Command that inserts a marker (default `/mark`) |
| `--no-markers` | Disable the marker command |

`main.py` also takes `--port`, `--baud` (default 115200) and `--list-ports`.
`tcp_logger.py` takes `--host` (default `localhost`) and a required `--port`.

### Interactive mode

Anything you type is sent to the device followed by the `--eol` ending. If a
device waits for a carriage return, as many UART shells do, use `--eol cr` or
`--eol crlf`.

Type `/mark` or `/mark some label` and press Enter to insert a divider in the
log, for example before starting a test. Marker lines are never sent to the
device.

### Log location

Each run gets its own folder:

```
logs/YYYY/MM_MON/MM_DD_YYYY/HH_MM_SS/<port>.log
```

### Line endings

Incoming data may end lines with `\n`, `\r\n`, a lone `\r`, or `\r\r\n`; each
counts as one line break.

## Keyword parsing

`keyword_parser.py` watches the log for keyword lines. When a line matches a
keyword, the **next line** is parsed with that keyword's regular expression
and its named groups are extracted as fields. There is no command-line option
for adding keywords; you edit the `KEYWORDS` dictionary.

The serial logger (`main.py`) has this wired in. `tcp_logger.py` does not yet.

### Add a keyword

Add an entry to `KEYWORDS` in `keyword_parser.py`: the key is the keyword name,
the value is a regex. **Named groups** (`(?P<name>...)`) become the fields.

```python
KEYWORDS = {
    "START": r"\bSTART\b(?:\s+(?P<id>\w+))?",
    "BOOT": r"Boot complete in (?P<ms>\d+) ms",
}
```

How it behaves:

- A line matching a keyword only arms the parser. The line after it is parsed
  with the same pattern, and the console shows:

  ```
  [12:00:01.234] Parsed keyword 'BOOT': {'ms': '1520'}
  ```

- Matching uses `re.search`, so the pattern can match anywhere in the line.
- Keywords are checked in dictionary order and **the first match wins**, so
  list specific patterns first.
- If the following line doesn't match the pattern, the result has empty
  `fields`.
- Patterns without named groups work; their `fields` is empty.

### Saved results

Every parsed result is appended as a JSON line to
`<session folder>/parsed_keyword/<KEYWORD>.jsonl`, next to the log file:

```json
{"keyword": "BOOT", "fields": {"ms": "1520"}, "lines": "Boot complete in 1520 ms"}
```

Each keyword's file is opened on its first result and kept open (flushed after
every result) until the logger stops. Pass `--no-save-keywords` to parse and
print results without saving them.

### Act on a keyword

Keyword-specific logic goes in `KeywordParser.parse()`. It already stores the
latest `START` result in `parser.data` (a `StartData` with `raw` and `fields`):

```python
if name == "START":
    self.data = StartData(raw=line.replace("\n", ""), fields=fields)
elif name == "BOOT":
    self.boot_ms = int(fields["ms"])
```

To turn keyword parsing off, pass `--no-keywords` or set `KEYWORDS = {}`.

## Tests

```
python test_logger.py
```

Prints the results and writes them to
`test_results/MM_DD_YYYY_HHmmss_test.log`. The exit status is non-zero if any
test fails. The tests use mocks, so no hardware or network is needed.

## Files

| File | Purpose |
| --- | --- |
| `main.py` | Serial port logger |
| `tcp_logger.py` | TCP socket logger |
| `log_common.py` | Shared code: line splitting, log writer, markers, arguments |
| `keyword_parser.py` | Keyword matching and field extraction |
| `test_logger.py` | Test suite |
| `run_serial.bat`, `run_tcp.bat` | Launcher templates |
