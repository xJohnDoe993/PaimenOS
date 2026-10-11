"""Exercise actual decoded video and X11 input, outside the offscreen unit suite."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from PyQt5 import sip
from PyQt5.QtWidgets import QApplication, QWidget
from Xlib import X, XK, display
from Xlib.ext import xtest
from paimenos import menu, menu_camera


def main():
    if os.environ.get('QT_QPA_PLATFORM') != 'xcb':
        raise SystemExit('This test requires QT_QPA_PLATFORM=xcb and an X11 display.')
    app = QApplication([])
    connection = display.Display()
    host = QWidget()
    with patch.object(menu_camera, 'read_settings', return_value={'bg_color': '#FF9F00'}), \
            patch.object(menu_camera, 'media_roots', return_value=[]), patch.object(menu_camera, 'submit_task'):
        browser = menu.CameraBrowser(host)

    def pump(seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            app.processEvents()
            time.sleep(.01)

    def key(name):
        code = connection.keysym_to_keycode(XK.string_to_keysym(name))
        xtest.fake_input(connection, X.KeyPress, code)
        xtest.fake_input(connection, X.KeyRelease, code)
        connection.sync()
        pump(.15)

    def double_click():
        surface = browser.video_viewer.surface
        point = surface.mapToGlobal(surface.rect().center())
        xtest.fake_input(connection, X.MotionNotify, x=point.x(), y=point.y())
        connection.sync()
        pump(.1)
        for _ in range(2):
            xtest.fake_input(connection, X.ButtonPress, 1)
            xtest.fake_input(connection, X.ButtonRelease, 1)
            connection.sync()
            pump(.06)
        pump(.15)

    try:
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / 'input.avi'
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                            '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=10', '-t', '30',
                            '-c:v', 'mjpeg', '-threads', '1', '-pix_fmt', 'yuvj420p',
                            str(clip)], check=True)
            host.showFullScreen()
            browser.images = [str(clip)]
            browser.showFullScreen()
            browser.activateWindow()
            browser.preview(str(clip))
            viewer = browser.video_viewer
            assert viewer.player is not None, 'Qt Multimedia unavailable'
            deadline = time.monotonic() + 10
            while viewer.player.position() < 500 and not viewer.failed and time.monotonic() < deadline:
                pump(.1)
            assert not viewer.failed, viewer.player.errorString()
            assert viewer.player.position() >= 500, 'Actual playback did not start'
            image = viewer.surface.grab().toImage()
            colors = {image.pixel(x, y) for x in range(0, image.width(), 20)
                      for y in range(0, image.height(), 20)}
            assert len(colors) > 6, 'No decoded test frame rendered: colors=' + str(len(colors))
            player = viewer.player
            position = player.position()

            double_click()
            assert viewer.video_fullscreen, 'Double-click did not enter fullscreen'
            assert viewer.surface.size() == browser.size(), 'Video does not fill screen'
            double_click()
            assert not viewer.video_fullscreen, 'Double-click did not exit fullscreen'
            key('F11')
            assert viewer.video_fullscreen, 'F11 did not enter fullscreen'
            key('F11')
            assert not viewer.video_fullscreen, 'F11 did not exit fullscreen'
            key('F11')
            key('Escape')
            assert not viewer.video_fullscreen and viewer.active, 'Esc must restore controls first'
            assert browser.stack.currentWidget() is viewer
            assert player.state() == viewer.player_type.PlayingState
            assert player.position() > position, 'Fullscreen change interrupted playback'
            key('space')
            assert player.state() == viewer.player_type.PausedState, 'Space did not pause'
            key('space')
            assert player.state() == viewer.player_type.PlayingState, 'Space did not resume'
            key('Escape')
            assert not viewer.active and browser.stack.currentWidget() is browser.overview
            print('OK: decoded frames, X11 double-click, F11, Esc, pause/resume and continuous playback.')
    finally:
        browser.done(0)
        host.close()
        connection.close()
        sip.delete(browser)
        sip.delete(host)
        sip.delete(app)


if __name__ == '__main__':
    main()
