"""Structured error types for the intermap sidecar.

Error codes:
  file_not_found  — skip file, continue analysis (recoverable)
  parse_error     — AST parse failed (recoverable)
  timeout         — analysis took too long (recoverable)
  internal_error  — bug in analysis code (fatal)
"""


class IntermapError(Exception):
    """Base error with structured JSON output."""

    def __init__(self, code: str, message: str, *, recoverable: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
        }


class FileNotFoundError_(IntermapError):
    def __init__(self, message: str):
        super().__init__("file_not_found", message, recoverable=True)


class ParseError(IntermapError):
    def __init__(self, message: str):
        super().__init__("parse_error", message, recoverable=True)


class TimeoutError_(IntermapError):
    def __init__(self, message: str):
        super().__init__("timeout", message, recoverable=True)


class InternalError(IntermapError):
    def __init__(self, message: str):
        super().__init__("internal_error", message, recoverable=False)
