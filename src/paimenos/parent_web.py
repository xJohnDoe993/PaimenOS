#!/usr/bin/env python3
"""Elternverwaltung im lokalen Netzwerk."""
from paimenos.i18n import t, language, regional_locale, english_catalog
from paimenos.paths import CONFIG_DIR as CONFIG_ROOT
import hashlib
import hmac
import os
import re
import secrets
import shutil
import threading
import time
from paimenos.paths import ASSETS_DIR, STATE_DIR

def _template(name):
    return (ASSETS_DIR / "parent-web" / name).read_text(encoding="utf-8")

from datetime import timedelta
from functools import wraps
from flask import Flask, abort, flash, redirect, render_template_string, request, session, url_for, send_file, Response, jsonify
import paimenos.parent as parents
import paimenos.emulators as emulators
from paimenos.emulator_install import install_request, InstallError
import paimenos.flatpak_store as flatpak_store
import paimenos.controller_profiles as controllers
from paimenos.pronunciation import default_pronunciation
from paimenos.state import read_settings
from paimenos.diagnostics import collect_diagnostics, diagnostics_text
from paimenos.bluetooth import bluetooth_request, BluetoothError
from paimenos.wifi import wifi_request, WifiError
from paimenos.updates import update_request, UpdateError
from paimenos.printing import print_request, print_document, PrintError

CONFIG_DIR = str(CONFIG_ROOT)
ICONS_DIR = parents.ICONS_DIR
HOST, PORT = '0.0.0.0', 80
app = Flask(__name__)
os.makedirs(CONFIG_DIR, exist_ok=True)
secret_file = os.path.join(CONFIG_DIR, '.parent-web-secret')
try:
    fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    pass
else:
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(secrets.token_hex(32))
with open(secret_file, encoding='utf-8') as handle:
    app.secret_key = handle.read().strip()
if len(app.secret_key) < 32:
    raise ValueError(t('Web-Sitzungsschlüssel fehlt oder ist beschädigt.'))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                  PERMANENT_SESSION_LIFETIME=timedelta(minutes=30), MAX_CONTENT_LENGTH=(9 * 1024 + 1) * 1024 * 1024)
attempts = {}
attempts_lock = threading.Lock()


def pin_fingerprint():
    return hmac.new(app.secret_key.encode(), read_settings()['pin'].encode(), 'sha256').hexdigest()


