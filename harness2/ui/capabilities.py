# SPDX-License-Identifier: Apache-2.0
"""Administrative catalog commands; no model calls or component execution."""

from __future__ import annotations

from ..capabilities import CapabilityStore


class Capabilities:
    def __init__(self, workspace, profiles):
        self.store = CapabilityStore(workspace.root)
        self.profiles = profiles

    def state(self):
        result = self.store.state()
        if not result["entries"]:
            return result
        uses = {}
        for summary in self.profiles.list():
            profile = self.profiles.store.get(summary["id"])
            for revision in profile["revisions"]:
                for ref in revision.get("evidence", []):
                    if isinstance(ref, dict) and ref.get("capability_id"):
                        uses.setdefault(ref["capability_id"], []).append({
                            "profile_id": profile["id"], "profile_name": profile["name"],
                            "revision_id": revision["id"], "revision_label": revision.get("label", ""),
                            "active": revision["id"] == profile.get("active_revision_id"),
                            "local_validation": ref.get("local_validation", "unvalidated"),
                        })
        for entry in result["entries"]:
            entry["forks"] = uses.get(entry["id"], [])
            entry["descendants"] = [row["id"] for row in result["entries"]
                                    if entry["id"] in row.get("derived_from", [])]
        return result

    def get(self, entry_id):
        entry = self.store.get(entry_id)
        summary = next(row for row in self.state()["entries"] if row["id"] == entry_id)
        entry["forks"] = summary["forks"]
        entry["descendants"] = summary["descendants"]
        return entry

    def command(self, action, data):
        if action == "settings":
            self.store.set_enabled(data.get("enabled"))
        elif action == "access":
            self.store.set_profile_access(data.get("profile_id"), data.get("enabled"))
        elif action == "publish":
            self.store.publish(
                data.get("profile_id"), data.get("revision_id"),
                configuration_id=data.get("configuration_id"), name=data.get("name", ""),
                description=data.get("description", ""), paths=data.get("paths", []),
                kind=data.get("kind", "other"), portability=data.get("portability", "unknown"),
                why=data.get("why", ""), evidence=data.get("evidence", []),
                evidence_status=data.get("evidence_status", "suspected"),
                costs=data.get("costs", {}), incompatibilities=data.get("incompatibilities", []),
                failures=data.get("failures", []), derived_from=data.get("derived_from", []),
                motivated_by=data.get("motivated_by", []),
            )
        elif action == "fork":
            target = data.get("target_profile_id")
            revision = self.store.fork(
                data.get("capability_id"), target, parent_revision_id=data.get("parent_revision_id"),
                label=data.get("label", ""), why=data.get("why", ""),
                replace_files=data.get("replace_files"), replace_paths=data.get("replace_paths", []),
            )
            return {"profile": self.profiles.get(target), "revision_id": revision["id"], "catalog": self.state()}
        else:
            raise KeyError(action)
        return self.state()
