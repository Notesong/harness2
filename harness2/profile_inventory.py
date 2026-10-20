# SPDX-License-Identifier: Apache-2.0
"""Read-only profile inventory; contents are counted and hashed, never executed.

The built-in catalog describes the pinned OpenCode source, not a live session's
effective tools. Profile files, runtime tools, and application permissions are
separate layers. In particular, external-directory permissions are not an OS
sandbox or a guarantee that host shell commands cannot access other files.
"""

from __future__ import annotations

import hashlib
import re

from . import config
from .core import tree as HT
from .profiles import MAX_TREE_BYTES, MAX_TREE_FILES, _hash, _id
from .substrate.opencode import OPENCODE_SURFACE

OPENCODE_VERSION = "1.14.18"
CATALOG_COMMIT = "23fb5e0516c99ac04a1aa46c193efda2e1b9bb24"
_MODE = frozenset({"allow", "ask", "deny"})
_PUBLIC_KEY = re.compile(r"[a-zA-Z0-9_*-]{1,80}\Z")

_ALWAYS_ON = {
    "systemprompt.md": ("System prompt", "The profile's always-on task instructions."),
    "guardrails.md": ("Guardrails", "The profile's always-on behavioral rules."),
    "LongTermMEMORY.md": ("Technical memory", "Harness instructions in the selected revision; separate from conversational memory."),
    "tool_descriptions/tool_guidance.md": ("Tool guidance", "The profile's always-on guidance for using its tools."),
}

_BUILTINS = (
    ("read", "Read files", "Read file contents.", "runtime-dependent",
     "Subject to agent permissions and OpenCode's file access checks."),
    ("glob", "Find files", "Find paths matching a pattern.", "runtime-dependent",
     "Subject to agent permissions and the runtime's filesystem access."),
    ("grep", "Search file contents", "Search text in files.", "runtime-dependent",
     "Subject to agent permissions and the runtime's filesystem tools."),
    ("edit", "Edit files", "Change existing text files.", "runtime-dependent",
     "The pinned router uses edit/write instead of apply_patch for model IDs outside its GPT filter."),
    ("write", "Write files", "Create or replace text files.", "runtime-dependent",
     "The pinned router uses edit/write instead of apply_patch for model IDs outside its GPT filter."),
    ("bash", "Run shell commands", "Run programs and shell commands on the host.", "runtime-dependent",
     "Commands run with the app user's OS permissions. OpenCode permissions are not an OS sandbox."),
    ("task", "Delegate work", "Ask a configured OpenCode subagent to work on a task.", "runtime-dependent",
     "OpenCode has built-in general/explore agents; profile-defined subagents and task permissions are separate."),
    ("todowrite", "Track a task list", "Maintain the current session's task list.", "runtime-dependent",
     "A session task list is separate from the durable conversation recovery ledger."),
    ("skill", "Load a skill", "Load an available on-demand skill.", "runtime-dependent",
     "The loader can exist with no profile-installed skills. Global or discovered skills were not inspected."),
    ("webfetch", "Fetch a web page", "Retrieve a supplied web URL.", "runtime-dependent",
     "Requires permitted network access and a reachable site; a local model does not make this tool offline."),
    ("websearch", "Search the web", "Search through OpenCode's Exa integration.", "conditional",
     "Exposed for the OpenCode provider or OPENCODE_ENABLE_EXA. A local provider needs that flag, permitted network access, and the remote Exa service."),
    ("codesearch", "Search external code references", "Find external API/library code references through Exa.", "conditional",
     "Exposed for the OpenCode provider or OPENCODE_ENABLE_EXA; depends on remote Exa access and service conditions."),
    ("apply_patch", "Apply a patch", "Apply structured file patches.", "conditional",
     "Selected for model IDs containing gpt- but excluding oss and gpt-4; edit/write are filtered out for that route."),
    ("question", "Ask a question", "Request an answer through OpenCode's question interface.", "conditional",
     "Depends on the client type or OPENCODE_ENABLE_QUESTION_TOOL, the answering interface, and permissions."),
    ("lsp", "Use a language server", "Inspect code through configured language servers.", "conditional",
     "Requires OPENCODE_EXPERIMENTAL_LSP_TOOL and a working language-server installation/configuration. Local-model setup disables automatic LSP downloads."),
    ("plan_exit", "Leave planning mode", "Leave an OpenCode planning session.", "conditional",
     "Requires the experimental plan-mode flag, CLI client, and applicable agent permissions."),
)


