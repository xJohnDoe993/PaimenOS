#!/usr/bin/python3
"""Generate component inventory. Run after adding/removing repository files."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
groups = {
    'shared': ['__init__', 'i18n', 'paths', 'state', 'theme', 'diagnostics', 'images', 'media_files', 'categories', 'fullscreen', 'webapp', 'osd_state', 'hardware_profile', 'pronunciation', 'printing'],
    'desktop': ['menu', 'menu_tiles', 'menu_camera', 'menu_support', 'speech', 'video', 'status_overlay', 'close_overlay', 'browser', 'lockscreen', 'timer', 'media', 'screen_guard', 'foreground', 'idle', 'power'],
    'controller': ['controller', 'controller_profiles', 'input_devices'],
    'network': ['wifi', 'bluetooth', 'network_status'],
    'emulators': ['backups', 'emulators', 'emulator_video', 'emulator_session', 'savestates', 'emulator_install', 'emulator_catalog', 'emulator_service', 'flatpak_service', 'offline_complete'],
    'backend': ['parent', 'parent_web', 'flatpak_store'],
    'updates': ['updates', 'release_source', 'update_service'],
    'cli': ['cli_osd_notify', 'cli_update_apps', 'cli_flatpak', 'cli_flatpak_menu', 'cli_emulator', 'cli_emulator_check'],
    'tools': [], 'services': [],
}
components = {key: {'files': ['src/paimenos/' + name + '.py' for name in names]} for key, names in groups.items()}
components['shared']['files'] += ['run.py', 'assets/webapp.css', 'assets/i18n/en.json', 'data/default-pronunciations.json']
components['shared']['files'] += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'assets/branding').glob('*.png'))
components['backend']['files'] += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'assets/parent-web').glob('*.html'))
components['emulators']['files'] += ['data/emulator-catalog.json', 'data/app-data-catalog.json']
components['emulators']['files'] += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'data/libretro-info').glob('*') if p.is_file())
components['cli']['files'] += sorted(str(p.relative_to(ROOT)) for folder in ('bin', 'sbin') for p in (ROOT / folder).glob('*') if p.is_file())
components['tools']['files'] += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'tools').glob('*.py') if p.name not in {'check.py', 'build-manifest.py', 'build-release.py'})
components['tools']['files'].append('data/update-packages.json')
components['tools']['files'] += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'assets/plymouth/paimenos').glob('*') if p.is_file())
components['services']['files'] += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'systemd').rglob('*') if p.is_file())
manifest = {'schema': 1, 'runtime_api': 3, 'version': (ROOT / 'VERSION').read_text().strip(), 'components': components}
(ROOT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print('manifest.json aktualisiert.')
