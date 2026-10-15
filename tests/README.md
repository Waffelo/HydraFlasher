# GUI tests

End-to-end tests of `HydraFlasher.py` without hardware. Each `run_*.py` starts the real GUI, replaces the Pico with a fake `Programmer` (scripted JEDEC and unique IDs per socket) and flashprog with one of the `fakeflash*.py` scripts (they log their command lines to `calls.log`), clicks through a scenario and prints `PASS`/`FAIL` lines.

```sh
tests/run_all.sh                    # every test
tests/run_all.sh run_onebyone.py    # one test
```

They need a display: Tk opens real windows for a few seconds each. `run_all.sh` puts them on a hidden one (`xvfb-run`, or a headless KWin with Xwayland) so they don't take your focus; `VISIBLE=1 tests/run_all.sh` shows them instead. They also need the packages from `requirements.txt`. A few also save screenshots with ImageMagick's `import`; without it those checks still run. Tests never touch your real config or flash log: each one points `CONFIG_FILE`, `DATA_DIR` and `FLASH_LOG` at its own folder, and `run_all.sh` runs each in a fresh temp copy.

To write a new one, copy the closest existing test: they all follow the same pattern of fakes, then steps chained with `app.after`, then `app.destroy()`.
