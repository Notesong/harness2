"""Public run stories and local-only wrap-ups; all HTTP calls are mocked."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2 import run_story
from harness2.cli import summarize

LOCAL = {
    "HARNESS2_LOCAL_BASE_URL": "http://127.0.0.1:8087/v1",
    "HARNESS2_LOCAL_MODEL": "fixture",
    "HARNESS2_LOCAL_API_KEY": "temporary-key",
}


class RunStoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="run story ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "experiment"
        self.output.mkdir()
        self.task_dir = self.output / "jb/adapt/namespace/writers__task1"
        self.task_dir.mkdir(parents=True)
        self.selected = self.output / "deliverables"
        self.selected.mkdir()
        (self.selected / "guide.md").write_text("published artifact", encoding="utf-8")
        self.summary = {
            "task_id": "writers/task1",
            "status": "ok",
            "selections": {
                "agg": {
                    "chosen_label": "agg1",
                    "chosen_run_dir": str(self.selected),
                    "why": "PRIVATE SELECTOR REASONING",
                }
            },
            "modes": {
                "agg": {
                    "stages": [
                        {
                            "label": "inv1",
                            "attempts": 2,
                            "fell_back": True,
                            "error": "timeout after 3600s",
                        },
                        {
                            "label": "dom1",
                            "attempts": 2,
                            "fell_back": False,
                            "error": "",
                            "notes": "PRIVATE PROPOSER REASONING",
                        },
                    ]
                }
            },
        }
        self.write(self.task_dir / "summary.json", self.summary)
        self.grade_path = self.task_dir.parent / "judged.json"
        self.grade = {
            "task_id": "writers/task1",
            "mode": "agg",
            "k": 1,
            "label": "agg1",
            "graded": True,
            "n_passed": 38.0,
            "n_criteria": 66.0,
            "all_pass": False,
            "raw": {
                "metric": "weighted",
                "criteria_passed": 37,
                "criteria_count": 40,
                "judge_model": "fixture",
            },
        }
        self.write(self.grade_path, [self.grade])

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_quality_is_separate_from_workflow_completion_and_no_improvement_claim(self):
        story = run_story.build_story(self.output)
        prose = "\n".join(story["overview"])
        self.assertIn("38/66 (57.58%)", prose)
        self.assertIn("37/40 criteria", prose)
        self.assertIn("does not establish improvement", prose)
        self.assertIn("No baseline grade", prose)
        self.assertIn("guide.md", prose)
        self.assertIn("fallback", " ".join(story["warnings"]))
        self.assertNotIn("PRIVATE", json.dumps(story))

    def test_ungraded_result_is_not_a_zero_score_or_model_request(self):
        self.grade.update(graded=False, n_passed=None, n_criteria=None)
        self.write(self.grade_path, [self.grade])
        with (
            patch.object(summarize, "model_wrapup") as model,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(summarize.main(["--output", str(self.output)]), 0)
        model.assert_not_called()
        story = run_story.build_story(self.output)
        self.assertEqual(story["summary_status"], "pending")
        self.assertNotIn("0/", " ".join(story["overview"]))

    def test_legacy_recovery_partial_probes_and_natural_final_stop(self):
        self.write(
            self.output / "evidence/completed-workflow-status.json",
            {
                "executor_outcomes": [
                    {
                        "label": "dom1",
                        "natural_final_stop_observed": False,
                        "delivered_required_files": 1,
                        "bytes": 10,
                    },
                    {
                        "label": "inv1",
                        "natural_final_stop_observed": False,
                        "delivered_required_files": 1,
                        "bytes": 15,
                    },
                    {
                        "label": "agg1",
                        "natural_final_stop_observed": True,
                        "delivered_required_files": 4,
                        "bytes": 42582,
                        "completed_utc": "2026-10-19T07:14:33Z",
                    },
                ],
                "accepted_harness_changes": {"dom1": ["LongTermMEMORY.md", "guardrails.md"]},
            },
        )
        self.write(
            self.output / "evidence/workflow-status.json",
            {"stages": [{"name": "judge", "exit_code": 2, "ended_utc": "2026-10-19T07:45:50Z"}]},
        )
        self.write(
            self.output / "evidence/unattended-follow-through/status.json",
            {"stages": {"judge": {"exit_code": 0, "ended_utc": "2026-10-19T08:02:42Z"}}},
        )
        story = run_story.build_story(self.output)
        self.assertIn("42,582", " ".join(story["overview"]))
        self.assertIn("natural final stop", " ".join(story["overview"]))
        self.assertEqual(
            sum("probe output was partial" in warning for warning in story["warnings"]), 2
        )
        self.assertTrue(any("Recovery" in row["title"] for row in story["events"]))

    def test_timeline_is_chronological_and_thinking_is_not_exposed(self):
        rows = [
            {
                "time": "2026-10-19T10:00:00+02:00",
                "title": "Later",
                "detail": "visible",
                "kind": "phase",
            },
            {
                "time": "2026-10-19T07:00:00Z",
                "title": "Earlier",
                "detail": "<think>PRIVATE</think>public",
                "kind": "phase",
            },
        ]
        (self.output / "run-timeline.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
        )
        events = run_story.build_story(self.output)["events"]
        self.assertEqual([row["title"] for row in events[:2]], ["Earlier", "Later"])
        self.assertEqual(events[0]["detail"], "public")

    def test_model_uses_only_local_profile_visible_content_and_bounded_response(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "reasoning_content": "PRIVATE CHAIN OF THOUGHT",
                            "content": "<think>PRIVATE</think>A factual wrap-up.",
                        }
                    }
                ]
            }
        ).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch.object(summarize.urllib.request, "build_opener", return_value=opener):
            text = summarize.model_wrapup(run_story.build_story(self.output), LOCAL)
        self.assertEqual(text, "A factual wrap-up.")
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "fixture")
        self.assertLessEqual(payload["max_tokens"], 800)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 180)
        response.read.assert_called_once_with(run_story.MAX_READ + 1)
        self.assertEqual(request.get_header("Authorization"), "Bearer temporary-key")
        self.assertNotIn("PRIVATE", request.data.decode())

    def test_model_failure_keeps_grade_and_saves_factual_fallback(self):
        with (
            patch.object(summarize, "model_wrapup", side_effect=OSError("do not leak secrets")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(summarize.main(["--output", str(self.output)]), 0)
        story = run_story.build_story(self.output)
        self.assertEqual(story["summary_status"], "unavailable")
        self.assertIn("57.58%", story["model_summary"])
        self.assertEqual(json.loads(self.grade_path.read_text())[0]["n_passed"], 38.0)
        for name in ("run-summary.md", "run-report.json", "run-timeline.json"):
            self.assertTrue((self.output / name).is_file())
        self.assertNotIn("do not leak", (self.output / "run-report.json").read_text())

    def test_no_model_replaces_cached_model_prose_with_deterministic_facts(self):
        story = run_story.build_story(self.output)
        story.update(model_summary="Cached model prose", summary_status="model")
        run_story.write_story(self.output, story)
        with (
            patch.object(summarize, "model_wrapup") as model,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(summarize.main(["--output", str(self.output), "--no-model"]), 0)
        model.assert_not_called()
        self.assertNotIn("Cached model prose", run_story.build_story(self.output)["model_summary"])

    def test_cached_wrapup_survives_unload_events_but_not_changed_grades(self):
        story = run_story.build_story(self.output)
        story.update(model_summary="Visible model wrap-up", summary_status="model")
        run_story.write_story(self.output, story)
        run_story.append_event(self.output, "Model unloaded")
        self.assertEqual(
            run_story.build_story(self.output)["model_summary"], "Visible model wrap-up"
        )
        self.grade["n_passed"] = 39.0
        self.write(self.grade_path, [self.grade])
        self.assertNotEqual(
            run_story.build_story(self.output)["model_summary"], "Visible model wrap-up"
        )

    def test_summary_creation_provenance_survives_report_refresh(self):
        story = run_story.build_story(self.output)
        story.update(model_summary="Visible model wrap-up", summary_status="model")
        run_story.write_story(self.output, story, summary_author="local model fixture")
        before = json.loads((self.output / "run-report.json").read_text())
        run_story.append_event(self.output, "Model unloaded")
        run_story.write_story(self.output, run_story.build_story(self.output))
        after = json.loads((self.output / "run-report.json").read_text())
        self.assertEqual(after["summary_created_at"], before["summary_created_at"])
        self.assertEqual(after["summary_created_by"], "local model fixture")
        self.assertEqual(after["model_summary"], before["model_summary"])

    def test_cloud_endpoint_and_missing_profile_never_make_requests(self):
        for environment in ({}, {**LOCAL, "HARNESS2_LOCAL_BASE_URL": "https://cloud.example/v1"}):
            with (
                self.subTest(environment=environment),
                patch.object(summarize.urllib.request, "build_opener") as request,
            ):
                with self.assertRaises(ValueError):
                    summarize.model_wrapup(run_story.build_story(self.output), environment)
                request.assert_not_called()

    def test_thinking_only_unclosed_and_orphan_blocks_are_filtered(self):
        for raw, expected in (
            ("<think>private", ""),
            ("private</think>visible", "visible"),
            ("<think>a<think>b</think>c</think>final", "final"),
        ):
            self.assertEqual(run_story.public_text(raw), expected)

    def test_oversized_or_reasoning_only_response_is_unavailable(self):
        for raw in (
            b"x" * (run_story.MAX_READ + 1),
            json.dumps(
                {"choices": [{"message": {"content": None, "reasoning_content": "PRIVATE"}}]}
            ).encode(),
        ):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = raw
            opener = Mock()
            opener.open.return_value = response
            with patch.object(summarize.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(ValueError):
                    summarize.model_wrapup(run_story.build_story(self.output), LOCAL)

    def test_malformed_optional_metadata_and_outside_files_are_not_exposed(self):
        outside = self.root / "private"
        outside.mkdir()
        (outside / "PRIVATE.txt").write_text("private", encoding="utf-8")
        self.summary["selections"]["agg"]["chosen_run_dir"] = str(outside)
        self.summary["modes"]["agg"]["stages"] = 5
        self.write(self.task_dir / "summary.json", self.summary)
        self.write(
            self.output / "evidence/completed-workflow-status.json",
            {"executor_outcomes": 3, "quality_result": []},
        )
        self.write(self.output / "run-report.json", {"warnings": None})
        story = run_story.build_story(self.output)
        self.assertNotIn("PRIVATE", json.dumps(story))
        self.assertIn("57.58%", " ".join(story["overview"]))


if __name__ == "__main__":
    unittest.main()
