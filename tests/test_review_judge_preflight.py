# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Real judge-import readiness probes; fixture modules make no network requests."""

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness2 import config, preflight


class JobBenchJudgePreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="judge preflight ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.dependencies = self.root / "dependencies"
        self.repo = self.dependencies / "job-bench-eval"
        (self.repo / "eval").mkdir(parents=True)
        self.opencode = self.dependencies / "opencode"
        (self.opencode / "node_modules").mkdir(parents=True)
        for repo in (self.repo, self.opencode):
            (repo / ".git").mkdir()
        # The production probe also verifies these optional packages. Fixture
        # modules isolate the judge import from the developer's installed extras.
        for name in ("pandas", "openpyxl", "docx", "mammoth", "pdfplumber", "pptx", "openai"):
            (self.repo / f"{name}.py").write_text("", encoding="utf-8")
        self.adapter = SimpleNamespace(
            spec=SimpleNamespace(name="jb"), tasks=Mock(), preflight=Mock()
        )
        self.calls = []
        self.patch_sources = []

    def check(self, judge=True, failed_patch=None):
        run = subprocess.run

        def run_probe(command, **kwargs):
            if Path(command[0]).stem == "git":
                status = 1 if Path(command[-1]).stem == failed_patch else 0
                return subprocess.CompletedProcess(command, status)
            self.calls.append((command, kwargs))
            return run(command, **kwargs)

        def checkout_head(command, **kwargs):
            return config.DEPENDENCY_PINS[Path(command[2]).name] + "\n"

        def source_for_patch(key):
            self.patch_sources.append(key)
            return nullcontext(f"{key}.diff")

        with ExitStack() as stack:
            for name, value in {
                "DEPENDENCIES": self.dependencies,
                "OPENCODE_DIR": self.opencode,
                "JB_JUDGE_PYTHON": Path(sys.executable),
                "SOLVER_MODEL_OC": "local/test",
                "IMPROVER_MODEL": "local/test",
                "JUDGE_MODEL": "local-test",
                "JB_JUDGE_API_BASE": "http://127.0.0.1:8087/v1",
                "JB_JUDGE_API_KEY": "fixture-key",
            }.items():
                stack.enter_context(patch.object(config, name, value))
            stack.enter_context(patch.object(config, "validate_models"))
            stack.enter_context(patch.object(preflight, "missing_runners", return_value=""))
            stack.enter_context(patch.object(preflight.OpencodeSubstrate, "preflight"))
            stack.enter_context(
                patch.object(preflight, "patch_source", side_effect=source_for_patch)
            )
            stack.enter_context(patch.object(preflight.subprocess, "run", side_effect=run_probe))
            stack.enter_context(
                patch.object(preflight.subprocess, "check_output", side_effect=checkout_head)
            )
            return preflight.check(self.adapter, judge=judge)

    def test_actual_judge_import_failure_is_a_readiness_error(self):
        (self.repo / "eval/judge.py").write_text(
            "import harness2_judge_platform_unavailable_fixture\n", encoding="utf-8"
        )
        self.assertEqual(
            self.check(),
            [
                "JobBench document and judge dependencies failed; run harness2-setup for this benchmark"
            ],
        )
        self.assertEqual(len(self.calls), 1)

    def test_actual_judge_import_uses_configured_interpreter_and_repo_without_main(self):
        (self.repo / "eval/judge.py").write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            "assert Path.cwd() == Path(__file__).resolve().parent.parent\n"
            "assert __name__ == '_harness2_judge_preflight'\n"
            "Path('imported.json').write_text(json.dumps({'python': sys.executable, "
            "'cwd': str(Path.cwd())}), encoding='utf-8')\n"
            "if __name__ == '__main__':\n"
            "    raise AssertionError('The judge entry point must not execute')\n",
            encoding="utf-8",
        )
        self.assertEqual(self.check(), [])
        self.assertEqual(len(self.calls), 1)
        command, kwargs = self.calls[0]
        self.assertEqual(command[:2], [str(Path(sys.executable)), "-c"])
        self.assertEqual(kwargs["cwd"], self.repo)
        imported = json.loads((self.repo / "imported.json").read_text(encoding="utf-8"))
        self.assertEqual(Path(imported["python"]).resolve(), Path(sys.executable).resolve())
        self.assertEqual(Path(imported["cwd"]), self.repo)

    def test_missing_native_overlay_is_reported_even_when_actual_judge_import_succeeds(self):
        (self.repo / "eval/judge.py").write_text(
            "from pathlib import Path\n"
            "assert __name__ == '_harness2_judge_preflight'\n"
            "Path('imported').write_text('success', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self.assertEqual(
            self.check(failed_patch="job-bench-eval-native-lock"),
            ["job-bench-eval-native-lock patch failed; run harness2-setup for this benchmark"],
        )
        self.assertEqual(
            self.patch_sources, ["job-bench-eval", "job-bench-eval-native-lock", "opencode"]
        )
        self.assertEqual((self.repo / "imported").read_text(encoding="utf-8"), "success")
        self.assertEqual(len(self.calls), 1)

    def test_solver_only_check_does_not_import_judge(self):
        (self.repo / "eval/judge.py").write_text(
            "raise AssertionError('Judge is not required for a solver-only check')\n",
            encoding="utf-8",
        )
        self.assertEqual(self.check(judge=False), [])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
