#!/usr/bin/env python3
"""TCP logger for capturing text streamed over a TCP socket - e.g. Xilinx's
JTAG UART terminal, which bridges JTAG-routed UART/CoreSight output to a
localhost Telnet/Raw socket (the same kind of connection PuTTY would make
with Connection type "Telnet" or "Raw") - to console and a timestamped log
file, with optional filtering.
"""
import argparse
import re
import socket
import sys
import time

from log_common import (
    add_common_args,
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
MAX_RETRIES = 5

def recv_or_none(sock):
    """recv() that returns None on a read timeout (b'' still means closed)."""
    try:
        return sock.recv(4096)
    except socket.timeout:
        return None


def run(args):
    pattern = re.compile(args.filter) if args.filter else None
    writer, log_path = open_log_writer(args, "tcp")
    color = args.color and sys.stdout.isatty()  # no ANSI in redirected output
    mark_cmd = args.mark_cmd if args.markers else None
    reader = start_stdin_reader(args.interactive, args.eol, mark_cmd,
                                lambda label: write_marker(writer, label, color))
    print(f"Logging {args.host}:{args.port} to {log_path}")
    if args.interactive:
        print("Interactive mode: type to send to the connection.")
    if reader and mark_cmd:
        print(f"Type '{mark_cmd} [label]' then Enter to insert a marker.")
    print("Press Ctrl+C to stop.\n")
    write_session_banner(writer, color)

    try:
        retry_count = 0
        while True:
            try:
                with socket.create_connection((args.host, args.port), timeout=5) as sock:
                    # Short timeout so Ctrl+C is honored on Windows (a fully
                    # blocking recv can't be interrupted); idle != disconnect.
                    sock.settimeout(1)
                    print(f"[{timestamp()}] connected to {args.host}:{args.port}")
                    retry_count = 0  # only consecutive failures count
                    if reader and args.interactive:
                        reader.set_target(sock.sendall)
                    for line in iter_lines(lambda: recv_or_none(sock)):
                        process_and_log(line, pattern, args.filter_only, writer, color)
                reason = "connection closed"
            except OSError as exc:
                reason = f"connection error: {exc}"
            finally:
                if reader and args.interactive:
                    reader.clear_target()
            if retry_count >= MAX_RETRIES:
                print(f"[{timestamp()}] {reason}; maximum retries reached; exiting.", file=sys.stderr)
                break
            retry_count += 1
            print(f"[{timestamp()}] {reason}; retrying in {RECONNECT_DELAY_SEC}s "
                  f"({retry_count}/{MAX_RETRIES})", file=sys.stderr)
            time.sleep(RECONNECT_DELAY_SEC)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        writer.close()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="localhost", help="Host to connect to (default: localhost)")
    p.add_argument("--port", type=int, required=True, help="TCP port (e.g. the port Vitis's JTAG UART terminal listens on)")
    add_common_args(p)
    return p.parse_args()


def main():
    run_guarded(run, parse_args())


if __name__ == "__main__":
    main()
