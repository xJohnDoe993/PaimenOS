#!/usr/bin/python3
"""Offline validation. No installation, network or device access."""
import ast
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('deploy', ROOT / 'tools/deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
manifest = deploy.validate_source(ROOT)
theme_spec = importlib.util.spec_from_file_location('plymouth_theme', ROOT / 'tools/install-paimenos-plymouth.py')
theme = importlib.util.module_from_spec(theme_spec)
theme_spec.loader.exec_module(theme)
theme.validate_assets()
python_files = sorted([ROOT / 'run.py', *(ROOT / 'src').rglob('*.py'), *(ROOT / 'tools').glob('*.py'), *(ROOT / 'tests').glob('*.py'), *(ROOT / 'iso').glob('*.py'), *(ROOT / '.github/scripts').glob('*.py')])
for path in python_files:
    compile(path.read_bytes(), str(path), 'exec')
    ast.parse(path.read_text())
shell_files = [*ROOT.glob('*.sh'), *ROOT.joinpath('installer').glob('*.sh'), *ROOT.joinpath('iso').glob('*.sh'), *ROOT.joinpath('tests/integration').glob('*.sh'), *ROOT.joinpath('bin').glob('*'), *ROOT.joinpath('sbin').glob('*'), ROOT / 'config/openbox/autostart']
for path in shell_files:
    subprocess.run(['bash', '-n', str(path)], check=True)
for path in [*ROOT.joinpath('data').glob('*.json'), ROOT / 'data/system-config/firefox/policies.json']:
    json.loads(path.read_text())
for path in [ROOT / 'config/openbox/rc.xml', *ROOT.joinpath('assets/icons').glob('*.svg')]:
    ET.parse(path)
# Imported package files and symbols must exist after extraction.
module_trees = {p.stem: ast.parse(p.read_text()) for p in (ROOT / 'src/paimenos').glob('*.py')}
def bindings(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
    return names
for path in (ROOT / 'src/paimenos').glob('*.py'):
    for node in ast.walk(module_trees[path.stem]):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('paimenos.'):
            name = node.module.split('.')[1]
            assert name in module_trees, (path.name, name)
            for alias in node.names:
                assert alias.name in bindings(module_trees[name]), (path.name, name, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith('paimenos.'):
                    assert alias.name.split('.')[1] in module_trees, (path.name, alias.name)
# All service launcher targets are allowlisted, and privileged code never comes from the child home.
launcher = ast.parse((ROOT / 'run.py').read_text())
allowed = next(ast.literal_eval(n.value) for n in launcher.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ALLOWED' for t in n.targets))
for path in (ROOT / 'systemd').rglob('*.service'):
    text = path.read_text()
    for name in re.findall(r'current/run\.py ([a-z_]+)', text):
        assert name in allowed and name in module_trees, (path.name, name)
    assert '/home/kids/.config/openbox/' not in text, path
for node in ast.walk(module_trees['parent_web']):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_template' and node.args:
        assert (ROOT / 'assets/parent-web' / ast.literal_eval(node.args[0])).is_file()
# Installer paths must refer to existing tracked files.
for path in (ROOT / 'installer').glob('*.sh'):
    for name in re.findall(r'(?:install_repo_file|render_repo_file) ([\w./-]+)', path.read_text()):
        assert (ROOT / name).is_file(), (path.name, name)
# All required project services must be installed before their dependents start.
installed_units = set()
for path in (ROOT / 'installer').glob('*.sh'):
    installed_units.update(re.findall(r'install_repo_file systemd/system/([\w.-]+)', path.read_text()))
for name in installed_units:
    text = (ROOT / 'systemd/system' / name).read_text()
    for line in text.splitlines():
        if line.startswith(('Requires=', 'Requisite=', 'BindsTo=')):
            for dependency in line.split('=', 1)[1].split():
                if dependency.startswith('paimenos-'):
                    assert dependency in installed_units, f'Installer: {name} benötigt fehlenden Dienst {dependency}'
print(f'OK: {len(python_files)} Python-Dateien, {len(shell_files)} Shell-Dateien, Daten, Ressourcen, Importe und Dienstpfade.')
subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'), '-v'], cwd=ROOT, check=True)
