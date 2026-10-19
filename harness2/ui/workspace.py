"""Workspace settings and inexpensive readiness information for the local UI."""

from __future__ import annotations

import collections
import importlib.util
import os
import platform
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .. import config

FIELDS = {
    "project": "GOOGLE_CLOUD_PROJECT",
    "region": "GOOGLE_CLOUD_LOCATION",
    "solver": "HARNESS2_SOLVER_MODEL",
    "improver": "HARNESS2_IMPROVER_MODEL",
    "judge": "HARNESS2_JUDGE_MODEL",
    "local_url": "HARNESS2_LOCAL_BASE_URL",
    "local_model": "HARNESS2_LOCAL_MODEL",
    "codex_url": "HARNESS2_CODEX_BASE_URL",
    "codex_model": "HARNESS2_CODEX_MODEL",
    "provider": "HARNESS2_UI_PROVIDER",
}
SECRET_FIELDS = ("HARNESS2_JUDGE_API_KEY", "HARNESS2_LOCAL_API_KEY", "HARNESS2_CODEX_API_KEY")
BENCHMARKS = (
    ("jb", "JobBench", "Professional work across many occupations", "jb", None),
    ("lab", "LAB", "Legal research, analysis, and drafting", "lab", None),
    (
        "wb-office",
        "WorkBuddy · Office",
        "Documents, spreadsheets, and presentations",
        "wb",
        "office",
    ),
    ("wb-web", "WorkBuddy · Web", "Websites and browser-based work", "wb", "web"),
    ("wb-code", "WorkBuddy · Code", "Software tasks in Linux containers", "wb", "code"),
)


