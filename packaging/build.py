"""Build the HydraFlasher app packages for this OS into release/ and self-test them.

    python packaging/build.py

Windows: HydraFlasher-Setup-<version>.exe (Inno Setup installer) and
         HydraFlasher-<version>-portable.exe (single file, nothing installed).
Linux:   HydraFlasher-<version>-x86_64.AppImage.

Needs the packages from requirements.txt plus pyinstaller; on Windows Inno Setup 6
(ISCC.exe), on Linux appimagetool (downloaded if missing). Every built program is
started with --self-test (a display is needed: xvfb-run on a headless Linux) and the
build fails if one doesn't come up. CI: .github/workflows/app.yml."""
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "packaging")
BUILD = os.path.join(ROOT, "build", "app")
DIST = os.path.join(ROOT, "dist")
RELEASE = os.path.join(ROOT, "release")
APPIMAGETOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/continuous/"
    "appimagetool-x86_64.AppImage"
)


def app_version() -> str:
    src = open(os.path.join(ROOT, "HydraFlasher.py"), encoding="utf-8").read()
    return re.search(r'^APP_VERSION = "([^"]+)"', src, re.M).group(1)


def run(cmd, **kw):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def pyinstaller(name: str, onefile: bool, icon: str, hidden: list):
    """Freeze HydraFlasher.py (windowed: no console)."""
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", name, "--icon", icon,
        "--distpath", DIST, "--workpath", BUILD, "--specpath", BUILD,
        # customtkinter's themes and icons are data files, tkinterdnd2 brings a
        # native library (tkdnd): neither is found by import analysis alone
        "--collect-data", "customtkinter",
    ]
    try:
        import tkinterdnd2  # noqa: F401  (optional: drag and drop)
        cmd += ["--collect-all", "tkinterdnd2"]
    except ImportError:
        print("tkinterdnd2 not installed: building without drag and drop")
    for module in hidden:
        cmd += ["--hidden-import", module]
    cmd += ["--onefile"] if onefile else ["--onedir"]
    run(cmd + [os.path.join(ROOT, "HydraFlasher.py")])


def self_test(program: list, env=None):
    """Start a built program with --self-test and check its report."""
    fd, report_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        result = subprocess.run(program + ["--self-test", report_path], env=env, timeout=180)
        report = json.load(open(report_path, encoding="utf-8")) if os.path.getsize(report_path) else {}
    finally:
        os.unlink(report_path)
    print("self-test", os.path.basename(program[-1]), json.dumps(report), flush=True)
    if result.returncode != 0 or not report.get("ok"):
        sys.exit(f"self-test failed: {program} (exit {result.returncode}) {report}")


def build_windows(version: str):
    icon = os.path.join(PKG, "icons", "hydraflasher.ico")
    hidden = ["plyer.platforms.win.notification"]
    pyinstaller("HydraFlasher", False, icon, hidden)  # folder: for the installer
    pyinstaller("HydraFlasher-portable", True, icon, hidden)
    self_test([os.path.join(DIST, "HydraFlasher", "HydraFlasher.exe")])
    portable = os.path.join(RELEASE, f"HydraFlasher-{version}-portable.exe")
    shutil.copy(os.path.join(DIST, "HydraFlasher-portable.exe"), portable)
    self_test([portable])

    iscc = shutil.which("ISCC") or os.path.join(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "Inno Setup 6", "ISCC.exe",
    )
    run([
        iscc, f"/DAppVersion={version}",
        f"/DSourceDir={os.path.join(DIST, 'HydraFlasher')}",
        f"/DOutputDir={RELEASE}", f"/DIconFile={icon}",
        f"/DLicenseFile={os.path.join(ROOT, 'LICENSE')}",
        os.path.join(PKG, "windows", "installer.iss"),
    ])

    # Install it for real (silently, this user only, into a temporary folder),
    # self-test the installed program, uninstall it again
    setup = os.path.join(RELEASE, f"HydraFlasher-Setup-{version}.exe")
    target = tempfile.mkdtemp(prefix="hydraflasher-install-")
    run([setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER",
         f"/DIR={target}"])
    self_test([os.path.join(target, "HydraFlasher.exe")])
    run([os.path.join(target, "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES"])


def build_linux(version: str):
    icon = os.path.join(PKG, "icons", "hydraflasher.png")
    pyinstaller("HydraFlasher", False, icon, ["plyer.platforms.linux.notification"])
    self_test([os.path.join(DIST, "HydraFlasher", "HydraFlasher")])

    appdir = os.path.join(ROOT, "build", "HydraFlasher.AppDir")
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(os.path.join(DIST, "HydraFlasher"), os.path.join(appdir, "usr", "lib", "hydraflasher"))
    for name in ("AppRun", "hydraflasher.desktop"):
        shutil.copy(os.path.join(PKG, "linux", name), appdir)
    os.chmod(os.path.join(appdir, "AppRun"), 0o755)
    shutil.copy(icon, os.path.join(appdir, "hydraflasher.png"))
    os.symlink("hydraflasher.png", os.path.join(appdir, ".DirIcon"))

    tool = shutil.which("appimagetool") or os.path.join(ROOT, "build", "appimagetool")
    if not os.path.exists(tool):
        print("downloading appimagetool", flush=True)
        urllib.request.urlretrieve(APPIMAGETOOL_URL, tool)
        os.chmod(tool, os.stat(tool).st_mode | stat.S_IEXEC)
    # Extract-and-run: no FUSE needed (CI runners, containers)
    env = dict(os.environ, ARCH="x86_64", APPIMAGE_EXTRACT_AND_RUN="1")
    image = os.path.join(RELEASE, f"HydraFlasher-{version}-x86_64.AppImage")
    run([tool, "--no-appstream", appdir, image], env=env)
    self_test([image], env=dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1"))


def main():
    version = app_version()
    shutil.rmtree(RELEASE, ignore_errors=True)
    os.makedirs(RELEASE)
    system = platform.system()
    if system == "Windows":
        build_windows(version)
    elif system == "Linux":
        build_linux(version)
    else:
        sys.exit(f"no package for {system}")
    print("built:", *sorted(os.listdir(RELEASE)), sep="\n  ")


if __name__ == "__main__":
    main()
