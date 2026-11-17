"""Structured log stream writer for the ingest pipeline."""

import sys


class LogStream:
    def __init__(self, stream=None):
        self._stream = stream or sys.stderr

    def warn(self, message):
        self._stream.write(f"WARN {message}\n")

    def info(self, message):
        self._stream.write(f"INFO {message}\n")
