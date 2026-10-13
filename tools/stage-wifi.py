#!/usr/bin/python3
"""Prepare ifupdown Wi-Fi offline; commit before network services at next boot."""
from pathlib import Path as _Path
import sys as _sys
_release = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(_release / ('app' if (_release / 'app').is_dir() else 'src')))
from paimenos.i18n import t
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location('handoff', Path(__file__).with_name('wifi-handoff.py'))
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


class Migration:
    def __init__(self, root=Path('/'), run=subprocess.run):
        self.root, self.run = root, run
        self.state = root/'var/lib/paimenos/wifi-migration'
        self.pending = self.state/'pending.json'
        self.journal = self.state/'transaction.json'
        self.connections = root/'etc/NetworkManager/system-connections'
        self.policies = root/'etc/NetworkManager/conf.d'

    def command(self, *args, required=True, input=None):
        try:
            result = self.run(list(args), input=input, text=True, capture_output=True, timeout=30, check=False)
        except FileNotFoundError:
            if required: raise
            result = subprocess.CompletedProcess(args, 127, stdout='', stderr='')
        if required and result.returncode:
            # nmcli may echo credential input on parse errors; never print output.
            raise ValueError(t('WLAN-Vorbereitung fehlgeschlagen: ') + args[0] + ' ' + args[1])
        return result

    @staticmethod
    def write(path, data, mode=0o600):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.paimenos-wifi-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data); os.fchmod(stream.fileno(), mode); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def prepare(self):
        if self.journal.exists():
            raise ValueError(t('Unvollständige WLAN-Boot-Übernahme; Wiederherstellung beim Neustart erforderlich.'))
        # Beim Offline-Setup (PAIMENOS_OFFLINE=1) gibt es kein Internet, das sich prüfen ließe.
        if os.environ.get('PAIMENOS_OFFLINE') != '1' and self.command('getent', 'ahostsv4', 'deb.debian.org', required=False).returncode:
            raise ValueError(t('Netzwerk/DNS funktioniert vor der WLAN-Vorbereitung nicht.'))
        devices = self.command('nmcli', '--terse', '--escape', 'no', '--fields', 'DEVICE,TYPE', 'device', 'status').stdout
        legacy = []
        for line in devices.splitlines():
            device, _, kind = line.partition(':')
            if kind == 'wifi' and self.command('ifquery', device, required=False).returncode == 0:
                if not re.fullmatch(r'[a-zA-Z0-9_-]{1,15}', device): raise ValueError(t('Ungültiger WLAN-Adaptername.'))
                legacy.append(device)
        if not legacy:
            self.pending.unlink(missing_ok=True)
            print(t('Bestehende NetworkManager-Verbindungen bleiben unverändert.'))
            return False
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.chmod(0o700)
        prepared = Path(tempfile.mkdtemp(prefix='plan-', dir=self.state))
        devices_plan = []
        previous_root = handoff.CONFIG_ROOT
        handoff.CONFIG_ROOT = self.root/'etc/network'
        try:
            for device in legacy:
                backup = prepared/device; backup.mkdir(mode=0o700)
                handoff.prepare(device, backup)
                handoff.profile(device, backup)
                profile = backup/'profile.nmconnection'
                normalized = self.command('nmcli', '--offline', 'connection', 'modify', 'connection.autoconnect', 'yes', input=profile.read_text()).stdout
                if not normalized.strip(): raise ValueError(t('NetworkManager hat kein gültiges WLAN-Profil erzeugt.'))
                self.write(profile, normalized.encode())
                inputs = {}
                for item in json.loads((backup/'manifest.json').read_text()):
                    inputs[item['path']] = item['old']
                    for raw, tokens in handoff.logical_lines((backup/f'{item["index"]}.old').read_text()):
                        if tokens and tokens[0] == 'wpa-conf':
                            path = Path(handoff.unquote(raw.strip().split(None, 1)[1]))
                            if path.is_symlink() or not path.is_file(): raise ValueError(t('WPA-Quelle hat sich geändert.'))
                            inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
                devices_plan.append({'device': device, 'backup': str(backup), 'uuid': (backup/'profile.uuid').read_text(),
                                     'profile_sha': hashlib.sha256(profile.read_bytes()).hexdigest(), 'inputs': inputs})
            # Publish only a complete, validated plan. Interrupted/resumed staging
            # leaves the old pending plan intact, and never modifies live devices.
            self.write(self.pending, json.dumps(devices_plan).encode())
            print(t('WLAN-Profile offline geprüft. Bestehende Verbindung bleibt aktiv; Übernahme beim Neustart.'))
            return True
        except BaseException:
            shutil.rmtree(prepared)
            raise
        finally:
            handoff.CONFIG_ROOT = previous_root

    def rollback(self, transaction):
        failed = False
        for unit, enabled in reversed(transaction['disabled']):
            args = ['systemctl'] + (['--runtime'] if enabled == 'enabled-runtime' else []) + ['enable',unit]
            failed |= self.command(*args, required=False).returncode != 0
        for unit in reversed(transaction['masked']):
            failed |= self.command('systemctl','unmask',unit,required=False).returncode != 0
        for filename, original in reversed(transaction['written']):
            path = Path(filename)
            if original is None: path.unlink(missing_ok=True)
            else: self.write(path, base64.b64decode(original[0]), original[1])
        for directory in reversed(transaction['applied']):
            try: handoff.write_files(Path(directory), restore=True)
            except (OSError, ValueError): failed = True
        if failed: raise ValueError(t('WLAN-Übernahme und Wiederherstellung fehlgeschlagen; Boot-Protokoll prüfen.'))

    def commit(self):
        if self.pending.exists(): os.replace(self.pending, self.state/'completed.json')
        self.journal.unlink(missing_ok=True)

    def apply(self):
        previous_root = handoff.CONFIG_ROOT
        handoff.CONFIG_ROOT = self.root/'etc/network'
        try:
            if self.journal.exists():
                interrupted = json.loads(self.journal.read_text())
                if interrupted['committed']:
                    self.commit(); return
                self.rollback(interrupted)
                self.journal.unlink()
            try:
                self.apply_plan()
            except (OSError, ValueError, subprocess.TimeoutExpired):
                if self.pending.exists() and not self.journal.exists():
                    os.replace(self.pending, self.state/'failed.json')
                raise
        finally:
            handoff.CONFIG_ROOT = previous_root

    def apply_plan(self):
        if not self.pending.exists(): return
        plans = json.loads(self.pending.read_text())
        # Check every input before changing anything, including WPA secrets.
        for plan in plans:
            profile = Path(plan['backup'])/'profile.nmconnection'
            if profile.is_symlink() or hashlib.sha256(profile.read_bytes()).hexdigest() != plan['profile_sha']:
                raise ValueError(t('Vorbereitetes WLAN-Profil wurde geändert.'))
            for filename, expected in plan['inputs'].items():
                path = Path(filename)
                if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError(t('WLAN-Konfiguration seit Vorbereitung geändert; Debian-Konfiguration bleibt erhalten.'))
        rollback = Path(tempfile.mkdtemp(prefix='boot-', dir=self.state))
        transaction = dict(applied=[], written=[], masked=[], disabled=[], committed=False)
        def save(): self.write(self.journal, json.dumps(transaction).encode())
        save()
        try:
            # Re-plan sequentially so several adapters sharing one interfaces
            # file do not overwrite each other's removals.
            for plan in plans:
                device = plan['device']
                backup = rollback/device; backup.mkdir(mode=0o700)
                handoff.prepare(device, backup)
                transaction['applied'].append(str(backup)); save()
                handoff.write_files(backup)
                for path, data, mode in (
                    (self.connections/f'paimenos-debian-{plan["uuid"]}.nmconnection', (Path(plan['backup'])/'profile.nmconnection').read_bytes(), 0o600),
                    (self.policies/f'99-paimenos-handoff-{device}.conf', f'[device-paimenos-handoff-{device}]\nmatch-device=interface-name:{device}\nmanaged=1\n'.encode(), 0o644),
                ):
                    if path.is_symlink() or (path.exists() and not path.is_file()): raise ValueError(t('Ungültiges WLAN-Konfigurationsziel.'))
                    original = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
                    if original is not None: original = (base64.b64encode(original[0]).decode(), original[1])
                    transaction['written'].append((str(path), original)); save()
                    self.write(path, data, mode)
                unit = f'ifup@{device}.service'
                enabled = self.command('systemctl', 'is-enabled', unit, required=False).stdout.strip()
                if enabled not in ('masked','masked-runtime'):
                    transaction['masked'].append(unit); save(); self.command('systemctl', 'mask', unit)
                for unit in (f'wpa_supplicant@{device}.service', f'wpa_supplicant-nl80211@{device}.service'):
                    enabled = self.command('systemctl', 'is-enabled', unit, required=False).stdout.strip()
                    if enabled in ('enabled','enabled-runtime'):
                        transaction['disabled'].append((unit, enabled)); save()
                        args = ['systemctl'] + (['--runtime'] if enabled == 'enabled-runtime' else []) + ['disable', unit]
                        self.command(*args)
            # No nmcli activation, device ownership changes, sockets or service
            # restarts: the normal boot now starts exactly one WLAN owner.
            transaction['committed'] = True; save(); self.commit()
            print(t('WLAN-Konfiguration übernommen. NetworkManager startet anschließend mit dem gespeicherten Profil.'))
        except BaseException:
            self.rollback(transaction)
            # Avoid retrying a rejected transaction on each service activation.
            os.replace(self.pending, self.state/'failed.json')
            self.journal.unlink(missing_ok=True)
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare','apply'])
    action = parser.parse_args().action
    if os.geteuid() != 0: parser.error(t('Bitte als root ausführen.'))
    try:
        migration = Migration()
        if action == 'prepare': migration.prepare()
        else: migration.apply()
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(t('FEHLER: WLAN-Vorbereitung/Übernahme nicht abgeschlossen. Debian-Konfiguration wurde beibehalten bzw. zurückgesetzt. Grund: ') + str(exc), file=__import__('sys').stderr)
        return 1
    return 0

if __name__ == '__main__': raise SystemExit(main())
