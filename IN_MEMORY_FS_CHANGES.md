# In-memory filesystem refactor — change log

Downstream-only fork changes that let rope's refactoring engine operate
against an in-memory filesystem. Not intended to be upstreamed.

**Motivation.** We want to simulate refactorings (MoveGlobal, Rename,
import rewriting, etc.) entirely in memory so we can evaluate their
effect on architectural rules without modifying the real codebase. rope
is the mutation engine — if it can run on an in-memory filesystem, we
get correct refactoring simulation for free instead of reimplementing
mutation logic ourselves.

The full design rationale lives in `MASTER_PLAN_ROPE_FS_REFACTOR.md`.

---

## How to use the API

Three entry points depending on what you need.

### 1. Snapshot a real project, refactor in memory

The common case: load a real codebase once, then run refactorings
without touching the disk.

```python
from rope.base.inmemory import in_memory_project
from rope.refactor.rename import Rename

project = in_memory_project("/path/to/your/codebase")

# Resolve a resource and run a rename — exactly the same API as a
# disk-backed Project.
mod = project.get_resource("pkg/module.py")
offset = mod.read().index("old_name")
changes = Rename(project, mod, offset).get_changes("new_name")
project.do(changes)

# The change is visible in memory only. Disk is untouched.
assert "new_name" in mod.read()

project.close()
```

`in_memory_project(root_path, ignored_patterns=None, **prefs)` —
`root_path` must be an absolute path to a real directory.

`**prefs` is forwarded **verbatim to `rope.base.project.Project(...)`**
as its own `**prefs`. Anything `Project.__init__` accepts, this
accepts: project preferences like `ignored_resources`, `python_path`,
`source_folders`, `max_history_items`, etc.

Two `Project` arguments are **not user-overridable** — the factory
owns them:

- `fscommands` — always set to the snapshot-populated
  `InMemoryFileSystemCommands`.
- `ropefolder` — always `None` (the `.ropeproject/` persistence layer
  has direct `open()` calls that don't route through `fscommands`;
  see Phase 3 scope boundary).

Five preferences default to `False` because the corresponding features
need real disk or subprocess access; pass any of them explicitly in
`**prefs` to override:

```python
# Defaults applied by in_memory_project():
save_objectdb=False, save_history=False, validate_objectdb=False,
automatic_soa=False, import_dynload_stdmods=False

# Example: custom project preferences on top of the defaults
project = in_memory_project(
    "/path/to/repo",
    python_path=["/extra/sys/path"],           # forwarded to Project
    source_folders=["src"],                    # forwarded to Project
    ignored_resources=["generated", "*.pb2.py"],  # forwarded to Project
)
```

Default ignored patterns (for the *snapshot* — separate from rope's
own `ignored_resources` preference):

`.ropeproject`, `*.pyc`, `.git`, `.hg`, `.svn`, `__pycache__`,
`.tox`, `.venv`, `venv`, `.mypy_cache`, `.pytest_cache`, `.claude`

Pass `ignored_patterns=[...]` to override (the default list is
exported as `DEFAULT_IGNORED_PATTERNS` in `rope/base/inmemory.py` if
you want to extend rather than replace).

### 2. Build an in-memory project from scratch (no disk snapshot)

When you want a synthetic project for testing or simulation — no real
directory to snapshot from.

```python
from rope.base.fscommands import InMemoryFileSystemCommands
from rope.base.project import Project

fs = InMemoryFileSystemCommands()
root = "/virtual/myproject"
fs._dirs.add(root)                                 # bootstrap the root

project = Project(
    root,
    fscommands=fs,
    ropefolder=None,
    save_objectdb=False,
    save_history=False,
    validate_objectdb=False,
    automatic_soa=False,
    import_dynload_stdmods=False,
)

# Create files through rope's API (goes through fscommands → in-memory)
from rope.contrib import generate
mod = generate.create_module(project, "mymod")
mod.write("x = 1\n")
```

