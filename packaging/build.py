"""Build the HydraFlasher app packages for this OS into release/ and self-test them.

    python packaging/build.py

Windows: HydraFlasher-Setup-<version>.exe (Inno Setup installer) and
         HydraFlasher-<version>-portable.exe (single file, nothing installed).
Linux:   HydraFlasher-<version>-x86_64.AppImage.

Needs the packages from requirements.txt plus pyinstaller; on Windows Inno Setup 6
(ISCC.exe), on Linux appimagetool (downloaded if missing). Every built program is
started with --self-test (a display is needed: xvfb-run on a headless Linux) and the
build fails if one doesn't come up. CI: .github/workflows/app.yml."""
import glob
import hashlib
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
# A fixed appimagetool release, checked against its SHA-256 (not "continuous", which
# changes under us)
APPIMAGETOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/1.9.1/"
    "appimagetool-x86_64.AppImage"
)
APPIMAGETOOL_SHA256 = "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
# Python packages that end up in the bundle (requirements-build.txt)
BUNDLED_DISTS = ["customtkinter", "darkdetect", "pyserial", "plyer", "tkinterdnd2",
                 "pyinstaller"]
LICENSES_NAME = "THIRD-PARTY-LICENSES.txt"


def app_version() -> str:
    src = open(os.path.join(ROOT, "HydraFlasher.py"), encoding="utf-8").read()
    return re.search(r'^APP_VERSION = "([^"]+)"', src, re.M).group(1)


def git_commit() -> str:
    """"9e2c15f", "9e2c15f modified" with local changes, "" without git."""
    def git(*args):
        return subprocess.run(["git", "-C", ROOT, *args], capture_output=True,
                              text=True).stdout.strip()
    try:
        head = git("rev-parse", "--short", "HEAD")
        changed = git("status", "--porcelain", "--untracked-files=no")
    except OSError:
        return ""
    return head + (" modified" if head and changed else "")


def _section(title: str, body: str) -> str:
    return f"{'=' * 78}\n{title}\n{'=' * 78}\n\n{body.strip()}\n\n"


def third_party_licenses(bundle: str = "") -> str:
    """The licence texts of everything the package contains besides HydraFlasher:
    the Python packages, Python (with Tcl/Tk etc.) and, on Linux, the system
    libraries PyInstaller copied into `bundle` (their Debian/Ubuntu copyright files).
    MIT/BSD-style licences require these notices to ship with the binaries."""
    from importlib import metadata

    parts = [
        "HydraFlasher is free software under the GNU General Public License v3 "
        "(LICENSE.txt). Its packages also contain the following software, under "
        "their own licences.\n\n"
    ]
    for name in BUNDLED_DISTS:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        texts = []
        for f in dist.files or []:
            if os.path.basename(str(f)).upper().startswith(
                ("LICENSE", "LICENCE", "COPYING", "NOTICE")
            ):
                try:
                    texts.append(f"--- {f} ---\n{f.read_text(encoding='utf-8')}")
                except (OSError, UnicodeDecodeError):
                    pass
        kind = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        body = "\n".join(texts) or f"License: {kind}"
        parts.append(_section(f"{name} {dist.version} ({kind[:60]})", body))

    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    for path in (
        os.path.join(sys.base_prefix, "LICENSE.txt"),  # python.org Windows build
        os.path.join(sys.base_prefix, "lib", f"python{version}", "LICENSE.txt"),
        f"/usr/share/doc/python{version}/copyright",  # Debian/Ubuntu
        f"/usr/share/doc/libpython{version}-stdlib/copyright",
    ):
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                parts.append(_section(f"Python {sys.version.split()[0]}", f.read()))
            break

    if bundle and shutil.which("dpkg"):
        packages = set()
        for lib in glob.glob(os.path.join(bundle, "**", "*.so*"), recursive=True):
            out = subprocess.run(["dpkg", "-S", "*/" + os.path.basename(lib)],
                                 capture_output=True, text=True).stdout
            packages.update(line.split(":")[0] for line in out.splitlines() if ":" in line)
        for pkg in sorted(packages):
            path = f"/usr/share/doc/{pkg}/copyright"
            if os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as f:
                    parts.append(_section(f"{pkg} (system library)", f.read()))
    return "".join(parts)


def write_licenses(folder: str, bundle: str = "") -> str:
    """LICENSE.txt (HydraFlasher, GPL) and THIRD-PARTY-LICENSES.txt into folder."""
    os.makedirs(folder, exist_ok=True)
    shutil.copy(os.path.join(ROOT, "LICENSE"), os.path.join(folder, "LICENSE.txt"))
    path = os.path.join(folder, LICENSES_NAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(third_party_licenses(bundle))
    return path


def run(cmd, **kw):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def pyinstaller(name: str, onefile: bool, icon: str, hidden: list, extra_data=()):
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
    # The git commit, shown next to the version in settings (app_commit())
    commit = os.path.join(BUILD, "commit.txt")
    os.makedirs(BUILD, exist_ok=True)
    with open(commit, "w", encoding="utf-8") as f:
        f.write(git_commit())
    cmd += ["--add-data", f"{commit}{os.pathsep}."]
    if extra_data:
        for path in extra_data:
            cmd += ["--add-data", f"{path}{os.pathsep}."]
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
    notices = os.path.join(BUILD, "notices")
    write_licenses(notices)  # python.org's LICENSE.txt covers its DLLs (Tcl/Tk, …)
    data = [os.path.join(notices, n) for n in ("LICENSE.txt", LICENSES_NAME)]
    pyinstaller("HydraFlasher", False, icon, hidden, data)  # folder: for the installer
    pyinstaller("HydraFlasher-portable", True, icon, hidden, data)
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
    internal = os.path.join(DIST, "HydraFlasher", "_internal")
    write_licenses(internal, bundle=internal)  # next to the app's own files
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
        with open(tool, "rb") as f:
            got = hashlib.sha256(f.read()).hexdigest()
        if got != APPIMAGETOOL_SHA256:
            os.unlink(tool)
            sys.exit(f"appimagetool checksum mismatch: {got}")
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
