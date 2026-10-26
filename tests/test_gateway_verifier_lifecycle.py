"""The gateway CLI owns workspaces created across its asyncio boundary."""

import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "gateway_acceptance_verifier", ROOT / "scripts/verify-gateway.py"
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
helper = sys.modules["verification_workspace"]


class GatewayVerifierLifecycleTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "build/tests"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / ".local/gateway.json"
        for context in (
            patch.object(helper, "ROOT", self.root),
            patch.object(helper, "idle", return_value=True),
            patch.dict("os.environ", {"TOOLFRAME_KEEP_TEST_WORKSPACE": "0"}),
            patch.object(
                sys,
                "argv",
                [
                    "verify-gateway.py",
                    "--binary",
                    str(self.root / "missing-binary"),
                    "--wheelhouse",
                    str(self.root / "wheelhouse"),
                    "--output",
                    str(self.output),
                ],
            ),
        ):
            context.start()
            self.addCleanup(context.stop)

    def summaries(self):
        return list(self.output.parent.glob("gateway.json.logs/*/workspace.json"))

    def test_initialization_failure_saves_logs_and_retains_only_latest_scratch(self):
        for _ in range(2):
            with self.assertRaises(FileNotFoundError):
                verifier.main()
        self.assertEqual(len(list((self.root / "build/verification").iterdir())), 1)
        self.assertEqual(len(self.summaries()), 2)
        for path in self.summaries():
            summary = json.loads(path.read_text())
            self.assertEqual(summary["status"], "failed")
            self.assertIn("FileNotFoundError", summary["error"])
            self.assertFalse(summary["report_written"])
            self.assertTrue(Path(summary["logs"][0]).is_file())

    def test_async_success_is_finalized_after_report_is_saved(self):
        created = []

        async def completed(args):
            await asyncio.sleep(0)
            created.append(
                verifier.create_workspace(
                    "toolframe-mcp-gateway-", evidence=args.output
                )
            )
            helper.write(args.output, {"cases": ["fixture"]})

        with patch.object(verifier, "verify", completed):
            verifier.main()
        self.assertFalse(created[0].exists())
        self.assertEqual(json.loads(self.output.read_text())["cases"], ["fixture"])
        self.assertFalse(json.loads(self.summaries()[0].read_text())["retained"])


if __name__ == "__main__":
    unittest.main()
