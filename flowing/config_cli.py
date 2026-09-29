"""``flowing-config`` 独立 Provider 配置命令行入口。"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from flowing.configuration import (
    ProviderConfigFileError,
    add_provider_entry,
    delete_provider_entry,
    read_providers,
    resolve_providers_path,
)
from flowing.providers import ProviderConfigField, provider_adapters


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
    providers = commands.add_parser("providers", help="Manage providers.yaml")
    operations = providers.add_subparsers(dest="operation", required=True)
    for operation, help_text in (
        ("add", "Interactively add a Provider entry"),
        ("list", "List Provider entries"),
        ("delete", "Interactively delete a Provider entry"),
    ):
        command = operations.add_parser(operation, help=help_text)
        command.add_argument(
            "path",
            nargs="?",
            help=(
                "providers.yaml file or its directory; when omitted, the Runtime "
                "configuration path is used after prompting"
            ),
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """运行 ``flowing-config providers`` 命令。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        path = _choose_path(args.path)
        if args.operation == "add":
            return _add_provider(path)
        if args.operation == "list":
            return _list_providers(path)
        if args.operation == "delete":
            return _delete_provider(path)
        parser.error(f"unknown operation: {args.operation}")
    except EOFError:
        print("\nInput ended; cancelled.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except ProviderConfigFileError as exc:
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


def _prompt_entry_name(data: Mapping[str, Any]) -> str:
    while True:
        entry_name = input("Provider entry name (identity): ").strip()
        if not entry_name:
            print("Entry name cannot be empty. Try again.")
            continue
        if entry_name in data:
            print(f"Entry '{entry_name}' already exists. Enter a different name.")
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
        if config_field.sensitive:
            value = getpass.getpass(prompt)
        else:
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
        readline.parse_and_bind("tab: complete")
        yield
    finally:
        readline.set_completer(previous)


if __name__ == "__main__":
    raise SystemExit(main())