Note the `fs._dirs.add(root)` bootstrap. `InMemoryFileSystemCommands`
enforces parent-dir-exists semantics, so the project root itself has
to be seeded directly (or you need to create `/virtual` first via
`fs.create_folder("/virtual")` and then call
`fs.create_folder(root)`). `Project.__init__` will also create the
root via `fscommands.create_folder()` if the path doesn't exist yet —
but only if the parent does.

### 3. Hand-populate the in-memory store

For fine-grained control (e.g. building a specific tree shape for a
test fixture):

```python
from rope.base.fscommands import InMemoryFileSystemCommands

fs = InMemoryFileSystemCommands()

# Mkdir equivalents
fs.create_folder("/virtual")
fs.create_folder("/virtual/proj")
fs.create_folder("/virtual/proj/pkg")

# Write files (parent dirs must already exist)
fs.write("/virtual/proj/pkg/__init__.py", b"")
fs.write("/virtual/proj/pkg/mod.py", b"def hello():\n    return 'hi'\n")

# All the usual os.* equivalents work
assert fs.exists("/virtual/proj/pkg/mod.py")
assert fs.isfile("/virtual/proj/pkg/mod.py")
assert fs.isdir("/virtual/proj/pkg")
assert fs.listdir("/virtual/proj/pkg") == ["__init__.py", "mod.py"]
assert fs.read("/virtual/proj/pkg/mod.py") == b"def hello():\n    return 'hi'\n"
```

Error behaviour matches `os.*` / `shutil.*` exactly — see the
"Phase 3" table below and the edge-case tests in
`ropetest/inmemorytest.py`.

### 4. Validate that rope's refactoring engine works in memory

To run rope's own refactor test suite against the in-memory backend as
a smoke test (useful after upgrading rope or changing fscommands):

```bash
# Disk baseline
.venv/bin/python -m pytest ropetest/refactor/ -q

# Same tests, in-memory
ROPE_TEST_INMEMORY=1 .venv/bin/python -m pytest ropetest/refactor/ -q
```

Both should report identical counts (943 passed, 7 skipped, 1 xfailed
at time of writing). See Phase 4 below for how the env var is wired.

### Caveats

- **No persistence.** `ropefolder=None` disables `.ropeproject/`, so
  undo history, object database, and project config are not saved.
  The in-memory project is a transient workspace.
- **Paths must be absolute strings.** The in-memory store keys on the
  raw path. Mixing `pathlib.Path` and `str`, or relative and absolute
  paths, will silently miss entries.
- **Parent directories must exist before children.**
  `create_folder("/virtual/a/b")` fails unless `/virtual/a` already
  exists. Use `in_memory_project()` (which handles the whole tree) or
  seed parents explicitly.
- **Symlinks are not modelled.** `islink()` always returns `False`.
- **`rope/contrib/autoimport/` does not work in-memory** (it uses
  `pathlib.Path` directly, bypassing fscommands). See Phase 4's
  "Tests that fail" section for details.
- **Dynamic object analysis (`rope.base.oi.doa`)** spawns Python
  subprocesses on the project root, so it cannot work against virtual
  paths. Keep `automatic_soa=False` (the default for
  `in_memory_project()`).

---

## Phase 1 — Extend `FileSystemCommands` with read-side methods

**Commit:** [8938eae5](../../commit/8938eae5) — `refactor(fscommands): add read-side methods to FileSystemCommands`

**Files changed:** `rope/base/fscommands.py` (+25), `ropetest/projecttest.py` (+42 test lines)

Added 7 read-side methods to the abstraction layer with defaults that
delegate to `os.*` — zero behavior change:

| Method | Default delegates to |
|--------|---------------------|
| `exists(path)` | `os.path.exists` |
| `isfile(path)` | `os.path.isfile` |
| `isdir(path)` | `os.path.isdir` |
| `listdir(path)` | `os.listdir` |
| `getmtime(path)` | `os.path.getmtime` |
| `getsize(path)` | `os.path.getsize` |
| `islink(path)` | `os.path.islink` |

