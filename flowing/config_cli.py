"""``flowing-config`` 独立 Provider 与模型配置命令行入口。"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from flowing.configuration import (
    ModelConfigFileError,
    ProviderConfigFileError,
    add_model_entry,
    add_provider_entry,
    delete_model_entry,
    delete_provider_entry,
    read_models,
    read_providers,
    resolve_models_path,
    resolve_providers_path,
)
from flowing.parsable import LITERAL, Parsable
from flowing.providers import ModelConfigField, ProviderConfigField, provider_adapters


_ENV_SHORT_RE = re.compile(r"env\.([A-Za-z_][A-Za-z0-9_]*)\Z")
_ENV_TEMPLATE_RE = re.compile(r"\{\{env\.([A-Za-z_][A-Za-z0-9_]*)\}\}\Z")
_SECRET_KEY_RE = re.compile(r"(?:api[_-]?key|token|secret|password|credential)", re.I)


def build_parser() -> argparse.ArgumentParser:
    """构建独立于 ``flowing`` Runtime 子命令集的参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="flowing-config",
        description="Manage Flowing configuration from the command line.",
        epilog="For details, run \"flowing-config <subcommand> -h\".",
    )
    commands = parser.add_subparsers(
        dest="resource",
        required=True,
        title="Subcommands",
    )
    for resource, description, entry_name in (
        ("providers", "Provider", "providers.yaml"),
        ("models", "model", "models.yaml"),
    ):
        resource_parser = commands.add_parser(
            resource,
            help=f"Manage {entry_name}",
        )
        operations = resource_parser.add_subparsers(dest="operation", required=True)
        for operation, help_text in (
            ("add", f"Interactively add a {description} entry"),
            ("list", f"List {description} entries"),
            ("delete", f"Interactively delete a {description} entry"),
        ):
            command = operations.add_parser(operation, help=help_text)
            command.add_argument(
                "path",
                nargs="?",
                help=(
                    f"{entry_name} file or its directory; when omitted, the Runtime "
                    "configuration path is used after prompting"
                ),
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """运行 ``flowing-config`` 配置命令。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.resource == "providers":
            path = _choose_path(args.path)
            if args.operation == "add":
                return _add_provider(path)
            if args.operation == "list":
                return _list_providers(path)
            if args.operation == "delete":
                return _delete_provider(path)
        elif args.resource == "models":
            path = _choose_models_path(args.path)
            if args.operation == "add":
                return _add_model(path)
            if args.operation == "list":
                return _list_models(path)
            if args.operation == "delete":
                return _delete_model(path)
        parser.error(f"unknown operation: {args.operation}")
    except EOFError:
        print("\nInput ended; cancelled.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (ProviderConfigFileError, ModelConfigFileError) as exc:
        print(f"flowing-config: {exc}", file=sys.stderr)
        return 2
    return 0


def _choose_path(path_arg: str | None):
    if path_arg is not None:
        return resolve_providers_path(path_arg)
    default_path = resolve_providers_path(None)
    value = input(f"Provider configuration file path [{default_path}]: ").strip()
    if not value:
        return default_path
    return resolve_providers_path(value)


def _choose_models_path(path_arg: str | None) -> Path:
    if path_arg is not None:
        return resolve_models_path(path_arg)
    default_path = resolve_models_path(None)
    value = input(f"Model configuration file path [{default_path}]: ").strip()
    if not value:
        return default_path
    return resolve_models_path(value)


def _add_provider(path) -> int:
    snapshot = read_providers(path)
    entry_name = _prompt_entry_name(snapshot.data)
    adapter_name, adapter_cls = _prompt_adapter()
    values: dict[str, Any] = {"adapter": adapter_name}
    for config_field in adapter_cls.config_fields_for():
        should_write, value = _prompt_field(config_field, adapter_cls)
        if should_write:
            values[config_field.name] = value

    add_provider_entry(
        path,
        entry_name,
        values,
        expected_fingerprint=snapshot.fingerprint,
    )
    print(
        f"Wrote Provider entry '{entry_name}' to {path}. "
        "Changes apply to newly constructed Runtime instances."
    )
    return 0


def _add_model(path: Path) -> int:
    snapshot = read_models(path)
    entry_name = _prompt_entry_name(snapshot.data, "Model")
    provider_map, provider_config_available = _provider_adapter_map()
    known_provider_names = tuple(provider_map)
    if known_provider_names:
        print("Known Provider entries: " + ", ".join(known_provider_names))
    elif not provider_config_available:
        print("Provider metadata is unavailable; provider names can still be entered manually.")
    provider_name = _prompt_model_provider()
    model_name = _prompt_model_name()

    fields: dict[str, Any] = {
        "provider": provider_name,
        "model": model_name,
    }
    for name, prompt in (
        ("thinking_budget", "Thinking budget in tokens"),
        ("context_window", "Context window in tokens"),
        ("max_output_tokens", "Maximum output tokens"),
    ):
        should_write, value = _prompt_model_value(
            name,
            prompt,
            _parse_integer_or_parsable,
        )
        if should_write:
            fields[name] = value

    adapter_cls = _adapter_class_for(provider_name, provider_map)
    model_fields = adapter_cls.model_fields_for(model_name) if adapter_cls else ()
    if adapter_cls is None:
        print(
            "No registered adapter metadata is available for this Provider entry; "
            "open-ended model fields remain available."
        )
    if model_fields:
        print(f"Suggested parameters for model '{model_name}':")
        for model_field in model_fields:
            if model_field.name in fields:
                raise ModelConfigFileError(
                    f"Adapter model field {model_field.name!r} conflicts with a built-in field")
            should_write, value = _prompt_model_value(
                model_field.name,
                model_field.prompt,
                model_field.parser,
            )
            if should_write:
                fields[model_field.name] = value
    elif adapter_cls is not None:
        print(f"No adapter parameter suggestions are declared for model '{model_name}'.")

    _prompt_extra_model_fields(fields)
    add_model_entry(
        path,
        entry_name,
        fields,
        expected_fingerprint=snapshot.fingerprint,
    )
    print(
        f"Wrote model entry '{entry_name}' to {path}. "
        "Changes apply when a Runtime next resolves this model entry."
    )
    return 0


def _prompt_model_provider() -> str:
    while True:
        value = input("Provider entry name (identity): ").strip()
        if value:
            return value
        print("Provider entry name cannot be empty. Try again.")


def _prompt_model_name() -> str:
    while True:
        value = input("API model ID: ").strip()
        if value:
            return value
        print("API model ID cannot be empty. Try again.")


def _prompt_model_value(
    name: str,
    prompt: str,
    parser: Callable[[Any], Any],
) -> tuple[bool, Any]:
    while True:
        raw = input(f"{prompt} ({name}; optional YAML value, press Enter to skip): ")
        if raw == "":
            return False, None
        try:
            value = _parse_yaml_value(raw)
            return True, parser(value)
        except (TypeError, ValueError, YAMLError):
            print("Invalid value. Enter a valid YAML value and try again.")


def _parse_yaml_value(raw: str) -> Any:
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    return yaml.load(raw)


def _parse_integer_or_parsable(value: Any) -> int | str:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and Parsable(value).type is not LITERAL:
        return value
    raise ValueError("Expected an integer or a Parsable string")


def _prompt_extra_model_fields(fields: dict[str, Any]) -> None:
    print(
        "Add optional model-specific parameters. Enter one-line YAML values; quote template or path strings and "
        "use flow-style collections such as {key: value} for mappings."
    )
    while True:
        name = input("Additional model field name (press Enter to finish): ").strip()
        if not name:
            return
        if name in fields:
            print(f"Field '{name}' is already configured. Enter another field name.")
            continue
        raw = input(f"YAML value for '{name}' (press Enter to skip this field): ")
        if raw == "":
            continue
        try:
            fields[name] = _parse_yaml_value(raw)
        except YAMLError:
            print("Invalid YAML value. Try again.")


def _provider_adapter_map() -> tuple[dict[str, tuple[str | None, type | None]], bool]:
    try:
        snapshot = read_providers(resolve_providers_path(None))
    except (ProviderConfigFileError, OSError):
        return {}, False
    adapters = dict(provider_adapters())
    result: dict[str, tuple[str | None, type | None]] = {}
    for entry_name, raw_fields in snapshot.data.items():
        if not isinstance(raw_fields, Mapping):
            result[str(entry_name)] = (None, None)
            continue
        adapter_name = raw_fields.get("adapter")
        adapter_cls = adapters.get(adapter_name) if isinstance(adapter_name, str) else None
        result[str(entry_name)] = (adapter_name, adapter_cls)
    return result, True


def _adapter_class_for(
    provider_name: str,
    provider_map: Mapping[str, tuple[str | None, type | None]],
) -> type | None:
    entry = provider_map.get(provider_name)
    return entry[1] if entry is not None else None


def _list_models(path: Path) -> int:
    snapshot = read_models(path)
    print(f"Model configuration file: {path}")
    if not snapshot.data:
        print("No model entries configured.")
        return 0

    provider_map, provider_config_available = _provider_adapter_map()
    if not provider_config_available:
        print("Provider metadata is unavailable; sensitive-looking field names are masked.")

    for entry_name, raw_fields in snapshot.data.items():
        print(f"\n{entry_name}")
        if not isinstance(raw_fields, Mapping):
            print(f"  Configuration: {_summarize(raw_fields)}")
            continue
        provider_name = raw_fields.get("provider")
        model_name = raw_fields.get("model")
        print(f"  provider: {provider_name if provider_name is not None else '<missing>'}")
        print(f"  model: {model_name if model_name is not None else '<missing>'}")
        adapter_cls = (
            _adapter_class_for(provider_name, provider_map)
            if isinstance(provider_name, str)
            else None
        )
        metadata = (
            {field.name: field for field in adapter_cls.model_fields_for(model_name)}
            if adapter_cls is not None and isinstance(model_name, str)
            else {}
        )
        for name, value in raw_fields.items():
            if name in {"provider", "model"}:
                continue
            field = metadata.get(str(name))
            print(f"  {name}: {_display_model_value(str(name), value, field)}")
    return 0


def _display_model_value(
    name: str,
    value: Any,
    model_field: ModelConfigField | None,
) -> str:
    sensitive = bool(_SECRET_KEY_RE.search(name)) or (
        model_field.sensitive if model_field is not None else False
    )
    if sensitive:
        return "<empty>" if value in (None, "") else "********"
    if isinstance(value, str):
        return repr(value) if "\n" in value or "\r" in value else value
    return _summarize(value)


def _delete_model(path: Path) -> int:
    snapshot = read_models(path)
    if not snapshot.data:
        print(f"Model configuration file has no entries: {path}")
        return 0

    entries = list(snapshot.data)
    print(f"Model configuration file: {path}")
    for index, entry_name in enumerate(entries, start=1):
        print(f"  {index}. {entry_name}")
    while True:
        selection = input("Select a model number to delete (press Enter to cancel): ").strip()
        if not selection:
            print("Deletion cancelled.")
            return 0
        try:
            index = int(selection)
        except ValueError:
            print("Enter a number from the list.")
            continue
        if 1 <= index <= len(entries):
            entry_name = str(entries[index - 1])
            break
        print("Number out of range. Try again.")

    confirm = input(f"Delete model entry '{entry_name}'? [y/N] ").strip()
    if confirm not in {"y", "Y"}:
        print("Deletion cancelled.")
        return 0

    delete_model_entry(
        path,
        entries[index - 1],
        expected_fingerprint=snapshot.fingerprint,
    )
    print(
        f"Deleted model entry '{entry_name}' from {path}. "
        "Changes apply when a Runtime next resolves this model entry."
    )
    return 0


def _prompt_entry_name(data: Mapping[str, Any], noun: str = "Provider") -> str:
    while True:
        entry_name = input(f"{noun} entry name (identity): ").strip()
        if not entry_name:
            print(f"{noun} entry name cannot be empty. Try again.")
            continue
        if entry_name in data:
            print(f"{noun} entry '{entry_name}' already exists. Enter a different name.")
            continue
        return entry_name


def _prompt_adapter() -> tuple[str, type]:
    adapters = provider_adapters()
    by_name = dict(adapters)
    if not adapters:
        raise ProviderConfigFileError("No Provider adapters are registered.")

    with _adapter_completion(tuple(by_name)):
        while True:
            value = input("Provider adapter: ").strip()
            if value in by_name:
                return value, by_name[value]
            if not value:
                print("Available adapters: " + ", ".join(by_name))
                continue
            matches = [name for name in by_name if name.startswith(value)]
            if len(matches) == 1:
                return matches[0], by_name[matches[0]]
            if matches:
                print("Multiple adapters match: " + ", ".join(matches))
            else:
                print("No adapter matched. Available adapters: " + ", ".join(by_name))


def _prompt_field(
    config_field: ProviderConfigField,
    adapter_cls: type,
) -> tuple[bool, Any]:
    default = config_field.default_for(adapter_cls)
    if default is None:
        suffix = "required"
    elif default == "":
        suffix = "optional; leave blank to skip"
    elif config_field.sensitive:
        suffix = "leave blank to use the default"
    else:
        suffix = f"leave blank to use {default!r}"
    prompt = f"{config_field.prompt} ({config_field.name}; {suffix}): "

    while True:
        value = input(prompt)
        if value == "":
            if default is None:
                print("This field is required. Enter a value.")
                continue
            if not config_field.persist_default:
                return False, default
            return True, default

        try:
            value = _expand_env_shortcut(value, config_field)
            return True, config_field.parser(value)
        except (TypeError, ValueError):
            print("Invalid value. Try again.")


def _expand_env_shortcut(value: str, config_field: ProviderConfigField) -> str:
    if not config_field.supports_env:
        return value
    if config_field.parser is not str:
        if value.startswith("env.") or value.startswith("{{env."):
            raise ValueError(
                "Environment-variable references are only supported for string fields"
            )
        return value

    if value.startswith("env."):
        match = _ENV_SHORT_RE.fullmatch(value)
        if match is None:
            raise ValueError("Invalid environment variable name")
        return "{{env." + match.group(1) + "}}"
    if value.startswith("{{env.") and _ENV_TEMPLATE_RE.fullmatch(value) is None:
        raise ValueError("Invalid environment-variable template")
    return value


def _list_providers(path) -> int:
    snapshot = read_providers(path)
    print(f"Provider configuration file: {path}")
    if not snapshot.data:
        print("No Provider entries configured.")
        return 0

    adapter_classes = dict(provider_adapters())
    for entry_name, raw_fields in snapshot.data.items():
        print(f"\n{entry_name}")
        if not isinstance(raw_fields, Mapping):
            print(f"  Configuration: {_summarize(raw_fields)}")
            continue
        adapter_name = raw_fields.get("adapter")
        print(f"  adapter: {adapter_name if adapter_name is not None else '<missing>'}")
        adapter_cls = adapter_classes.get(adapter_name) if isinstance(adapter_name, str) else None
        metadata = ({field.name: field for field in adapter_cls.config_fields_for()}
                    if adapter_cls is not None else {})
        for name, value in raw_fields.items():
            if name == "adapter":
                continue
            field = metadata.get(str(name))
            print(f"  {name}: {_display_config_value(str(name), value, field)}")
    return 0


def _display_config_value(
    name: str,
    value: Any,
    config_field: ProviderConfigField | None,
) -> str:
    if isinstance(value, str) and _ENV_TEMPLATE_RE.fullmatch(value):
        return value
    sensitive = (
        config_field.sensitive
        if config_field is not None
        else bool(_SECRET_KEY_RE.search(name))
    )
    if sensitive:
        return "<empty>" if value in (None, "") else "********"
    if config_field is None:
        return _summarize(value)
    if isinstance(value, str):
        return repr(value) if "\n" in value or "\r" in value else value
    return _summarize(value)


def _summarize(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return f"<string: {len(value)} characters>"
    if isinstance(value, Mapping):
        return f"<mapping: {len(value)} items>"
    if isinstance(value, (list, tuple)):
        return f"<list: {len(value)} items>"
    return f"<{type(value).__name__}>"


def _delete_provider(path) -> int:
    snapshot = read_providers(path)
    if not snapshot.data:
        print(f"Provider configuration file has no entries: {path}")
        return 0

    entries = list(snapshot.data)
    print(f"Provider configuration file: {path}")
    for index, entry_name in enumerate(entries, start=1):
        print(f"  {index}. {entry_name}")
    while True:
        selection = input("Select an entry number to delete (press Enter to cancel): ").strip()
        if not selection:
            print("Deletion cancelled.")
            return 0
        try:
            index = int(selection)
        except ValueError:
            print("Enter a number from the list.")
            continue
        if 1 <= index <= len(entries):
            entry_name = str(entries[index - 1])
            break
        print("Number out of range. Try again.")

    confirm = input(f"Delete Provider entry '{entry_name}'? [y/N] ").strip()
    if confirm not in {"y", "Y"}:
        print("Deletion cancelled.")
        return 0

    delete_provider_entry(
        path,
        entries[index - 1],
        expected_fingerprint=snapshot.fingerprint,
    )
    print(
        f"Deleted Provider entry '{entry_name}' from {path}. "
        "Changes apply to newly constructed Runtime instances."
    )
    return 0


@contextmanager
def _adapter_completion(names: tuple[str, ...]) -> Iterator[None]:
    if not sys.stdin.isatty():
        print(
            "Tab completion is unavailable because stdin is not a terminal; "
            "enter an adapter name or prefix."
        )
        yield
        return
    try:
        import readline
    except ImportError:
        print(
            "Tab completion is unavailable because readline is not supported "
            "on this platform; enter an adapter name or prefix."
        )
        yield
        return

    previous = readline.get_completer()

    def complete(text: str, state: int) -> str | None:
        matches = [name for name in names if name.startswith(text)]
        return matches[state] if state < len(matches) else None

    try:
        readline.set_completer(complete)
        if getattr(readline, "backend", "readline") == "editline":
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
            readline.parse_and_bind("set show-all-if-ambiguous on")
        yield
    finally:
        readline.set_completer(previous)


if __name__ == "__main__":
    raise SystemExit(main())
