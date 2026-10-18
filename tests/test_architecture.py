from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ArchitectureTests(unittest.TestCase):
    def detect(self, arch):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / 'dpkg'
            stub.write_text('#!/bin/sh\necho ' + arch + '\n')
            stub.chmod(0o755)
            script = ('paimenos_text() { echo "$1"; }; PATH="%s:$PATH"; source %s/installer/architecture.sh; '
                      'echo "$PAIMENOS_ARCH $PAIMENOS_IS_ARM"' % (tmp, ROOT))
            return subprocess.run(['bash', '-c', script], capture_output=True, text=True)

    def test_flags_follow_the_dpkg_architecture(self):
        self.assertTrue(self.detect('amd64').stdout.endswith('amd64 0\n'))
        self.assertTrue(self.detect('arm64').stdout.endswith('arm64 1\n'))
        self.assertTrue(self.detect('armhf').stdout.endswith('armhf 1\n'))

    def test_unsupported_architecture_aborts(self):
        result = self.detect('riscv64')
        self.assertNotEqual(result.returncode, 0)

    def test_installer_loads_detection_before_changing_the_system(self):
        source = (ROOT / 'install.sh').read_text()
        self.assertLess(source.index('installer/architecture.sh'), source.index('paimenos-maintenance.lock'))


if __name__ == '__main__':
    unittest.main()
