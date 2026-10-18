import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unittest.mock import patch

from paimenos import hardware_profile as hp
from paimenos import emulator_video as video

MEM = 'MemTotal:       16303000 kB\n'


def cpu(model):
    return 'processor\t: 0\nmodel name\t: ' + model + '\n'


class DetectTests(unittest.TestCase):
    def test_suggestions_follow_cpu_generation_and_tier(self):
        cases = {
            'Intel(R) Core(TM) i5-4300U CPU @ 1.90GHz': 'ultra-low',
            'Intel(R) Core(TM) i3-7100U CPU @ 2.40GHz': 'ultra-low',
            'Intel(R) Celeron(R) N4020 CPU @ 1.10GHz': 'ultra-low',
            'Intel(R) Core(TM) i5 CPU       M 520  @ 2.40GHz': 'ultra-low',
            'Intel(R) Core(TM) i7-920 CPU @ 2.67GHz': 'ultra-low',
            'Intel(R) Core(TM) i5-5300U CPU @ 2.30GHz': 'low',
            'Intel(R) Core(TM) i7-7500U CPU @ 2.70GHz': 'low',
            'Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz': 'medium',
            'Intel(R) Core(TM) i5-10210U CPU @ 1.60GHz': 'medium',
            'Intel(R) Core(TM) i5-1035G1 CPU @ 1.00GHz': 'medium',
            '11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz': 'high',
            '12th Gen Intel(R) Core(TM) i7-1260P': 'high',
            'Intel(R) Core(TM) Ultra 5 125U': 'high',
            'AMD Ryzen 5 2500U with Radeon Vega Mobile Gfx': 'low',
            'AMD Ryzen 5 4500U with Radeon Graphics': 'medium',
            'AMD Ryzen 3 5300U with Radeon Graphics': 'medium',
            'AMD Ryzen 7 5800H with Radeon Graphics': 'high',
            'AMD A6-9225 RADEON R4, 5 COMPUTE CORES 2C+3G': 'ultra-low',
            'Some Unknown CPU': 'low',
        }
        for model, expected in cases.items():
            with self.subTest(model=model):
                self.assertEqual(hp.detect(cpu(model), MEM), expected)

    def test_arm_boards_are_detected_by_board_model(self):
        def board(model):
            return 'processor\t: 0\nBogoMIPS\t: 108.00\nModel\t\t: ' + model + '\n'
        self.assertEqual(hp.detect(board('Raspberry Pi 5 Model B Rev 1.0'), MEM), 'low')
        self.assertEqual(hp.detect(board('Raspberry Pi 4 Model B Rev 1.4'), MEM), 'ultra-low')
        self.assertEqual(hp.detect(board('Raspberry Pi 5 Model B Rev 1.0'), 'MemTotal: 2000000 kB\n'), 'ultra-low')
        self.assertEqual(hp.detect('processor\t: 0\nCPU part\t: 0xd0b\n', MEM), 'low')

    def test_little_memory_lowers_the_suggestion(self):
        self.assertEqual(hp.detect(cpu('Intel(R) Core(TM) i7-1260P'), 'MemTotal: 2900000 kB\n'), 'ultra-low')
        self.assertEqual(hp.detect(cpu('Intel(R) Core(TM) i7-1260P'), 'MemTotal: 3900000 kB\n'), 'low')
        self.assertEqual(hp.detect(cpu('Intel(R) Core(TM) i7-1260P'), 'MemTotal: 5900000 kB\n'), 'low')
        self.assertEqual(hp.detect(cpu('Intel(R) Core(TM) i7-1260P'), 'MemTotal: 7900000 kB\n'), 'high')

    def test_missing_or_invalid_file_falls_back_to_previous_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profile'
            with patch.object(hp, 'PROFILE_FILE', path):
                self.assertEqual(hp.current(), 'low')
                path.write_text('turbo\n')
                self.assertEqual(hp.current(), 'low')
                path.write_text('high\n')
                self.assertEqual(hp.current(), 'high')


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def test_luanti_keeps_user_keys_and_replaces_managed_ones(self):
        conf = self.home / '.minetest/minetest.conf'
        conf.parent.mkdir()
        conf.write_text('name = Kind\nviewing_range = 500\n# viewing_range = 1\nviewing_range = 600\n')
        self.assertEqual(hp.apply_luanti('ultra-low', self.home), [conf])
        text = conf.read_text()
        self.assertIn('name = Kind\n', text)
        self.assertIn('# viewing_range = 1\n', text)
        self.assertEqual(text.count('viewing_range = 50\n'), 1)
        self.assertNotIn('600', text)
        self.assertIn('enable_dynamic_shadows = false', text)
        self.assertEqual(hp.apply_luanti('ultra-low', self.home), [])

    def test_fresh_flatpak_install_gets_settings_before_first_start(self):
        apps = self.home / '.config/paimenos/apps.json'
        apps.parent.mkdir(parents=True)
        apps.write_text(json.dumps([{'id': 'minetest', 'flatpak_id': 'org.luanti.luanti'}]))
        changed = hp.apply_luanti('high', self.home)
        conf = self.home / '.var/app/org.luanti.luanti/.minetest/minetest.conf'
        self.assertEqual(changed, [conf])
        self.assertIn('enable_dynamic_shadows = true', conf.read_text())
        self.assertFalse((self.home / '.minetest').exists())

    def test_symlinked_luanti_folders_are_not_followed(self):
        target = self.home / 'elsewhere'; target.mkdir()
        (self.home / '.minetest').symlink_to(target)
        self.assertEqual(hp.apply_luanti('low', self.home), [])
        self.assertFalse((target / 'minetest.conf').exists())

    def test_no_luanti_means_no_new_folders(self):
        self.assertEqual(hp.apply_luanti('low', self.home), [])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_firefox_preferences_scale_with_profile(self):
        self.assertIn('"dom.ipc.processCount", 1', hp.firefox_preferences('ultra-low'))
        self.assertIn('"ui.prefersReducedMotion", 1', hp.firefox_preferences('low'))
        self.assertEqual(hp.firefox_preferences('high'), '')


