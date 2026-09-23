# CI and release setup

## Run the same package validation locally

Use Git, uv 0.12.8, Python 3.14.7, and network access to PyPI. From this repository:

```sh
uv run --isolated --no-project --python 3.14.7 python scripts/ci.py \
  --output-dir "$(mktemp -d "${TMPDIR:-/tmp}/toolframe-fastmcp-result.XXXXXX")"
```

The script prints the result directory. It accepts an absent or empty output
directory and refuses to reuse existing output. `--sdk-source /path/to/toolframe-sdk`
selects the SDK Git checkout (the default is `../toolframe-sdk`). That checkout
must contain commit `121e60eb5ba77717a382cd474c7a31d2de086d13`.

Every run copies the current tracked and non-ignored files, including local
edits, to a temporary workspace and extracts the SDK from that exact commit.
It creates a new uv cache, test environment and wheel environment. It does not
write into the sibling checkout, reuse either repository's `.venv` or `dist`,
or update the committed lockfile. It performs these checks in order:

1. `uv sync --locked` against the existing lockfile, followed by
   `ruff check src tests scripts` and the complete existing `pytest` suite.
2. A new SDK wheel and this project's sdist/wheel using `uv build --no-sources`.
   uv builds the provider wheel from the newly built sdist.
3. `twine==7.0.0 check --strict` for both provider distributions and byte-for-byte
   preservation of their `LICENSE` and `NOTICE` files.
4. A fresh installation of the provider wheel and pinned SDK wheel, resolving
   all runtime dependencies normally, followed by `uv pip check`.
5. `python -I` outside the source tree to verify installed module paths and
   versions, load the `toolframe.plugins` entry point, exercise cryptography's
   native encryption/decryption, and connect a real FastMCP client to the
   installed provider's private HTTP listener.

Only successful runs export `dist/`, `pytest.xml`, `wheel-smoke.json`, and
`validation.json` (versions, platform, SDK commit and artifact SHA-256 values).
Temporary source trees and environments are removed when the script exits.
The SDK wheel is used for validation and is not included in the upload artifact.
Twine 7.0.0 is required for the current build backend's metadata 2.5 output;
[Twine's changelog](https://twine.readthedocs.io/en/stable/changelog.html#twine-7-0-0-2026-07-27)
documents that compatibility fix.

## Workflows

| File | Trigger and behavior |
| --- | --- |
| `.github/workflows/ci.yml` | Push, pull request, or manual run; calls the shared validation workflow. New CI runs supersede older runs for the same ref. |
| `.github/workflows/validate.yml` | Reusable workflow; Python 3.14.7 and uv 0.12.8 on `ubuntu-24.04` (x64) and `macos-15` (arm64), with a 20-minute job timeout. Each job runs `scripts/ci.py` and saves its validated distributions and evidence for 7 days. |
| `.github/workflows/publish.yml` | Manual only; choose `testpypi` (default) or `pypi`, and run on the matching version tag. Validates the tag, reruns the complete shared matrix at that revision, then uploads that run's validated Linux artifact without rebuilding it. |

Actions are pinned to full commit IDs with version comments. Default permissions
are `contents: read`; only the publishing job receives `id-token: write`.
There is no PR upload or `pull_request_target` execution. The reusable workflow
has no concurrency group, so it cannot cancel its caller. Publishing uses a
separate non-cancelling concurrency group for each tag and target.

The SDK checkout uses
`${{ vars.TOOLFRAME_REPOSITORY_OWNER || github.repository_owner }}/toolframe-sdk`
at the full commit above. A fork pull request uses the base repository's owner,
not the contributor's owner. Local validation also extracts this commit even if
the sibling checkout has advanced or contains uncommitted edits.

Runner labels and architectures follow the
[official GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Python and uv setup follow the
[official uv Actions guide](https://docs.astral.sh/uv/guides/integration/github/).

## External setup before a future upload

1. Choose the real GitHub owner and push this repository and the pinned SDK
   commit to `toolframe-fastmcp` and `toolframe-sdk`. The default owner comes from
   the repository running the workflow. Set repository variable
   `TOOLFRAME_REPOSITORY_OWNER` if the SDK has a different owner. The sibling
   checkout currently assumes a public, readable SDK repository; private
   cross-repository access needs a separately configured read credential and
   an explicit checkout update, since `GITHUB_TOKEN` is scoped to one repository.
2. Enable Actions and create GitHub environments named exactly `testpypi` and
   `pypi`. Restrict deployment to release tags and configure required reviewers
   according to the repository's release policy.
3. In **each** of TestPyPI and PyPI, configure a GitHub Trusted Publisher for
   project `toolframe-fastmcp` (a pending publisher works for a new project).
   Enter the real owner, repository `toolframe-fastmcp`, workflow filename
   `publish.yml`, and the matching environment name `testpypi` or `pypi`.
   No API-token secret is needed. See
   [PyPI's Trusted Publishing instructions](https://docs.pypi.org/trusted-publishers/using-a-publisher/).
4. Ensure consumers can obtain the runtime dependency `toolframe-sdk==0.2.0`
   from their package index or a supplied wheelhouse. CI builds it from the
   pinned source because it need not be publicly released yet; this workflow
   publishes only `toolframe-fastmcp`.
5. Put the workflow files on the default branch so GitHub exposes the manual
   workflow. Create the release tag at the intended commit with these workflow
   files present. It must be exactly `v<project.version>`: currently `v0.2.0`.
   Select that **tag** when manually dispatching `Publish Python package`.
   A branch named `v0.2.0` and any mismatched version tag are rejected.
6. Select `testpypi` for the first upload, review the uploaded distribution, then
   dispatch `pypi` on the same release tag when ready. Each dispatch validates
   that revision again. Existing releases are not silently skipped or replaced.

These files prepare publishing. Local validation neither registers publishers
nor pushes tags, uploads distributions, or runs a remote deployment.

## Platform and integration limits

This is package CI on Linux and Apple Silicon macOS. It does not establish
support for the complete Toolframe application on Linux or Windows. The current
native Intel wheel preparation remains `scripts/build-macos-intel.py`: the Core
repository's `scripts/build-release.sh` explicitly requires its cryptography
companion wheel for the macOS Intel **full** launcher distribution. See
[the native wheel instructions](../README.md#macos-intel-release-artifact).

The provider's normal PyPI release is a pure-Python wheel plus sdist. Its native
cryptography companion belongs in the Core release wheelhouse; it must not be
uploaded as a `toolframe-fastmcp` distribution. This package workflow does not
add a duplicate native build/release route. Python-only installation on Intel
may still require compiling cryptography and its native dependencies.

The existing gateway and Codex probes require a running disposable application,
the sibling Core launcher and its complete wheelhouse, and, for the Codex probe,
the installed client. They are separate integration acceptance work. This CI
does not execute those probes or the desktop Codex integration. Its HTTP smoke
connects directly to the provider, not through the Core gateway. Physical Intel,
macOS 12, signing, and notarization remain separate release checks.
