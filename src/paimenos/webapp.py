"""Firefox-Webapp-Profile mit vereinfachter, nativer Navigation."""
from paimenos.i18n import t, browser_locale
import json
import os
import re
from paimenos.paths import ASSETS_DIR
from paimenos import hardware_profile, theme
from paimenos.state import read_settings
import tempfile

BAR_HEIGHT = 64
MENU_WIDTH = 160
CHROME_IMPORT = '@import url("paimenos-webapp.css");'
BEGIN = '// PaimenOS Webapp-Navigation: Anfang'
END = '// PaimenOS Webapp-Navigation: Ende'
PREFERENCES = '''user_pref("network.proxy.type", 0);
user_pref("cookiebanners.service.mode", 1);
user_pref("cookiebanners.service.mode.privateBrowsing", 1);
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
user_pref("browser.fullscreen.autohide", false);
user_pref("browser.tabs.drawInTitlebar", true);
user_pref("browser.startup.page", 0);
user_pref("browser.startup.couldRestoreSession.count", -1);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.sessionstore.resume_session_once", false);
user_pref("browser.sessionstore.resuming_after_os_restart", false);
user_pref("browser.warnOnQuit", false);
user_pref("browser.tabs.warnOnClose", false);
user_pref("browser.tabs.warnOnCloseOtherTabs", false);
user_pref("browser.cache.disk.enable", true);
user_pref("browser.cache.disk.smart_size.enabled", true);
user_pref("browser.cache.memory.enable", true);
user_pref("privacy.sanitize.sanitizeOnShutdown", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.newtabpage.enabled", false);
user_pref("browser.newtab.preload", false);
user_pref("browser.pagethumbnails.capturing_disabled", true);
user_pref("browser.newtabpage.activity-stream.feeds.system.topsites", false);
user_pref("browser.newtabpage.activity-stream.feeds.topsites", false);
user_pref("browser.preonboarding.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.link.open_newwindow", 1);
user_pref("browser.link.open_newwindow.restriction", 0);
// Kinder sollen nicht versehentlich Entwicklerwerkzeuge (F12, Strg+Umschalt+I/J/C/K),
// Cursor-Navigation (F7), Menüleiste (Alt), Drucken oder Bildschirmfotos öffnen.
user_pref("devtools.policy.disabled", true);
user_pref("devtools.chrome.enabled", false);
user_pref("accessibility.browsewithcaret_shortcut.enabled", false);
user_pref("accessibility.browsewithcaret", false);
user_pref("ui.key.menuAccessKeyFocuses", false);
user_pref("print.enabled", false);
user_pref("screenshots.browser.component.enabled", false);
'''


# Der Eltern-Bereich am Gerät läuft in einem Profil, das nichts behält: Die Anmeldung (Cookie, 30 Minuten gültig)
# darf das Schließen des Fensters nicht überleben, und die PIN darf nicht gespeichert werden.
PARENT_PREFERENCES = '''user_pref("signon.rememberSignons", false);
user_pref("signon.autofillForms", false);
user_pref("signon.generation.enabled", false);
user_pref("browser.formfill.enable", false);
user_pref("browser.cache.disk.enable", false);
user_pref("privacy.sanitize.sanitizeOnShutdown", true);
user_pref("privacy.clearOnShutdown.cookies", true);
user_pref("privacy.clearOnShutdown.cache", true);
user_pref("privacy.clearOnShutdown.sessions", true);
user_pref("privacy.clearOnShutdown.history", true);
user_pref("privacy.clearOnShutdown.offlineApps", true);
'''


def write_text(path, text):
    # Avoid rewriting and fsyncing three unchanged files on every launch.
    if read_text(path) == text:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.paimenos-webapp-', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_text(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return handle.read()
    except FileNotFoundError:
        return ''


LEGACY_PROXY = re.compile(r'^\s*user_pref\("network\.proxy\.[a-z_]+",.*\);\s*$', re.M)


def prepare_profile(profile_dir, template, extra_preferences=''):
    os.makedirs(profile_dir, exist_ok=True)
    user_path = os.path.join(profile_dir, 'user.js')
    current = read_text(template) if os.path.isfile(template) else read_text(user_path)
    if BEGIN in current and END in current:
        before, rest = current.split(BEGIN, 1)
        current = before + rest.split(END, 1)[1]
    # Früher lief der Browser über einen lokalen Squid-Proxy (127.0.0.1:3128); Vorlagen älterer
    # Installationen enthalten diese Einstellungen noch. Der Proxy ist abgeschaltet, also entfernen.
    current = LEGACY_PROXY.sub('', current)
    languages = 'en-US,en' if browser_locale() == 'en' else 'de-DE,de,en-US,en'
    locale_preferences = (
        'user_pref("intl.locale.requested", ' + json.dumps(browser_locale()) + ');\n'
        'user_pref("intl.accept_languages", ' + json.dumps(languages) + ');\n')
    write_text(user_path, current.rstrip() + '\n' + BEGIN + '\n'
               + PREFERENCES + locale_preferences + hardware_profile.firefox_preferences() + extra_preferences + END + '\n')
    chrome_path = os.path.join(profile_dir, 'chrome', 'userChrome.css')
    current = read_text(chrome_path)
    if CHROME_IMPORT not in current:
        write_text(chrome_path, CHROME_IMPORT + '\n' + current)
    style = read_text(str(ASSETS_DIR / 'webapp.css'))
    if not style:
        raise OSError(t('Die Webapp-Navigationsleiste fehlt. Bitte die gemeinsame Code-Komponente aktualisieren.'))
    try:
        color = read_settings().get('bg_color')
    except (OSError, ValueError):
        color = None
    top, bottom = theme.gradient(color)
    style = (':root { --paimenos-back-label: ' + json.dumps(t('Zurück'), ensure_ascii=False)
             + '; --paimenos-forward-label: ' + json.dumps(t('Weiter'), ensure_ascii=False)
             + '; --paimenos-bar-top: ' + top + '; --paimenos-bar-bottom: ' + bottom + '; }\n' + style)
    write_text(os.path.join(profile_dir, 'chrome', 'paimenos-webapp.css'), style)


def browser_command(profile_dir, url):
    # Openbox maximiert das Fenster; Firefox reserviert selbst den Leistenplatz.
    return ['firefox-esr', '--no-remote', '--profile', profile_dir, url]