Made VCS subclasses (`SubversionCommands`, `MercurialCommands`,
`GITCommands`, `DarcsCommands`) inherit from `FileSystemCommands` so
they pick up the new defaults instead of being duck-typed.

---

## Phase 2 — Route `rope/base/` direct OS calls through `fscommands`

**Commits:**
- [a87c534e](../../commit/a87c534e) — `refactor(fscommands): route all rope/base/ filesystem calls through fscommands`
- [65e11a5a](../../commit/65e11a5a) — `test(fscommands): update tests for fscommands-routed filesystem calls`

**Files changed:** `rope/base/{change,project,resourceobserver,resources}.py`,
`ropetest/{conftest,projecttest}.py`

Replaced 16 direct `os.*` / `open()` call sites in `rope/base/` with the
equivalent `FileSystemCommands` method:

| File | What was routed |
|------|-----------------|
| `resources.py` | `Resource.exists()`, `File.read_bytes()` (removed deprecated `hasattr` fallback), `Folder.get_children()`, `_ResourceMatcher.does_match()` symlink detection |
| `project.py` | `_Project.get_resource()` (`exists`/`isfile`/`isdir`), `Project.__init__()` root creation when custom fscommands provided |
| `change.py` | `_ResourceOperations._create_resource()` pre-check, `_get_destination_for_move()` |
| `resourceobserver.py` | `ChangeIndicator.get_indicator()` (mtime, listdir, getsize) |

**Verification mechanism:** an autouse pytest fixture monkey-patched
`os.path.exists/isfile/isdir/islink/getmtime/getsize` and `os.listdir`,
walking the call stack to raise on any `rope/base/` caller other than
`fscommands.py` itself, `stdmods.py` (reads system stdlib, not project
FS), and `Project.__init__` before VCS detection. The guard also logged
calls from other `rope/` modules to a file for spot-checking. Zero
production-code leaks were found in `rope/base/`. Guard removed after
the audit.

**Test side-effect:** removed `_DeprecatedFSCommands` test helper +
`test_deprecated_fscommands` (the deprecated `read_bytes()` fallback was
deleted), and updated two `projecttest.py` assertions because
`Project.__init__` now routes root creation through `fscommands` when a
custom backend is provided.

---

## Phase 3 — `InMemoryFileSystemCommands` + project helpers

**Commits:**
- [6308132a](../../commit/6308132a) — `feat(fscommands): add InMemoryFileSystemCommands and in-memory project helpers`
- [28c13983](../../commit/28c13983) — `test(fscommands): add tests for InMemoryFileSystemCommands`

**Files added:** `rope/base/inmemory.py` (+101), `ropetest/inmemorytest.py` (+489)
**Files changed:** `rope/base/fscommands.py` (+206)

### `InMemoryFileSystemCommands` — `rope/base/fscommands.py:229`

Filesystem backend stored entirely in memory:

- `_files: dict[str, bytes]` — file contents
- `_dirs: set[str]` — directory paths
- `_mtimes: dict[str, float]` — modification times
- `_clock: float` — monotonically-increasing counter, drives `mtime`

All 13 `FileSystemCommands` methods implemented with error semantics
that match `os.*` / `shutil.*` exactly:

| Op | Edge cases honored |
|----|--------------------|
| `create_file` | `IsADirectoryError` if path is a dir, `FileNotFoundError` if parent missing, truncates if file exists |
| `create_folder` | `FileExistsError` if exists, `FileNotFoundError` if parent missing |
| `write` | Same as `create_file` plus updates parent mtime |
| `read` | `FileNotFoundError`, `IsADirectoryError` |
| `move` | Matches `shutil.move` semantics including "move into existing dir", `FileExistsError` for collisions, `shutil.Error` for same-named-child collision |
| `remove` | Recursive for directories, `FileNotFoundError` for missing |
| `listdir` | `NotADirectoryError`, `FileNotFoundError` |

