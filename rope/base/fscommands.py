"""Project file system commands.

This modules implements file system operations used by rope.  Different
version control systems can be supported by implementing the interface
provided by `FileSystemCommands` class.  See `SubversionCommands` and
`MercurialCommands` for example.

"""

import os
import re
import shutil
import subprocess
import typing

FileContent = typing.NewType("FileContent", bytes)


def create_fscommands(root):
    dirlist = os.listdir(root)
    commands = {
        ".hg": MercurialCommands,
        ".svn": SubversionCommands,
        ".git": GITCommands,
        "_svn": SubversionCommands,
        "_darcs": DarcsCommands,
    }
    for key in commands:
        if key in dirlist:
            try:
                return commands[key](root)
            except (ImportError, OSError):
                pass
    return FileSystemCommands()


class FileSystemCommands:
    def create_file(self, path):
        open(path, "w").close()

    def create_folder(self, path):
        os.mkdir(path)

    def move(self, path, new_location):
        shutil.move(path, new_location)

    def remove(self, path):
        if os.path.isfile(path):
            os.remove(path)
        else:
            shutil.rmtree(path)

    def write(self, path, data):
        file_ = open(path, "wb")
        try:
            file_.write(data)
        finally:
            file_.close()

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def exists(self, path):
        return os.path.exists(path)

    def isfile(self, path):
        return os.path.isfile(path)

    def isdir(self, path):
        return os.path.isdir(path)

    def listdir(self, path):
        return os.listdir(path)

    def getmtime(self, path):
        return os.path.getmtime(path)

    def getsize(self, path):
        return os.path.getsize(path)

    def islink(self, path):
        return os.path.islink(path)


class SubversionCommands(FileSystemCommands):
    def __init__(self, *args):
        self.normal_actions = FileSystemCommands()
        import pysvn  # type:ignore

        self.client = pysvn.Client()

    def create_file(self, path):
        self.normal_actions.create_file(path)
        self.client.add(path, force=True)

    def create_folder(self, path):
        self.normal_actions.create_folder(path)
        self.client.add(path, force=True)

    def move(self, path, new_location):
        self.client.move(path, new_location, force=True)

    def remove(self, path):
        self.client.remove(path, force=True)

    def write(self, path, data):
        self.normal_actions.write(path, data)

    def read(self, path):
        return self.normal_actions.read(path)


class MercurialCommands(FileSystemCommands):
    def __init__(self, root):
        self.hg = self._import_mercurial()
        self.normal_actions = FileSystemCommands()
        try:
            self.ui = self.hg.ui.ui(
                verbose=False,
                debug=False,
                quiet=True,
                interactive=False,
                traceback=False,
                report_untrusted=False,
            )
        except Exception:
            self.ui = self.hg.ui.ui()
            self.ui.setconfig("ui", "interactive", "no")
            self.ui.setconfig("ui", "debug", "no")
            self.ui.setconfig("ui", "traceback", "no")
            self.ui.setconfig("ui", "verbose", "no")
            self.ui.setconfig("ui", "report_untrusted", "no")
            self.ui.setconfig("ui", "quiet", "yes")

        self.repo = self.hg.hg.repository(self.ui, root)

    def _import_mercurial(self):
        import mercurial.commands  # type:ignore
        import mercurial.hg  # type:ignore
        import mercurial.ui  # type:ignore

        return mercurial

    def create_file(self, path):
        self.normal_actions.create_file(path)
        self.hg.commands.add(self.ui, self.repo, path)

    def create_folder(self, path):
        self.normal_actions.create_folder(path)

    def move(self, path, new_location):
        self.hg.commands.rename(self.ui, self.repo, path, new_location, after=False)

    def remove(self, path):
        self.hg.commands.remove(self.ui, self.repo, path)

    def write(self, path, data):
        self.normal_actions.write(path, data)

    def read(self, path):
        return self.normal_actions.read(path)