def logged_in():
    return bool(session.get('parent_authenticated')) and hmac.compare_digest(
        session.get('pin_fingerprint', ''), pin_fingerprint())


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not logged_in():
            return redirect(url_for('login', next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


app.jinja_env.globals.update(t=t, language=language, regional_locale=regional_locale, csrf_token=csrf_token, duration=parents.duration, app_active=parents.app_active)


@app.before_request
def check_csrf():
    if request.method == 'POST':
        expected, supplied = session.get('csrf_token', ''), request.form.get('csrf_token', '')
        if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
            abort(400, t('Formular abgelaufen. Bitte Seite neu laden.'))


@app.after_request
def response_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


@app.context_processor
def translation_context():
    return {'translation_catalog': english_catalog() if language() == 'en' else {}}


BASE = _template('base.html')


@app.get('/branding/logo.png')
def brand_logo():
    # Public on the PIN page too; expose only this bundled asset.
    return send_file(ASSETS_DIR / 'branding/paimenos-logo.png', mimetype='image/png')


def render(body, title=None, section='dashboard'):
    title = t('Übersicht') if title is None else title
    field = '<input type="hidden" name="csrf_token" value="' + csrf_token() + '">'
    body = re.sub(r'(<form\b[^>]*method="post"[^>]*>)', lambda m: m.group(1) + field, body)
    # Grouped like the local parent area: child, connections, system.
    nav = [[(t('Übersicht'), 'dashboard'), (t('Bildschirmzeit'), 'time_page'), ('Apps', 'apps_page'), (t('Emulatoren'), 'emulators_page'), (t('Flatpak-Apps'), 'flatpaks_page')],
           [(t('WLAN'), 'wifi_page'), ('Bluetooth', 'bluetooth_page'), ('Controller', 'controllers_page'), (t('Drucken'), 'printing_page')],
           [(t('USB-Backups'), 'backups_page'), ('Updates', 'updates_page'), (t('Einstellungen'), 'settings_page'), (t('Diagnose'), 'diagnostics_page')]]
    return render_template_string(BASE, body=body, title=title, section=section,
                                  authenticated=logged_in(), navigation=nav)


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(503)
def http_error(error):
    return render(render_template_string(_template('http-error.html'), code=error.code, message=error.description), t('Hinweis')), error.code


@app.errorhandler(413)
def upload_too_large(error):
    return render(render_template_string(_template('upload-error.html')), t('Upload zu groß'), 'apps_page'), 413


@app.errorhandler(OSError)
@app.errorhandler(ValueError)
def data_error(error):
    app.logger.error(t('Elternverwaltung: %s'), error)
    # Ohne settings() rendern, falls genau diese Datei beschädigt ist.
    return render_template_string(BASE, body=render_template_string(_template('data-error.html')), title=t('Datenfehler'), authenticated=False), 503


@app.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.values.get('next') or url_for('dashboard')
    parsed = parents.urllib.parse.urlsplit(next_url)
    if parsed.netloc or parsed.scheme or not next_url.startswith('/') or next_url.startswith('//') or '\\' in next_url or re.search(r'[\x00-\x20]', next_url):
        next_url = url_for('dashboard')
    status = 200
    if request.method == 'POST':
        address, now = request.remote_addr or 'local', time.monotonic()
        with attempts_lock:
            for key in list(attempts):
                if now - attempts[key][1] >= 60:
                    del attempts[key]
            count, started = attempts.get(address, (0, now))
            blocked = count >= 5
            if not blocked:
                attempts[address] = (count + 1, started)
        if blocked:
            flash(t('Zu viele Versuche. Bitte nach einer Minute erneut versuchen.'), 'error')
            status = 429
        elif hmac.compare_digest(request.form.get('pin', '').encode(), read_settings()['pin'].encode()):
            with attempts_lock:
                attempts.pop(address, None)
            session.clear()
            session.update(parent_authenticated=True, pin_fingerprint=pin_fingerprint())
            session.permanent = True
            return redirect(next_url)
        else:
            flash(t('Die Eltern-PIN stimmt nicht.'), 'error')
    body = render_template_string(_template('login.html'), next_url=next_url)
    response = app.make_response((render(body, t('Anmeldung')), status))
    if status == 429:
        response.headers['Retry-After'] = '60'
    return response


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


BONUS_BUTTONS = _template('bonus-buttons.html')
STATS = _template('stats.html')


@app.route('/updates')
@login_required
def updates_page():
    return render(render_template_string(_template('updates.html')), 'Updates', 'updates_page')


@app.route('/updates/status')
def updates_status():
    if not logged_in():
        return jsonify(ok=False, error=t('Bitte anmelden.')), 401
    try:
        return jsonify(update_request())
    except UpdateError as exc:
        return jsonify(ok=False, error=str(exc)), 503


@app.route('/updates/check', methods=['POST'], endpoint='updates_check')
@app.route('/updates/install', methods=['POST'], endpoint='updates_install')
def updates_action():
    if not logged_in():
        return jsonify(ok=False, error=t('Bitte anmelden.')), 401
    action = 'install' if request.path.endswith('/install') else 'check'
    try:
        force_source = request.form.get('force_source', '')
        if force_source not in ('', '1'):
            raise UpdateError(t('Ungültige Quellenbestätigung.'))
        tag = request.form.get('tag') if action == 'install' else None
        return jsonify(update_request(action, tag, force_source=True) if force_source else update_request(action, tag))
    except UpdateError as exc:
        return jsonify(ok=False, error=str(exc)), 409


@app.route('/')
@login_required
def dashboard():
    st, items = read_settings(), parents.managed_apps()
    info = parents.usage(st)
    active = sum(parents.app_active(a, st) for a in items)
    body = render_template_string(_template('dashboard-head.html') + STATS + _template('dashboard-summary.html') + BONUS_BUTTONS + _template('dashboard-footer.html'), info=info, active=active, count=len(items), third_label=t('Apps freigegeben'), third_value=f'{active} / {len(items)}', return_to='dashboard')
    return render(body)


@app.route('/emulators/diagnostics')
@login_required
def emulator_diagnostics():
    body = render_template_string(_template('emulator-diagnostics.html'), report=emulators.emulator_log())
    return render(body, t('Emulator-Diagnose'), 'emulators_page')


@app.route('/emulators/diagnostics/download')
@login_required
def emulator_diagnostics_download():
    return Response(emulators.emulator_log(), mimetype='text/plain', headers={'Content-Disposition': 'attachment; filename="PaimenOS-Emulator-Diagnose.txt"'})


@app.route('/emulators/install/status')
def emulator_install_status():
    if not logged_in():
        return jsonify(ok=False, error=t('Bitte anmelden.')), 401
    try:
        return jsonify(install_request())
    except InstallError as exc:
        return jsonify(ok=False, error=str(exc)), 503


@app.route('/emulators/install', methods=['POST'])
def emulator_install_action():
    if not logged_in():
        return jsonify(ok=False, error=t('Bitte anmelden.')), 401
    wants_json = request.accept_mimetypes.best == 'application/json'
    try:
        keys = request.form.getlist('systems')
        if not keys or len(keys) > len(emulators.SYSTEMS) or any(key not in emulators.SYSTEMS for key in keys):
            raise ValueError(t('Bitte gültige Konsolen auswählen.'))
        result = install_request('install', keys)
        if wants_json:
            return jsonify(result)
        flash(t('Installation gestartet. Fortschritt wird auf der Emulator-Seite angezeigt.'), 'success')
    except (ValueError, InstallError) as exc:
        if wants_json:
            return jsonify(ok=False, error=str(exc)), 400
        flash(str(exc), 'error')
    return redirect(url_for('emulators_page'))


@app.route('/flatpaks')
@login_required
def flatpaks_page():
    body = render_template_string(_template('flatpaks.html'), installed=flatpak_store.installed())
    return render(body, t('Flatpak-Apps'), 'flatpaks_page')


@app.get('/flatpaks/search')
def flatpak_search():
    if not logged_in():
        return jsonify(ok=False, error=t('Bitte anmelden.')), 401
    try:
        results = flatpak_store.search(request.args.get('q', ''))
    except flatpak_store.StoreError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    installed = {item['app_id'] for item in flatpak_store.installed()}
    return jsonify(ok=True, results=[dict(item, installed=item['app_id'] in installed) for item in results])


@app.get('/flatpaks/status')
def flatpak_status():
    if not logged_in():
        return jsonify(ok=False, error=t('Bitte anmelden.')), 401
    try:
        return jsonify({**install_request('flatpak_status'), 'installed': flatpak_store.installed()})
    except InstallError as exc:
        return jsonify(ok=False, error=str(exc)), 503


def _flatpak_job(action, **fields):
    if not logged_in():
        return jsonify(ok=False, error=t('Bitte anmelden.')), 401
    app_id = request.form.get('app_id', '')
    if not flatpak_store.valid_app_id(app_id):
        return jsonify(ok=False, error=t('Ungültige Flatpak-App-ID.')), 400
    try:
        return jsonify(install_request(action, app_id=app_id, **fields))
    except InstallError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post('/flatpaks/install')
def flatpak_install_action():
    return _flatpak_job('flatpak_install')


@app.post('/flatpaks/remove')
def flatpak_remove_action():
    return _flatpak_job('flatpak_remove', delete_data=request.form.get('delete_data') == '1')


@app.route('/emulators', methods=['GET', 'POST'])
@login_required
def emulators_page():
    if request.method == 'POST':
        try:
            if request.form.get('action') == 'bios':
                upload = request.files.get('bios')
                if not upload or not upload.filename:
                    raise ValueError(t('Bitte eine BIOS-Datei auswählen.'))
                emulators.upload_bios(upload)
                flash(t('BIOS gespeichert.'), 'success')
            else:
                emulators.upload_game(request.form.get('system', ''), request.form.get('title', ''), request.files.getlist('roms'))
                flash(t('Spiel hochgeladen. Die Kachel erscheint automatisch im Kinder-Menü.'), 'success')
            return redirect(url_for('emulators_page'))
        except (ValueError, OSError, UnicodeError) as exc:
            flash(str(exc), 'error')
    body = render_template_string(_template('emulators.html'), any_ready=any(item['ready'] for item in emulators.status()), systems=emulators.status(), accepts=','.join(sorted(set(ext for value in emulators.SYSTEMS.values() for ext in value[2]) | {'.bin', '.sbi', '.zip'})), bios_report=emulators.bios_inventory(), bios=[name for name in emulators.BIOS if (emulators.ROOT / 'bios' / name).is_file()])
    return render(body, t('Emulatoren'), 'emulators_page')


@app.route('/apps')
@login_required
def apps_page():
    st, items = read_settings(), parents.managed_apps()
    body = render_template_string(_template('apps.html'), st=st, items=items, editable_app=parents.editable_app, deletable_app=parents.deletable_app, app_source_label=parents.app_source_label)
    return render(body, 'Apps', 'apps_page')


@app.route('/icons/<path:name>')
@login_required
def icon(name):
    if '/' in name or '\\' in name or name.startswith('.'):
        abort(404)
    path = os.path.join(ICONS_DIR, name)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path)


@app.route('/apps/<app_id>/toggle', methods=['POST'])
@login_required
def app_toggle(app_id):
    parents.toggle_app(app_id)
    flash(t('App-Freigabe gespeichert.'))
    return redirect(url_for('apps_page'))


@app.route('/apps/bulk', methods=['POST'])
@login_required
def apps_bulk():
    active = request.form.get('active')
    if active not in ('0', '1'):
        abort(400, t('Ungültige Freigabe.'))
    parents.set_apps_active([a['id'] for a in parents.managed_apps()], active == '1')
    flash(t('App-Freigaben gespeichert.'))
    return redirect(url_for('apps_page'))


def app_form(item, heading):
    target, arguments = parents.app_launch_fields(item)
    target = item.get('target', target)
    arguments = item.get('arguments', arguments)
    body = render_template_string(_template('app-form.html'), item=item, heading=heading, target=target, arguments=arguments,
                                 default_pronunciation=default_pronunciation(item.get('title', '')))
    return render(body, heading, 'apps_page')


def submitted_app(item=None):
    values = {k: request.form.get(k, '') for k in ('title', 'target', 'arguments', 'icon_url')}
    # Alte /webapps-Formulare bleiben nutzbar.
    if 'target' not in request.form:
        values['target'] = request.form.get('url', '')
    if item and (item.get('type') == 'camera' or item.get('emulator')):
        values.update(target='', arguments='')
    values['category'] = request.form.get('category', 'auto')
    # Alte Formulare ohne Feld lassen eine gespeicherte Aussprache unverändert.
    values['pronunciation'] = request.form['pronunciation'] if 'pronunciation' in request.form else None
    values['reset_icon'] = request.form.get('reset_icon') == '1'
    return values


def submitted_icon():
    uploaded = request.files.get('icon_upload')
    if uploaded and uploaded.filename:
        return uploaded.read(parents.MAX_ICON_BYTES + 1)
    return None


@app.route('/webapps/new', methods=['GET', 'POST'])
@app.route('/apps/new', methods=['GET', 'POST'])
@login_required
def app_new():
    item = {}
    if request.method == 'POST':
        item = submitted_app()
        try:
            parents.save_app(**item, icon_data=submitted_icon())
        except (ValueError, OSError, TimeoutError) as exc:
            flash(str(exc), 'error')
        else:
            flash(t('App hinzugefügt.'))
            return redirect(url_for('apps_page'))
    return app_form(item, t('App hinzufügen'))


@app.route('/webapps/<app_id>/edit', methods=['GET', 'POST'])
@app.route('/apps/<app_id>/edit', methods=['GET', 'POST'])
@login_required
def app_edit(app_id):
    item = next((a for a in parents.read_apps() if a.get('id') == app_id and parents.editable_app(a)), None)
    if item is None:
        abort(404)
    if request.method == 'POST':
        values = submitted_app(item)
        try:
            parents.save_app(**values, app_id=app_id, icon_data=submitted_icon())
        except (ValueError, OSError, TimeoutError) as exc:
            item = dict(item, **values)
            flash(str(exc), 'error')
        else:
            flash(t('App gespeichert.'))
            return redirect(url_for('apps_page'))
    return app_form(item, t('App bearbeiten'))


@app.route('/webapps/<app_id>/delete', methods=['GET', 'POST'])
@app.route('/apps/<app_id>/delete', methods=['GET', 'POST'])
@login_required
def app_delete(app_id):
    item = next((a for a in parents.read_apps() if a.get('id') == app_id and parents.deletable_app(a)), None)
    if item is None:
        abort(404)
    if request.method == 'POST':
        parents.delete_app(app_id)
        flash(t('App aus dem Kinder-Menü entfernt. Bei Emulator-Spielen werden auch die hochgeladenen ROM-Dateien entfernt; Spielstände bleiben erhalten. Installierte Programme und Browserprofile bleiben erhalten.'))
        return redirect(url_for('apps_page'))
    body = render_template_string(_template('app-delete.html'), item=item)
    return render(body, t('App löschen'), 'apps_page')


@app.route('/time', methods=['GET', 'POST'])
@login_required
def time_page():
    if request.method == 'POST':
        try:
            parents.set_time(request.form.get('daily_limit_minutes'))
        except ValueError as exc:
            flash(str(exc), 'error')
        else:
            flash(t('Tageslimit gespeichert.'))
        return redirect(url_for('time_page'))
    info = parents.usage()
    body = render_template_string(_template('time-head.html') + STATS + _template('time-limit-form.html') + BONUS_BUTTONS + _template('time-bonus-form.html'), info=info, third_label=t('Bonus heute'), third_value=t('{value0} Min.', value0=info['bonus']), return_to='time_page')
    return render(body, t('Bildschirmzeit'), 'time_page')


@app.route('/bonus', methods=['POST'])
@login_required
def bonus():
    try:
        added = parents.add_bonus(request.form.get('minutes'))
    except ValueError as exc:
        flash(str(exc), 'error')
    else:
        flash(t('{value0} Minuten Bonus hinzugefügt.', value0=added) if added else t('Die maximale Bonuszeit von 600 Minuten ist erreicht.'))
    endpoint = 'time_page' if request.form.get('return_to') == 'time_page' else 'dashboard'
    return redirect(url_for(endpoint))


@app.route('/bonus/clear', methods=['POST'])
@login_required
def clear_bonus():
    parents.clear_bonus()
    flash(t('Bonuszeit entfernt.'))
    return redirect(url_for('time_page'))


@app.route('/time/reset', methods=['POST'])
@login_required
def reset_usage():
    parents.reset_today()
    flash(t('Heutiger Verbrauch und Bonus zurückgesetzt.'))
    return redirect(url_for('time_page'))


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings_page():
    st = read_settings()
    if request.method == 'POST':
        try:
            st = parents.save_preferences(request.form.get('pin', '').strip(), request.form.get('pin_confirm', '').strip(), request.form.get('bg_color', ''), request.form.get('category_tabs') == '1', request.form.get('speak_titles') == '1', request.form.get('speak_voice') or None)
        except ValueError as exc:
            flash(str(exc), 'error')
        else:
            session['pin_fingerprint'] = pin_fingerprint()
            flash(t('Einstellungen gespeichert. Andere Web-Sitzungen müssen sich nach einer PIN-Änderung neu anmelden.'))
            return redirect(url_for('settings_page'))
    body = render_template_string(_template('settings.html'), st=st)
    return render(body, t('Einstellungen'), 'settings_page')


BLUETOOTH_PAGE = _template('bluetooth-page.html')




CONTROLLERS_PAGE = _template('controllers-page.html')

@app.route('/controllers')
@login_required
def controllers_page():
    return render(render_template_string(CONTROLLERS_PAGE), 'Controller', 'controllers_page')


@app.route('/controllers/status')
def controllers_status():
    if not logged_in():
        return jsonify(error=t('Bitte zuerst im Elternbereich anmelden.')), 401
    try:
        return jsonify(controllers.status())
    except controllers.ProfileError as exc:
        return jsonify(error=str(exc)), 400


@app.route('/controllers/action', methods=['POST'])
def controllers_action():
    if not logged_in():
        return jsonify(error=t('Bitte zuerst im Elternbereich anmelden.')), 401
    if 'controller_owner' not in session:
        session['controller_owner'] = secrets.token_hex(24)
    owner = session['controller_owner']
    action = request.form.get('action', '')
    token = request.form.get('token', '')
    try:
        if action == 'start':
            return jsonify(controllers.calibration.start(request.form.get('device', ''), owner))
        if action == 'reset':
            if controllers.calibration_active():
                raise controllers.ProfileError(t('Bitte die geöffnete Konfiguration zuerst schließen.'))
            controllers.reset_profile(request.form.get('profile', ''))
            return jsonify(reset=True)
        if action == 'capture':
            return jsonify(controllers.calibration.capture(token, owner, request.form.get('control', '')))
        return jsonify(controllers.calibration.action(action, token, owner, request.form.get('control', '')))
    except controllers.ProfileError as exc:
        return jsonify(error=str(exc)), 400

@app.route('/bluetooth')
@login_required
def bluetooth_page():
    return render(render_template_string(BLUETOOTH_PAGE), 'Bluetooth', 'bluetooth_page')


@app.route('/bluetooth/status')
def bluetooth_status():
    if not logged_in():
        return jsonify(error=t('Bitte im Elternbereich anmelden.')), 401
    try:
        return jsonify(bluetooth_request())
    except BluetoothError as exc:
        return jsonify(error=str(exc)), 503 if exc.unavailable else 409


@app.route('/bluetooth/action', methods=['POST'])
def bluetooth_action():
    if not logged_in():
        return jsonify(error=t('Bitte im Elternbereich anmelden.')), 401
    action = request.form.get('action', '')
    if action not in ('power_on', 'power_off', 'scan', 'stop_scan', 'pair', 'connect',
                      'disconnect', 'remove', 'cancel', 'answer'):
        return jsonify(error=t('Unbekannte Bluetooth-Aktion.')), 400
    values = {key: request.form[key] for key in ('adapter', 'device', 'prompt_id', 'accept', 'value') if key in request.form}
    try:
        return jsonify(bluetooth_request(action, **values))
    except BluetoothError as exc:
        return jsonify(error=str(exc)), 503 if exc.unavailable else 409


WIFI_PAGE = _template('wifi-page.html')


@app.route('/wifi')
@login_required
def wifi_page():
    return render(render_template_string(WIFI_PAGE), t('WLAN'), 'wifi_page')


@app.route('/wifi/status')
def wifi_status():
    if not logged_in():
        return jsonify(error=t('Bitte im Elternbereich anmelden.')), 401
    try:
        return jsonify(wifi_request())
    except WifiError as exc:
        return jsonify(error=str(exc)), 503 if exc.unavailable else 409


@app.route('/wifi/action', methods=['POST'])
def wifi_action():
    if not logged_in():
        return jsonify(error=t('Bitte im Elternbereich anmelden.')), 401
    action = request.form.get('action', '')
    if action not in ('power_on', 'power_off', 'scan', 'connect', 'hidden', 'disconnect', 'forget', 'autoconnect', 'cancel'):
        return jsonify(error=t('Unbekannte WLAN-Aktion.')), 400
    values = {key:request.form[key] for key in ('adapter', 'network', 'profile', 'ssid', 'security', 'password', 'enabled') if key in request.form}
    try:
        return jsonify(wifi_request(action, **values))
    except WifiError as exc:
        return jsonify(error=str(exc)), 503 if exc.unavailable else 409


NETWORK_EDITOR_REQUEST = 'paimenos-menu.network-editor'


@app.route('/wifi/advanced', methods=['POST'])
def wifi_advanced():
    """Der Netzwerkeditor (Firmen-/Schulnetze, VPN) ist ein Fenster am Laptop: Das Kindermenü öffnet es auf Anfrage."""
    if not logged_in():
        return jsonify(error=t('Bitte im Elternbereich anmelden.')), 401
    if not shutil.which('nm-connection-editor'):
        return jsonify(error=t('Der Netzwerkeditor fehlt. Bitte das WLAN-Update installieren.')), 409
    try:
        (STATE_DIR / NETWORK_EDITOR_REQUEST).touch()
    except OSError:
        return jsonify(error=t('Der Netzwerkeditor konnte nicht angefordert werden.')), 503
    return jsonify(message=t('Der Netzwerkeditor öffnet sich am Laptop.'))


PRINTING_PAGE = _template('printing-page.html')
PRINT_ACTIONS = {'mode': ('mode',), 'release': ('job',), 'cancel': ('job',), 'add': ('title', 'uri'),
                 'remove': ('printer',), 'default': ('printer',), 'test': ('printer',), 'discover': ()}


@app.route('/printing')
@login_required
def printing_page():
    return render(render_template_string(PRINTING_PAGE), t('Drucken'), 'printing_page')


@app.route('/printing/status')
def printing_status():
    if not logged_in():
        return jsonify(error=t('Bitte im Elternbereich anmelden.')), 401
    try:
        return jsonify(print_request())
    except PrintError as exc:
        return jsonify(error=str(exc)), 503 if exc.unavailable else 409


@app.route('/printing/action', methods=['POST'])
def printing_action():
    if not logged_in():
        return jsonify(error=t('Bitte im Elternbereich anmelden.')), 401
    action = request.form.get('action', '')
    if action not in PRINT_ACTIONS:
        return jsonify(error=t('Unbekannte Druckaktion.')), 400
    values = {key: request.form[key] for key in PRINT_ACTIONS[action] if key in request.form}
    try:
        return jsonify(print_request(action, **values))
    except PrintError as exc:
        return jsonify(error=str(exc)), 503 if exc.unavailable else 409


@app.route('/printing/jobs/<int:job>/document')
@login_required
def printing_document(job):
    try:
        data, mime, name, inline = print_document(job)
    except PrintError as exc:
        abort(503 if exc.unavailable else 404, str(exc))
    disposition = ('inline' if inline else 'attachment') + '; filename="' + name + '"'
    return Response(data, mimetype=mime, headers={'Content-Disposition': disposition})


@app.route('/diagnostics')
@login_required
def diagnostics_page():
    body = render_template_string(_template('diagnostics.html'), text=diagnostics_text(collect_diagnostics()))
    return render(body, t('Diagnose'), 'diagnostics_page')


@app.route('/backups', methods=['GET', 'POST'])
@login_required
def backups_page():
    from paimenos import backups
    if request.method == 'POST':
        try:
            action = request.form.get('action', '')
            if action == 'restore' and request.form.get('confirm') != 'yes':
                raise ValueError(t('Bitte das Ersetzen vorhandener Dateien bestätigen.'))
            backups.start(action, request.form.get('device', ''), request.form.getlist('groups'),
                          request.form.get('backup', ''))
            flash(t('Backup-Vorgang gestartet.'))
        except (ValueError, OSError) as exc:
            flash(str(exc), 'error')
        return redirect(url_for('backups_page', device=request.form.get('device', '')))
    devices = backups.drives()
    selected = request.args.get('device') or (devices[0]['id'] if devices else '')
    found = []
    if selected:
        try:
            found = backups.available(selected)
        except (ValueError, OSError) as exc:
            flash(str(exc), 'error')
    return render(render_template_string(_template('backups.html'), devices=devices, selected=selected,
                  found=found, labels=backups.labels(), offered=backups.offered(), job=backups.job_status()),
                  t('USB-Backups'), 'backups_page')


@app.route('/backups/status')
@login_required
def backups_status():
    from paimenos.backups import job_status
    return jsonify(job_status())


@app.route('/diagnostics/download')
@login_required
def diagnostics_download():
    return Response(diagnostics_text(collect_diagnostics()), mimetype='text/plain',
                    headers={'Content-Disposition': 'attachment; filename="PaimenOS-Diagnose.txt"'})


if __name__ == '__main__':
    from waitress import serve
    serve(app, host=HOST, port=PORT, threads=4, connection_limit=32,
          channel_timeout=300, max_request_body_size=app.config['MAX_CONTENT_LENGTH'],
          max_request_header_size=16384, expose_tracebacks=False)
