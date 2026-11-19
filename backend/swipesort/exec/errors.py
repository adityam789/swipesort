"""Executor error hierarchy."""

from __future__ import annotations


class ExecError(Exception):
    """Base class for all executor failures."""


class OutsideRootsError(ExecError):
    """A src/dst path is outside the session's input and output roots."""


class SymlinkError(ExecError):
    """A path (or one of its ancestors below the root) is a symlink; never followed."""


class LockedFileError(ExecError):
    """The file is locked by another process; retries were exhausted. Never forced."""


class UndoConflictError(ExecError):
    """A targeted undo was refused: later operations depend on the ones it would reverse."""
