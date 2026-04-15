"""In-memory project support for rope.

Provides ``snapshot_project()`` to read a directory tree from disk into an
``InMemoryFileSystemCommands``, and ``in_memory_project()`` to create a
rope ``Project`` backed entirely by that in-memory store.
"""

import fnmatch
import os

from rope.base.fscommands import InMemoryFileSystemCommands
from rope.base.project import Project, _realpath

DEFAULT_IGNORED_PATTERNS = [
    ".ropeproject",
    "*.pyc",
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".tox",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
]


def snapshot_project(root_path, ignored_patterns=None):
    """Read a directory tree from disk into an InMemoryFileSystemCommands.

    Args:
        root_path: Absolute path to the project root on disk.
        ignored_patterns: Optional list of fnmatch patterns to skip
            (matched against basenames of files and directories).
            If None, uses DEFAULT_IGNORED_PATTERNS.

    Returns:
        An InMemoryFileSystemCommands pre-populated with the directory
        tree's files and folders.
    """
    if ignored_patterns is None:
        ignored_patterns = DEFAULT_IGNORED_PATTERNS

    root_path = _realpath(root_path)
    fs = InMemoryFileSystemCommands()
    fs._dirs.add(root_path)

    def _is_ignored(name):
        return any(fnmatch.fnmatch(name, pat) for pat in ignored_patterns)

    for dirpath, dirnames, filenames in os.walk(root_path, topdown=True):
        # Prune ignored directories in-place
        dirnames[:] = [d for d in dirnames if not _is_ignored(d)]

        for d in dirnames:
            full = os.path.join(dirpath, d)
            fs._dirs.add(full)

        for f in filenames:
            if _is_ignored(f):
                continue
            full = os.path.join(dirpath, f)
            try:
                with open(full, "rb") as fh:
                    fs._files[full] = fh.read()
            except (OSError, PermissionError):
                pass

    # Reset clock so first real mutation starts at 1.0
    fs._clock = 0.0

    return fs


def in_memory_project(root_path, ignored_patterns=None, **prefs):
    """Create a rope Project backed entirely by an in-memory filesystem.

    Reads the project from disk once via ``snapshot_project()``, then all
    subsequent operations (reads, writes, refactors) happen in memory.

    Args:
        root_path: Absolute path to the real project root.
        ignored_patterns: Optional list of fnmatch patterns for
            ``snapshot_project()``.
        **prefs: Additional project preferences. ``ropefolder`` is forced
            to ``None``.

    Returns:
        A rope ``Project`` instance using ``InMemoryFileSystemCommands``.
    """
    fs = snapshot_project(root_path, ignored_patterns=ignored_patterns)
    defaults = {
        "save_objectdb": False,
        "save_history": False,
        "validate_objectdb": False,
        "automatic_soa": False,
        "import_dynload_stdmods": False,
    }
    defaults.update(prefs)
    return Project(root_path, fscommands=fs, ropefolder=None, **defaults)
