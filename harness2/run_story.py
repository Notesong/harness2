# SPDX-License-Identifier: Apache-2.0
"""Readable run facts from public result metadata, never agent transcripts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

from .locking import atomic_replace

MAX_READ = 128 * 1024
MAX_EVENTS = 250


def public_text(value, limit: int = 8000) -> str:
    """Remove thinking sections, including unmatched tags, from a final message."""
    if not isinstance(value, str):
        return ""
    chunks, depth, offset = [], 0, 0
    for match in re.finditer(
        r"<\s*(/?)\s*(?:think|thinking|analysis|reasoning)\b[^>]*>", value, re.I
    ):
        if depth == 0:
            chunks.append(value[offset : match.start()])
        if match.group(1):
            if depth:
                depth -= 1
            else:
                chunks.clear()  # An orphan closer may follow unlabelled reasoning.
        else:
            depth += 1
        offset = match.end()
    if depth == 0:
        chunks.append(value[offset:])
    return "".join(c for c in "".join(chunks) if c in "\n\t" or ord(c) >= 32)[:limit].strip()


def _load(path: Path, root: Path):
    try:
        if not path.resolve().is_relative_to(root.resolve()):
            return None
        with path.open("rb") as stream:
            raw = stream.read(MAX_READ + 1)
        return json.loads(raw) if len(raw) <= MAX_READ else None
    except (OSError, ValueError):
        return None


def _files(root: Path, pattern: str):
    if root.is_dir():
        for path in sorted(islice(root.rglob(pattern), 1000)):
            if path.resolve().is_relative_to(root.resolve()):
                yield path


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _rows(value) -> list[dict]:
    rows = (
        value
        if isinstance(value, list)
        else value.get("rows", [])
        if isinstance(value, dict)
        else []
    )
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _label(value) -> str:
    return public_text(str(value or ""), 160)


def _facts(output: Path, record: dict | None) -> tuple[dict, bool]:
    root = Path(output).resolve()
    overview, warnings, events = [], [], []
    graded = 0
    baseline_graded = False
    summaries = []
    judged = {}

    def event(time, title, detail, kind="result"):
        row = {
            "time": _label(time),
            "title": public_text(title, 200),
            "detail": public_text(detail, 1800),
            "kind": _label(kind) or "result",
        }
        if row["title"] and row not in events:
            events.append(row)

    timeline = root / "run-timeline.jsonl"
    try:
        if not timeline.resolve().is_relative_to(root):
            raise OSError("Timeline is outside the experiment")
        with timeline.open("rb") as stream:
            for line in stream.read(MAX_READ).splitlines()[:MAX_EVENTS]:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    event(
                        row.get("time"),
                        row.get("title", ""),
                        row.get("detail", ""),
                        row.get("kind", "phase"),
                    )
    except OSError:
        pass

    for path in _files(root, "summary.json"):
        # Dataset documents and archived failures are not experiment summaries.
        if any(
            part in ("_audit", "evidence", "dataset", "task_folder", "harnesses")
            or part.startswith(".")
            for part in path.relative_to(root).parts
        ):
            continue
        data = _load(path, root)
        if (
            isinstance(data, dict)
            and isinstance(data.get("task_id"), str)
            and (isinstance(data.get("selections"), dict) or isinstance(data.get("modes"), dict))
        ):
            data["_story_directory"] = path.parent
            summaries.append(data)
            grade_path = path.parent.parent / "judged.json"
            for row in _rows(_load(grade_path, root)):
                key = (row.get("task_id"), row.get("mode"), row.get("k"), row.get("label"))
                if all(isinstance(item, (str, int, float, bool, type(None))) for item in key):
                    judged[key] = row
            baseline_graded = baseline_graded or any(
                row.get("graded") is True
                for row in _rows(_load(path.parent.parent / "base_judged.json", root))
            )
    if summaries:
        done = sum(row.get("status") == "ok" for row in summaries)
        overview.append(
            f"{len(summaries)} task{'s' if len(summaries) != 1 else ''} recorded; "
            f"{done} workflow{'s' if done != 1 else ''} finished."
        )
    else:
        overview.append("No task outcome has been recorded yet.")

    seen_stages = set()
    for summary in summaries[:100]:
        task = _label(summary["task_id"])
        selections = summary.get("selections") or {}
        if isinstance(selections, dict):
            for mode, selection in sorted(selections.items()):
                if not isinstance(selection, dict):
                    continue
                chosen = _label(selection.get("chosen_label"))
                if chosen:
                    mode_name = {"agg": "parallel", "seq": "sequential"}.get(mode, _label(mode))
                    overview.append(f"{task}: selected {chosen} for the {mode_name} result.")
                    tree = summary["_story_directory"] / "harnesses" / chosen
                    if tree.resolve().is_relative_to(root):
                        for name in ("LongTermMEMORY.md", "guardrails.md"):
                            policy = tree / name
                            try:
                                if not policy.resolve().is_relative_to(root):
                                    continue
                                with policy.open("rb") as handle:
                                    policy_text = handle.read(MAX_READ).decode(
                                        "utf-8", errors="replace"
                                    )
                                headings = re.findall(r"(?m)^#{1,6}\s+(.+)$", policy_text)[:8]
                                if headings:
                                    overview.append(
                                        f"Selected guidance in {name}: {'; '.join(public_text(item, 180) for item in headings)}."
                                    )
                            except OSError:
                                pass
                target = selection.get("chosen_run_dir")
                if isinstance(target, str) and target:
                    folder = Path(target)
                    if not folder.is_absolute():
                        folder = root / folder
                    if folder.resolve().is_relative_to(root) and folder.is_dir():
                        files = [
                            p
                            for p in islice(folder.rglob("*"), 2000)
                            if p.is_file()
                            and p.resolve().is_relative_to(folder.resolve())
                            and not any(
                                part.startswith(".") for part in p.relative_to(folder).parts
                            )
                        ]
                        total = 0
                        for item in files:
                            try:
                                total += item.stat().st_size
                            except OSError:
                                pass
                        overview.append(
                            f"Selected output: {len(files)} file{'s' if len(files) != 1 else ''} totaling {total:,} bytes."
                        )
                        if 0 < len(files) <= 8:
                            overview.append(
                                "Output files: "
                                + "; ".join(sorted(_label(p.name) for p in files))
                                + "."
                            )
        modes = summary.get("modes") or {}
        for result in modes.values() if isinstance(modes, dict) else []:
            if not isinstance(result, dict):
                continue
            stages = result.get("stages")
            for stage in stages if isinstance(stages, list) else []:
                if not isinstance(stage, dict):
                    continue
                label = _label(stage.get("label"))
                seen_stages.add(label)
                attempts = stage.get("attempts") if type(stage.get("attempts")) is int else None
                detail = f"{task}: {attempts} attempt(s)." if attempts else task
                if stage.get("fell_back"):
                    detail += " The proposal fell back to the existing harness."
                    warnings.append(
                        f"{label}: proposal fallback recorded; this was not an accepted improvement."
                    )
                elif stage.get("kept_base"):
                    detail += " The existing harness was retained."
                elif not stage.get("error"):
                    detail += " The candidate harness was accepted for the next step."
                else:
                    detail += " The proposal did not complete successfully."
                if "timeout" in str(stage.get("error", "")).lower():
                    detail += " A proposal timeout was recorded."
                event(
                    "",
                    f"Harness proposal · {label}",
                    detail,
                    "fallback" if stage.get("fell_back") else "proposal",
                )

    for row in judged.values():
        passed, possible = _number(row.get("n_passed")), _number(row.get("n_criteria"))
        task = _label(row.get("task_id"))
        if (
            row.get("graded") is not True
            or passed is None
            or possible is None
            or possible <= 0
            or not 0 <= passed <= possible
        ):
            warnings.append(
                f"{task}: grading is unavailable or incomplete; no zero score is inferred."
            )
            continue
        graded += 1
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        weighted = "Weighted score" if raw.get("metric") == "weighted" else "Score"
        detail = f"{task}: {weighted} {passed:g}/{possible:g} ({100 * passed / possible:.2f}%)."
        cp, cc = _number(raw.get("criteria_passed")), _number(raw.get("criteria_count"))
        if cp is not None and cc is not None and cc > 0 and 0 <= cp <= cc:
            detail += f" {cp:g}/{cc:g} criteria passed."
        if row.get("all_pass") is False:
            detail += " Not all criteria passed."
        overview.append(detail)
        if raw.get("judge_model"):
            overview.append(f"Judge model: {_label(raw['judge_model'])}.")
        event("", "Grading result recorded", detail, "grade")

    legacy = _load(root / "evidence/completed-workflow-status.json", root)
    if not isinstance(legacy, dict):
        legacy = _load(root / "evidence/latest-numeric-status.json", root)
    if isinstance(legacy, dict):
        outcomes = legacy.get("executor_outcomes")
        for outcome in outcomes if isinstance(outcomes, list) else []:
            if not isinstance(outcome, dict):
                continue
            label = _label(outcome.get("label"))
            amount = outcome.get("delivered_required_files")
            size = _number(outcome.get("bytes"))
            detail = (
                f"{amount} recorded deliverable(s)"
                if type(amount) is int
                else "Executor output recorded"
            )
            if size is not None:
                detail += f", {size:,.0f} bytes"
            natural = outcome.get("natural_final_stop_observed")
            if natural is True:
                detail += "; a natural final stop was observed."
            elif natural is False:
                detail += "; no natural final stop was observed. Treat this intermediate output as partial."
                warnings.append(
                    f"{label}: intermediate probe output was partial; nonempty output does not establish task completion."
                )
            event(
                outcome.get("completed_utc") or outcome.get("output_collected_utc"),
                f"Executor outcome · {label}",
                detail,
                "executor",
            )
            if natural is True and label.startswith(("agg", "seq", "final")):
                overview.append(f"Final execution: {detail}")
        accepted = legacy.get("accepted_harness_changes") or {}
        if isinstance(accepted, dict):
            for label, files in accepted.items():
                if isinstance(files, list) and files:
                    overview.append(
                        f"{_label(label)} harness changes accepted in: {', '.join(_label(name) for name in files[:10])}."
                    )
        quality = legacy.get("quality_result")
        quality = quality if isinstance(quality, dict) else {}
        if not graded and not judged and quality.get("graded") is True:
            passed, possible = (
                _number(quality.get("weighted_points_passed")),
                _number(quality.get("weighted_points_possible")),
            )
            if passed is not None and possible is not None and possible > 0:
                overview.append(
                    f"Weighted score {passed:g}/{possible:g} ({100 * passed / possible:.2f}%)."
                )
                graded += 1
        provenance = quality.get("provenance")
        if isinstance(provenance, str):
            warnings.append(public_text(provenance, 1200))
        if legacy.get("native_repair_applied") is True:
            overview.append(
                "Judging recovered after a Windows compatibility repair. The original failed attempt is preserved in the history."
            )

    for relative, recovery in (
        ("evidence/workflow-status.json", False),
        ("evidence/unattended-follow-through/status.json", True),
    ):
        data = _load(root / relative, root)
        if not isinstance(data, dict):
            continue
        stages = data.get("stages") or []
        if isinstance(stages, dict):
            stages = [
                {**value, "name": key} for key, value in stages.items() if isinstance(value, dict)
            ]
        for stage in stages if isinstance(stages, list) else []:
            if not isinstance(stage, dict):
                continue
            name = _label(stage.get("name"))
            friendly = {
                "readiness": "Setup check",
                "sweep": "Harness refinement",
                "judge": "Judging",
                "report": "Score report",
            }.get(name, name)
            title = f"{'Recovery · ' if recovery else ''}{friendly}"
            if stage.get("started_utc"):
                event(
                    stage["started_utc"],
                    title + " started",
                    "Recorded workflow phase started.",
                    "phase",
                )
            code = stage.get("exit_code")
            if stage.get("ended_utc"):
                event(
                    stage["ended_utc"],
                    title + " finished",
                    f"Recorded exit code: {code}."
                    if type(code) is int
                    else "Phase return recorded.",
                    "phase",
                )
            if type(code) is int and code != 0:
                warnings.append(
                    f"{'Original ' if not recovery else 'Recovery '}{name} phase returned exit {code}; later recovery events are shown separately."
                )

    for path in _files(root, "*.meta.json"):
        if "_audit" not in path.relative_to(root).parts:
            continue
        meta = _load(path, root)
        if not isinstance(meta, dict) or not isinstance(meta.get("ok"), bool):
            continue
        label = _label(meta.get("label"))
        if label.split(".a", 1)[0] in seen_stages:
            continue
        detail = (
            "Valid proposal output recorded."
            if meta["ok"]
            else "Proposal failed; no improvement is established."
        )
        if type(meta.get("attempts")) is int:
            detail += f" Attempts: {meta['attempts']}."
        event("", f"Proposal outcome · {label}", detail, "proposal")

    if record:
        if record.get("started_at"):
            event(record["started_at"], "Operation started", _label(record.get("title")), "phase")
        if record.get("finished_at"):
            event(
                record["finished_at"],
                "Operation finished",
                f"Recorded status: {_label(record.get('status'))}.",
                "phase",
            )
        if record.get("status") in ("failed", "cancelled", "interrupted"):
            warnings.append(
                "The operation did not finish normally. Completed files and any recorded grades remain available."
            )
    if not graded:
        overview.append("Grading has not produced a recorded quality score yet.")
    elif not baseline_graded:
        overview.append(
            "No baseline grade is recorded. The selected result's score does not establish improvement over the baseline."
        )

    def event_order(row):
        try:
            timestamp = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return (0, timestamp.timestamp())
        except (TypeError, ValueError, OverflowError):
            return (1, 0)

    events.sort(key=event_order)
    warnings = list(dict.fromkeys(warning for warning in warnings if warning))[:40]
    overview = list(dict.fromkeys(overview))[:80]
    story = {
        "overview": overview,
        "events": events[:MAX_EVENTS],
        "model_summary": "\n\n".join(
            overview + (["Recorded cautions: " + " ".join(warnings)] if warnings else [])
        ),
        "summary_status": "deterministic" if graded else "pending",
        "warnings": warnings,
    }
    return story, bool(graded)


def evidence_digest(story: dict) -> str:
    """Keep a wrap-up through later unload events; invalidate changed task/grade facts."""
    return hashlib.sha256(json.dumps(story["overview"], ensure_ascii=False).encode()).hexdigest()


def build_story(output: Path, record: dict | None = None) -> dict:
    story, _ = _facts(Path(output), record)
    saved = _load(Path(output) / "run-report.json", Path(output))
    if isinstance(saved, dict) and saved.get("evidence_digest") == evidence_digest(story):
        summary = public_text(saved.get("model_summary"))
        if summary and saved.get("summary_status") in (
            "model",
            "deterministic",
            "unavailable",
            "pending",
        ):
            story.update(model_summary=summary, summary_status=saved["summary_status"])
            saved_warnings = saved.get("warnings")
            for warning in saved_warnings if isinstance(saved_warnings, list) else []:
                if isinstance(warning, str) and warning.startswith("Model wrap-up:"):
                    story["warnings"].append(public_text(warning, 1000))
    return story


def has_graded_results(output: Path) -> bool:
    return _facts(Path(output), None)[1]


def deterministic_story(output: Path) -> dict:
    return _facts(Path(output), None)[0]


def write_story(output: Path, story: dict, *, summary_author: str | None = None) -> None:
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    written_at = datetime.now(timezone.utc).isoformat()
    saved = _load(output / "run-report.json", output)
    saved = saved if isinstance(saved, dict) else {}
    same_summary = saved.get("model_summary") == story["model_summary"]
    created_at = (
        (saved.get("summary_created_at") or saved.get("written_at")) if same_summary else None
    ) or written_at
    created_by = (
        summary_author
        or (saved.get("summary_created_by") if same_summary else None)
        or (
            "local model (identity not recorded)"
            if story["summary_status"] == "model"
            else "recorded result metadata"
        )
    )
    report = {
        **story,
        "evidence_digest": evidence_digest(story),
        "written_at": written_at,
        "summary_created_at": created_at,
        "summary_created_by": created_by,
    }
    markdown = (
        "# Run summary\n\n"
        + f"Prepared from saved results at {written_at}.\n\n"
        + "\n\n".join(story["overview"])
    )
    markdown += "\n\n## Wrap-up\n\n" + story["model_summary"]
    markdown += f"\n\nWrap-up created at {created_at} by {created_by}, from saved result metadata."
    if story["warnings"]:
        markdown += "\n\n## Recorded cautions\n\n" + "\n".join(
            "- " + item for item in story["warnings"]
        )
    markdown += "\n\n## Timeline\n\n" + "\n".join(
        f"- {row['time'] or 'Time not recorded'} — {row['title']}: {row['detail']}"
        for row in story["events"]
    )
    for name, body in (
        ("run-report.json", json.dumps(report, indent=2, ensure_ascii=False)),
        ("run-timeline.json", json.dumps(story["events"], indent=2, ensure_ascii=False)),
        ("run-summary.md", markdown + "\n"),
    ):
        target = output / name
        descriptor, temporary_name = tempfile.mkstemp(prefix=".run-report-", dir=output)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(body)
            atomic_replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def append_event(output: Path, title: str, detail: str = "", kind: str = "phase") -> None:
    root = Path(output).resolve()
    target = root / "run-timeline.jsonl"
    if not target.resolve().is_relative_to(root):
        raise ValueError("Timeline is outside the experiment")
    row = {
        "time": datetime.now(timezone.utc).isoformat(),
        "title": public_text(title, 200),
        "detail": public_text(detail, 1800),
        "kind": _label(kind),
    }
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