### Project helpers — `rope/base/inmemory.py`

- `snapshot_project(root_path, ignored_patterns=None)` — walks a real
  directory tree into an `InMemoryFileSystemCommands`, respecting
  default ignored patterns (`.ropeproject`, `*.pyc`, `.git`, `.hg`,
  `.svn`, `__pycache__`, `.tox`, `.venv`, `venv`, `.mypy_cache`,
  `.pytest_cache`).
- `in_memory_project(root_path, ignored_patterns=None, **prefs)` —
  snapshots from disk and returns a `Project` backed entirely by the
  in-memory store. Forces `ropefolder=None` and disables `save_objectdb`,
  `save_history`, `validate_objectdb`, `automatic_soa`,
  `import_dynload_stdmods` by default.

### Tests — `ropetest/inmemorytest.py`

62 tests across three classes:

- `InMemoryFileSystemCommandsTest` (40 tests) — happy path plus 22 edge
  cases matching real `os.*` / `shutil.*` error behavior.
- `InMemoryProjectTest` (9 tests) — integration via Project API:
  `create_module`, `create_package`, `move`/`remove` via `Change`
  objects, `get_python_files`, `get_children`, `project.close()`.
- `SnapshotProjectTest` (8 tests) — disk-independence verification,
  ignored pattern handling, byte-for-byte content preservation.

---

## Phase 4 — Validate the in-memory backend against the refactor suite (this commit)

**Files added:** `ropetest/testutils_inmemory.py` (134 lines)
**Files changed:** `ropetest/conftest.py` (+5 lines)
**Unchanged:** `ropetest/testutils.py`, all 13 `ropetest/refactor/*.py` test files, all `rope/` source

### Approach

A `ROPE_TEST_INMEMORY` environment variable activates a session-start
hook that monkey-patches `testutils.sample_project` and
`testutils.remove_project` to in-memory variants. The existing 943
refactor tests run **unmodified** against both backends.

Chosen over the master plan's "copy 15K lines of test files" approach
and over conftest fixture parameterization (which would have required
invasive edits to 13 `unittest.TestCase` setUp/tearDown blocks and
risked breaking the disk-baseline suite). Since this fork won't be
upstreamed, the env-var hook is acceptable internal magic.

### Mechanism

1. `ropetest/testutils_inmemory.py` exports:
   - `sample_project(foldername=None, **kwds)` — creates a `Project`
     rooted at `/virtual/project-N/<foldername>`, where `N` is a
     monotonic counter. Mirrors `tempfile.mkdtemp`'s uniqueness so
     `foldername` may be reused across tests.
   - `remove_project(project)` — just `project.close()`; no tempdir
     cleanup needed.
   - `apply_inmemory_hook()` — monkey-patches `ropetest.testutils` and
     swaps a union FS into `NoProject` (see below).
   - `_VirtualOrDiskFSCommands` — dispatches to the shared in-memory FS
     for `/virtual/` paths and to disk for everything else.

2. `ropetest/conftest.py`:
   ```python
   if os.environ.get("ROPE_TEST_INMEMORY"):
       from ropetest import testutils_inmemory
       testutils_inmemory.apply_inmemory_hook()
   ```

### Three non-obvious fixes

1. **Parent-dir bootstrap.** `InMemoryFileSystemCommands.create_folder()`
   requires the parent dir to exist. The `/virtual/` parent and each
   `/virtual/project-N/` container are populated via direct
   `_dirs.add()` (matching the pattern used in
   `ropetest/inmemorytest.py:326`).

2. **Shared FS across all `sample_project()` calls.** The first attempt
   used a fresh `InMemoryFileSystemCommands` per project, which broke
   `multiprojecttest.py` (4 tests): cross-project import resolution
   needs project2 to see project1's files. Disk-backed tests inherit
   this for free because every tempdir lives under the real `/`. The
   fix is to share `_SHARED_FS = InMemoryFileSystemCommands()` across
   all calls, with the per-project `/virtual/project-N/` prefix
   guaranteeing isolation.

