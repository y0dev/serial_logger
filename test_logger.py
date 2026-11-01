#!/usr/bin/env python3
"""Tests for the serial/TCP logger helpers.

Run:  python test_logger.py
Results are printed and also written to
test_results/MM_DD_YYYY_HHmmss_test.log. Exit status is non-zero on failure.
"""
import argparse
import csv
import io
import json
import re
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import log_common as lc  # noqa: E402
import main as serial_main  # noqa: E402
import tcp_logger  # noqa: E402
from keyword_parser import KEYWORDS, KeywordParser  # noqa: E402


def lines(*chunks, stop_on_empty=True):
    """Feed chunks (bytes/None) through iter_lines; return the lines as str."""
    it = iter(chunks)
    return list(lc.iter_lines(lambda: next(it, b""), stop_on_empty=stop_on_empty))


class FakeWriter:
    timestamps = True

    def __init__(self):
        self.records = []

    def stamp(self):
        return "00:00:00.000"

    def write(self, ts, line, matched):
        self.records.append((ts, line, bool(matched)))


class IterLinesTests(unittest.TestCase):
    def test_lf(self):
        self.assertEqual(lines(b"a\nb\n"), ["a", "b"])

    def test_crlf(self):
        self.assertEqual(lines(b"a\r\nb\r\n"), ["a", "b"])

    def test_lone_cr(self):
        self.assertEqual(lines(b"a\rb\r"), ["a", "b"])

    def test_double_cr_lf(self):
        self.assertEqual(lines(b"a\r\r\nb\n"), ["a", "b"])

    def test_crlf_split_across_chunks(self):
        self.assertEqual(lines(b"a\r", b"\nb\n"), ["a", "b"])

    def test_cr_cr_lf_split_after_first_cr(self):
        self.assertEqual(lines(b"a\r", b"\r\nb\n"), ["a", "b"])

    def test_line_split_across_chunks(self):
        self.assertEqual(lines(b"he", b"llo\nwor", b"ld\n"), ["hello", "world"])

    def test_blank_lines_kept(self):
        self.assertEqual(lines(b"a\n\nb\n"), ["a", "", "b"])

    def test_trailing_partial_line_flushed_on_close(self):
        self.assertEqual(lines(b"a\nb"), ["a", "b"])

    def test_timeout_none_is_skipped(self):
        self.assertEqual(lines(None, b"a\n", None), ["a"])

    def test_empty_read_is_a_timeout_for_serial(self):
        # stop_on_empty=False never ends on b"", so end the stream with an exception.
        chunks = iter([b"", b"a\n", b"", b"b\n"])

        def read():
            try:
                return next(chunks)
            except StopIteration:
                raise EOFError

        got = []
        with self.assertRaises(EOFError):
            for line in lc.iter_lines(read, stop_on_empty=False):
                got.append(line)
        self.assertEqual(got, ["a", "b"])

    def test_invalid_utf8_replaced(self):
        self.assertEqual(lines(b"\xff ok\n"), ["� ok"])


