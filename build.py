#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package the add-on into an installable zip.

    python build.py            -> dist/auto_retopology-<version>.zip

The zip contains a single ``auto_retopology/`` folder with the
``blender_manifest.toml``, so it installs both as a Blender 4.2+ extension
(Edit > Preferences > Get Extensions > Install from Disk) and as a legacy
add-on (Edit > Preferences > Add-ons > Install).
"""

import os
import re
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PKG = "auto_retopology"


def version():
    with open(os.path.join(ROOT, PKG, "blender_manifest.toml"), encoding="utf-8") as fh:
        return re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.M).group(1)


def main():
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{PKG}-{version()}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, dirs, files in os.walk(os.path.join(ROOT, PKG)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")):
                    continue
                path = os.path.join(folder, name)
                zf.write(path, os.path.relpath(path, ROOT))
    print(out)


if __name__ == "__main__":
    main()