class GITCommands(FileSystemCommands):
    def __init__(self, root):
        self.root = root
        self._do(["version"])
        self.normal_actions = FileSystemCommands()

    def create_file(self, path):
        self.normal_actions.create_file(path)
        self._do(["add", self._in_dir(path)])

    def create_folder(self, path):
        self.normal_actions.create_folder(path)

    def move(self, path, new_location):
        self._do(["mv", self._in_dir(path), self._in_dir(new_location)])

    def remove(self, path):
        self._do(["rm", self._in_dir(path)])

    def write(self, path, data):
        # XXX: should we use ``git add``?
        self.normal_actions.write(path, data)

    def read(self, path):
        return self.normal_actions.read(path)

    def _do(self, args):
        _execute(["git"] + args, cwd=self.root)

    def _in_dir(self, path):
        if path.startswith(self.root):
            return path[len(self.root) + 1 :]
        return self.root


class DarcsCommands(FileSystemCommands):
    def __init__(self, root):
        self.root = root
        self.normal_actions = FileSystemCommands()

    def create_file(self, path):
        self.normal_actions.create_file(path)
        self._do(["add", path])

    def create_folder(self, path):
        self.normal_actions.create_folder(path)
        self._do(["add", path])

    def move(self, path, new_location):
        self._do(["mv", path, new_location])

    def remove(self, path):
        self.normal_actions.remove(path)

    def read(self, path):
        return self.normal_actions.read(path)

    def write(self, path, data):
        self.normal_actions.write(path, data)

    def _do(self, args):
        _execute(["darcs"] + args, cwd=self.root)


