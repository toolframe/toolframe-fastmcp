# Build the macOS Intel companion wheel

The provider pins `cryptography==50.0.2`. Upstream stopped publishing macOS Intel
wheels in cryptography 49; see the [upstream changelog](https://cryptography.io/en/latest/changelog/).
Toolframe's Intel launcher automatically downloads a companion from its matching
GitHub Release and verifies the SHA-256 pinned into the executable. An ordinary PyPI-only installation on Intel may attempt a source
build and require native build dependencies.

```sh
# From this repository; requires Xcode command-line tools, make, Perl, Rust and uv.
# On Apple Silicon, also install Rosetta and the x86_64-apple-darwin Rust target.
uv run python scripts/build-macos-intel.py --out-dir ../toolframe-core/wheelhouse
```

The script verifies pinned source SHA-256 values, builds OpenSSL 4.0.3 in a
temporary private prefix, and statically links it into the Python 3.14 Intel
cryptography wheel with a macOS 12 deployment target. It makes no system OpenSSL
installation. Publish this wheel as a separate Core GitHub Release asset; retain its upstream
license metadata. No Python wheel is embedded in the launcher. Updating either native dependency requires updating the pins,
checksums and release acceptance together.

Qualify the exact Intel release on its native runner. Rosetta, physical Intel
hardware and the macOS 12 minimum are separate verification environments.