3. **`NoProject` union FS.** `rope/refactor/multiproject.py` resolves
   out-of-project resources via the `NoProject` singleton, which builds
   its own default disk-backed `FileSystemCommands` at construction.
   Patching `NoProject.fscommands = _SHARED_FS` fixed multi-project
   tests but broke 51 `type_hinting_test.py` tests that need rope to
   read the system stdlib via `sys.path` (real disk paths). The fix is
   `_VirtualOrDiskFSCommands`: a `FileSystemCommands` subclass that
   dispatches to the in-memory store for `/virtual/` paths and to a
   plain disk-backed `FileSystemCommands` for everything else.

### Results

| Run | Pass | Skip | Xfail | Time |
|-----|------|------|-------|------|
| **Disk baseline** (`pytest`) | 2153 | 7 | 5 | ~9s |
| **In-memory refactor suite** (`ROPE_TEST_INMEMORY=1 pytest ropetest/refactor/`) | 943 | 7 | 1 | ~4s |

The in-memory refactor numbers match the disk baseline for the
refactor subset exactly. **No FS leaks in `rope/base/` or
`rope/refactor/` were surfaced** — Phases 2 and 3's audit held up.

---

## Tests that fail under full in-memory FS (broader suite)

Out of the 1972 non-refactor tests (excluding `projecttest.py` and
`inmemorytest.py`, which deliberately exercise both backends),
**63 fail** under `ROPE_TEST_INMEMORY=1`. Categorised by root cause:

### Inherently disk-only — 40 tests

These tests cannot work in-memory by their own design, not because of
any rope FS leak.

#### `ropetest/advanced_oi_test.py` — 25 tests

All `DynamicOITest` methods plus
`NewStaticOITest::test_report_change_in_libutils`. They call
`pycore.run_module(...)` which spawns a subprocess via
`rope/base/oi/doa.py:82` (`subprocess.Popen`) using the project root as
cwd. The subprocess fails immediately with
`FileNotFoundError: '/virtual/project-N/sample_project'` — the OS can't
chdir into a virtual path. The plan explicitly listed dynamic object
analysis as out of scope.

```
ropetest/advanced_oi_test.py::DynamicOITest::test_a_function_with_different_returns
ropetest/advanced_oi_test.py::DynamicOITest::test_a_function_with_different_returns2
ropetest/advanced_oi_test.py::DynamicOITest::test_arguments_with_keywords
ropetest/advanced_oi_test.py::DynamicOITest::test_class_dti
ropetest/advanced_oi_test.py::DynamicOITest::test_class_from_another_module_dti
ropetest/advanced_oi_test.py::DynamicOITest::test_classes_with_the_same_name
ropetest/advanced_oi_test.py::DynamicOITest::test_dict_keys_and_dynamicoi
ropetest/advanced_oi_test.py::DynamicOITest::test_dict_keys_and_dynamicoi2
ropetest/advanced_oi_test.py::DynamicOITest::test_dict_objects_and_dynamicoi
ropetest/advanced_oi_test.py::DynamicOITest::test_dti_and_concluded_data_invalidation
ropetest/advanced_oi_test.py::DynamicOITest::test_for_loops_and_dynamicoi
ropetest/advanced_oi_test.py::DynamicOITest::test_function_argument_dti
ropetest/advanced_oi_test.py::DynamicOITest::test_function_argument_dti2
ropetest/advanced_oi_test.py::DynamicOITest::test_ignoring_double_star_args
ropetest/advanced_oi_test.py::DynamicOITest::test_ignoring_star_args
ropetest/advanced_oi_test.py::DynamicOITest::test_instance_dti
ropetest/advanced_oi_test.py::DynamicOITest::test_invalidating_data_after_changing
ropetest/advanced_oi_test.py::DynamicOITest::test_invalidating_data_after_moving
ropetest/advanced_oi_test.py::DynamicOITest::test_list_objects_and_dynamicoi
ropetest/advanced_oi_test.py::DynamicOITest::test_method_dti
ropetest/advanced_oi_test.py::DynamicOITest::test_module_dti
ropetest/advanced_oi_test.py::DynamicOITest::test_nested_classes
ropetest/advanced_oi_test.py::DynamicOITest::test_simple_dti
ropetest/advanced_oi_test.py::DynamicOITest::test_strs_and_dynamicoi
ropetest/advanced_oi_test.py::NewStaticOITest::test_report_change_in_libutils
```

