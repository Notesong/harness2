# SPDX-License-Identifier: Apache-2.0
"""Saved inventory metadata and honest runtime qualifications; no model calls."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2 import config
from harness2.profile_inventory import CATALOG_COMMIT, build_inventory
from harness2.profiles import ProfileStore
from harness2.ui.profiles import Profiles
from harness2.ui.workspace import Workspace


class ProfileInventoryTests(unittest.TestCase):
    def profile(self):
        return {
            "id": "owen", "name": "Owen", "active_configuration_id": "a" * 64,
            "active_revision_id": "b" * 64,
            "configurations": [{"id": "a" * 64, "label": "Managed local recipe", "snapshot": {
                "settings": {"provider": "local", "solver": "local/qwen-fixture", "local_context": "65536",
                             "local_output": "8192", "local_runtime": "managed",
                             "local_weights": "C:/PRIVATE_WEIGHTS/model.gguf"},
                "identity": {"tools": {"status": "not_declared", "policy": None,
                    "declared_configuration_sha256": None,
                    "configuration_file": {"path": "C:/PRIVATE_CONFIG/provider.json"}}}}}],
            "revisions": [{"id": "b" * 64, "label": "Composed harness", "status": "active",
                "operation": "compose", "domain": "technical_writers", "tree": {
                    "systemprompt.md": "PRIVATE_SYSTEM_PROMPT_SENTINEL",
                    "guardrails.md": "PRIVATE_GUARDRAIL_SENTINEL",
                    "LongTermMEMORY.md": "PRIVATE_TECHNICAL_MEMORY_SENTINEL",
                    "tool_descriptions/tool_guidance.md": "PRIVATE_TOOL_GUIDANCE_SENTINEL"},
                "experience": {"lesson": "PRIVATE_EXPERIENCE_SENTINEL"}}],
            "notes": [{"text": "PRIVATE_NOTE_SENTINEL"}],
        }

    def test_four_always_on_files_and_no_custom_components_do_not_mean_no_runtime_tools(self):
        inventory = build_inventory(self.profile())
        self.assertEqual(len(inventory["components"]["always_on"]), 4)
        self.assertTrue(all(item["present"] for item in inventory["components"]["always_on"]))
        for name in ("skills", "scripts", "subagents", "plugins"):
            self.assertEqual(inventory["components"][name], [])
        tools = {item["id"]: item for item in inventory["builtin_tools"]}
        self.assertIn("bash", tools)
        self.assertIn("read", tools)
        self.assertIn("general/explore", tools["task"]["qualification"])
        self.assertIn("no profile-installed skills", tools["skill"]["qualification"])
        self.assertEqual(inventory["configuration"]["tool_policy_status"], "not_declared")
        self.assertIsNone(inventory["configuration"]["tool_policy_sha256"])
        self.assertEqual(inventory["runtime"]["commit"], CATALOG_COMMIT)
        self.assertEqual(inventory["runtime"]["version"], "1.14.18")
        self.assertIn("not a live", inventory["runtime"]["source"])

    def test_inventory_never_returns_prompt_bodies_experience_or_private_configuration_paths(self):
        inventory = build_inventory(self.profile())
        serialized = json.dumps(inventory)
        for marker in ("PRIVATE_SYSTEM_PROMPT", "PRIVATE_GUARDRAIL", "PRIVATE_TECHNICAL_MEMORY",
                       "PRIVATE_TOOL_GUIDANCE", "PRIVATE_EXPERIENCE", "PRIVATE_NOTE",
                       "PRIVATE_CONFIG", "PRIVATE_WEIGHTS"):
            self.assertNotIn(marker, serialized)
        memory = next(item for item in inventory["components"]["always_on"] if item["path"] == "LongTermMEMORY.md")
        self.assertEqual(memory["label"], "Technical memory")
        self.assertIn("separate from conversational", memory["description"])
        self.assertEqual(len(memory["sha256"]), 64)
        self.assertTrue(all(not Path(item["path"]).is_absolute() for item in inventory["components"]["always_on"]))

    def test_component_inventory_does_not_import_compile_or_execute_model_authored_code(self):
        profile = self.profile()
        tree = profile["revisions"][0]["tree"]
        tree.update({
            "skills/checkpoint/SKILL.md": "---\nname: checkpoint\ndescription: PRIVATE_SKILL_TEXT\n---\nDo work.",
            "skills/checkpoint/scripts/helper.py": "THIS IS NOT EVEN VALID PYTHON",
            "sub_agents/reviewer/AGENT.md": "---\nmode: subagent\ndescription: PRIVATE_AGENT_TEXT\n---\nRead things.",
            "scripts/check.py": "raise AssertionError('MUST NOT EXECUTE')",
            "plugin/inspect.ts": "throw new Error('MUST NOT EXECUTE')",
        })
        with patch("subprocess.Popen", side_effect=AssertionError("Must not start a process")), \
                patch("builtins.compile", side_effect=AssertionError("Must not compile a component")), \
                patch("importlib.import_module", side_effect=AssertionError("Must not import a component")):
            inventory = build_inventory(profile)
        self.assertEqual([item["name"] for item in inventory["components"]["skills"]], ["checkpoint"])
        self.assertEqual([item["name"] for item in inventory["components"]["subagents"]], ["reviewer"])
        self.assertEqual(len(inventory["components"]["scripts"]), 2)
        self.assertEqual(inventory["components"]["plugins"][0]["path"], "plugin/inspect.ts")
        self.assertNotIn("PRIVATE_SKILL_TEXT", json.dumps(inventory))
        self.assertNotIn("PRIVATE_AGENT_TEXT", json.dumps(inventory))

    def test_selected_candidate_and_configuration_are_distinct_from_active_selection(self):
        profile = self.profile()
        candidate = copy.deepcopy(profile["revisions"][0])
        candidate.update(id="c" * 64, label="A smaller candidate", status="candidate", operation="simplify")
        candidate["tree"].pop("LongTermMEMORY.md")
        profile["revisions"].append(candidate)
        configuration = copy.deepcopy(profile["configurations"][0])
        configuration.update(id="d" * 64, label="A different budget")
        configuration["snapshot"]["settings"]["local_context"] = "32768"
        profile["configurations"].append(configuration)
        before = copy.deepcopy(profile)
        selected = build_inventory(profile, revision_id=candidate["id"], configuration_id=configuration["id"])
        self.assertFalse(selected["revision"]["is_active"])
        self.assertFalse(selected["configuration"]["is_active"])
        self.assertEqual(selected["revision"]["status"], "candidate")
        self.assertEqual(selected["configuration"]["context"], "32768")
        self.assertNotEqual(selected["revision"]["content_sha256"], build_inventory(profile)["revision"]["content_sha256"])
        self.assertFalse(next(item for item in selected["components"]["always_on"] if item["path"] == "LongTermMEMORY.md")["present"])
        self.assertIn("does not establish", selected["configuration"]["association"])
        self.assertEqual(profile, before)

    def test_application_permissions_do_not_claim_an_os_or_shell_sandbox(self):
        inventory = build_inventory(self.profile())
        permissions = inventory["permissions"]
        self.assertFalse(permissions["os_sandbox"])
        self.assertFalse(permissions["shell_confined"])
        self.assertEqual(permissions["kind"], "application-policy")
        self.assertIn("not an OS sandbox", permissions["summary"])
        self.assertIn("host temporary", permissions["working_run"])
        self.assertIn("conversation workspace", permissions["idle_chat"])
        self.assertIn("command/path patterns", permissions["qualification"])
        app_tool = inventory["application_tools"][0]
        self.assertEqual(app_tool["id"], "harness2_conversation")
        self.assertIn("not a profile-installed plugin", app_tool["qualification"])

    def test_network_services_and_model_dependent_tools_are_qualified(self):
        tools = {item["id"]: item for item in build_inventory(self.profile())["builtin_tools"]}
        self.assertEqual(tools["websearch"]["status"], "conditional")
        self.assertIn("OPENCODE_ENABLE_EXA", tools["websearch"]["qualification"])
        self.assertIn("remote Exa", tools["codesearch"]["qualification"])
        self.assertIn("reachable site", tools["webfetch"]["qualification"])
        self.assertIn("gpt-4", tools["apply_patch"]["qualification"])
        self.assertIn("language-server installation", tools["lsp"]["qualification"])
        self.assertTrue(all(item["status"] != "enabled" for item in tools.values()))

    def test_saved_policy_denials_are_qualified_and_private_rules_are_not_returned(self):
        profile = self.profile()
        profile["configurations"][0]["snapshot"]["identity"]["tools"].update({
            "status": "declared_sources_captured", "declared_configuration_sha256": "e" * 64,
            "policy": {"tools": {"bash": False, "read": True}, "permission": {
                "edit": "deny", "external_directory": {"*": "deny", "C:/PRIVATE_PATH/**": "allow"}}}})
        inventory = build_inventory(profile)
        tools = {item["id"]: item for item in inventory["builtin_tools"]}
        self.assertEqual(tools["bash"]["status"], "disabled-by-declared-policy")
        self.assertEqual(tools["write"]["status"], "disabled-by-declared-policy")
        self.assertEqual(tools["read"]["status"], "runtime-dependent")
        self.assertIn("live session were not inspected", tools["bash"]["qualification"])
        self.assertEqual(inventory["configuration"]["tool_policy_sha256"], "e" * 64)
        self.assertNotIn("PRIVATE_PATH", json.dumps(inventory))
        profile["configurations"][0]["snapshot"]["identity"]["tools"]["policy"]["permission"]["read"] = {
            "*": "deny", "C:/PRIVATE_EXCEPTION/**": "allow"}
        patterned = {item["id"]: item for item in build_inventory(profile)["builtin_tools"]}
        self.assertEqual(patterned["read"]["status"], "runtime-dependent")

    def test_unknown_pin_does_not_assert_an_outdated_builtin_catalog(self):
        with patch.dict(config.DEPENDENCY_PINS, {"opencode": "f" * 64}):
            inventory = build_inventory(self.profile())
        self.assertFalse(inventory["runtime"]["catalog_matches_pin"])
        self.assertIsNone(inventory["runtime"]["version"])
        self.assertEqual(inventory["builtin_tools"], [])
        self.assertTrue(any("pin changed" in item for item in inventory["limits"]))

    def test_bad_or_missing_selections_and_unsafe_paths_fail_without_mutation(self):
        profile = self.profile()
        before = copy.deepcopy(profile)
        for arguments in ({"revision_id": "f" * 64}, {"configuration_id": "f" * 64}):
            with self.assertRaises(ValueError):
                build_inventory(profile, **arguments)
        profile["revisions"][0]["tree"]["../outside.py"] = "Never read it"
        with self.assertRaises(ValueError):
            build_inventory(profile)
        del profile["revisions"][0]["tree"]["../outside.py"]
        self.assertEqual(profile, before)

    def test_profiles_service_exposes_active_and_candidate_inventory_without_archive_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ProfileStore(root)
            store.create("Owen")
            configuration = store.add_configuration("owen", {"settings": {"provider": "local"}, "identity": {}})
            active = store.add_revision("owen", {"guardrails.md": "An active rule."})
            candidate = store.add_revision("owen", {"guardrails.md": "A candidate rule.", "scripts/check.py": "pass\n"})
            store.activate("owen", configuration_id=configuration["id"], revision_id=active["id"])
            before = {path: path.read_bytes() for path in store.root.rglob("*.json")}
            service = Profiles(Workspace(root), SimpleNamespace(list=lambda: []))
            self.assertEqual(service.get("owen")["capabilities"]["revision"]["id"], active["id"])
            projected = service.inventory("owen", revision_id=candidate["id"])
            self.assertEqual(projected["revision"]["id"], candidate["id"])
            self.assertFalse(projected["revision"]["is_active"])
            self.assertEqual(len(projected["components"]["scripts"]), 1)
            self.assertEqual(store.get("owen")["active_revision_id"], active["id"])
            self.assertEqual({path: path.read_bytes() for path in store.root.rglob("*.json")}, before)


if __name__ == "__main__":
    unittest.main()
