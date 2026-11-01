"""ANSI escape codes for console colors (kept separate so log_common and
keyword_parser can both use them without importing each other).
"""
ANSI_RESET = "\033[0m"
ANSI_RED = "\033[31;1m"
ANSI_YELLOW = "\033[33m"
ANSI_GREEN = "\033[32m"
ANSI_BRIGHT_GREEN = "\033[92m"
ANSI_DIM = "\033[2m"
ANSI_CYAN = "\033[36;1m"
