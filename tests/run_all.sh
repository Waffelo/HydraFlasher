#!/bin/sh
# Run the GUI tests: ./run_all.sh [run_name.py ...]   (default: every run_*.py)
# Each test gets a fresh copy of this folder in a short temp dir, so tests don't see
# each other's leftovers, and long paths don't wrap the log's welcome box.
here=$(cd "$(dirname "$0")" && pwd)
src=$(dirname "$here")

# The tests open real windows that would take focus on your desktop, so they run on
# a hidden display: xvfb-run, or else a headless KWin with its own Xwayland.
# VISIBLE=1 runs them on your display instead.
if [ -z "$HYDRA_TEST_INNER" ] && [ -z "$VISIBLE" ]; then
    export HYDRA_TEST_INNER=1
    if command -v xvfb-run >/dev/null 2>&1; then
        exec xvfb-run -a -s "-screen 0 1280x1024x24" "$here/run_all.sh" "$@"
    fi
    if command -v kwin_wayland >/dev/null 2>&1; then
        tmp=$(mktemp -d)
        # kwin starts one command line: wrap ours (with its arguments) in a script
        {
            printf '#!/bin/sh\n"%s"' "$here/run_all.sh"
            for a in "$@"; do printf ' "%s"' "$a"; done
            printf '\necho $? > "%s/rc"\n' "$tmp"
        } > "$tmp/inner.sh"
        chmod +x "$tmp/inner.sh"
        kwin_wayland --virtual --xwayland --socket "hydra-test-$$" \
            --width 1280 --height 1024 --exit-with-session "$tmp/inner.sh" 2>/dev/null
        rc=$(cat "$tmp/rc" 2>/dev/null || echo 1)
        rm -rf "$tmp"
        exit "$rc"
    fi
    echo "(no xvfb-run or kwin_wayland: the test windows open on your display)" >&2
fi

[ $# -eq 0 ] && set -- $(cd "$here" && ls run_*.py)
failed=0
for t in "$@"; do
    t=$(basename "$t")
    work=$(mktemp -d)
    cp "$here"/*.py "$work"/
    out=$(cd "$work" && timeout 120 python3 "$t" "$src" "$work/shot.png" 2>&1)
    pass=$(printf '%s\n' "$out" | grep -c '^PASS')
    fail=$(printf '%s\n' "$out" | grep -c '^FAIL')
    printf '%-22s %2d passed, %d failed\n' "$t" "$pass" "$fail"
    if [ "$fail" -gt 0 ] || [ "$pass" -eq 0 ]; then
        printf '%s\n' "$out" | grep -v '^PASS' | tail -n 15 | sed 's/^/    /'
        failed=$((failed + 1))
    fi
    rm -rf "$work"
done
[ "$failed" -eq 0 ] && echo "all passed" || echo "$failed test(s) failed"
[ "$failed" -eq 0 ]
