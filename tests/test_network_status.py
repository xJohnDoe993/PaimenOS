from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from paimenos import network_status


class ProbeTests(unittest.TestCase):
    def test_one_reachable_target_is_enough(self):
        calls = []
        def connect(target, timeout):
            calls.append(target)
            if target[0] != 'deb.debian.org':
                raise OSError('gesperrt')
            return unittest.mock.MagicMock()
        with patch.object(network_status.socket, 'create_connection', side_effect=connect):
            self.assertTrue(network_status.NetworkStatus.probe())
        self.assertEqual(len(calls), 3)

    def test_offline_when_every_target_fails(self):
        with patch.object(network_status.socket, 'create_connection', side_effect=OSError):
            self.assertFalse(network_status.NetworkStatus.probe())


if __name__ == '__main__':
    unittest.main()