def _permission(value):
    """Expose decision modes, never command/path patterns or arbitrary values."""
    if isinstance(value, str):
        return value if value in _MODE else None
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if isinstance(key, str) and _PUBLIC_KEY.fullmatch(key):
                mode = _permission(item)
                if mode is not None:
                    result[key] = mode
        return result
    return None


def _policy(configuration):
    tools = configuration.get("snapshot", {}).get("identity", {}).get("tools", {})
    tools = tools if isinstance(tools, dict) else {}
    captured = tools.get("policy")
    captured = captured if isinstance(captured, dict) else {}
    gates = captured.get("tools")
    gates = gates if isinstance(gates, dict) else {}
    public = {key: value for key, value in gates.items()
              if isinstance(key, str) and _PUBLIC_KEY.fullmatch(key) and type(value) is bool}
    digest = tools.get("declared_configuration_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        digest = None
    status = tools.get("status")
    if status not in {"not_declared", "declared_sources_captured"}:
        status = "unknown"
    return {"tools": public, "permission": _permission(captured.get("permission"))}, status, digest


def _denied(tool_id, policy):
    gates = policy["tools"]
    if gates.get(tool_id, gates.get("*")) is False:
        return True
    permission = policy["permission"]
    if isinstance(permission, str):
        return permission == "deny"
    if isinstance(permission, dict):
        key = "edit" if tool_id in {"edit", "write", "apply_patch"} else tool_id
        decision = permission.get(key, permission.get("*"))
        # Patterned rules can contain exceptions whose private paths were
        # deliberately omitted. Do not turn a pattern default into a whole-tool
        # denial claim.
        return decision == "deny"
    return False


def _component(path, body, *, name, label, description, present=True):
    encoded = body.encode("utf-8")
    return {"name": name, "label": label, "path": path, "present": present,
            "chars": len(body), "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest(),
            "description": description}


def build_inventory(profile: dict, *, revision_id: str | None = None,
                    configuration_id: str | None = None) -> dict:
    """Project selected immutable metadata without changing active selections."""
    revision_id = revision_id or profile.get("active_revision_id")
    configuration_id = configuration_id or profile.get("active_configuration_id")
    revision = next((row for row in profile["revisions"] if row["id"] == revision_id), None)
    configuration = next((row for row in profile["configurations"] if row["id"] == configuration_id), None)
    if not revision or not configuration:
        raise ValueError("Choose a saved configuration and harness revision for the inventory.")
    _id(profile["id"])
    _id(revision_id, digest=True)
    _id(configuration_id, digest=True)
    source = revision.get("tree")
    if not isinstance(source, dict) or len(source) > MAX_TREE_FILES:
        raise ValueError("The inventory needs a bounded harness file tree.")
    tree = HT._normalized_tree(source)
    if (any(not HT.is_allowed(OPENCODE_SURFACE, path) or not isinstance(body, str)
            for path, body in tree.items())
            or sum(len(body.encode("utf-8")) for body in tree.values()) > MAX_TREE_BYTES):
        raise ValueError("The inventory contains unsupported or oversized harness files.")
    components = {key: [] for key in ("always_on", "skills", "subagents", "scripts", "plugins")}
    for path in OPENCODE_SURFACE.always_on:
        label, description = _ALWAYS_ON[path]
        components["always_on"].append(_component(path, tree.get(path, ""), name=path,
            label=label, description=description, present=path in tree))
    for path, body in sorted(tree.items()):
        if path in OPENCODE_SURFACE.always_on:
            continue
        if path.endswith("/SKILL.md"):
            name, category = path.split("/")[1], "skills"
            description = "An on-demand skill included in this harness revision."
        elif path.endswith("/AGENT.md"):
            name, category = path.split("/")[1], "subagents"
            description = "A profile-defined OpenCode subagent."
        elif path.startswith("plugin/"):
            name, category = path.rsplit("/", 1)[-1], "plugins"
            description = "An OpenCode middleware file; its code and dependencies were not executed or inspected."
        else:
            name, category = path.rsplit("/", 1)[-1], "scripts"
            description = "A bundled helper file; execution and installed dependencies were not inspected."
        components[category].append(_component(path, body, name=name, label=name, description=description))
    policy, policy_status, policy_hash = _policy(configuration)
    settings = configuration["snapshot"]["settings"]
    builtin_tools = []
    supported_pin = config.DEPENDENCY_PINS.get("opencode") == CATALOG_COMMIT
    if supported_pin:
        for identifier, label, description, status, qualification in _BUILTINS:
            if _denied(identifier, policy):
                status = "disabled-by-declared-policy"
                qualification = "The saved configuration declares a denial; other configuration sources and the live session were not inspected. " + qualification
            builtin_tools.append({"id": identifier, "label": label, "description": description,
                                  "status": status, "qualification": qualification})
    limits = [
        "This lists the selected saved harness and configuration, not a live session's observed toolset.",
        "Configuration association does not prove which model configuration authored a harness component.",
        "Global OpenCode settings, discovered tools/plugins/skills, and service availability were not inspected.",
        "An empty custom-component group does not remove OpenCode's built-in tools or agents.",
        "Technical memory in the harness, selected experience, and conversational memory are separate stores; only harness files are listed here.",
        "No model, helper script, plugin, network request, or activation was run for this inventory.",
    ]
    if not supported_pin:
        limits.append("The OpenCode pin changed; this source catalog has not been updated, so no built-in tool list is asserted.")
    return {
        "version": 1, "profile_id": profile["id"], "profile_name": profile["name"],
        "configuration": {"id": configuration_id, "label": configuration.get("label", ""),
            "is_active": configuration_id == profile.get("active_configuration_id"),
            "model": settings.get("solver") or settings.get("local_model") or "Unknown",
            "context": settings.get("local_context") or None, "output": settings.get("local_output") or None,
            "runtime": settings.get("local_runtime") or "external",
            "tool_policy_status": policy_status, "tool_policy_sha256": policy_hash,
            "association": "Selected runtime recipe; this selection does not establish which configuration authored the harness."},
        "revision": {"id": revision_id, "label": revision.get("label", ""), "status": revision.get("status", "candidate"),
            "is_active": revision_id == profile.get("active_revision_id"), "operation": revision.get("operation", ""),
            "domain": revision.get("domain", ""), "content_sha256": _hash(tree)},
        "components": components,
        "runtime": {"substrate": "OpenCode", "version": OPENCODE_VERSION if supported_pin else None,
            "commit": config.DEPENDENCY_PINS.get("opencode"), "catalog_matches_pin": supported_pin,
            "source": "Pinned source inventory; not a live tool observation.",
            "source_path": "packages/opencode/src/tool/registry.ts"},
        "builtin_tools": builtin_tools,
        "application_tools": [{"id": "harness2_conversation", "label": "Conversation and working continuity",
            "status": "conditional", "description": "Speak with the user or record a decision/checkpoint.",
            "qualification": "The first-party session plugin is added only for explicitly conversational sessions. It is not a profile-installed plugin."}],
        "permissions": {"kind": "application-policy", "os_sandbox": False, "shell_confined": False,
            "summary": "OpenCode application permissions are not an OS sandbox. Bash runs host commands with the app user's OS permissions; external-directory settings do not guarantee arbitrary shell confinement.",
            "declared_policy": policy,
            "idle_chat": "Idle chat permits its conversation workspace through OpenCode's external-directory rules.",
            "working_run": "The JobBench runner permits its task workspace and host temporary/scratch directories. Improver sessions also permit their declared context directories.",
            "qualification": "Saved permission modes omit command/path patterns. Other configuration and plugins can affect tool exposure; no effective policy or OS isolation was probed."},
        "limits": limits,
    }