class KeywordParserTests(unittest.TestCase):
    def setUp(self):
        self.p = KeywordParser(keywords={"START": r"START (?P<id>\w+)", "END": "END"})

    def test_keyword_line_arms_and_returns_none(self):
        self.assertIsNone(self.p.feed("x START abc"))
        self.assertEqual(self.p.pending, "START")

    def test_next_line_is_parsed(self):
        self.p.feed("begin START abc")
        r = self.p.feed("now START xyz")
        self.assertEqual(r["keyword"], "START")
        self.assertEqual(r["fields"], {"id": "xyz"})
        self.assertIsNone(self.p.pending)

    def test_next_line_without_match_has_no_fields(self):
        self.p.feed("START abc")
        self.assertEqual(self.p.feed("unrelated")["fields"], {})

    def test_start_sets_data(self):
        self.p.feed("START abc")
        self.p.feed("START abc\n")
        self.assertEqual(self.p.data.raw, "START abc")
        self.assertEqual(self.p.data.fields, {"id": "abc"})

    def test_no_match_returns_none(self):
        self.assertIsNone(self.p.feed("nothing"))
        self.assertIsNone(self.p.pending)

    def test_first_keyword_wins(self):
        self.p.feed("START abc END")
        self.assertEqual(self.p.pending, "START")

    def test_defaults_to_module_keywords(self):
        self.assertEqual(set(KeywordParser().patterns), set(KEYWORDS))

    def test_session_dir_saves_results_as_jsonl(self):
        d = Path(tempfile.mkdtemp())
        p = KeywordParser(d, {"A": r"a(?P<n>\d)"})
        p.feed("a0")
        self.assertFalse((d / "parsed_keyword").exists())  # nothing until parsed
        p.feed("a1")
        p.feed("a0")
        p.feed("a2")
        recs = [json.loads(x) for x in (d / "parsed_keyword" / "A.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["fields"] for r in recs], [{"n": "1"}, {"n": "2"}])
        self.assertEqual(recs[0]["keyword"], "A")
        p.close()

    def test_result_file_kept_open_between_results(self):
        d = Path(tempfile.mkdtemp())
        p = KeywordParser(d, {"A": "a"})
        with mock.patch("builtins.open", wraps=open) as opened:
            for _ in range(3):
                p.feed("a")
                p.feed("a")
        self.assertEqual(opened.call_count, 1)
        p.close()
        self.assertEqual(len((d / "parsed_keyword" / "A.jsonl").read_text(encoding="utf-8").splitlines()), 3)

    def test_close_is_safe_without_files(self):
        KeywordParser(keywords={"A": "a"}).close()

    def test_no_session_dir_writes_nothing(self):
        p = KeywordParser(keywords={"A": "a"})
        p.feed("a")
        self.assertIsNotNone(p.feed("a"))

    def test_unknown_keyword_raises(self):
        with self.assertRaises(KeyError):
            self.p.parse("NOPE", "x")


class BuildKeywordParserTests(unittest.TestCase):
    def test_uses_default_keywords(self):
        p = lc.build_keyword_parser(types.SimpleNamespace())
        self.assertIsInstance(p, KeywordParser)
        self.assertEqual(set(p.patterns), set(lc.KEYWORDS))
        p.feed("START abc")
        self.assertEqual(p.feed("START abc")["fields"], {"id": "abc"})

    def test_args_optional(self):
        self.assertIsNotNone(lc.build_keyword_parser())

    def test_session_dir_passed_through(self):
        d = Path(tempfile.mkdtemp())
        p = lc.build_keyword_parser(None, d)
        p.feed("START a")
        p.feed("START a")
        p.close()
        self.assertTrue((d / "parsed_keyword" / "START.jsonl").is_file())

    def test_no_save_keywords_flag(self):
        ap = argparse.ArgumentParser()
        lc.add_common_args(ap)
        self.assertFalse(ap.parse_args([]).no_save_keywords)
        d = Path(tempfile.mkdtemp())
        p = lc.build_keyword_parser(ap.parse_args(["--no-save-keywords"]), d)
        self.assertIsNotNone(p)  # still parses
        p.feed("START a")
        self.assertEqual(p.feed("START a")["fields"], {"id": "a"})
        self.assertFalse((d / "parsed_keyword").exists())

    def test_no_keywords_flag(self):
        ap = argparse.ArgumentParser()
        lc.add_common_args(ap)
        self.assertFalse(ap.parse_args([]).no_keywords)
        args = ap.parse_args(["--no-keywords"])
        self.assertTrue(args.no_keywords)
        self.assertIsNone(lc.build_keyword_parser(args))
        self.assertIsNotNone(lc.build_keyword_parser(ap.parse_args([])))

    def test_none_when_no_keywords(self):
        with mock.patch.object(lc, "KEYWORDS", {}):
            self.assertIsNone(lc.build_keyword_parser())


class ProcessAndLogTests(unittest.TestCase):
    def run_one(self, line, **kw):
        w = FakeWriter()
        out = io.StringIO()
        with redirect_stdout(out):
            lc.process_and_log(line, kw.pop("pattern", None), kw.pop("filter_only", False),
                               w, kw.pop("color", False), kw.pop("kw_parser", None))
        return w, out.getvalue()

    def test_logs_and_prints(self):
        w, out = self.run_one("hello")
        self.assertEqual(w.records, [("00:00:00.000", "hello", False)])
        self.assertIn("[00:00:00.000] hello", out)

    def test_keyword_result_printed(self):
        p = KeywordParser(keywords={"START": r"START (?P<id>\w+)"})
        self.run_one("START abc", kw_parser=p)  # arms the parser
        _, out = self.run_one("START xyz", kw_parser=p)
        self.assertIn("Parsed keyword 'START': {'id': 'xyz'}", out)

    def test_keyword_result_printed_once_and_uncolored(self):
        p = KeywordParser(keywords={"START": r"START (?P<id>\w+)"})
        self.run_one("START abc", kw_parser=p)
        _, out = self.run_one("START xyz", kw_parser=p, color=False)
        self.assertEqual(out.count("xyz"), 2)  # the line itself + one parse result
        self.assertNotIn("\033[", out)

    def test_parse_prints_nothing(self):
        out = io.StringIO()
        with redirect_stdout(out):
            KeywordParser(keywords={"A": "a"}).parse("A", "a")
        self.assertEqual(out.getvalue(), "")

    def test_filter_only_drops_nonmatching(self):
        w, out = self.run_one("nope", pattern=re.compile("yes"), filter_only=True)
        self.assertEqual((w.records, out), ([], ""))

    def test_filter_match_marked(self):
        w, _ = self.run_one("yes", pattern=re.compile("yes"))
        self.assertTrue(w.records[0][2])

    def test_color_only_when_enabled(self):
        self.assertIn("\033[", self.run_one("ERROR boom", color=True)[1])
        self.assertNotIn("\033[", self.run_one("ERROR boom", color=False)[1])


class LogWriterTests(unittest.TestCase):
    def test_formats_and_rotation(self):
        d = Path(tempfile.mkdtemp())
        for fmt in ("text", "csv", "json"):
            w = lc.LogWriter(d / f"t.{fmt}", fmt, True, False, False, 200)
            for i in range(40):
                w.write("ts", f'line,"{i}" é', i % 2 == 0)
            w.close()
        self.assertGreater(len(list(d.glob("t.part*.text"))), 1)
        rows = list(csv.reader(open(d / "t.part0001.csv", encoding="utf-8", newline="")))
        self.assertEqual(rows[0], ["timestamp", "matched", "line"])
        rec = json.loads(open(d / "t.part0001.json", encoding="utf-8").readline())
        self.assertEqual(set(rec), {"timestamp", "matched", "line"})
        self.assertIn("CONTINUED", (d / "t.part0002.text").read_text(encoding="utf-8"))

    def test_no_rotation_single_file(self):
        d = Path(tempfile.mkdtemp())
        w = lc.LogWriter(d / "t.log", "text", False)
        w.write("ts", "x", False)
        w.close()
        self.assertEqual((d / "t.log").read_text(encoding="utf-8"), "x\n")

    def test_overwrite(self):
        d = Path(tempfile.mkdtemp())
        for _ in range(2):
            w = lc.LogWriter(d / "t.log", "text", False, overwrite=True)
            w.write("ts", "x", False)
            w.close()
        self.assertEqual((d / "t.log").read_text(encoding="utf-8"), "x\n")


class PathTests(unittest.TestCase):
    def test_port_names_stay_inside_dir(self):
        d = Path(tempfile.mkdtemp())
        for prefix in ("COM5", "/dev/ttyUSB0", "\\\\.\\COM12", "tcp"):
            p = lc.log_base_path(d, prefix, None, "log")
            self.assertEqual(p.parent, d, prefix)

    def test_custom_name_extension(self):
        d = Path(tempfile.mkdtemp())
        self.assertEqual(lc.log_base_path(d, "COM5", "run", "log").name, "run.log")
        self.assertEqual(lc.log_base_path(d, "COM5", "run.txt", "log").name, "run.txt")

    def test_session_dir_layout(self):
        s = lc.build_session_log_dir("logs", datetime(2024, 3, 5, 14, 22, 1))
        self.assertEqual(Path(s).parts[-4:], ("2024", "03_MAR", "03_05_2024", "14_22_01"))


class StdinReaderTests(unittest.TestCase):
    def test_marker_label(self):
        r = lc.StdinReader(True, "crlf", "/mark", lambda label: None)
        got = [r._marker_label(x) for x in ("/mark", "/mark hi there", "/markx", "hello")]
        self.assertEqual(got, ["", "hi there", None, None])

    def test_marker_disabled(self):
        r = lc.StdinReader(True, "lf", None, lambda label: None)
        self.assertIsNone(r._marker_label("/mark"))

    def run_reader(self, typed, send):
        sent_lines = []
        r = lc.StdinReader(True, "crlf", "/mark", lambda label: None, sent_lines.append)
        r.set_target(send)
        with mock.patch.object(lc.sys, "stdin", io.StringIO(typed)), redirect_stderr(io.StringIO()):
            r.run()
        return sent_lines

    def test_sent_lines_reported(self):
        wire = []
        self.assertEqual(self.run_reader("help\n/mark x\nreboot\n", wire.append), ["help", "reboot"])
        self.assertEqual(wire, [b"help\r\n", b"reboot\r\n"])

    def test_failed_send_not_reported(self):
        def fail(data):
            raise OSError("gone")
        self.assertEqual(self.run_reader("help\n", fail), [])

    def test_write_sent_logs_tx_line(self):
        w = FakeWriter()
        lc.write_sent(w, "reboot")
        self.assertEqual(w.records, [("00:00:00.000", "[TX] reboot", False)])


class RetryLimitTests(unittest.TestCase):
    def test_default_gives_up_within_10_seconds(self):
        for mod in (serial_main, tcp_logger):
            self.assertLessEqual(mod.MAX_RETRIES * mod.RECONNECT_DELAY_SEC, 10, mod.__name__)


class ChoosePortTests(unittest.TestCase):
    def ports(self, *descs):
        return [types.SimpleNamespace(device=f"COM{i}", description=d) for i, d in enumerate(descs, 1)]

    def test_no_ports_exits(self):
        with mock.patch.object(serial_main, "list_ports", return_value=[]), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            serial_main.choose_port()

    def test_all_undescribed_exits_not_loops(self):
        with mock.patch.object(serial_main, "list_ports", return_value=self.ports("", "")), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            serial_main.choose_port()

    def test_single_port_auto(self):
        with mock.patch.object(serial_main, "list_ports", return_value=self.ports("A", "")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(serial_main.choose_port(), "COM1")

    def test_select_by_number_and_quit(self):
        with mock.patch.object(serial_main, "list_ports", return_value=self.ports("A", "B")), \
                redirect_stdout(io.StringIO()):
            with mock.patch("builtins.input", side_effect=["x", "9", "1"]):
                self.assertEqual(serial_main.choose_port(), "COM2")
            with mock.patch("builtins.input", side_effect=["q"]), self.assertRaises(KeyboardInterrupt):
                serial_main.choose_port()


class RunGuardedTests(unittest.TestCase):
    def py(self, body, **env):
        code = (f"import sys; sys.path.insert(0, {str(HERE)!r}); import log_common as lc\n" + body)
        e = {**__import__("os").environ, **env}
        return subprocess.run([sys.executable, "-c", code], capture_output=True, env=e)

    def test_ctrl_c_exits_130_without_traceback(self):
        r = self.py("def r(a): raise KeyboardInterrupt\nlc.run_guarded(r, None)")
        self.assertEqual(r.returncode, 130)
        self.assertNotIn(b"Traceback", r.stderr)

    def test_eof_exits_130(self):
        r = self.py("def r(a): raise EOFError\nlc.run_guarded(r, None)")
        self.assertEqual(r.returncode, 130)

    def test_unencodable_output_does_not_crash(self):
        r = self.py("lc.run_guarded(lambda a: print(b'\\xff'.decode('utf-8', 'replace')), None)",
                    PYTHONIOENCODING="cp1252:strict")
        self.assertEqual(r.returncode, 0, r.stderr)


def args_for(**over):
    base = dict(log_dir=Path(tempfile.mkdtemp()), name=None, overwrite=False, max_mb=0,
                filter=None, color=False, mark_cmd="/mark", markers=False, filter_only=False,
                timestamps=True, date=False, format="text", interactive=False, eol="lf")
    base.update(over)
    return types.SimpleNamespace(**base)


class RetryTests(unittest.TestCase):
    """Reconnect loops must give up after MAX_RETRIES consecutive failures."""

    def test_tcp_gives_up(self):
        with mock.patch.object(tcp_logger, "MAX_RETRIES", 2), \
                mock.patch.object(tcp_logger, "RECONNECT_DELAY_SEC", 0), \
                mock.patch.object(tcp_logger.socket, "create_connection", side_effect=ConnectionRefusedError("no")) as c, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            tcp_logger.run(args_for(host="localhost", port=1))
        self.assertEqual(c.call_count, 3)  # first try + 2 retries
        self.assertIn("maximum retries reached", err.getvalue())

    def test_serial_gives_up(self):
        import serial
        with mock.patch.object(serial_main, "MAX_RETRIES", 2), \
                mock.patch.object(serial_main, "RECONNECT_DELAY_SEC", 0), \
                mock.patch.object(serial_main.serial, "Serial", side_effect=serial.SerialException("gone")) as c, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            serial_main.run(args_for(port="COMX", baud=115200))
        self.assertEqual(c.call_count, 3)
        self.assertIn("maximum retries reached", err.getvalue())

    def test_tcp_counter_resets_after_successful_connect(self):
        class ClosedSock:  # connects fine, peer closes immediately
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def settimeout(s, t): pass
            def recv(s, n): return b""

        outcomes = iter([ConnectionRefusedError("no"), ClosedSock(), ConnectionRefusedError("no"),
                         ClosedSock(), ConnectionRefusedError("no"), ConnectionRefusedError("no")])

        def connect(*a, **k):
            o = next(outcomes)
            if isinstance(o, Exception):
                raise o
            return o

        with mock.patch.object(tcp_logger, "MAX_RETRIES", 2),                 mock.patch.object(tcp_logger, "RECONNECT_DELAY_SEC", 0),                 mock.patch.object(tcp_logger.socket, "create_connection", side_effect=connect) as c,                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            tcp_logger.run(args_for(host="localhost", port=1))
        # Without the reset it would give up after 3 attempts; it must survive
        # the two successful connections and stop only on two failures in a row.
        self.assertEqual(c.call_count, 6)


class TeeStream(io.TextIOBase):
    """Write to several streams at once."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def main():
    out_dir = HERE / "test_results"
    out_dir.mkdir(exist_ok=True)
    log_path = out_dir / f"{datetime.now().strftime('%m_%d_%Y_%H%M%S')}_test.log"
    with open(log_path, "w", encoding="utf-8") as f:
        tee = TeeStream(sys.stdout, f)
        tee.write(f"Test run {datetime.now():%Y-%m-%d %H:%M:%S} (python {sys.version.split()[0]})\n\n")
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        result = unittest.TextTestRunner(stream=tee, verbosity=2).run(suite)
        status = "PASSED" if result.wasSuccessful() else "FAILED"
        tee.write(f"\n{status}: {result.testsRun} run, {len(result.failures)} failed, "
                  f"{len(result.errors)} errors, {len(result.skipped)} skipped\n")
    print(f"\nResults written to {log_path}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
