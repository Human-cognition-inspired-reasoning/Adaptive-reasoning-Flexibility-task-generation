#!/usr/bin/env python3
"""Convert TOML settings into Hanoi runner arguments without making API calls.

Run ``python run.py --profile sol|opus|sonnet`` from the terminal.
This module neither reads nor stores keys and does not change experiment or grading logic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import tomllib

from .policies import BINARY, policy_name
from .prompts import FEEDBACK_POLICIES


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATHS = {
    "sol": ROOT / "configs" / "sol.toml",
    "opus": ROOT / "configs" / "opus.toml",
    "sonnet": ROOT / "configs" / "sonnet.toml",
}
DEFAULT_CONFIG = CONFIG_PATHS["sol"]
TOP_KEYS = {"config_version", "default_profile", "experiment", "profiles"}
EXPERIMENT_KEYS = {
    "dataset",
    "problems",
    "start_problem",
    "T",
    "k",
    "r1_apply_k",
    "max_output_tokens",
    "timeout",
}
OPTIONAL_EXPERIMENT_KEYS = {"feedback_policy"}
PROFILE_KEYS = {"label", "provider", "model", "effort"}
PROVIDERS = {"openai", "anthropic"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}


class ConfigError(ValueError):
    """Configuration error that does not expose file contents or secret values."""


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a nonempty string.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ConfigError(f"{name} cannot contain line breaks, tabs or control characters.")
    return value


def _keys(value: object, allowed: set[str], name: str, *, optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a TOML table.")
    if set(value) - allowed - optional:
        # Names and values may contain secrets; never echo the supplied contents.
        raise ConfigError(
            f"{name} contains an unsupported setting. Keep API keys out of the configuration file."
        )
    if allowed - set(value):
        missing = ", ".join(sorted(allowed - set(value)))
        raise ConfigError(f"{name} is missing required fields: {missing}")
    return value


def _absolute_path(value: str | Path, base: Path, name: str) -> Path:
    text = _string(str(value), name)
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base / path
    resolved = path.resolve()
    _string(str(resolved), name)
    return resolved


def _provider_model(provider: object, model: object) -> None:
    _string(provider, "provider")
    _string(model, "model")
    if provider not in PROVIDERS:
        raise ConfigError("provider must be openai or anthropic.")
    if any(character.isspace() for character in model) or model.startswith("-"):
        raise ConfigError("model cannot contain whitespace or start with a hyphen.")
    if model.startswith("claude-") != (provider == "anthropic"):
        raise ConfigError("The model does not match the provider. Claude models use anthropic.")


def load_config(config_path: str | Path | None = None) -> tuple[Path, dict]:
    """Validate the complete TOML and return (absolute path, settings object)."""
    path = _absolute_path(config_path or DEFAULT_CONFIG, Path.cwd(), "config path")
    try:
        with path.open("rb") as stream:
            config = tomllib.load(stream)
    except OSError as exc:
        raise ConfigError(
            "Could not read the configuration file. Check the --config path."
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            "Invalid TOML in the configuration file. Enclose strings in quotes."
        ) from exc
    except UnicodeError as exc:
        raise ConfigError("The configuration file must use UTF-8.") from exc
    _keys(config, TOP_KEYS, "top-level configuration")
    if type(config["config_version"]) is not int or config["config_version"] != 1:
        raise ConfigError("config_version must be the integer 1.")
    experiment = _keys(
        config["experiment"], EXPERIMENT_KEYS, "experiment", optional=OPTIONAL_EXPERIMENT_KEYS
    )
    experiment["feedback_policy"] = policy_name(experiment.get("feedback_policy", BINARY))
    if (
        not isinstance(experiment["feedback_policy"], str)
        or experiment["feedback_policy"] not in FEEDBACK_POLICIES
    ):
        raise ConfigError("experiment.feedback_policy must be binary or four_way.")
    _string(experiment["dataset"], "experiment.dataset")
    for name in ("problems", "start_problem", "T", "k", "max_output_tokens"):
        if type(experiment[name]) is not int or experiment[name] < 1:
            raise ConfigError(f"experiment.{name} must be an integer of at least 1.")
    if experiment["k"] > experiment["T"]:
        raise ConfigError("T and k must satisfy 1 <= k <= T.")
    if type(experiment["r1_apply_k"]) is not bool:
        raise ConfigError("experiment.r1_apply_k must be true or false.")
    timeout = experiment["timeout"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError("experiment.timeout must be a finite positive number.")
    profiles = config["profiles"]
    if not isinstance(profiles, dict) or not profiles:
        raise ConfigError("profiles must contain at least one model profile.")
    for identifier, profile in profiles.items():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", identifier):
            raise ConfigError(
                "Profile names must start with a letter or digit and contain only ASCII letters, digits, underscores or hyphens."
            )
        _keys(profile, PROFILE_KEYS, f"profiles.{identifier}")
        _string(profile["label"], f"profiles.{identifier}.label")
        _provider_model(profile["provider"], profile["model"])
        if not isinstance(profile["effort"], str) or profile["effort"] not in EFFORTS:
            raise ConfigError(
                f"profiles.{identifier}.effort must be low, medium, high, xhigh or max."
            )
    default = _string(config["default_profile"], "default_profile")
    if default not in profiles:
        raise ConfigError("default_profile must name a registered profile.")
    for identifier, standard_path in CONFIG_PATHS.items():
        if path == standard_path.resolve() and (
            set(profiles) != {identifier} or default != identifier
        ):
            raise ConfigError(
                f"configs/{identifier}.toml must contain only the {identifier} profile and use it as its default."
            )
    return path, config


def _selected_config(config_path: str | Path | None, profile: str | None) -> str | Path | None:
    """Select the model-specific TOML for a built-in profile."""
    if config_path is not None or profile is None:
        return config_path
    if not isinstance(profile, str) or profile not in CONFIG_PATHS:
        raise ConfigError(
            "Unknown profile. Run python -m hanoi.config --list-profiles to list available profiles."
        )
    return CONFIG_PATHS[profile]


def list_profiles(config_path: str | Path | None = None) -> dict[str, dict]:
    """Return profiles from the default model files or an explicit configuration."""
    if config_path is not None:
        return load_config(config_path)[1]["profiles"]
    profiles = {}
    for identifier, path in CONFIG_PATHS.items():
        config = load_config(path)[1]
        profiles[identifier] = config["profiles"][identifier]
    return profiles


def load_settings(config_path: str | Path | None = None, profile: str | None = None) -> dict:
    """Merge the selected profile with shared experiment settings.

    The dataset path is absolute. Returned keys include the experiment fields
    plus profile, label, provider, model, and effort. No API keys or environment
    variables are read.
    """
    path, config = load_config(_selected_config(config_path, profile))
    identifier = profile if profile is not None else config["default_profile"]
    if not isinstance(identifier, str) or identifier not in config["profiles"]:
        raise ConfigError(
            "Unknown profile. Run python -m hanoi.config --list-profiles to list available profiles."
        )
    settings = {**config["experiment"], **config["profiles"][identifier], "profile": identifier}
    dataset = _absolute_path(settings["dataset"], path.parent, "dataset path")
    if not dataset.is_file():
        raise ConfigError(
            "Dataset file not found. Relative paths are resolved from the directory containing the TOML file."
        )
    try:
        content = json.loads(dataset.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ConfigError("Could not read the dataset file as valid JSON.") from exc
    problems = content.get("problems") if isinstance(content, dict) else None
    if not isinstance(problems, list) or not problems:
        raise ConfigError("The dataset must contain a nonempty problems array.")
    if settings["start_problem"] + settings["problems"] - 1 > len(problems):
        raise ConfigError(
            f"The selected range exceeds the {len(problems)} problems in the dataset. "
            "Check start_problem and problems."
        )
    settings["dataset"] = str(dataset)
    return settings


def _resume_settings(resume: str | Path) -> tuple[Path, dict]:
    directory = _absolute_path(resume, Path.cwd(), "resume path")
    try:
        protocol = json.loads((directory / "protocol.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ConfigError("Could not read protocol.json from the run directory.") from exc
    if not isinstance(protocol, dict) or not isinstance(protocol.get("frozen"), dict):
        raise ConfigError("Invalid frozen settings in the saved protocol.json.")
    frozen = protocol["frozen"]
    canonical = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if checksum != protocol.get("frozen_sha256"):
        raise ConfigError(
            "The configuration hash in the saved protocol.json does not match its contents."
        )
    saved = frozen.get("config")
    if not isinstance(saved, dict):
        raise ConfigError("Invalid config object in the saved protocol.json.")
    _provider_model(saved.get("provider"), saved.get("model"))
    if not isinstance(saved.get("effort"), str) or saved["effort"] not in EFFORTS:
        raise ConfigError("Invalid effort setting in the saved protocol.json.")
    return directory, saved


def resolve_arguments(
    config_path: str | Path | None = None,
    profile: str | None = None,
    resume: str | Path | None = None,
) -> list[str]:
    """Return arguments for the experiment runner.

    Resuming ignores experiment settings in the TOML. If config or profile is
    explicit, only verify that provider, model, and effort match the saved run.
    """
    if resume is not None:
        directory, saved = _resume_settings(resume)
        if profile is not None or config_path is not None:
            _, config = load_config(_selected_config(config_path, profile))
            selected = config["profiles"].get(profile or config["default_profile"])
            if selected is None:
                raise ConfigError(
                    "The profile selected for resume is missing from the configuration file."
                )
            if any(selected[key] != saved[key] for key in ("provider", "model", "effort")):
                raise ConfigError(
                    "The selected provider, model or effort differs from the saved run. Resume requires the original settings."
                )
            if config["experiment"]["feedback_policy"] != policy_name(
                saved.get("feedback_policy", BINARY)
            ):
                raise ConfigError(
                    "The selected feedback policy differs from the saved run. Resume requires the original policy."
                )
        return ["--provider", saved["provider"], "--resume", str(directory)]
    settings = load_settings(config_path, profile)
    arguments = []
    for option, key in (
        ("--provider", "provider"),
        ("--model", "model"),
        ("--effort", "effort"),
        ("--dataset", "dataset"),
        ("--problems", "problems"),
        ("--start-problem", "start_problem"),
        ("--T", "T"),
        ("--k", "k"),
        ("--max-output-tokens", "max_output_tokens"),
        ("--timeout", "timeout"),
        ("--feedback-policy", "feedback_policy"),
    ):
        arguments.extend((option, str(settings[key])))
    arguments.append("--r1-apply-k" if settings["r1_apply_k"] else "--no-r1-apply-k")
    arguments.extend(("--context-format", "messages", "--output-format", "plain"))
    return arguments


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, help="TOML configuration file (default: configs/sol.toml)"
    )
    parser.add_argument("--profile", help="Model profile: sol, opus or sonnet (default: sol)")
    parser.add_argument(
        "--resume",
        type=Path,
        help="Use the saved experiment settings from an existing run directory",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--list-profiles",
        action="store_true",
        help="Print each profile ID and label, separated by a tab",
    )
    mode.add_argument(
        "--default-profile",
        action="store_true",
        help="Print the default profile ID from the configuration file",
    )
    mode.add_argument(
        "--resolve",
        action="store_true",
        help="Print one runner argument per line without calling the API",
    )
    args = parser.parse_args(argv)
    if not args.resolve and (args.profile is not None or args.resume is not None):
        parser.error("--profile and --resume require --resolve.")
    try:
        if args.resolve:
            print("\n".join(resolve_arguments(args.config, args.profile, args.resume)))
        else:
            if args.default_profile:
                print(load_config(args.config)[1]["default_profile"])
            else:
                for identifier, profile in list_profiles(args.config).items():
                    print(f"{identifier}\t{profile['label']}")
    except (ConfigError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
