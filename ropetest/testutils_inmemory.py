"""In-memory replacements for testutils.sample_project /
testutils.remove_project, activated by ROPE_TEST_INMEMORY=1 via the
hook in conftest.py.
"""

import rope.base.project
from rope.base.fscommands import FileSystemCommands, InMemoryFileSystemCommands

# A single shared FS for all sample_project() calls in a pytest session.
# This mirrors disk-backed tests, where every temp project shares the real
# filesystem — essential for multi-project refactorings that resolve
# imports across project boundaries (see multiprojecttest.py).
_SHARED_FS = InMemoryFileSystemCommands()
_SHARED_FS._dirs.add("/virtual")


class _VirtualOrDiskFSCommands(FileSystemCommands):
    """Dispatches filesystem ops to the shared in-memory fs for paths
    under ``/virtual/`` and to a regular disk-backed fscommands for
    everything else.

    This is what ``NoProject`` uses under ROPE_TEST_INMEMORY.  Its job
    is to let out-of-project lookups reach BOTH in-memory test projects
    (so cross-project refactorings resolve) AND the real stdlib / site
    packages on ``sys.path`` (so type hinting, auto-import, and
    dynamic object analysis tests still work).
    """

    def __init__(self, inmem_fs):
        self._inmem = inmem_fs
        self._disk = FileSystemCommands()

    def _pick(self, path):
        return self._inmem if str(path).startswith("/virtual/") else self._disk

    def create_file(self, path):
        return self._pick(path).create_file(path)

    def create_folder(self, path):
        return self._pick(path).create_folder(path)

    def move(self, path, new_location):
        return self._pick(path).move(path, new_location)

    def remove(self, path):
        return self._pick(path).remove(path)

    def write(self, path, data):
        return self._pick(path).write(path, data)

    def read(self, path):
        return self._pick(path).read(path)

    def exists(self, path):
        return self._pick(path).exists(path)

    def isfile(self, path):
        return self._pick(path).isfile(path)

    def isdir(self, path):
        return self._pick(path).isdir(path)

    def listdir(self, path):
        return self._pick(path).listdir(path)

    def getmtime(self, path):
        return self._pick(path).getmtime(path)

    def getsize(self, path):
        return self._pick(path).getsize(path)

    def islink(self, path):
        return self._pick(path).islink(path)


_VIRTUAL_ROOT_COUNTER = 0


def sample_project(foldername=None, **kwds):
    """In-memory replacement for testutils.sample_project().

    Uses a virtual path as the project root — no tempdir, no disk
    touch.  Each call produces a unique parent container so foldername
    may safely be reused across tests (same semantics as
    ``tempfile.mkdtemp`` in the disk variant).
    """
    global _VIRTUAL_ROOT_COUNTER
    _VIRTUAL_ROOT_COUNTER += 1
    unique = f"project-{_VIRTUAL_ROOT_COUNTER}"
    base = foldername or "sample_project"
    root = f"/virtual/{unique}/{base}"
    _SHARED_FS._dirs.add(f"/virtual/{unique}")
    _SHARED_FS._dirs.add(root)
    prefs = {
        "save_objectdb": False,
        "save_history": False,
        "validate_objectdb": False,
        "automatic_soa": False,
        "ignored_resources": [".ropeproject", "*.pyc"],
        "import_dynload_stdmods": False,
    }
    prefs.update(kwds)
    return rope.base.project.Project(
        root, fscommands=_SHARED_FS, ropefolder=None, **prefs
    )


def remove_project(project):
    """In-memory replacement for testutils.remove_project().

    No tempdir to clean up — just close the project so observers
    detach and resources are released.
    """
    project.close()


def apply_inmemory_hook():
    """Monkey-patch ropetest.testutils to use the in-memory variants.

    Called from conftest.py when ROPE_TEST_INMEMORY is set.
    """
    from ropetest import testutils

    testutils.sample_project = sample_project
    testutils.remove_project = remove_project

    # Cross-project refactorings (see multiprojecttest.py) resolve
    # out-of-project resources via `NoProject`, which creates its own
    # disk-backed FileSystemCommands.  Swap in a union fs so NoProject
    # lookups reach both in-memory test projects (under /virtual/) AND
    # real stdlib / site packages on sys.path (everything else).
    from rope.base.project import get_no_project

    get_no_project().fscommands = _VirtualOrDiskFSCommands(_SHARED_FS)