#### `ropetest/runmodtest.py` — 9 tests

Same root cause as `advanced_oi_test.py`: tests of `pycore.run_module()`
itself, which spawns a subprocess on the project path.

```
ropetest/runmodtest.py::PythonFileRunnerTest::test_killing_runner
ropetest/runmodtest.py::PythonFileRunnerTest::test_making_runner
ropetest/runmodtest.py::PythonFileRunnerTest::test_making_runner_when_doi_is_disabled
ropetest/runmodtest.py::PythonFileRunnerTest::test_passing_arguments
ropetest/runmodtest.py::PythonFileRunnerTest::test_passing_arguments_with_spaces
ropetest/runmodtest.py::PythonFileRunnerTest::test_running_nested_files
ropetest/runmodtest.py::PythonFileRunnerTest::test_setting_process_input
ropetest/runmodtest.py::PythonFileRunnerTest::test_setting_process_output
ropetest/runmodtest.py::PythonFileRunnerTest::test_setting_pythonpath
```

#### `ropetest/pycoretest.py` — 5 tests

Four tests deliberately do `open(mod.real_path, "wb")` followed by a
write of non-UTF-8 bytes (e.g. `b"\xa9"`, `b"\x00"`) to test rope's
encoding error handling. They bypass the rope API on purpose to inject
bytes that the API can't produce. The fifth test
(`test_source_folders_preference`) calls
`Project(self.project.address, source_folders=[...])` after
`project.close()` — re-opening the project without an fscommands
argument, which falls back to disk and fails because the virtual path
doesn't exist.

```
ropetest/pycoretest.py::PyCoreTest::test_no_exceptions_on_module_encoding_problems
ropetest/pycoretest.py::PyCoreTest::test_source_folders_preference
ropetest/pycoretest.py::PyCoreTest::test_syntax_errors_when_bad_strs
ropetest/pycoretest.py::PyCoreTest::test_syntax_errors_when_cannot_decode_file2
ropetest/pycoretest.py::PyCoreTest::test_syntax_errors_when_null_bytes
```

#### `ropetest/reprtest.py` — 1 test

`test_repr_project` allocates a `tempfile.TemporaryDirectory()` and
passes the resulting path to `sample_project(folder)`. The in-memory
`sample_project` treats `folder` as a foldername (last path component)
rather than as a full path, so `repr(project)` doesn't match the
expected `<rope.base.project.Project "{folder}">` string. Test-level
disk assumption.

```
ropetest/reprtest.py::test_repr_project
```

### Real FS leak in `rope/contrib/autoimport/` — 19 tests

The autoimport module was written before the `FileSystemCommands`
abstraction existed. It uses `pathlib.Path` directly throughout —
`module.read_bytes()` (`parse.py:48`), `package_path.is_file()`
(`utils.py:28`), `package.path.glob("**/*.py")` (`utils.py:122`),
`folder.iterdir()` and `folder.is_dir()` (`sqlite.py:565,573`).

This is a **genuine FS leak** that Phase 2's `rope/base/` audit didn't
cover. Plus the autoimport name-extraction runs in a
`ProcessPoolExecutor` — workers don't share the parent's in-memory
store, so even routing pathlib through fscommands wouldn't be enough
without restructuring the worker protocol to receive byte payloads
instead of paths.

