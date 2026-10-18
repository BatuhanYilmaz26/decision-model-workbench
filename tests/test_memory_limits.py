"""Available RAM never exceeds a process's inherited Linux memory limits."""

from pathlib import Path
import tempfile
import unittest
from decision_workbench.training.memory_limits import available_linux_memory


class MemoryLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc, self.groups = self.root / "proc", self.root / "cgroup"
        (self.proc / "self").mkdir(parents=True)
        self.groups.mkdir()
        (self.proc / "meminfo").write_text("MemAvailable: 1073741824 kB\n")

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text))

    def value(self):
        return available_linux_memory(self.proc, self.groups)

    def test_bare_host_and_unlimited_control_group_keep_host_available_memory(self):
        self.assertEqual(self.value(), 1024**4)
        self.write(self.groups / "memory.max", "max")
        self.assertEqual(self.value(), 1024**4)

    def test_v2_nested_and_ancestor_limits_subtract_their_current_usage(self):
        self.write(self.proc / "self/cgroup", "0::/parent/job\n")
        self.write(self.groups / "parent/job/memory.max", 512 * 1024**3)
        self.write(self.groups / "parent/job/memory.current", 16 * 1024**3)
        self.write(self.groups / "parent/memory.max", 480 * 1024**3)
        self.write(self.groups / "parent/memory.current", 80 * 1024**3)
        self.assertEqual(self.value(), 400 * 1024**3)

    def test_v1_container_memory_is_bounded_in_a_hybrid_hierarchy(self):
        self.write(self.proc / "self/cgroup", "9:memory:/owner\n8:cpu:/other\n")
        self.write(self.groups / "memory/owner/memory.limit_in_bytes", 32 * 1024**3)
        self.write(self.groups / "memory/owner/memory.usage_in_bytes", 3 * 1024**3)
        self.assertEqual(self.value(), 29 * 1024**3)

    def test_unknown_usage_or_exhausted_limit_cannot_establish_free_memory(self):
        self.write(self.groups / "memory.max", 100)
        self.assertEqual(self.value(), 0)
        self.write(self.groups / "memory.current", 101)
        self.assertEqual(self.value(), 0)

    def test_malformed_limits_and_escaping_membership_are_refused(self):
        for value in ("bad", "-1"):
            self.write(self.groups / "memory.max", value)
            with self.assertRaises(ValueError):
                self.value()
        (self.groups / "memory.max").unlink()
        self.write(self.proc / "self/cgroup", "0::/../../elsewhere\n")
        with self.assertRaises(ValueError):
            self.value()
