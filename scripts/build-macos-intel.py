#!/usr/bin/env python3
"""Build the current cryptography release for macOS Intel without system installs.

Upstream stopped distributing Intel wheels in 49.0.0. This companion wheel is a
release artifact for the provider wheelhouse, not a downgrade of cryptography.
Requires Xcode command-line tools, make, Perl, Rust, uv; Rosetta on Apple Silicon.
"""

import argparse
import hashlib
import os
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

SOURCES = {
    "openssl-4.0.3": (
        "https://github.com/openssl/openssl/releases/download/openssl-4.0.3/openssl-4.0.3.tar.gz",
        "325b5c806167c13b40b1ffeadfe0248197c00eccc4cf123ec1e28d2d2fd216d9",
    ),
    "cryptography-50.0.2": (
        "https://files.pythonhosted.org/packages/9d/af/182eb91b0df3fe75c4d9f26fe70684569566745f6ba7e5c9c73a862c5252/cryptography-50.0.2.tar.gz",
        "7b46165bb56eb4704e2eaaf86f3c940d19154535d9b0ca7d6d590b04060e00d5",
    ),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("dist/intel"))
    args = parser.parse_args()
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="toolframe-intel-build-") as directory:
        root = Path(directory)
        for name, (url, checksum) in SOURCES.items():
            archive = root / f"{name}.tar.gz"
            urllib.request.urlretrieve(url, archive)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
                raise RuntimeError(f"Source checksum mismatch: {name}")
            with tarfile.open(archive) as bundle:
                bundle.extractall(root, filter="data")
        prefix = root / "openssl"
        environment = {
            **os.environ,
            "MACOSX_DEPLOYMENT_TARGET": "12.0",
            "CC": "clang -arch x86_64",
        }
        commands = (
            [
                "perl",
                "Configure",
                "darwin64-x86_64-cc",
                "no-shared",
                "no-tests",
                f"--prefix={prefix}",
            ],
            ["make", "-j8"],
            ["make", "install_sw"],
        )
        for command in commands:
            subprocess.run(
                command, cwd=root / "openssl-4.0.3", env=environment, check=True
            )
        environment.update(OPENSSL_DIR=str(prefix), OPENSSL_STATIC="1")
        subprocess.run(
            [
                "uv",
                "build",
                "--no-sources",
                "--wheel",
                "--python",
                "cpython-3.14.7-macos-x86_64-none",
                "--out-dir",
                str(output),
                str(root / "cryptography-50.0.2"),
            ],
            env=environment,
            check=True,
        )
        print(f"Intel companion wheel: {output}", flush=True)


if __name__ == "__main__":
    main()
