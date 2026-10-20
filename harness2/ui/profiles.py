# SPDX-License-Identifier: Apache-2.0
"""Profile commands for Studio; all model execution remains in JobManager."""

from __future__ import annotations

from ..core import tree
from ..profile_runtime import candidate_from_run
from ..profiles import ProfileStore
from ..substrate.opencode import OPENCODE_SURFACE


class Profiles:
    def __init__(self, workspace, jobs):
        self.workspace = workspace
        self.jobs = jobs
        self.store = ProfileStore(workspace.root)

    def list(self):
        rows = self.store.list()
        for row in rows:
            for name in ("configuration", "revision", "run"):
                row[name + "_count"] = row.get(name + "s_count", 0)
        return rows

    def get(self, identifier):
        result = self.store.get(identifier)
        outputs = {row.get("output_dir"): row["id"] for row in self.jobs.list()}
        for row in result.get("runs", []):
            if not row.get("job_id") and row.get("output_dir") in outputs:
                row["job_id"] = outputs[row["output_dir"]]
        if result.get("active_configuration_id") and result.get("active_revision_id"):
            from ..profile_inventory import build_inventory

            result["capabilities"] = build_inventory(result)
        return result

    def inventory(self, identifier, revision_id=None, configuration_id=None):
        from ..profile_inventory import build_inventory

        return build_inventory(self.store.get(identifier), revision_id=revision_id,
                               configuration_id=configuration_id)

    def create(self, data):
        return self.store.create(data.get("name", ""), data.get("description", ""))

    def command(self, identifier, action, data):
        current = self.store.get(identifier)
        if action == "configurations":
            from ..profile_import import capture_configuration

            self.store.add_configuration(
                identifier, capture_configuration(self.workspace.environment()),
                label=data.get("label", ""),
            )
        elif action == "baseline":
            from ..benchmarks.jobbench import OC_BASE_HARNESS

            self.store.add_revision(
                identifier, tree.read_tree(OPENCODE_SURFACE, OC_BASE_HARNESS),
                label=data.get("label") or "Original JobBench baseline",
                why="Clean bundled JobBench baseline; no other profile's adaptations.",
                operation="baseline",
            )
        elif action == "activate":
            self.store.activate(
                identifier, configuration_id=data.get("configuration_id"),
                revision_id=data.get("revision_id"), why=data.get("why", ""),
            )
        elif action == "notes":
            self.store.add_note(
                identifier, text=data.get("text", ""),
                evidence_status=data.get("evidence_status", "suspected"),
                references=data.get("references", ()), motivated_by=data.get("motivated_by", ()),
            )
        elif action == "retire":
            self.store.retire(identifier, data.get("revision_id"), why=data.get("why", ""))
        elif action == "import":
            source_id = data.get("source_profile_id")
            if source_id == identifier:
                raise ValueError("Choose another profile to borrow from; use ancestry for local revisions.")
            source = self.store.get(source_id)
            revision = next((row for row in source["revisions"]
                             if row["id"] == data.get("revision_id")), None)
            if not revision:
                raise ValueError("Choose the source revision to import.")
            self.store.add_revision(
                identifier, revision["tree"], label=data.get("label") or "Borrowed from " + source["name"],
                parents=[current["active_revision_id"]] if current.get("active_revision_id") else [],
                borrowed_from=[{"profile_id": source_id, "revision_id": revision["id"]}],
                why=data.get("why") or "Imported as a hypothesis; not yet tested for this profile.",
                domain=revision.get("domain", ""), operation="import",
                experience=revision.get("experience", {}),
            )
        elif action == "from-run":
            record = self.jobs.get(data.get("job_id"))
            parents = data.get("parents")
            if parents is None:
                pinned = record.get("profile") or {}
                parent = pinned.get("revision_id") if pinned.get("profile_id") == identifier else None
                parents = [parent] if parent else []
            candidate_from_run(
                self.store, identifier, record, label=data.get("label", ""),
                why=data.get("why", ""), parents=parents,
                task_id=data.get("task_id"), mode=data.get("mode"),
            )
        elif action == "revisions":
            revision = next((row for row in current["revisions"]
                             if row["id"] == data.get("parent_id")), None)
            if not revision:
                raise ValueError("Choose the parent revision to simplify.")
            removals = data.get("remove_paths", [])
            if (not isinstance(removals, list) or not removals
                    or any(not isinstance(path, str) or path not in revision["tree"]
                           for path in removals)):
                raise ValueError("Choose existing files to remove from this candidate.")
            simplified = {path: body for path, body in revision["tree"].items() if path not in removals}
            self.store.add_revision(
                identifier, simplified, parents=[revision["id"]],
                label=data.get("label") or "Simplification candidate", operation="simplify",
                why=data.get("why") or "Test whether these files still earn their cost.",
                domain=revision.get("domain", ""), experience=revision.get("experience", {}),
                motivated_by=data.get("motivated_by", ()),
            )
        else:
            raise KeyError(action)
        return self.get(identifier)
