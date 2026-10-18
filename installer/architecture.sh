#!/bin/bash
# Von install.sh geladen: erkennt CPU-Architektur und Raspberry Pi, bevor das System geändert wird.
# Setzt PAIMENOS_ARCH (amd64|arm64|armhf), PAIMENOS_IS_ARM (0|1) und PAIMENOS_IS_RPI (0|1).
PAIMENOS_ARCH=$(dpkg --print-architecture)
case "$PAIMENOS_ARCH" in
    amd64) PAIMENOS_IS_ARM=0 ;;
    arm64) PAIMENOS_IS_ARM=1 ;;
    armhf) PAIMENOS_IS_ARM=1 ;;
    *)
        echo "$(paimenos_text 'FEHLER: Architektur {value0} wird nicht unterstützt (amd64, arm64, armhf).' "$PAIMENOS_ARCH")" >&2
        exit 1
        ;;
esac
PAIMENOS_IS_RPI=0
if [[ "$PAIMENOS_IS_ARM" == 1 ]] && { [[ -e /etc/rpi-issue ]] || grep -aqi 'raspberry pi' /proc/device-tree/model 2>/dev/null; }; then
    PAIMENOS_IS_RPI=1
fi
export PAIMENOS_ARCH PAIMENOS_IS_ARM PAIMENOS_IS_RPI
if [[ "$PAIMENOS_IS_ARM" == 1 ]]; then
    echo "$(paimenos_text 'ARM-Gerät erkannt ({value0}); ARM-Anpassungen werden angewendet.' "$PAIMENOS_ARCH")"
    if [[ "$PAIMENOS_ARCH" == armhf ]]; then
        echo "$(paimenos_text 'Hinweis: 32-Bit-ARM (armhf) ist ungetestet; 64-Bit-Debian (arm64) wird empfohlen.')" >&2
    fi
fi
