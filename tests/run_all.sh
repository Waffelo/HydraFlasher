#!/bin/sh
# Run the GUI tests: ./run_all.sh [run_name.py ...]   (default: every run_*.py)
# Each test gets a fresh copy of this folder in a short temp dir, so tests don't see
# each other's leftovers, and long paths don't wrap the log's welcome box.
here=$(cd "$(dirname "$0")" && pwd)
src=$(dirname "$here")
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
