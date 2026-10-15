import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deploy', ROOT / 'tools/deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        package_patch = patch.object(deploy, 'ensure_packages', return_value=[])
        package_patch.start(); self.addCleanup(package_patch.stop)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT.parent)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        shutil.copytree(ROOT, self.source, ignore=shutil.ignore_patterns('__pycache__', '.git', 'tmp*'))
        self.base = self.root / 'installed'
        self.base.mkdir()
    def tearDown(self):
        self.temp.cleanup()
    def initial(self):
        release, names = deploy.stage_release(self.source, self.base)
        deploy.activate(self.base, release, initial=True, live=False)
        return release
    def test_full_release_installs_package_and_resources(self):
        release = self.initial()
        self.assertEqual((self.base / 'current').resolve(), release)
        self.assertTrue((release / 'app/paimenos/menu.py').is_file())
        self.assertTrue((release / 'assets/parent-web/base.html').is_file())
        self.assertEqual((release / 'assets/branding/paimenos-logo.png').read_bytes(),
                         (ROOT / 'assets/branding/paimenos-logo.png').read_bytes())
        self.assertTrue((release / 'tools/install-paimenos-plymouth.py').is_file())
        self.assertTrue((release / 'assets/plymouth/paimenos/logo.png').is_file())
        self.assertTrue((release / 'assets/plymouth/paimenos/SHA256SUMS').is_file())
        self.assertEqual(len(list((release / 'assets/plymouth/paimenos').glob('spin-*.png'))), 48)
        self.assertEqual(release.stat().st_mode & 0o777, 0o755)
        self.assertEqual((release / 'app/paimenos/menu.py').stat().st_mode & 0o777, 0o644)
    def test_component_update_preserves_other_code_and_user_data(self):
        old = self.initial()
        data = self.root / 'home/kids/.config/paimenos/settings.json'
        data.parent.mkdir(parents=True)
        data.write_text('{"pin":"6789","bonus_minutes":9}')
        menu = (old / 'app/paimenos/menu.py').read_bytes()
        controller = self.source / 'src/paimenos/controller.py'
        controller.write_text(controller.read_text() + '\n# Controller fix\n')
        # An unrelated change in the source tree is deliberately not installed.
        unrelated = self.source / 'src/paimenos/menu.py'
        unrelated.write_text(unrelated.read_text() + '\n# Do not deploy this file\n')
        release, names = deploy.stage_release(self.source, self.base, ['controller'])
        deploy.activate(self.base, release, live=False)
        self.assertEqual(names, ['controller'])
        self.assertEqual((release / 'app/paimenos/menu.py').read_bytes(), menu)
        self.assertIn('Controller fix', (release / 'app/paimenos/controller.py').read_text())
        self.assertEqual(data.read_text(), '{"pin":"6789","bonus_minutes":9}')
        self.assertEqual((self.base / 'previous').resolve(), old)
    def test_english_runtime_requires_full_update_from_api_two(self):
        old = self.initial()
        legacy = old.with_name('0.64.1-runtime-api2')
        old.rename(legacy)
        (self.base / 'current').unlink()
        (self.base / 'current').symlink_to(legacy)
        old = legacy
        record = json.loads((old / 'installed.json').read_text())
        record['runtime_api'] = 2
        (old / 'installed.json').write_text(json.dumps(record))
        (old / 'app/paimenos/i18n.py').unlink()
        shutil.rmtree(old / 'assets/i18n')
        with self.assertRaisesRegex(ValueError, 'Paket-API'):
            deploy.stage_release(self.source, self.base, ['desktop'])
        self.assertEqual((self.base / 'current').resolve(), old)
        release, _ = deploy.stage_release(self.source, self.base)
        deploy.activate(self.base, release, live=False)
        script = ('import sys,os; sys.path.insert(0,sys.argv[1]); '
                  'os.environ["PAIMENOS_LANGUAGE"]="en"; '
                  'from paimenos.i18n import t; assert t("Ausschalten")=="Shut down"')
        subprocess.run([sys.executable, '-I', '-c', script, str(release / 'app')], check=True)
        self.assertEqual((self.base / 'previous').resolve(), old)
    def test_invalid_python_does_not_switch_current(self):
        old = self.initial()
        (self.source / 'src/paimenos/controller.py').write_text('def broken(:\n')
        with self.assertRaises(SyntaxError):
            deploy.stage_release(self.source, self.base, ['controller'])
        self.assertEqual((self.base / 'current').resolve(), old)
        self.assertFalse(list((self.base / 'releases').glob('.stage-*')))
    def test_rollback_restores_previous_release(self):
        old = self.initial()
        path = self.source / 'src/paimenos/controller.py'
        path.write_text(path.read_text() + '\n# second release\n')
        new, _ = deploy.stage_release(self.source, self.base, ['controller'])
        deploy.activate(self.base, new, live=False)
        deploy.activate(self.base, (self.base / 'previous').resolve(), live=False)
        self.assertEqual((self.base / 'current').resolve(), old)
        self.assertEqual((self.base / 'previous').resolve(), new)
    def test_service_failure_restores_code_and_restarts_old_services(self):
        old = self.initial()
        path = self.source / 'src/paimenos/controller.py'
        path.write_text(path.read_text() + '\n# second release\n')
        new, _ = deploy.stage_release(self.source, self.base, ['controller'])
        error = subprocess.CalledProcessError(1, 'systemctl')
        with patch.object(deploy, 'install_launchers'), patch.object(deploy, 'restart_services', side_effect=[error, None]) as restart:
            with self.assertRaises(subprocess.CalledProcessError):
                deploy.activate(self.base, new)
        self.assertEqual((self.base / 'current').resolve(), old)
        self.assertEqual(restart.call_count, 2)
    def test_source_symlink_is_rejected(self):
        file = self.source / 'src/paimenos/controller.py'
        file.unlink()
        file.symlink_to(ROOT / 'src/paimenos/controller.py')
        with self.assertRaises(ValueError):
            deploy.validate_source(self.source)
    def test_unknown_component_is_rejected(self):
        self.initial()
        with self.assertRaises(ValueError):
            deploy.stage_release(self.source, self.base, ['does-not-exist'])
    def test_deleted_component_file_is_removed_from_new_release(self):
        extra = self.source / 'src/paimenos/obsolete.py'
        extra.write_text('VALUE = 1\n')
        manifest = json.loads((self.source / 'manifest.json').read_text())
        manifest['components']['controller']['files'].append('src/paimenos/obsolete.py')
        (self.source / 'manifest.json').write_text(json.dumps(manifest))
        old = self.initial()
        extra.unlink()
        manifest['components']['controller']['files'].remove('src/paimenos/obsolete.py')
        (self.source / 'manifest.json').write_text(json.dumps(manifest))
        new, _ = deploy.stage_release(self.source, self.base, ['controller'])
        self.assertTrue((old / 'app/paimenos/obsolete.py').exists())
        self.assertFalse((new / 'app/paimenos/obsolete.py').exists())
    def test_traversal_is_rejected(self):
        for value in ('../etc/shadow', '/etc/shadow', 'src/../bin/foo', 'src/another_package/foo.py'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                deploy.destination(value)
    def test_full_update_removes_deleted_component_and_its_receipt(self):
        obsolete = self.source / 'src/paimenos/obsolete.py'
        obsolete.write_text('VALUE = 1\n')
        manifest = json.loads((self.source / 'manifest.json').read_text())
        manifest['components']['obsolete'] = {'files': ['src/paimenos/obsolete.py']}
        (self.source / 'manifest.json').write_text(json.dumps(manifest))
        old = self.initial()
        obsolete.unlink(); del manifest['components']['obsolete']
        (self.source / 'manifest.json').write_text(json.dumps(manifest))
        new, _ = deploy.stage_release(self.source, self.base)
        self.assertTrue((old / 'app/paimenos/obsolete.py').exists())
        self.assertFalse((new / 'app/paimenos/obsolete.py').exists())
        self.assertNotIn('obsolete', deploy.read_json(new / 'installed.json')['components'])
    def test_update_service_is_enabled_and_removed_on_rollback(self):
        release = self.initial()
        unit_root = self.root / 'units'
        with patch.object(deploy.subprocess, 'run') as run:
            deploy.apply_units(release, unit_root=unit_root)
            self.assertIn(unittest.mock.call(['systemctl', 'enable', 'paimenos-updates.service'], check=True), run.call_args_list)
        prior = self.root / 'prior'
        shutil.copytree(release, prior)
        (prior / 'systemd/system/paimenos-updates.service').unlink()
        with patch.object(deploy.subprocess, 'run') as run:
            deploy.apply_units(prior, previous=release, unit_root=unit_root)
            self.assertIn(unittest.mock.call(['systemctl', 'disable', '--now', 'paimenos-updates.service'], check=True), run.call_args_list)
            self.assertFalse((unit_root / 'system/paimenos-updates.service').exists())
    def test_print_service_is_enabled_on_every_update_when_installed(self):
        release = self.initial()
        unit_root = self.root / 'etc-units'
        unit_root.mkdir()
        run = unittest.mock.Mock()
        with patch.object(deploy, 'BASE', self.base):
            (self.base / 'current/systemd/system').mkdir(parents=True, exist_ok=True)
            (self.base / 'current/systemd/system/paimenos-print.service').write_text('[Unit]\n')
            deploy.enable_installed_units(run, unit_root)
            run.assert_not_called()
            (unit_root / 'paimenos-print.service').write_text('[Unit]\n')
            deploy.enable_installed_units(run, unit_root)
            run.assert_called_once_with(['systemctl', 'enable', 'paimenos-print.service'], check=True)
    def test_unit_install_failure_restores_previous_units_and_code(self):
        old = self.initial()
        path = self.source / 'src/paimenos/controller.py'
        path.write_text(path.read_text() + '\n# second release\n')
        new, _ = deploy.stage_release(self.source, self.base, ['controller'])
        error = subprocess.CalledProcessError(1, 'systemctl daemon-reload')
        with patch.object(deploy, 'install_launchers'), patch.object(deploy, 'apply_units', side_effect=[error, None]) as units, patch.object(deploy, 'restart_services') as restart:
            with self.assertRaises(subprocess.CalledProcessError):
                deploy.activate(self.base, new, units=True)
        self.assertEqual((self.base / 'current').resolve(), old)
        self.assertEqual(units.call_args_list[1].args, (old,))
        self.assertEqual(units.call_args_list[1].kwargs, {'previous': new})
        self.assertEqual(restart.call_count, 1)
