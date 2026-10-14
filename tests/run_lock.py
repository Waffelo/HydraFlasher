"""Second process can't take the instance lock while the first holds it; can after."""
import os
import subprocess
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
src = sys.argv[1]
probe = (
    "import sys; sys.path.insert(0, %r); import serial.tools.list_ports;"
    "import HydraFlasher as H; H.DATA_DIR = %r; "
    "l = H.acquire_instance_lock(); print('got' if l else 'busy', flush=True); "
    "import time; time.sleep(float(sys.argv[1]))" % (src, here)
)
holder = subprocess.Popen([sys.executable, "-c", probe, "3"], stdout=subprocess.PIPE, text=True)
first = holder.stdout.readline().strip()
second = subprocess.run([sys.executable, "-c", probe, "0"], capture_output=True, text=True).stdout.strip()
holder.wait()
third = subprocess.run([sys.executable, "-c", probe, "0"], capture_output=True, text=True).stdout.strip()
print("PASS" if first == "got" else "FAIL", "first instance gets the lock:", first)
print("PASS" if second == "busy" else "FAIL", "second instance refused:", second)
print("PASS" if third == "got" else "FAIL", "lock free after first exits:", third)
