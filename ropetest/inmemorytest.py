"""Tests for InMemoryFileSystemCommands, snapshot_project, and in_memory_project."""

import os
import shutil
import tempfile
import unittest

from rope.base.fscommands import InMemoryFileSystemCommands
from rope.base.inmemory import in_memory_project, snapshot_project
from rope.contrib import generate


class InMemoryFileSystemCommandsTest(unittest.TestCase):
    """Unit tests for InMemoryFileSystemCommands in isolation (no Project)."""

    def setUp(self):
        self.fs = InMemoryFileSystemCommands()
        # Bootstrap a root directory
        self.root = "/memroot"
        self.fs._dirs.add(self.root)

    # -- happy path: create / read / write ---------------------------------

    def test_create_file_and_read(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.assertEqual(self.fs.read(f"{self.root}/a.py"), b"")

    def test_create_folder(self):
        self.fs.create_folder(f"{self.root}/pkg")
        self.assertTrue(self.fs.isdir(f"{self.root}/pkg"))

    def test_write_and_read(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.fs.write(f"{self.root}/a.py", b"hello")
        self.assertEqual(self.fs.read(f"{self.root}/a.py"), b"hello")

    # -- exists / isfile / isdir -------------------------------------------

    def test_exists_file(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.assertTrue(self.fs.exists(f"{self.root}/a.py"))

    def test_exists_folder(self):
        self.fs.create_folder(f"{self.root}/pkg")
        self.assertTrue(self.fs.exists(f"{self.root}/pkg"))

    def test_exists_nonexistent(self):
        self.assertFalse(self.fs.exists(f"{self.root}/ghost"))

    def test_isfile_true(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.assertTrue(self.fs.isfile(f"{self.root}/a.py"))

    def test_isfile_false_for_folder(self):
        self.fs.create_folder(f"{self.root}/pkg")
        self.assertFalse(self.fs.isfile(f"{self.root}/pkg"))

    def test_isdir_true(self):
        self.fs.create_folder(f"{self.root}/pkg")
        self.assertTrue(self.fs.isdir(f"{self.root}/pkg"))

    def test_isdir_false_for_file(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.assertFalse(self.fs.isdir(f"{self.root}/a.py"))

    # -- write updates mtime -----------------------------------------------

    def test_write_updates_mtime(self):
        self.fs.create_file(f"{self.root}/a.py")
        mtime1 = self.fs.getmtime(f"{self.root}/a.py")
        self.fs.write(f"{self.root}/a.py", b"v2")
        mtime2 = self.fs.getmtime(f"{self.root}/a.py")
        self.assertGreater(mtime2, mtime1)

    def test_mtime_monotonically_increases(self):
        mtimes = []
        for i in range(5):
            name = f"{self.root}/f{i}.py"
            self.fs.create_file(name)
            mtimes.append(self.fs.getmtime(name))
        self.assertEqual(mtimes, sorted(mtimes))
        self.assertEqual(len(set(mtimes)), 5)

    # -- remove ------------------------------------------------------------

    def test_remove_file(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.fs.remove(f"{self.root}/a.py")
        self.assertFalse(self.fs.exists(f"{self.root}/a.py"))

    def test_remove_folder_recursive(self):
        self.fs.create_folder(f"{self.root}/pkg")
        self.fs.create_file(f"{self.root}/pkg/a.py")
        self.fs.create_folder(f"{self.root}/pkg/sub")
        self.fs.create_file(f"{self.root}/pkg/sub/b.py")
        self.fs.remove(f"{self.root}/pkg")
        self.assertFalse(self.fs.exists(f"{self.root}/pkg"))
        self.assertFalse(self.fs.exists(f"{self.root}/pkg/a.py"))
        self.assertFalse(self.fs.exists(f"{self.root}/pkg/sub"))
        self.assertFalse(self.fs.exists(f"{self.root}/pkg/sub/b.py"))

    # -- move --------------------------------------------------------------

    def test_move_file(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.fs.write(f"{self.root}/a.py", b"content")
        self.fs.move(f"{self.root}/a.py", f"{self.root}/b.py")
        self.assertFalse(self.fs.exists(f"{self.root}/a.py"))
        self.assertEqual(self.fs.read(f"{self.root}/b.py"), b"content")

    def test_move_folder(self):
        self.fs.create_folder(f"{self.root}/pkg")
        self.fs.create_file(f"{self.root}/pkg/a.py")
        self.fs.write(f"{self.root}/pkg/a.py", b"content")
        self.fs.create_folder(f"{self.root}/pkg/sub")
        self.fs.create_file(f"{self.root}/pkg/sub/b.py")

        self.fs.move(f"{self.root}/pkg", f"{self.root}/newpkg")

        self.assertFalse(self.fs.exists(f"{self.root}/pkg"))
        self.assertFalse(self.fs.exists(f"{self.root}/pkg/a.py"))
        self.assertTrue(self.fs.isdir(f"{self.root}/newpkg"))
        self.assertEqual(self.fs.read(f"{self.root}/newpkg/a.py"), b"content")
        self.assertTrue(self.fs.isdir(f"{self.root}/newpkg/sub"))
        self.assertTrue(self.fs.isfile(f"{self.root}/newpkg/sub/b.py"))

    # -- listdir -----------------------------------------------------------

    def test_listdir_immediate_children(self):
        self.fs.create_folder(f"{self.root}/pkg")
        self.fs.create_file(f"{self.root}/pkg/a.py")
        self.fs.create_file(f"{self.root}/pkg/b.py")
        self.fs.create_folder(f"{self.root}/pkg/sub")
        self.fs.create_file(f"{self.root}/pkg/sub/deep.py")
        self.assertEqual(
            self.fs.listdir(f"{self.root}/pkg"),
            ["a.py", "b.py", "sub"],
        )

    def test_listdir_empty_directory(self):
        self.fs.create_folder(f"{self.root}/empty")
        self.assertEqual(self.fs.listdir(f"{self.root}/empty"), [])

    # -- getsize -----------------------------------------------------------

    def test_getsize_file(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.fs.write(f"{self.root}/a.py", b"hello")
        self.assertEqual(self.fs.getsize(f"{self.root}/a.py"), 5)

    def test_getsize_empty_file(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.assertEqual(self.fs.getsize(f"{self.root}/a.py"), 0)

    def test_getsize_folder(self):
        self.fs.create_folder(f"{self.root}/pkg")
        self.assertEqual(self.fs.getsize(f"{self.root}/pkg"), 0)

    # -- islink ------------------------------------------------------------

    def test_islink_always_false(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.fs.create_folder(f"{self.root}/pkg")
        self.assertFalse(self.fs.islink(f"{self.root}/a.py"))
        self.assertFalse(self.fs.islink(f"{self.root}/pkg"))
        self.assertFalse(self.fs.islink(f"{self.root}/nonexistent"))

    # -- trailing slash normalization --------------------------------------

    def test_trailing_slash_normalization(self):
        self.fs.create_folder(f"{self.root}/pkg")
        self.assertTrue(self.fs.isdir(f"{self.root}/pkg/"))
        self.assertTrue(self.fs.exists(f"{self.root}/pkg/"))
        self.assertEqual(self.fs.listdir(f"{self.root}/pkg/"), [])

    # ======================================================================
    # Edge cases — matching real os.*/shutil.* error behavior
    # ======================================================================

    # -- create_file edge cases --------------------------------------------

    def test_create_file_nonexistent_parent(self):
        with self.assertRaises(FileNotFoundError):
            self.fs.create_file(f"{self.root}/nodir/a.py")

    def test_create_file_already_exists_as_file(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.fs.write(f"{self.root}/a.py", b"content")
        self.fs.create_file(f"{self.root}/a.py")  # no error
        self.assertEqual(self.fs.read(f"{self.root}/a.py"), b"")  # truncated

    def test_create_file_where_dir_exists(self):
        self.fs.create_folder(f"{self.root}/pkg")
        with self.assertRaises(IsADirectoryError):
            self.fs.create_file(f"{self.root}/pkg")

    # -- create_folder edge cases ------------------------------------------

    def test_create_folder_nonexistent_parent(self):
        with self.assertRaises(FileNotFoundError):
            self.fs.create_folder(f"{self.root}/nodir/sub")

    def test_create_folder_already_exists_as_dir(self):
        self.fs.create_folder(f"{self.root}/pkg")
        with self.assertRaises(FileExistsError):
            self.fs.create_folder(f"{self.root}/pkg")

    def test_create_folder_where_file_exists(self):
        self.fs.create_file(f"{self.root}/a.py")
        with self.assertRaises(FileExistsError):
            self.fs.create_folder(f"{self.root}/a.py")

    # -- write edge cases --------------------------------------------------

    def test_write_nonexistent_parent(self):
        with self.assertRaises(FileNotFoundError):
            self.fs.write(f"{self.root}/nodir/a.py", b"data")

    def test_write_to_directory(self):
        self.fs.create_folder(f"{self.root}/pkg")
        with self.assertRaises(IsADirectoryError):
            self.fs.write(f"{self.root}/pkg", b"data")

    def test_write_creates_file_if_not_exists(self):
        self.fs.write(f"{self.root}/new.py", b"created")
        self.assertTrue(self.fs.isfile(f"{self.root}/new.py"))
        self.assertEqual(self.fs.read(f"{self.root}/new.py"), b"created")

    # -- read edge cases ---------------------------------------------------

    def test_read_nonexistent(self):
        with self.assertRaises(FileNotFoundError):
            self.fs.read(f"{self.root}/ghost.py")

    def test_read_directory(self):
        self.fs.create_folder(f"{self.root}/pkg")
        with self.assertRaises(IsADirectoryError):
            self.fs.read(f"{self.root}/pkg")

    # -- remove edge cases -------------------------------------------------

    def test_remove_nonexistent(self):
        with self.assertRaises(FileNotFoundError):
            self.fs.remove(f"{self.root}/ghost.py")

    # -- move edge cases ---------------------------------------------------

    def test_move_nonexistent_source(self):
        with self.assertRaises(FileNotFoundError):
            self.fs.move(f"{self.root}/ghost", f"{self.root}/dest")

    def test_move_nonexistent_dest_parent(self):
        self.fs.create_file(f"{self.root}/a.py")
        with self.assertRaises(FileNotFoundError):
            self.fs.move(f"{self.root}/a.py", f"{self.root}/nodir/b.py")

    def test_move_file_onto_existing_file(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.fs.write(f"{self.root}/a.py", b"aaa")
        self.fs.create_file(f"{self.root}/b.py")
        self.fs.write(f"{self.root}/b.py", b"bbb")
        self.fs.move(f"{self.root}/a.py", f"{self.root}/b.py")
        self.assertFalse(self.fs.exists(f"{self.root}/a.py"))
        self.assertEqual(self.fs.read(f"{self.root}/b.py"), b"aaa")

    def test_move_dir_onto_existing_file(self):
        self.fs.create_folder(f"{self.root}/pkg")
        self.fs.create_file(f"{self.root}/target")
        with self.assertRaises(FileExistsError):
            self.fs.move(f"{self.root}/pkg", f"{self.root}/target")

    def test_move_dir_onto_existing_empty_dir(self):
        self.fs.create_folder(f"{self.root}/src")
        self.fs.create_file(f"{self.root}/src/a.py")
        self.fs.create_folder(f"{self.root}/dest")

        self.fs.move(f"{self.root}/src", f"{self.root}/dest")

        self.assertFalse(self.fs.exists(f"{self.root}/src"))
        self.assertTrue(self.fs.isdir(f"{self.root}/dest/src"))
        self.assertTrue(self.fs.isfile(f"{self.root}/dest/src/a.py"))

    def test_move_dir_onto_dir_with_same_named_child(self):
        self.fs.create_folder(f"{self.root}/src")
        self.fs.create_folder(f"{self.root}/dest")
        self.fs.create_folder(f"{self.root}/dest/src")
        with self.assertRaises(shutil.Error):
            self.fs.move(f"{self.root}/src", f"{self.root}/dest")

    def test_move_onto_itself(self):
        self.fs.create_file(f"{self.root}/a.py")
        self.fs.write(f"{self.root}/a.py", b"content")
        self.fs.move(f"{self.root}/a.py", f"{self.root}/a.py")
        self.assertEqual(self.fs.read(f"{self.root}/a.py"), b"content")

    # -- listdir edge cases ------------------------------------------------

    def test_listdir_on_file(self):
        self.fs.create_file(f"{self.root}/a.py")
        with self.assertRaises(NotADirectoryError):
            self.fs.listdir(f"{self.root}/a.py")

    def test_listdir_nonexistent(self):
        with self.assertRaises(FileNotFoundError):
            self.fs.listdir(f"{self.root}/ghost")

    # -- getmtime / getsize edge cases -------------------------------------

    def test_getmtime_nonexistent(self):
        with self.assertRaises(FileNotFoundError):
            self.fs.getmtime(f"{self.root}/ghost")

    def test_getsize_nonexistent(self):
        with self.assertRaises(FileNotFoundError):
            self.fs.getsize(f"{self.root}/ghost")


class InMemoryProjectTest(unittest.TestCase):
    """Integration tests via Project API with InMemoryFileSystemCommands."""

    def setUp(self):
        from rope.base.project import Project

        self.fs = InMemoryFileSystemCommands()
        self.root = "/virtual/project"
        self.fs._dirs.add(self.root)
        self.project = Project(
            self.root,
            fscommands=self.fs,
            ropefolder=None,
            save_objectdb=False,
            save_history=False,
            validate_objectdb=False,
            automatic_soa=False,
            import_dynload_stdmods=False,
        )

    def tearDown(self):
        self.project.close()

    def test_root_exists(self):
        self.assertTrue(self.project.root.exists())

    def test_create_module_and_read(self):
        mod = generate.create_module(self.project, "mod1")
        mod.write("x = 1\n")
        self.assertEqual(mod.read(), "x = 1\n")

    def test_create_package(self):
        pkg = generate.create_package(self.project, "pkg")
        self.assertTrue(pkg.exists())
        self.assertTrue(pkg.is_folder())
        init = pkg.get_child("__init__.py")
        self.assertTrue(init.exists())

    def test_get_resource(self):
        generate.create_module(self.project, "mod1")
        resource = self.project.get_resource("mod1.py")
        self.assertTrue(resource.exists())

    def test_get_python_files(self):
        generate.create_module(self.project, "mod1")
        generate.create_module(self.project, "mod2")
        py_files = self.project.get_python_files()
        names = sorted(f.name for f in py_files)
        self.assertEqual(names, ["mod1.py", "mod2.py"])

    def test_folder_get_children(self):
        generate.create_module(self.project, "a")
        generate.create_module(self.project, "b")
        generate.create_package(self.project, "pkg")
        children = sorted(c.name for c in self.project.root.get_children())
        self.assertEqual(children, ["a.py", "b.py", "pkg"])

    def test_move_resource(self):
        from rope.base.change import MoveResource

        mod = generate.create_module(self.project, "old")
        mod.write("x = 1\n")
        change = MoveResource(mod, "new.py")
        self.project.do(change)
        self.assertFalse(self.project.get_file("old.py").exists())
        new = self.project.get_resource("new.py")
        self.assertEqual(new.read(), "x = 1\n")

    def test_remove_resource(self):
        from rope.base.change import RemoveResource

        mod = generate.create_module(self.project, "mod1")
        change = RemoveResource(mod)
        self.project.do(change)
        self.assertFalse(self.fs.exists(os.path.join(self.root, "mod1.py")))

    def test_project_close_no_error(self):
        self.project.close()


class SnapshotProjectTest(unittest.TestCase):
    """Tests for snapshot_project() and in_memory_project()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="rope-snapshot-test-")
        # Create a small project structure on disk
        os.makedirs(os.path.join(self.tmpdir, "pkg"))
        with open(os.path.join(self.tmpdir, "mod.py"), "wb") as f:
            f.write(b"x = 1\n")
        with open(os.path.join(self.tmpdir, "pkg", "__init__.py"), "wb") as f:
            f.write(b"")
        with open(os.path.join(self.tmpdir, "pkg", "inner.py"), "wb") as f:
            f.write(b"y = 2\n")
        # Create files that should be ignored
        os.makedirs(os.path.join(self.tmpdir, "__pycache__"))
        with open(
            os.path.join(self.tmpdir, "__pycache__", "mod.cpython-313.pyc"),
            "wb",
        ) as f:
            f.write(b"\x00\x00")
        os.makedirs(os.path.join(self.tmpdir, ".git"))
        with open(os.path.join(self.tmpdir, ".git", "HEAD"), "wb") as f:
            f.write(b"ref: refs/heads/master\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_snapshot_loads_files(self):
        fs = snapshot_project(self.tmpdir)
        mod_path = os.path.join(os.path.realpath(self.tmpdir), "mod.py")
        self.assertTrue(fs.isfile(mod_path))
        self.assertEqual(fs.read(mod_path), b"x = 1\n")

    def test_snapshot_loads_nested_directories(self):
        fs = snapshot_project(self.tmpdir)
        real_root = os.path.realpath(self.tmpdir)
        self.assertTrue(fs.isdir(os.path.join(real_root, "pkg")))
        inner_path = os.path.join(real_root, "pkg", "inner.py")
        self.assertTrue(fs.isfile(inner_path))
        self.assertEqual(fs.read(inner_path), b"y = 2\n")

    def test_snapshot_ignores_patterns(self):
        fs = snapshot_project(self.tmpdir)
        real_root = os.path.realpath(self.tmpdir)
        self.assertFalse(fs.exists(os.path.join(real_root, "__pycache__")))
        self.assertFalse(fs.exists(os.path.join(real_root, ".git")))

    def test_snapshot_preserves_file_content(self):
        fs = snapshot_project(self.tmpdir)
        real_root = os.path.realpath(self.tmpdir)
        self.assertEqual(
            fs.read(os.path.join(real_root, "pkg", "inner.py")),
            b"y = 2\n",
        )

    def test_snapshot_independent_of_disk(self):
        fs = snapshot_project(self.tmpdir)
        real_root = os.path.realpath(self.tmpdir)
        mod_path = os.path.join(real_root, "mod.py")
        # Modify disk after snapshot
        with open(mod_path, "wb") as f:
            f.write(b"x = 999\n")
        # In-memory still has original
        self.assertEqual(fs.read(mod_path), b"x = 1\n")

    def test_in_memory_project_returns_working_project(self):
        project = in_memory_project(self.tmpdir)
        try:
            mod = project.get_resource("mod.py")
            self.assertEqual(mod.read(), "x = 1\n")
            py_files = project.get_python_files()
            names = sorted(f.path for f in py_files)
            self.assertEqual(
                names,
                ["mod.py", "pkg/__init__.py", "pkg/inner.py"],
            )
        finally:
            project.close()

    def test_in_memory_project_writes_dont_touch_disk(self):
        project = in_memory_project(self.tmpdir)
        try:
            mod = project.get_resource("mod.py")
            mod.write("x = 42\n")
            # In-memory changed
            self.assertEqual(mod.read(), "x = 42\n")
            # Disk unchanged
            real_root = os.path.realpath(self.tmpdir)
            with open(os.path.join(real_root, "mod.py"), "rb") as f:
                self.assertEqual(f.read(), b"x = 1\n")
        finally:
            project.close()
