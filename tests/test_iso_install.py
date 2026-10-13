"""Reine Funktionen des ISO-Installers (iso/install.sh); es wird nichts partitioniert oder gemountet."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'iso/install.sh'
GB = 10 ** 9


def shell(code, stdin=''):
    result = subprocess.run(['bash', '-c', f'source {SCRIPT}\n{code}'], input=stdin, text=True,
                            capture_output=True, check=False)
    return result.returncode, result.stdout, result.stderr


class InstallerFunctionTests(unittest.TestCase):
    def test_syntax_and_sourcing_does_not_start_the_installer(self):
        subprocess.run(['bash', '-n', str(SCRIPT)], check=True)
        self.assertEqual(shell('true')[0], 0)

    def test_partition_names_follow_the_kernel_naming(self):
        names = shell('for d in /dev/sda /dev/nvme0n1 /dev/mmcblk0; do partition_name $d 2; echo; done')[1].split()
        self.assertEqual(names, ['/dev/sda2', '/dev/nvme0n1p2', '/dev/mmcblk0p2'])

    def test_disk_filter_skips_medium_small_and_virtual_devices(self):
        lines = '\n'.join([
            f'NAME="/dev/sda" SIZE="{500 * GB}" TYPE="disk" MODEL="Samsung SSD"',
            f'NAME="/dev/sdb" SIZE="{32 * GB}" TYPE="disk" MODEL="USB Stick"',
            f'NAME="/dev/sdc" SIZE="{8 * GB}" TYPE="disk" MODEL="Klein"',
            f'NAME="/dev/nvme0n1" SIZE="{256 * GB}" TYPE="disk" MODEL=""',
            f'NAME="/dev/zram0" SIZE="{64 * GB}" TYPE="disk" MODEL=""',
            f'NAME="/dev/loop0" SIZE="{64 * GB}" TYPE="loop" MODEL=""',
            f'NAME="/dev/sr0" SIZE="{64 * GB}" TYPE="rom" MODEL="DVD"',
        ])
        code, out, _ = shell('filter_disks /dev/sdb', lines)
        self.assertEqual(out.splitlines(), [f'/dev/sda|{500 * GB}|Samsung SSD', f'/dev/nvme0n1|{256 * GB}|?'])

    def test_disk_filter_ignores_garbage_lines(self):
        _, out, _ = shell('filter_disks none', 'kaputt; touch /tmp/pwned\n')
        self.assertEqual(out, '')

    def test_fstab_for_efi_and_bios(self):
        self.assertEqual(shell('fstab_text AAA BBB')[1].splitlines()[1:],
                         ['UUID=AAA / ext4 errors=remount-ro 0 1', 'UUID=BBB /boot/efi vfat umask=0077 0 2'])
        self.assertEqual(shell('fstab_text AAA')[1].splitlines()[1:], ['UUID=AAA / ext4 errors=remount-ro 0 1'])

    def test_partition_layout_has_esp_or_bios_boot(self):
        self.assertIn('type=U', shell('sfdisk_script efi')[1])
        bios = shell('sfdisk_script bios')[1]
        self.assertIn('21686148-6449-6E6F-744E-656564454649', bios)
        self.assertNotIn('type=U', bios)

    def test_locale_keyboard_and_user_names(self):
        self.assertEqual(shell('locale_for en; echo; locale_for de')[1].split(), ['en_US.UTF-8', 'de_DE.UTF-8'])
        self.assertIn('XKBLAYOUT="ch"', shell('keyboard_text ch:de')[1])
        self.assertIn('XKBVARIANT="de"', shell('keyboard_text ch:de')[1])
        for name, valid in (('admin', True), ('papa', True), ('kids', False), ('root', False), ('live', False),
                            ('Admin', False), ('1abc', False), ('a b', False), ('', False)):
            with self.subTest(name=name):
                self.assertEqual(shell(f'valid_username {name!r}')[0] == 0, valid)


if __name__ == '__main__':
    unittest.main()
