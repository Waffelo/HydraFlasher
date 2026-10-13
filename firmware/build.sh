#!/bin/bash
# Usage: ./build.sh          build build/pico_serprog.uf2
#        ./build.sh flash    build, then copy it to a Pico in BOOTSEL mode (RPI-RP2 drive)
set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$BASE_DIR/build"

# Pico SDK: $PICO_SDK_PATH, or a pico-sdk checkout inside / next to this project
if [ -z "$PICO_SDK_PATH" ]; then
	for d in "$BASE_DIR/pico-sdk" "$BASE_DIR/../pico-sdk"; do
		if [ -f "$d/pico_sdk_init.cmake" ]; then
			PICO_SDK_PATH="$(cd "$d" && pwd)"
			break
		fi
	done
fi
if [ -z "$PICO_SDK_PATH" ]; then
	echo "Pico SDK not found. Set PICO_SDK_PATH or put pico-sdk next to this folder." >&2
	exit 1
fi
export PICO_SDK_PATH

cmake -B "$BUILD_DIR" -S "$BASE_DIR" -DPICO_SDK_PATH="$PICO_SDK_PATH"
make -C "$BUILD_DIR" -j"$(nproc)"
UF2="$BUILD_DIR/pico_serprog.uf2"
echo "Built: $UF2"

find_rp2() { findmnt -rn -S LABEL=RPI-RP2 -o TARGET | head -n1; }

if [ "$1" = "flash" ]; then
	MNT="$(find_rp2)"
	if [ -z "$MNT" ]; then
		# Firmware with S_CMD_BOOTSEL (0x21) can reboot itself into the bootloader
		PORT="$(python3 -c '
import serial.tools.list_ports as l
print(next((p.device for p in l.comports() if "HydraFlasher" in (p.product or "")), ""))' 2>/dev/null)"
		if [ -n "$PORT" ]; then
			echo "Rebooting $PORT into the bootloader..."
			python3 -c 'import serial, sys; serial.Serial(sys.argv[1], 115200, timeout=1).write(b"\x21")' "$PORT" || true
			for _ in $(seq 20); do
				sleep 0.5
				MNT="$(find_rp2)"
				[ -n "$MNT" ] && break
			done
		fi
	fi
	if [ -z "$MNT" ]; then
		echo "No RPI-RP2 drive mounted. Hold BOOTSEL while plugging in the Pico." >&2
		exit 1
	fi
	cp "$UF2" "$MNT/" && sync
	echo "Flashed via $MNT"
fi