class InMemoryFileSystemCommands(FileSystemCommands):
    """Filesystem commands backed by an in-memory store.

    All paths are absolute. Populated either programmatically or via
    ``snapshot_project()`` in ``rope.base.inmemory``, then all subsequent
    reads/writes go to memory.
    """

    def __init__(self):
        self._files: dict[str, bytes] = {}
        self._dirs: set[str] = set()
        self._mtimes: dict[str, float] = {}
        self._clock: float = 0.0
        # parent directory → set of basenames directly under it.
        # Maintained eagerly on every mutation so listdir() is O(direct
        # children) instead of O(total entries in project).
        self._children: dict[str, set[str]] = {}

    # -- internal helpers --------------------------------------------------

    def _normalize(self, path: str) -> str:
        stripped = path.rstrip("/\\")
        return stripped if stripped else path

    def _tick(self) -> float:
        self._clock += 1.0
        return self._clock

    def _parent(self, path: str):
        parent = os.path.dirname(path)
        return parent if parent != path else None

    def _index_add(self, path: str) -> None:
        parent = self._parent(path)
        if parent is not None:
            self._children.setdefault(parent, set()).add(os.path.basename(path))

    def _index_remove(self, path: str) -> None:
        parent = self._parent(path)
        if parent is not None:
            bucket = self._children.get(parent)
            if bucket is not None:
                bucket.discard(os.path.basename(path))
                if not bucket:
                    del self._children[parent]

    def _rebuild_index(self) -> None:
        """Rebuild the children index from _files and _dirs.

        Used by snapshot_project() which populates _files/_dirs in bulk
        without going through the mutation helpers.
        """
        self._children = {}
        for p in self._files:
            self._index_add(p)
        for p in self._dirs:
            self._index_add(p)

    def _touch_parents(self, path: str) -> None:
        parent = self._parent(path)
        while parent is not None and parent in self._dirs:
            self._mtimes[parent] = self._tick()
            parent = self._parent(parent)

    def _check_parent_exists(self, path: str) -> None:
        parent = self._parent(path)
        if parent is not None and parent not in self._dirs:
            raise FileNotFoundError(f"No such file or directory: '{path}'")

    # -- write operations --------------------------------------------------

    def create_file(self, path):
        path = self._normalize(path)
        if path in self._dirs:
            raise IsADirectoryError(f"Is a directory: '{path}'")
        self._check_parent_exists(path)
        is_new = path not in self._files
        self._files[path] = b""
        self._mtimes[path] = self._tick()
        if is_new:
            self._index_add(path)
        self._touch_parents(path)

    def create_folder(self, path):
        path = self._normalize(path)
        if path in self._files or path in self._dirs:
            raise FileExistsError(f"File exists: '{path}'")
        self._check_parent_exists(path)
        self._dirs.add(path)
        self._mtimes[path] = self._tick()
        self._index_add(path)
        self._touch_parents(path)

    def write(self, path, data):
        path = self._normalize(path)
        if path in self._dirs:
            raise IsADirectoryError(f"Is a directory: '{path}'")
        self._check_parent_exists(path)
        is_new = path not in self._files
        self._files[path] = data
        self._mtimes[path] = self._tick()
        if is_new:
            self._index_add(path)
        self._touch_parents(path)

    def move(self, path, new_location):
        path = self._normalize(path)
        new_location = self._normalize(new_location)

        if path == new_location:
            return

        is_file = path in self._files
        is_dir = path in self._dirs

        if not is_file and not is_dir:
            raise FileNotFoundError(f"No such file or directory: '{path}'")

        if is_file:
            new_parent = self._parent(new_location)
            if new_parent is not None and new_parent not in self._dirs:
                raise FileNotFoundError(f"No such file or directory: '{new_location}'")
            self._files[new_location] = self._files.pop(path)
            self._mtimes[new_location] = self._tick()
            self._mtimes.pop(path, None)
            self._index_remove(path)
            self._index_add(new_location)
            self._touch_parents(path)
            self._touch_parents(new_location)
        else:
            # Directory move — match shutil.move semantics
            if new_location in self._files:
                raise FileExistsError(f"File exists: '{new_location}'")

            if new_location in self._dirs:
                # shutil.move moves src INTO existing dest dir
                basename = os.path.basename(path)
                final = os.path.join(new_location, basename)
                if final in self._dirs or final in self._files:
                    raise shutil.Error(f"Destination path '{final}' already exists")
                new_location = final

            new_parent = self._parent(new_location)
            if new_parent is not None and new_parent not in self._dirs:
                raise FileNotFoundError(f"No such file or directory: '{new_location}'")

            old_prefix = path + "/"
            new_prefix = new_location + "/"

            # Move directory entry
            self._dirs.discard(path)
            self._dirs.add(new_location)
            self._mtimes[new_location] = self._mtimes.pop(path, self._tick())
            self._index_remove(path)
            self._index_add(new_location)

            # Move all children (files and subdirs). Drop the old subtree's
            # children index entries; they'll be rebuilt under new_prefix.
            for old_p in list(self._files):
                if old_p.startswith(old_prefix):
                    new_p = new_prefix + old_p[len(old_prefix) :]
                    self._files[new_p] = self._files.pop(old_p)
                    self._mtimes[new_p] = self._mtimes.pop(old_p, self._tick())
            for old_p in list(self._dirs):
                if old_p.startswith(old_prefix):
                    new_p = new_prefix + old_p[len(old_prefix) :]
                    self._dirs.discard(old_p)
                    self._dirs.add(new_p)
                    self._mtimes[new_p] = self._mtimes.pop(old_p, self._tick())
            # Drop stale children entries under old_prefix, then rebuild
            # entries for the moved subtree. The subtree is bounded so this
            # is cheaper than a full reindex.
            for indexed in list(self._children):
                if indexed == path or indexed.startswith(old_prefix):
                    del self._children[indexed]
            for p in self._files:
                if p.startswith(new_prefix):
                    self._index_add(p)
            for p in self._dirs:
                if p == new_location or p.startswith(new_prefix):
                    # new_location itself already added above
                    if p != new_location:
                        self._index_add(p)

            self._touch_parents(path)
            self._touch_parents(new_location)

    def remove(self, path):
        path = self._normalize(path)
        if path in self._files:
            del self._files[path]
            self._mtimes.pop(path, None)
            self._index_remove(path)
            self._touch_parents(path)
        elif path in self._dirs:
            prefix = path + "/"
            for p in [k for k in self._files if k.startswith(prefix)]:
                del self._files[p]
                self._mtimes.pop(p, None)
            for p in [k for k in self._dirs if k.startswith(prefix)]:
                self._dirs.discard(p)
                self._mtimes.pop(p, None)
            self._dirs.discard(path)
            self._mtimes.pop(path, None)
            # Drop the whole subtree's children index, then detach `path`
            # from its parent.
            for indexed in list(self._children):
                if indexed == path or indexed.startswith(prefix):
                    del self._children[indexed]
            self._index_remove(path)
            self._touch_parents(path)
        else:
            raise FileNotFoundError(f"No such file or directory: '{path}'")

    # -- read operations ---------------------------------------------------

    def read(self, path):
        path = self._normalize(path)
        if path in self._dirs:
            raise IsADirectoryError(f"Is a directory: '{path}'")
        if path not in self._files:
            raise FileNotFoundError(f"No such file or directory: '{path}'")
        return self._files[path]

    def exists(self, path):
        path = self._normalize(path)
        return path in self._files or path in self._dirs

    def isfile(self, path):
        path = self._normalize(path)
        return path in self._files

    def isdir(self, path):
        path = self._normalize(path)
        return path in self._dirs

    def listdir(self, path):
        path = self._normalize(path)
        if path in self._files:
            raise NotADirectoryError(f"Not a directory: '{path}'")
        if path not in self._dirs:
            raise FileNotFoundError(f"No such file or directory: '{path}'")
        return sorted(self._children.get(path, ()))

    def getmtime(self, path):
        path = self._normalize(path)
        if path not in self._files and path not in self._dirs:
            raise FileNotFoundError(f"No such file or directory: '{path}'")
        return self._mtimes.get(path, 0.0)

    def getsize(self, path):
        path = self._normalize(path)
        if path in self._files:
            return len(self._files[path])
        if path in self._dirs:
            return 0
        raise FileNotFoundError(f"No such file or directory: '{path}'")

    def islink(self, path):
        return False


