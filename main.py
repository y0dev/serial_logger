#!/usr/bin/env python3
"""Serial logger for capturing UART/console output (e.g. from a board's
debug UART exposed as a real or virtual COM port) to console and a
timestamped log file, with optional filtering.
"""
import argparse
import re
import sys
import time

import serial
import serial.tools.list_ports

from datetime import datetime
from pathlib import Path

from keyword_parser import KeywordParser
from log_common import (
    add_common_args,
    build_keyword_parser,
    iter_lines,
    open_log_writer,
    process_and_log,
    run_guarded,
    start_stdin_reader,
    timestamp,
    write_marker,
    write_session_banner,
)

RECONNECT_DELAY_SEC = 2
MAX_RETRIES = 10


def list_ports():
    return list(serial.tools.list_ports.comports())


def choose_port():
    ports = list_ports()
    # ignore ports with no description (e.g. Bluetooth)
    ports = [p for p in ports if p.description]
    if not ports:
        print("No serial ports found.", file=sys.stderr)
        sys.exit(1)

    if len(ports) == 1:
        print(f"Using only available port: {ports[0].device} ({ports[0].description})")
        return ports[0].device
    print("Available serial ports:")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p.device} - {p.description}")
    while True:
        choice = input(f"Select port [0-{len(ports) - 1}, q to quit]: ").strip()
        if choice.lower() in ("q", "quit", "exit"):
            raise KeyboardInterrupt
        if choice.isdigit() and 0 <= int(choice) < len(ports):
            return ports[int(choice)].device
        print("Invalid selection, try again.")


def run(args):
    port = args.port or choose_port()
    pattern = re.compile(args.filter) if args.filter else None
    writer, log_path = open_log_writer(args, port)
    color = args.color and sys.stdout.isatty()  # no ANSI in redirected output
    mark_cmd = args.mark_cmd if args.markers else None
    keyword_parser: KeywordParser = build_keyword_parser(args, log_path.parent)
    reader = start_stdin_reader(args.interactive, args.eol, mark_cmd,
                                lambda label: write_marker(writer, label, color))
    print(f"Logging {port} @ {args.baud} baud to {log_path}")
    if args.interactive:
        print("Interactive mode: type to send to the port.")
    if reader and mark_cmd:
        print(f"Type '{mark_cmd} [label]' then Enter to insert a marker.")
    print("Press Ctrl+C to stop.\n")
    write_session_banner(writer, color)

    try:
        retry_count = 0
        while True:
            try:
                with serial.Serial(port, args.baud, timeout=1) as ser:
                    print(f"[{timestamp()}] connected to {port}")
                    retry_count = 0  # only consecutive failures count
                    if reader and args.interactive:
                        reader.set_target(ser.write)
                    for line in iter_lines(lambda: ser.read(ser.in_waiting or 1), stop_on_empty=False):
                        process_and_log(line, pattern, args.filter_only, writer, color, keyword_parser)
            except (serial.SerialException, OSError) as exc:
                if retry_count >= MAX_RETRIES:
                    print(f"[{timestamp()}] port error: {exc}; maximum retries reached; exiting.", file=sys.stderr)
                    break
                retry_count += 1
                print(f"[{timestamp()}] port error: {exc}; retrying in {RECONNECT_DELAY_SEC}s "
                      f"({retry_count}/{MAX_RETRIES})", file=sys.stderr)
                time.sleep(RECONNECT_DELAY_SEC)
            finally:
                if reader and args.interactive:
                    reader.clear_target()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        writer.close()
        if keyword_parser:
            keyword_parser.close()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", help="Serial port (e.g. COM5). Prompts to choose if omitted.")
    p.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    p.add_argument("--list-ports", action="store_true", help="List available serial ports and exit")
    add_common_args(p)
    return p.parse_args()


def main():
    args = parse_args()
    if args.list_ports:
        for p in list_ports():
            print(f"{p.device} - {p.description}")
        return
    run_guarded(run, args)


if __name__ == "__main__":
    main()
