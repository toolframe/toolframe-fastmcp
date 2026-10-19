#!/usr/bin/env python3
"""Run the package CI in disposable source trees and fresh environments."""

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

SDK_COMMIT = "2cd153ec36c585a9adb5ad566effc2985d9e222d"
ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd, env, capture=False):
    command = [str(arg) for arg in args]
    print(f"+ {shlex.join(command)}", flush=True)
    result = subprocess.run(
        command, cwd=cwd, env=env, check=True, text=True, capture_output=capture
    )
    return result.stdout.strip() if capture else None


def copy_working_tree(destination):
    """Include local edits and new files, excluding ignored environments/artifacts."""
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    )
    for name in files.decode().split("\0"):
        if name and (ROOT / name).is_file():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)


def check_licenses(project, wheel, sdist):
    with zipfile.ZipFile(wheel) as archive:
        for name in ("LICENSE", "NOTICE"):
            matches = [
                item
                for item in archive.namelist()
                if item.endswith(f".dist-info/licenses/{name}")
            ]
            if (
                len(matches) != 1
                or archive.read(matches[0]) != (project / name).read_bytes()
            ):
                raise RuntimeError(f"Wheel has missing or changed {name}")
    with tarfile.open(sdist) as archive:
        for name in ("LICENSE", "NOTICE"):
            member = archive.extractfile(f"{sdist.name.removesuffix('.tar.gz')}/{name}")
            if member is None or member.read() != (project / name).read_bytes():
                raise RuntimeError(f"Sdist has missing or changed {name}")
    print("Wheel and sdist retain LICENSE and NOTICE unchanged", flush=True)


def validate(sdk_source, output, work):
    project = work / "toolframe-fastmcp"
    sdk = work / "toolframe-sdk"
    sdk.mkdir()
    copy_working_tree(project)
    env = os.environ.copy()
    for key in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    env.update(
        UV_CACHE_DIR=str(work / "uv-cache"),
        UV_PROJECT_ENVIRONMENT=str(work / "test-env"),
        UV_PYTHON=sys.executable,
        PYTHONNOUSERSITE="1",
    )
    print(f"Disposable validation workspace: {work}", flush=True)
    archive_path = work / "sdk.tar"
    run(
        "git",
        "archive",
        "--format=tar",
        f"--output={archive_path}",
        SDK_COMMIT,
        cwd=sdk_source,
        env=env,
    )
    with tarfile.open(archive_path) as archive:
        archive.extractall(sdk, filter="data")

    run("uv", "sync", "--locked", "--python", sys.executable, cwd=project, env=env)
    test_python = work / "test-env/bin/python"
    run(
        test_python,
        "-m",
        "ruff",
        "check",
        "src",
        "tests",
        "scripts",
        cwd=project,
        env=env,
    )
    run(
        test_python,
        "-m",
        "pytest",
        "-q",
        f"--junitxml={work / 'pytest.xml'}",
        cwd=project,
        env=env,
    )

    wheelhouse = work / "wheelhouse"
    run(
        "uv",
        "build",
        "--no-sources",
        "--wheel",
        "--python",
        sys.executable,
        "--out-dir",
        wheelhouse,
        sdk,
        cwd=work,
        env=env,
    )
    dist = work / "dist"
    run(
        "uv",
        "build",
        "--no-sources",
        "--python",
        sys.executable,
        "--out-dir",
        dist,
        project,
        cwd=work,
        env=env,
    )
    [wheel] = dist.glob("*.whl")
    [sdist] = dist.glob("*.tar.gz")
    run(
        "uv",
        "tool",
        "run",
        "--from",
        "twine==7.0.0",
        "twine",
        "check",
        "--strict",
        wheel,
        sdist,
        cwd=work,
        env=env,
    )
    check_licenses(project, wheel, sdist)

    smoke_env = work / "wheel-env"
    run("uv", "venv", "--python", sys.executable, smoke_env, cwd=work, env=env)
    smoke_python = smoke_env / "bin/python"
    [sdk_wheel] = wheelhouse.glob("toolframe_sdk-*.whl")
    run(
        "uv",
        "pip",
        "install",
        "--python",
        smoke_python,
        "--strict",
        sdk_wheel,
        wheel,
        cwd=work,
        env=env,
    )
    run("uv", "pip", "check", "--python", smoke_python, cwd=work, env=env)
    version = tomllib.loads((project / "pyproject.toml").read_text())["project"][
        "version"
    ]
    run(
        smoke_python,
        "-I",
        project / "scripts/wheel-smoke.py",
        "--expected-version",
        version,
        "--output",
        work / "wheel-smoke.json",
        cwd=work,
        env=env,
    )
    report = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "uv": run("uv", "--version", cwd=work, env=env, capture=True),
        "sdk_commit": SDK_COMMIT,
        "version": version,
        "artifacts": {
            artifact.name: hashlib.sha256(artifact.read_bytes()).hexdigest()
            for artifact in (wheel, sdist)
        },
        "checks": [
            "locked fresh development environment",
            "ruff",
            "pytest",
            "uv build --no-sources",
            "twine check --strict",
            "license contents",
            "fresh wheel installation with runtime dependencies",
            "uv pip check",
            "installed entry point, native cryptography and live MCP HTTP smoke",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dist, output / "dist")
    shutil.copy2(work / "pytest.xml", output / "pytest.xml")
    shutil.copy2(work / "wheel-smoke.json", output / "wheel-smoke.json")
    (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"All package checks passed. Validated artifacts and evidence: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sdk-source", type=Path, default=ROOT.parent / "toolframe-sdk"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info[:3] != (3, 14, 7):
        parser.error("Run with Python 3.14.7, matching GitHub Actions")
    output = args.output_dir.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error(
            "--output-dir must be absent or empty; existing output is never reused"
        )
    with tempfile.TemporaryDirectory(prefix="toolframe-fastmcp-ci-") as directory:
        validate(args.sdk_source.resolve(), output, Path(directory))


if __name__ == "__main__":
    main()
