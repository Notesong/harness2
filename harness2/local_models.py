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

"""An explicit OpenAI-compatible local provider for host OpenCode sessions."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

DUMMY_LOCAL_API_KEY = "local"


def validate_api_base_url(value: str) -> str:
    """Validate a public endpoint identity without accepting or echoing credentials."""
    value = value.strip()
    message = (
        "Use a valid HTTP(S) API base URL without URL credentials, a query, or a fragment."
    )
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in value
            or "#" in value
            or any(char.isspace() or ord(char) < 32 for char in value)
        ):
            raise ValueError(message)
        # Accessing .port rejects malformed and out-of-range ports. Its original
        # exception may quote URL content, so expose only the fixed message below.
        parsed.port
    except ValueError:
        raise ValueError(message) from None
    return value.rstrip("/")


@dataclass(frozen=True)
class LocalProfile:
    base_url: str
    model: str
    context: int = 32768
    output: int = 4096

    @property
    def opencode_model(self) -> str:
        return "local/" + self.model


def profile(environment: Mapping[str, str]) -> LocalProfile | None:
    """Read the opt-in profile; incomplete settings fail instead of selecting a cloud."""
    base = environment.get("HARNESS2_LOCAL_BASE_URL", "").strip().rstrip("/")
    model = environment.get("HARNESS2_LOCAL_MODEL", "").strip()
    if not base and not model:
        return None
    if not base or not model:
        raise ValueError("Set both HARNESS2_LOCAL_BASE_URL and HARNESS2_LOCAL_MODEL")
    base = validate_api_base_url(base)
    if any(char.isspace() for char in model):
        raise ValueError("HARNESS2_LOCAL_MODEL must be a server model ID without whitespace")
    limits = []
    for key, default in (("HARNESS2_LOCAL_CONTEXT", 32768), ("HARNESS2_LOCAL_OUTPUT", 4096)):
        try:
            value = int(environment.get(key) or default)
        except ValueError as exc:
            raise ValueError(f"{key} must be a positive integer") from exc
        if value < 1:
            raise ValueError(f"{key} must be a positive integer")
        limits.append(value)
    if limits[1] >= limits[0]:
        raise ValueError("HARNESS2_LOCAL_OUTPUT must be smaller than HARNESS2_LOCAL_CONTEXT")
    return LocalProfile(base, model, *limits)


def profile_defaults(environment: Mapping[str, str]) -> dict[str, str]:
    """Supply model/judge settings from the local profile; explicit settings win."""
    result = dict(environment)
    local = profile(result)
    if local is None:
        return result
    for key, value in {
        "HARNESS2_SOLVER_MODEL": local.opencode_model,
        "HARNESS2_IMPROVER_MODEL": local.opencode_model,
        "HARNESS2_JUDGE_MODEL": local.model,
        "HARNESS2_JUDGE_BASE_URL": local.base_url,
        "HARNESS2_JUDGE_API_KEY": result.get("HARNESS2_LOCAL_API_KEY") or DUMMY_LOCAL_API_KEY,
    }.items():
        if not result.get(key):
            result[key] = value
    return result


def profile_identity(environment: Mapping[str, str]) -> dict | None:
    """Public model/endpoint/budget identity, excluding authentication values."""
    local = profile(environment)
    if local is None:
        return None
    resolved = profile_defaults(environment)
    local_session = any(resolved[key].startswith("local/") for key in (
        "HARNESS2_SOLVER_MODEL", "HARNESS2_IMPROVER_MODEL",
    ))
    local_judge = (resolved["HARNESS2_JUDGE_MODEL"] == local.model
                   and resolved["HARNESS2_JUDGE_BASE_URL"].rstrip("/") == local.base_url)
    return asdict(local) if local_session or local_judge else None


def check_profile_identity(recorded: Mapping, environment: Mapping[str, str]) -> None:
    if recorded.get("local_profile") != profile_identity(environment):
        raise ValueError("Local model profile changed for this experiment; choose a new HARNESS2_RUNS_DIR")


def _object(parent: dict, key: str) -> dict:
    if key not in parent:
        parent[key] = {}
    if not isinstance(parent[key], dict):
        raise ValueError(f"OPENCODE_CONFIG_CONTENT {key!r} must be an object")
    return parent[key]


def opencode_env(environment: Mapping[str, str], *, model: str | None = None) -> dict[str, str]:
    """Merge this provider into a copied environment, preserving other configuration.

    This configures the host OpenCode CLI. It does not start a server, disable
    harness plugins, or make configured plugins and web tools operate offline.
    """
    result = dict(environment)
    local = profile(result)
    if local is None:
        return result
    selected = model or result.get("HARNESS2_SOLVER_MODEL") or local.opencode_model
    if not selected.startswith("local/"):
        return result
    try:
        content = json.loads(result.get("OPENCODE_CONFIG_CONTENT") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("OPENCODE_CONFIG_CONTENT must be valid JSON to merge the local provider") from exc
    if not isinstance(content, dict):
        raise ValueError("OPENCODE_CONFIG_CONTENT must be a JSON object")
    content = copy.deepcopy(content)
    providers = _object(content, "provider")
    provider = _object(providers, "local")
    provider.update({"npm": "@ai-sdk/openai-compatible", "name": "Local OpenAI-compatible"})
    options = _object(provider, "options")
    options["baseURL"] = local.base_url
    if result.get("HARNESS2_LOCAL_API_KEY"):
        options["apiKey"] = result["HARNESS2_LOCAL_API_KEY"]
    else:
        options.setdefault("apiKey", DUMMY_LOCAL_API_KEY)
    model_config = _object(_object(provider, "models"), local.model)
    model_config.update({
        "id": local.model,
        "name": local.model,
        "tool_call": True,
        "reasoning": False,
        "temperature": True,
        "attachment": False,
        "cost": {"input": 0, "output": 0},
        "limit": {"context": local.context, "output": local.output},
        "modalities": {"input": ["text"], "output": ["text"]},
    })
    enabled = content.setdefault("enabled_providers", ["local"])
    if not isinstance(enabled, list):
        raise ValueError("OPENCODE_CONFIG_CONTENT enabled_providers must be an array")
    if "local" not in enabled:
        enabled.append("local")
    if "local" in content.get("disabled_providers", []):
        raise ValueError("The local provider is disabled in OPENCODE_CONFIG_CONTENT")
    content.update({"model": local.opencode_model, "small_model": local.opencode_model,
                    "share": "disabled", "autoupdate": False})
    result["OPENCODE_CONFIG_CONTENT"] = json.dumps(content, ensure_ascii=False)
    result.update({
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
        "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX": str(local.output),
    })
    return result
