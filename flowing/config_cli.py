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
        description="管理 Provider 配置文件。此命令不启动 Runtime 或访问模型服务。",
    )
    commands = parser.add_subparsers(dest="resource", required=True)
    providers = commands.add_parser("providers", help="管理 providers.yaml")
    operations = providers.add_subparsers(dest="operation", required=True)
    for operation, help_text in (
        ("add", "交互式添加 Provider 条目"),
        ("list", "列出 Provider 条目"),
        ("delete", "交互式删除 Provider 条目"),
    ):
        command = operations.add_parser(operation, help=help_text)
        command.add_argument(
            "path",
            nargs="?",
            help="providers.yaml 文件或其所在目录；省略时询问并默认使用 Runtime 配置路径",
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
        print("\n输入结束，已取消。", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except ProviderConfigFileError as exc:
        print(f"flowing-config: {exc}", file=sys.stderr)
        return 2
    return 0


def _choose_path(path_arg: str | None):
    if path_arg is not None:
        return resolve_providers_path(path_arg)
    default_path = resolve_providers_path(None)
    value = input(f"Provider 配置文件路径 [{default_path}]: ").strip()
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
    print(f"已写入 {path}：Provider 条目“{entry_name}”。新 Runtime 构造后生效。")
    return 0


def _prompt_entry_name(data: Mapping[str, Any]) -> str:
    while True:
        entry_name = input("Provider 条目名（身份名）: ").strip()
        if not entry_name:
            print("条目名不能为空，请重新输入。")
            continue
        if entry_name in data:
            print(f"条目“{entry_name}”已存在，请输入另一个名称。")
            continue
        return entry_name


def _prompt_adapter() -> tuple[str, type]:
    adapters = provider_adapters()
    by_name = dict(adapters)
    if not adapters:
        raise ProviderConfigFileError("没有已注册的 Provider adapter。")

    with _adapter_completion(tuple(by_name)):
        while True:
            value = input("Provider 类别（adapter）: ").strip()
            if value in by_name:
                return value, by_name[value]
            if not value:
                print("可用类别：" + ", ".join(by_name))
                continue
            matches = [name for name in by_name if name.startswith(value)]
            if len(matches) == 1:
                return matches[0], by_name[matches[0]]
            if matches:
                print("匹配多个类别：" + ", ".join(matches))
            else:
                print("没有匹配的类别。可用类别：" + ", ".join(by_name))


def _prompt_field(
    config_field: ProviderConfigField,
    adapter_cls: type,
) -> tuple[bool, Any]:
    default = config_field.default_for(adapter_cls)
    if default is None:
        suffix = "必填"
    elif default == "":
        suffix = "可选，留空跳过"
    elif config_field.sensitive:
        suffix = "留空采用默认值"
    else:
        suffix = f"留空采用 {default!r}"
    prompt = f"{config_field.prompt}（{config_field.name}，{suffix}）: "

    while True:
        if config_field.sensitive:
            value = getpass.getpass(prompt)
        else:
            value = input(prompt)
        if value == "":
            if default is None:
                print("此项必填，请输入值。")
                continue
            if not config_field.persist_default:
                return False, default
            return True, default

        try:
            value = _expand_env_shortcut(value, config_field)
            return True, config_field.parser(value)
        except (TypeError, ValueError):
            print("输入格式无效，请重试此项。")


def _expand_env_shortcut(value: str, config_field: ProviderConfigField) -> str:
    if not config_field.supports_env:
        return value
    if config_field.parser is not str:
        if value.startswith("env.") or value.startswith("{{env."):
            raise ValueError("环境变量引用只支持字符串字段")
        return value

    if value.startswith("env."):
        match = _ENV_SHORT_RE.fullmatch(value)
        if match is None:
            raise ValueError("环境变量名无效")
        return "{{env." + match.group(1) + "}}"
    if value.startswith("{{env.") and _ENV_TEMPLATE_RE.fullmatch(value) is None:
        raise ValueError("环境变量模板无效")
    return value


def _list_providers(path) -> int:
    snapshot = read_providers(path)
    print(f"Provider 配置文件：{path}")
    if not snapshot.data:
        print("没有已配置的 Provider。")
        return 0

    adapter_classes = dict(provider_adapters())
    for entry_name, raw_fields in snapshot.data.items():
        print(f"\n{entry_name}")
        if not isinstance(raw_fields, Mapping):
            print(f"  配置：{_summarize(raw_fields)}")
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
    sensitive = config_field.sensitive if config_field is not None else bool(_SECRET_KEY_RE.search(name))
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
        return f"<字符串，{len(value)} 字符>"
    if isinstance(value, Mapping):
        return f"<映射，{len(value)} 项>"
    if isinstance(value, (list, tuple)):
        return f"<列表，{len(value)} 项>"
    return f"<{type(value).__name__}>"


def _delete_provider(path) -> int:
    snapshot = read_providers(path)
    if not snapshot.data:
        print(f"Provider 配置文件没有条目：{path}")
        return 0

    entries = list(snapshot.data)
    print(f"Provider 配置文件：{path}")
    for index, entry_name in enumerate(entries, start=1):
        print(f"  {index}. {entry_name}")
    while True:
        selection = input("选择要删除的条目序号（直接回车取消）: ").strip()
        if not selection:
            print("已取消删除。")
            return 0
        try:
            index = int(selection)
        except ValueError:
            print("请输入列表中的序号。")
            continue
        if 1 <= index <= len(entries):
            entry_name = str(entries[index - 1])
            break
        print("序号超出范围，请重新输入。")

    confirm = input(f"确认删除条目“{entry_name}”？[y/N] ").strip()
    if confirm not in {"y", "Y"}:
        print("已取消删除。")
        return 0

    delete_provider_entry(
        path,
        entries[index - 1],
        expected_fingerprint=snapshot.fingerprint,
    )
    print(f"已从 {path} 删除 Provider 条目“{entry_name}”。新 Runtime 构造后生效。")
    return 0


@contextmanager
def _adapter_completion(names: tuple[str, ...]) -> Iterator[None]:
    if not sys.stdin.isatty():
        print("当前输入不是终端，Tab 补全不可用；可以输入类别名前缀。")
        yield
        return
    try:
        import readline
    except ImportError:
        print("当前平台不支持 readline，Tab 补全不可用；可以输入类别名前缀。")
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
