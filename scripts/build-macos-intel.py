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
    "openssl-4.0.2": (
        "https://github.com/openssl/openssl/releases/download/openssl-4.0.2/openssl-4.0.2.tar.gz",
        "736b467530f916737b7031310ccb21d8218c6229e61e8e160cd1d3458cd543a8",
    ),
    "cryptography-50.0.1": (
        "https://files.pythonhosted.org/packages/bb/ad/5d6702db60b1e40b41ef513b6967ff5848f307d50f8449baf1634f5908f1/cryptography-50.0.1.tar.gz",
        "5dd9bda1c12b4162f6ff568eeb5e0ff956c28d14406e875cfe8a63a2d414ff20",
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
                command, cwd=root / "openssl-4.0.2", env=environment, check=True
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
                str(root / "cryptography-50.0.1"),
            ],
            env=environment,
            check=True,
        )
        print(f"Intel companion wheel: {output}", flush=True)


if __name__ == "__main__":
    main()