def _execute(args, cwd=None):
    process = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE)
    process.wait()
    return process.returncode


def unicode_to_file_data(contents: str, encoding=None, newlines=None) -> FileContent:
    assert isinstance(contents, str)
    if newlines and newlines != "\n":
        contents = contents.replace("\n", newlines)
    if encoding is None:
        encoding = read_str_coding(contents)
    if encoding is not None:
        return FileContent(contents.encode(encoding))
    try:
        return FileContent(contents.encode())
    except UnicodeEncodeError:
        return FileContent(contents.encode("utf-8"))


def file_data_to_unicode(data, encoding=None):
    result = _decode_data(data, encoding)
    newline = "\n"
    if "\r\n" in result:
        result = result.replace("\r\n", "\n")
        newline = "\r\n"
    if "\r" in result:
        result = result.replace("\r", "\n")
        newline = "\r"
    return result, newline


def _decode_data(data, encoding):
    if isinstance(data, str):
        return data
    if encoding is None:
        encoding = read_str_coding(data)
    if encoding is None:
        # there is no encoding tip, we need to guess.
        # PEP263 says that "encoding not explicitly defined" means it is ascii,
        # but we will use utf8 instead since utf8 fully covers ascii and btw is
        # the only non-latin sane encoding.
        encoding = "utf-8"
    try:
        return data.decode(encoding)
    except (UnicodeError, LookupError):
        # fallback to latin1: it should never fail
        return data.decode("latin1")


def read_str_coding(source):
    # as defined by PEP-263 (https://www.python.org/dev/peps/pep-0263/)
    CODING_LINE_PATTERN = b"^[ \t\f]*#.*?coding[:=][ \t]*([-_.a-zA-Z0-9]+)"

    if type(source) == bytes:
        newline = b"\n"
        CODING_LINE_PATTERN = re.compile(CODING_LINE_PATTERN)
    else:
        newline = "\n"
        CODING_LINE_PATTERN = re.compile(CODING_LINE_PATTERN.decode("ascii"))
    for line in source.split(newline, 2)[:2]:
        if re.match(CODING_LINE_PATTERN, line):
            return _find_coding(line)
    else:
        return


def _find_coding(text):
    if isinstance(text, str):
        text = text.encode("utf-8")
    coding = b"coding"
    to_chr = chr
    try:
        start = text.index(coding) + len(coding)
        if text[start] not in b"=:":
            return
        start += 1
        while start < len(text) and to_chr(text[start]).isspace():
            start += 1
        end = start
        while end < len(text):
            c = text[end]
            if not to_chr(c).isalnum() and c not in b"-_":
                break
            end += 1
        result = text[start:end]
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        return result
    except ValueError:
        pass
