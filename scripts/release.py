"""Release steps that used to be done by hand.

  python scripts/release.py 2.1.5          tests, VERSION, commit "2.1.5", tag v2.1.5 (RELEASE_NOTES.md first)
  python scripts/release.py winget 2.1.5   after CI published the release: manifest in winget/2.1.5,
                                           commit, and the same files as a commit in the winget-pkgs fork

Pushing stays with a person: the script prints the commands.
"""
import hashlib, re, shutil, subprocess, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO, PKG = "NikolayYaroslavcev/battery-tray", "NikolayYaroslavcev.BatteryTray"
FORK, FORK_BRANCH = "https://github.com/NikolayYaroslavcev/winget-pkgs.git", f"{PKG}-2.0.0"   # the branch of PR #487833
FORK_DIR = ROOT / ".winget-pkgs"                     # sparse clone, git-ignored
MANIFESTS = "manifests/n/NikolayYaroslavcev/BatteryTray"


def run(*cmd, cwd=ROOT):
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def version_key(v):
    return tuple(map(int, v.split(".")))


def release(ver):
    if run("git", "status", "--porcelain").strip():
        sys.exit("commit or stash your changes first")
    if f"What's new in {ver}:" not in (ROOT / "RELEASE_NOTES.md").read_text("utf-8"):
        sys.exit(f"RELEASE_NOTES.md has no \"What's new in {ver}:\" section yet")
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests")
    src = ROOT / "battery_tray.py"
    text = src.read_text("utf-8")
    old = re.search(r'^VERSION = "(.+)"$', text, re.M).group(1)
    if version_key(ver) <= version_key(old):
        sys.exit(f"{ver} is not newer than {old}")
    src.write_text(text.replace(f'VERSION = "{old}"', f'VERSION = "{ver}"', 1), "utf-8")
    run("git", "commit", "-qam", ver)
    run("git", "tag", f"v{ver}")
    print(f"\nnow:\n  git push origin main\n  git push origin v{ver}\n"
          f"then, once the release is built:\n  python scripts/release.py winget {ver}")


def winget(ver):
    state = run("gh", "pr", "view", "487833", "-R", "microsoft/winget-pkgs", "--json", "state", "-q", ".state").strip()
    if state != "OPEN":
        sys.exit(f"PR #487833 is {state}: from now on each version is a new PR (wingetcreate update {PKG})")
    url = f"https://github.com/{REPO}/releases/download/v{ver}/BatteryTray.exe"
    with urllib.request.urlopen(url, timeout=60) as r:
        sha = hashlib.sha256(r.read()).hexdigest().upper()
    prev = max((p for p in (ROOT / "winget").iterdir() if p.name != ver and re.fullmatch(r"[\d.]+", p.name)),
               key=lambda p: version_key(p.name))
    if not prev.name.startswith("2."):
        sys.exit(f"newest manifest is {prev.name}, expected a {PKG} one")
    out = ROOT / "winget" / ver
    out.mkdir(exist_ok=True)
    for f in prev.glob(f"{PKG}*.yaml"):
        text = f.read_text("utf-8").replace(prev.name, ver)
        text = re.sub(r"(?m)^ReleaseDate: .*$", f"ReleaseDate: {run('git', 'log', '-1', '--format=%cs', f'v{ver}').strip()}", text)
        text = re.sub(r"(?m)^(  InstallerSha256: ).*$", rf"\g<1>{sha}", text)
        (out / f.name).write_text(text, "utf-8")
    if shutil.which("winget"):
        run("winget", "validate", str(out))
    run("git", "add", str(out))
    run("git", "commit", "-qm", f"winget manifest for {ver}")

    if not FORK_DIR.exists():
        run("git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", "-b", FORK_BRANCH, FORK, str(FORK_DIR))
        run("git", "sparse-checkout", "set", MANIFESTS, cwd=FORK_DIR)
    run("git", "pull", "-q", "--ff-only", cwd=FORK_DIR)
    pkg = FORK_DIR / MANIFESTS
    for old in [p for p in pkg.iterdir() if p.is_dir()] if pkg.exists() else []:
        run("git", "rm", "-rq", str(old.relative_to(FORK_DIR)), cwd=FORK_DIR)   # a new package: one version only
    shutil.copytree(out, pkg / ver)
    run("git", "add", MANIFESTS, cwd=FORK_DIR)
    run("git", "commit", "-qm", f"New package: {PKG} version {ver}", cwd=FORK_DIR)
    print(f"\nnow:\n  git push origin main\n  git -C {FORK_DIR.as_posix()} push origin {FORK_BRANCH}\n"
          f'  gh pr edit 487833 -R microsoft/winget-pkgs --title "New package: {PKG} version {ver}"')


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 1 and re.fullmatch(r"\d+\.\d+\.\d+", args[0]):
        release(args[0])
    elif len(args) == 2 and args[0] == "winget" and re.fullmatch(r"\d+\.\d+\.\d+", args[1]):
        winget(args[1])
    else:
        sys.exit(__doc__)