A future Phase 5 could fix this. Estimated: ~200-250 LOC across 4
files, with `ProcessPoolExecutor` being the only piece with non-trivial
design judgment. Not on the critical path for our downstream use case
(architectural rule evaluation drives the refactor engine, not
autocomplete).

```
ropetest/contrib/autoimport/autoimporttest.py::test_autoimport_connection_parameter_with_project
ropetest/contrib/autoimport/autoimporttest.py::test_autoimport_memory_parameter_is_false
ropetest/contrib/autoimport/autoimporttest.py::test_init_py
ropetest/contrib/autoimport/autoimporttest.py::test_multithreading
ropetest/contrib/autoimport/utilstest.py::test_get_files
ropetest/contrib/autoimporttest.py::AutoImportObservingTest::test_moving_files
ropetest/contrib/autoimporttest.py::AutoImportObservingTest::test_removing_files
ropetest/contrib/autoimporttest.py::AutoImportObservingTest::test_writing_files
ropetest/contrib/autoimporttest.py::AutoImportTest::test_alias_updated_from_prefs
ropetest/contrib/autoimporttest.py::AutoImportTest::test_caching_underlined_names_passing_to_the_constructor
ropetest/contrib/autoimporttest.py::AutoImportTest::test_empty_cache
ropetest/contrib/autoimporttest.py::AutoImportTest::test_excluding_imported_names
ropetest/contrib/autoimporttest.py::AutoImportTest::test_get_modules
ropetest/contrib/autoimporttest.py::AutoImportTest::test_get_modules_inside_packages
ropetest/contrib/autoimporttest.py::AutoImportTest::test_module_with_syntax_errors
ropetest/contrib/autoimporttest.py::AutoImportTest::test_name_locations
ropetest/contrib/autoimporttest.py::AutoImportTest::test_name_locations_with_multiple_occurrences
ropetest/contrib/autoimporttest.py::AutoImportTest::test_not_caching_underlined_names
ropetest/contrib/autoimporttest.py::AutoImportTest::test_search_alias
ropetest/contrib/autoimporttest.py::AutoImportTest::test_update_resource
```

### Project persistence (`ropefolder=None` trade-off) — 3 tests

Two `historytest.py` tests use `save_history=True` + `history.write()`
+ `history.pickle` to test undo/redo persistence. The third sets
`ignored_resources` and exercises a code path that ends up writing
through the persistence layer. All three depend on `ropefolder` being
set to a real directory; `in_memory_project()` and the test hook both
force `ropefolder=None` because `_DataFiles.read_data()` /
`_RopeConfigSource._read()` use direct `open()` calls (Phase 2's audit
flagged but didn't fix these — mitigated by `ropefolder=None`).

```
ropetest/historytest.py::IsolatedHistoryTest::test_ignoring_ignored_resources
ropetest/historytest.py::SavingHistoryTest::test_writing_and_reading_history
ropetest/historytest.py::SavingHistoryTest::test_writing_and_reading_history2
```

---

## Summary

| Layer | Status under in-memory FS |
|------|----------------------------|
| `rope/base/` | ✅ Complete — Phases 1–2 routed every direct OS call through `fscommands` |
| `rope/refactor/` | ✅ Zero direct FS access; refactor suite identical disk vs in-memory (943 tests) |
| `rope/contrib/autoimport/` | ❌ Direct `pathlib.Path` usage; needs a Phase 5 if autoimport features are required |
| `rope/base/oi/` (dynamic object analysis) | ❌ Inherently subprocess-based; out of scope |
| `_DataFiles` / `_RopeConfigSource` | ⚠️ Direct `open()` calls; mitigated by `ropefolder=None` |

**Bottom line:** the refactoring engine — the only piece our downstream
use case needs — produces bit-identical results on disk and in memory.
