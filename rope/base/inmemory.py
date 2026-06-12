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
    ".claude",
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

    # Build the parent→children index from the bulk-populated _files/_dirs.
    fs._rebuild_index()

    return fs


class InMemoryProject(Project):
    """Project backed by an in-memory filesystem.

    Excludes python-path folders located under the project root:
    sys.path often includes in-project source folders (e.g. via
    editable-install .pth files). For an in-memory project those resolve to
    real-disk resources, so find_module() would see stale on-disk files once
    in-memory mutations diverge from the disk state. In-project modules are
    already covered by the project's source folders.
    """

    def get_python_path_folders(self):
        real_root = os.path.realpath(self.address)

        def _in_project(folder):
            real = os.path.realpath(folder.real_path)
            return real == real_root or real.startswith(real_root + os.sep)

        return [
            folder
            for folder in super().get_python_path_folders()
            if not _in_project(folder)
        ]


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
        An ``InMemoryProject`` instance using ``InMemoryFileSystemCommands``.
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
    return InMemoryProject(root_path, fscommands=fs, ropefolder=None, **defaults)