def dotenv(path: Path) -> dict[str, str]:
    """Read values without mutating the server's environment or exposing secrets."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}
    values = {}
    for raw in lines:
        line = raw.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Use an http:// or https:// endpoint without credentials in its URL.")
    if parsed.query or parsed.fragment:
        raise ValueError("The endpoint should not contain a query or fragment.")
    return value.rstrip("/")


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.state_dir = self.root / ".harness2-ui"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def environment(self) -> dict[str, str]:
        # Saved UI settings apply to jobs launched here. Direct CLI invocations
        # keep the CLI's usual shell-over-.env precedence.
        env = {**os.environ, **dotenv(self.root / ".env")}
        scripts = Path(os.sys.executable).parent
        env["PATH"] = str(scripts) + os.pathsep + env.get("PATH", "")
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    @property
    def runs_root(self) -> Path:
        value = self.environment().get("HARNESS2_RUNS_DIR")
        path = Path(value).expanduser() if value else self.root / "runs"
        return (self.root / path).resolve() if not path.is_absolute() else path.resolve()

    def settings(self) -> dict:
        env = self.environment()
        result = {key: env.get(name, "") for key, name in FIELDS.items()}
        result["provider"] = result["provider"] or (
            "vertex" if result["solver"].startswith("google-vertex/") else "local"
        )
        result["region"] = result["region"] or "global"
        result["local_url"] = result["local_url"] or "http://127.0.0.1:8087/v1"
        result["local_model"] = result["local_model"] or "qwen3.8-local"
        result["key_configured"] = any(env.get(name) for name in SECRET_FIELDS)
        return result

    def save(self, data: dict) -> dict:
        if not isinstance(data, dict) or set(data) - set(FIELDS) - {"api_key", "key_configured"}:
            raise ValueError("Unrecognized settings fields.")
        current = self.settings()
        for key in FIELDS:
            if key in data:
                value = data[key]
                if (
                    not isinstance(value, str)
                    or len(value) > 2048
                    or any(c in value for c in "\r\n\0")
                ):
                    raise ValueError(f"Invalid value for {key}.")
                current[key] = value.strip()
        if current["provider"] not in ("local", "vertex", "custom"):
            raise ValueError("Choose a local, Vertex, or custom connection.")
        if current["provider"] == "local":
            current["local_url"] = validate_url(current["local_url"])
            if not current["local_model"] or any(c.isspace() for c in current["local_model"]):
                raise ValueError("Enter the model ID served by your local server.")
            current["solver"] = "local/" + current["local_model"]
            current["improver"] = current["solver"]
            current["judge"] = current["local_model"]
        elif current["provider"] == "vertex":
            if (
                any(not current[name] or current[name].startswith("local/")
                    for name in ("solver", "improver", "judge"))
                or current["judge"] == current["local_model"]
            ):
                raise ValueError(
                    "Choose your Vertex solver, improver, and judge models in Advanced model settings."
                )
            current["local_url"] = ""
            current["local_model"] = ""
            if not current["project"]:
                raise ValueError("Enter your Google Cloud project ID.")
            for name in ("solver", "improver"):
                if "/" not in current[name]:
                    current[name] = "google-vertex/" + current[name]
        elif not current["codex_url"] or not current["codex_model"]:
            raise ValueError("Enter the Codex Responses endpoint and its model ID.")
        if current["codex_url"]:
            current["codex_url"] = validate_url(current["codex_url"])
        updates = {name: current[key] for key, name in FIELDS.items()}
        api_key = data.get("api_key")
        if api_key is not None and (
            not isinstance(api_key, str)
            or len(api_key) > 4096
            or any(c in api_key for c in "\r\n\0")
        ):
            raise ValueError("Invalid API key.")
        if current["provider"] == "local":
            updates["HARNESS2_JUDGE_BASE_URL"] = current["local_url"]
            updates["HARNESS2_JUDGE_API_KEY"] = (
                api_key or self.environment().get("HARNESS2_LOCAL_API_KEY") or "local"
            )
            if api_key:
                updates["HARNESS2_LOCAL_API_KEY"] = api_key
        elif current["provider"] == "vertex":
            updates["HARNESS2_JUDGE_BASE_URL"] = ""
            updates["HARNESS2_JUDGE_API_KEY"] = ""
        elif api_key:
            updates["HARNESS2_CODEX_API_KEY"] = api_key
        path = self.root / ".env"
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except FileNotFoundError:
            lines = ["# Harness² workspace settings. Keep this file private."]
        remaining = dict(updates)
        output = []
        for line in lines:
            key = line.strip().removeprefix("export ").partition("=")[0].strip()
            if key in updates:
                if key in remaining:
                    output.append(f'{key}="{remaining.pop(key)}"')
            else:
                output.append(line)
        output += [f'{key}="{value}"' for key, value in remaining.items()]
        handle, temporary = tempfile.mkstemp(prefix=".settings-", dir=self.root)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write("\n".join(output) + "\n")
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return self.settings()

    def tools(self) -> list[dict]:
        path = self.environment().get("PATH", "")
        result = [{"name": "Python", "available": True, "detail": platform.python_version()}]
        for name, binary in (
            ("Git", "git"),
            ("uv", "uv"),
            ("Node.js", "node"),
            ("Docker", "docker"),
            ("Google Cloud CLI", "gcloud"),
            ("Pandoc", "pandoc"),
            ("Poppler", "pdftoppm"),
        ):
            found = shutil.which(binary, path=path)
            result.append(
                {"name": name, "available": bool(found), "detail": found or "Not found on PATH"}
            )
        result.append(
            {
                "name": "OpenCode",
                "available": config.BUN.is_file()
                and (config.OPENCODE_DIR / "node_modules").is_dir(),
                "detail": "Pinned CLI source and Bun runtime",
            }
        )
        result.append(
            {
                "name": "Document rendering",
                "available": all(
                    importlib.util.find_spec(name) is not None for name in ("openpyxl", "pptx")
                ),
                "detail": "Optional spreadsheet and presentation readers",
            }
        )
        return result

    def benchmarks(self) -> list[dict]:
        from ..benchmarks.tasks import discover

        result = []
        for key, name, description, bench, subset in BENCHMARKS:
            tasks, problem = {}, ""
            try:
                tasks = discover(bench, subset)
            except (OSError, RuntimeError, ValueError) as exc:
                problem = str(exc)
            repo = (
                config.DEPENDENCIES
                / {"jb": "job-bench-eval", "lab": "harvey-labs", "wb": "workbuddy-bench"}[bench]
            )
            result.append(
                {
                    "id": key,
                    "name": name,
                    "description": description,
                    "installed": bool(tasks) and config.venv_python(repo).is_file(),
                    "task_count": len(tasks),
                    "domains": [
                        {"id": domain, "count": count}
                        for domain, count in sorted(collections.Counter(tasks.values()).items())
                    ],
                    "problem": problem,
                }
            )
        return result

    def secrets(self) -> list[str]:
        return [
            value
            for name, value in self.environment().items()
            if any(part in name.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
            and value
        ]
