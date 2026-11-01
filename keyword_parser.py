"""Matches log lines against named keyword patterns and extracts their fields."""
import json
import re
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

from ansi import ANSI_DIM, ANSI_RESET


# Keywords matched by default. Name -> regex; named groups become the fields.
# Add new keywords here (first match wins, so list specific patterns first).
KEYWORDS = {
    "START": r"\bSTART\b(?:\s+(?P<id>\w+))?",
}


def _timestamp():
    """Current time as HH:MM:SS.mmm (same format as log_common.timestamp, which
    can't be imported here without a circular import)."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


@dataclass
class StartData:
    """Data captured from a START line.

    Attributes:
        raw: The matched line with newline characters removed.
        fields: Named regex groups extracted from the line.
    """
    raw: str
    fields: dict = field(default_factory=dict)


class KeywordParser:
    """Classifies log lines by keyword and extracts named fields from them.

    Each keyword maps to a regular expression. Named groups in the expression
    (e.g. ``(?P<value>\\d+)``) become the entries of the result's ``fields``.

    Example:
        parser = KeywordParser(keywords={"START": r"START (?P<id>\\w+)", "END": r"END"})
        parser.feed("START")         # keyword line: arms the parser, returns None
        result = parser.feed("START abc123")
        # {"keyword": "START", "fields": {"id": "abc123"}, "lines": "START abc123"}

    Attributes:
        patterns: Mapping of keyword name to compiled regular expression.
        data: The StartData from the most recent START line, or None.
    """

    def __init__(self, session_dir=None, keywords=KEYWORDS):
        """Initializes the parser.

        Args:
            session_dir: Session log directory; if given, every parsed result
                is appended as a JSON line to
                ``<session_dir>/parsed_keyword/<KEYWORD>.jsonl``.
            keywords: Mapping of keyword name to a regular expression, given
                either as a string or as an already compiled pattern.
        """
        self.pending = None
        self.session_dir = Path(session_dir) / "parsed_keyword" if session_dir else None
        self.patterns = {name: re.compile(pattern) for name, pattern in keywords.items()}
        self.data = None

    def feed(self, line):
        """Detects a keyword line, then parses the line that follows it.

        A line matching a keyword only arms the parser (the first match wins)
        and returns None. The next line is then parsed with that keyword's
        pattern and its result returned.

        Args:
            line: One log line, with or without a trailing newline.

        Returns:
            The parse() result for the line after a keyword line, or None for
            every other line.
        """
        if self.pending:
            name, self.pending = self.pending, None
            result = self.parse(name, line)
            self._save(result)
            return result
        for name, pattern in self.patterns.items():
            if pattern.search(line):
                self.pending = name
                break
        return None

    def _save(self, result):
        """Appends a parse result to its keyword's JSON Lines file, if enabled."""
        if not self.session_dir:
            return
        self.session_dir.mkdir(parents=True, exist_ok=True)
        path = self.session_dir / f"{result['keyword']}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    def parse(self, name, line):
        """Extracts the fields of a line for a known keyword.

        Args:
            name: Keyword name; must be a key of ``patterns``.
            line: The log line to parse.

        Returns:
            A dict with the keys "keyword" (the name), "fields" (dict of named
            groups, empty if the pattern has none) and "lines" (the line).

        Raises:
            KeyError: If name is not a configured keyword.
        """
        match = self.patterns[name].search(line)
        fields = match.groupdict() if match else {}
        result = {"keyword": name, "fields": fields, "lines": line}

        if name == "START":
            self.data = StartData(raw=line.replace("\n", ""), fields=fields)
        # Additional logic for other keywords can be added here.

        if fields:
            pretty = " ".join(f"{k}={v}" for k, v in fields.items())
            print(f"{ANSI_DIM}[{_timestamp()}] {name} -> {pretty}{ANSI_RESET}")
        else:
            print(f"{ANSI_DIM}[{_timestamp()}] {name} -> {line}{ANSI_RESET}")

        return result
