from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ouro_mcp.constants import ENV_WORKSPACE_ROOT
from ouro_mcp.utils import resolve_local_path


class TestResolveLocalPathNoWorkspace(unittest.TestCase):
    """Without WORKSPACE_ROOT, paths are resolved as-is (desktop user mode)."""

    def setUp(self) -> None:
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        os.environ.pop(ENV_WORKSPACE_ROOT, None)

    def tearDown(self) -> None:
        self._env_patch.stop()

    def test_absolute_path_returned_as_is(self) -> None:
        result = resolve_local_path("/tmp/foo.cif")
        self.assertEqual(result, Path("/tmp/foo.cif"))

    def test_relative_path_resolved_against_cwd(self) -> None:
        result = resolve_local_path("relative.cif")
        self.assertEqual(result, (Path.cwd() / "relative.cif").resolve())

    def test_home_relative_expanded(self) -> None:
        result = resolve_local_path("~/somewhere/foo.cif")
        self.assertEqual(result, (Path.home() / "somewhere/foo.cif").resolve())


class TestResolveLocalPathWithWorkspace(unittest.TestCase):
    """With WORKSPACE_ROOT set, paths are sandboxed to that root."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name).resolve()
        self._env_patch = mock.patch.dict(
            os.environ, {ENV_WORKSPACE_ROOT: str(self.workspace)}, clear=False
        )
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmp.cleanup()

    def test_relative_path_joined_to_workspace(self) -> None:
        result = resolve_local_path("data/out.cif")
        self.assertEqual(result, (self.workspace / "data/out.cif").resolve())

    def test_plain_filename_lands_in_workspace(self) -> None:
        result = resolve_local_path("foo.cif")
        self.assertEqual(result, self.workspace / "foo.cif")

    def test_absolute_path_inside_workspace_allowed(self) -> None:
        inside = self.workspace / "nested" / "file.cif"
        result = resolve_local_path(str(inside))
        self.assertEqual(result, inside.resolve())

    def test_absolute_path_outside_workspace_rejected(self) -> None:
        with self.assertRaises(PermissionError) as cm:
            resolve_local_path("/tmp/escaped.cif")
        self.assertIn("escapes the agent workspace", str(cm.exception))

    def test_relative_traversal_rejected(self) -> None:
        with self.assertRaises(PermissionError) as cm:
            resolve_local_path("../escaped.cif")
        self.assertIn("escapes the agent workspace", str(cm.exception))

    def test_deep_traversal_landing_in_tmp_rejected(self) -> None:
        with self.assertRaises(PermissionError):
            resolve_local_path("subdir/../../../tmp/escaped.cif")

    def test_traversal_that_resolves_back_inside_allowed(self) -> None:
        result = resolve_local_path("subdir/../foo.cif")
        self.assertEqual(result, self.workspace / "foo.cif")

    def test_home_relative_outside_workspace_rejected(self) -> None:
        with self.assertRaises(PermissionError):
            resolve_local_path("~/definitely-not-in-the-workspace.cif")


if __name__ == "__main__":
    unittest.main()
