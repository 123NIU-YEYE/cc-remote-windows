#!/usr/bin/env python3
"""Prepare/build the standalone HarmonyOS client without storing signing data in git."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def locate_studio(explicit):
    candidates = [explicit, os.environ.get("DEVECO_STUDIO_PATH"),
                  "/Applications/DevEco-Studio.app/Contents",
                  str(Path.home() / "Applications/DevEco-Studio.app/Contents")]
    for name in candidates:
        if not name:
            continue
        path = Path(name).expanduser().resolve()
        if path.suffix == ".app":
            path = path / "Contents"
        if (path / "tools/hvigor/bin/hvigorw.js").is_file():
            return path
    return None


def prepare(studio):
    for parent in [ROOT, ROOT / "entry"]:
        target = parent / "build-profile.json5"
        if not target.exists():
            shutil.copyfile(parent / "build-profile.template.json5", target)
            os.chmod(target, 0o600)
    if studio:
        sdk = studio / "sdk"
        properties = ROOT / "local.properties"
        if not properties.exists():
            properties.write_text(f"sdk.dir={sdk}\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "check", "build"])
    parser.add_argument("--studio", help="DevEco-Studio.app or its Contents directory")
    args = parser.parse_args()
    studio = locate_studio(args.studio)
    if args.command in {"init", "build"}:
        prepare(studio)
    if args.command == "init":
        print("Local project configs prepared; private signing fields remain gitignored.")
        if not studio:
            print("DevEco Studio not found. Open this directory in the official IDE after installing it.")
        return 0
    if not studio:
        print("DevEco Studio is required. Install the official Mac ARM build and set --studio.", file=sys.stderr)
        return 2
    node = studio / "tools/node/bin/node"
    java = studio / "jbr/Contents/Home"
    ohpm = studio / "tools/ohpm/bin/ohpm"
    hdc = studio / "sdk/default/openharmony/toolchains/hdc"
    env = dict(os.environ)
    env["DEVECO_SDK_HOME"] = str(studio / "sdk")
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    if java.is_dir():
        env["JAVA_HOME"] = str(java)
    subprocess.run([str(node), "--version"], env=env, check=True)
    if args.command == "check":
        if hdc.is_file():
            subprocess.run([str(hdc), "list", "targets"], env=env, check=True)
        return 0
    subprocess.run([str(ohpm), "install"], cwd=ROOT, env=env, check=True)
    subprocess.run([str(node), str(studio / "tools/hvigor/bin/hvigorw.js"),
                    "--mode", "module", "-p", "product=default", "-p", "module=entry@default",
                    "-p", "buildMode=debug", "assembleHap", "--no-daemon"],
                   cwd=ROOT, env=env, check=True)
    for path in (ROOT / "entry/build").rglob("*.hap"):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