class SuperTuxKartTests(unittest.TestCase):
    FIXTURE = Path(__file__).resolve().parent / 'fixtures/stk-config-v8.xml'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.conf = self.home / '.var/app/net.supertuxkart.SuperTuxKart/config/supertuxkart/config-0.10/config.xml'
        self.conf.parent.mkdir(parents=True)
        self.conf.write_text(self.FIXTURE.read_text())

    def attributes(self, element):
        import xml.etree.ElementTree as ET
        return ET.parse(self.conf).getroot().find(element).attrib

    def test_ultra_low_changes_only_graphics_and_keeps_the_rest(self):
        self.assertEqual(hp.apply_stk('ultra-low', self.home), [self.conf])
        video, gfx = self.attributes('Video'), self.attributes('GFX')
        self.assertEqual(video['enable_dynamic_lights'], 'false')
        self.assertEqual(video['enable_high_definition_textures'], '2')
        self.assertEqual(video['scale_rtts_factor'], '0.750000')
        self.assertEqual(gfx['geometry-level'], '0')
        self.assertEqual(gfx['animated-characters'], 'false')
        # Untouched: resolution, render driver, unrelated settings, comments.
        self.assertEqual(video['real_width'], '1024')
        self.assertEqual(video['render_driver'], 'opengl')
        self.assertEqual(gfx['swap-interval-vsync'], '1')
        text = self.conf.read_text()
        self.assertIn('<kart value="tux" />', text)
        self.assertIn('Bit flag: bit 0 = enabled/disabled', text)
        self.assertEqual(text.count('max_fps='), 1)

    def test_high_enables_effects(self):
        hp.apply_stk('high', self.home)
        video, gfx = self.attributes('Video'), self.attributes('GFX')
        self.assertEqual(video['enable_bloom'], 'true')
        self.assertEqual(gfx['shadows_resolution'], '512')
        self.assertEqual(gfx['Degraded_IBL'], 'false')

    def test_pending_mode_respects_in_game_changes_until_the_profile_changes(self):
        hp.apply_stk('low', self.home)
        self.conf.write_text(self.conf.read_text().replace('geometry-level="1"', 'geometry-level="2"'))
        self.assertEqual(hp.apply_stk('low', self.home, only_pending=True), [])
        self.assertIn('geometry-level="2"', self.conf.read_text())
        self.assertEqual(hp.apply_stk('medium', self.home, only_pending=True), [self.conf])

    def test_missing_config_or_links_are_left_alone(self):
        self.conf.unlink()
        self.assertEqual(hp.apply_stk('low', self.home), [])
        self.assertFalse(self.conf.exists())
        target = self.home / 'target.xml'
        target.write_text(self.FIXTURE.read_text())
        self.conf.symlink_to(target)
        self.assertEqual(hp.apply_stk('low', self.home), [])
        self.assertIn('enable_dynamic_lights="true"', target.read_text())


class EmulatorProfileTests(unittest.TestCase):
    def test_low_profile_keeps_previous_efficient_defaults(self):
        self.assertEqual(video.core_settings('psp', 'low')['ppsspp_internal_resolution'], '480x272')
        self.assertEqual(video.core_settings('psp', 'low')['ppsspp_texture_anisotropic_filtering'], '2x')
        self.assertEqual(video.core_settings('n64', 'low')['mupen64plus-next-EnableFBEmulation'], 'True')
        self.assertEqual(video.core_settings('ps1', 'low')['beetle_psx_internal_resolution'], '1x(native)')
        self.assertEqual(video.video_settings('snes', 'low')['video_threaded'], 'false')

    def test_profiles_scale_resolution_and_costly_options(self):
        self.assertEqual(video.video_settings('snes', 'ultra-low')['video_threaded'], 'true')
        self.assertEqual(video.core_settings('psp', 'ultra-low')['ppsspp_auto_frameskip'], 'enabled')
        self.assertEqual(video.core_settings('n64', 'ultra-low')['mupen64plus-EnableFBEmulation'], 'False')
        self.assertEqual(video.core_settings('psp', 'medium')['ppsspp_internal_resolution'], '960x544')
        self.assertEqual(video.core_settings('n64', 'high')['mupen64plus-next-EnableNativeResFactor'], '3')
        self.assertEqual(video.core_settings('ps1', 'high')['beetle_psx_internal_resolution'], '4x')
        # Dolphin stays at native resolution and without threaded video in every profile.
        for profile in hp.PROFILES:
            self.assertEqual(video.core_settings('dolphin', profile)['dolphin_efb_scale'], '1')
            self.assertEqual(video.video_settings('dolphin', profile)['video_threaded'], 'false')
            self.assertEqual(video.video_settings('dolphin', profile)['audio_latency'], '64')

    def test_profile_file_is_read_when_no_profile_is_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profile'
            path.write_text('medium\n')
            with patch.object(hp, 'PROFILE_FILE', path):
                self.assertEqual(video.core_settings('psp')['ppsspp_internal_resolution'], '960x544')


if __name__ == '__main__':
    unittest.main()
