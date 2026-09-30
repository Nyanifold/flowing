"""独立配置命令使用的 ``providers.yaml`` 原始读写工具。"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


class ProviderConfigFileError(Exception):
    """配置路径或 ``providers.yaml`` 原始读写操作失败。"""


class ProviderConfigConflictError(ProviderConfigFileError):
    """交互配置期间文件发生变化。"""


class ProviderEntryExistsError(ProviderConfigFileError):
    """目标文件已包含指定条目名。"""


class ProviderEntryMissingError(ProviderConfigFileError):
    """目标文件已不包含所选条目。"""


class ModelConfigFileError(Exception):
    """配置路径或 ``models.yaml`` 原始读写操作失败。"""


class ModelConfigConflictError(ModelConfigFileError):
    """交互配置期间模型文件发生变化。"""


class ModelEntryExistsError(ModelConfigFileError):
    """目标模型文件已包含指定条目名。"""


class ModelEntryMissingError(ModelConfigFileError):
    """目标模型文件已不包含所选条目。"""


@dataclass(frozen=True)
class ProvidersSnapshot:
    """Round-trip YAML 映射及其来源文件版本指纹。"""

    data: Any
    fingerprint: str | None


@dataclass(frozen=True)
class ModelsSnapshot:
    """Round-trip YAML 映射及其来源文件版本指纹。"""

    data: Any
    fingerprint: str | None


def default_providers_path(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """返回与 ``Runtime`` 相同的默认 Provider 配置文件。

    环境路径沿用 Runtime 的解释方式：``FLOWING_PROVIDERS_PATH`` 指向
    文件，``FLOWING_CONFIG_HOME`` 指向包含 ``providers.yaml`` 的目录。
    """
    env = os.environ if env is None else env
    if "FLOWING_PROVIDERS_PATH" in env:
        return Path(env["FLOWING_PROVIDERS_PATH"])
    if "FLOWING_CONFIG_HOME" in env:
        return Path(env["FLOWING_CONFIG_HOME"]) / "providers.yaml"
    config_home = Path.home() if home is None else Path(home).expanduser()
    return config_home / ".flowing" / "providers.yaml"


def resolve_providers_path(
    path_arg: str | Path | None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """将命令行路径解析为 ``providers.yaml`` 文件。

    目录路径映射到其下的 ``providers.yaml``；``.yaml`` / ``.yml`` 路径
    直接指向文件；不存在且无此类后缀的路径按目录处理。未提供参数时，
    直接使用 Runtime 当前的默认文件路径。
    """
    if path_arg is None:
        candidate = default_providers_path(env, home)
        candidate = candidate.resolve(strict=False)
        if candidate.exists() and candidate.is_dir():
            raise ProviderConfigFileError(
                f"The configured providers path is a directory, expected a file: {candidate}")
        return candidate

    candidate = Path(path_arg).expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except OSError as exc:
        raise ProviderConfigFileError(
            f"Cannot resolve providers path {candidate}: {exc.strerror or exc}") from None

    if candidate.exists():
        if candidate.is_dir():
            return candidate / "providers.yaml"
        if not candidate.is_file():
            raise ProviderConfigFileError(
                f"Providers path is not a regular file or directory: {candidate}")
        if candidate.suffix.lower() not in {".yaml", ".yml"}:
            raise ProviderConfigFileError(
                f"Providers file must use a .yaml or .yml suffix: {candidate}")
        return candidate

    if candidate.suffix.lower() in {".yaml", ".yml"}:
        return candidate
    return candidate / "providers.yaml"


def default_models_path(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """返回与 ``Runtime`` 相同的默认模型配置文件。"""
    env = os.environ if env is None else env
    if "FLOWING_MODELS_PATH" in env:
        return Path(env["FLOWING_MODELS_PATH"])
    if "FLOWING_CONFIG_HOME" in env:
        return Path(env["FLOWING_CONFIG_HOME"]) / "models.yaml"
    config_home = Path.home() if home is None else Path(home).expanduser()
    return config_home / ".flowing" / "models.yaml"


def resolve_models_path(
    path_arg: str | Path | None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """将命令行路径解析为 ``models.yaml`` 文件。"""
    if path_arg is None:
        candidate = default_models_path(env, home).resolve(strict=False)
        if candidate.exists() and candidate.is_dir():
            raise ModelConfigFileError(
                f"The configured models path is a directory, expected a file: {candidate}")
        return candidate

    candidate = Path(path_arg).expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except OSError as exc:
        raise ModelConfigFileError(
            f"Cannot resolve models path {candidate}: {exc.strerror or exc}") from None

    if candidate.exists():
        if candidate.is_dir():
            return candidate / "models.yaml"
        if not candidate.is_file():
            raise ModelConfigFileError(
                f"Models path is not a regular file or directory: {candidate}")
        if candidate.suffix.lower() not in {".yaml", ".yml"}:
            raise ModelConfigFileError(
                f"Models file must use a .yaml or .yml suffix: {candidate}")
        return candidate

    if candidate.suffix.lower() in {".yaml", ".yml"}:
        return candidate
    return candidate / "models.yaml"


def read_providers(path: Path) -> ProvidersSnapshot:
    """以 round-trip 模式读取原始 YAML，不展开环境变量引用。"""
    return _read_config(path, "providers", ProvidersSnapshot, ProviderConfigFileError)


def read_models(path: Path) -> ModelsSnapshot:
    """以 round-trip 模式读取原始模型 YAML，不求值 Parsable 字符串。"""
    return _read_config(path, "models", ModelsSnapshot, ModelConfigFileError)


def _read_config(path, resource, snapshot_type, error_type):
    path = Path(path)
    try:
        path_stat = path.stat()
    except FileNotFoundError:
        return snapshot_type(_empty_mapping(), None)
    except OSError as exc:
        raise error_type(
            f"Cannot inspect {resource} file {path}: {exc.strerror or exc}") from None
    if not stat.S_ISREG(path_stat.st_mode):
        raise error_type(f"{resource.title()} path is not a regular file: {path}")

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise error_type(
            f"Cannot read {resource} file {path}: {exc.strerror or exc}") from None

    yaml = _round_trip_yaml()
    try:
        data = yaml.load(raw.decode("utf-8")) if raw.strip() else None
    except UnicodeDecodeError:
        raise error_type(f"{resource.title()} file is not valid UTF-8: {path}") from None
    except YAMLError as exc:
        # 解析器错误可能包含源文本片段；只报告路径和行列，避免回显任意配置内容。
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise error_type(
            f"Invalid YAML in {resource} file {path}{location}") from None

    if data is None:
        data = _empty_mapping()
    if not isinstance(data, Mapping):
        raise error_type(
            f"{resource.title()} file root must be a YAML mapping: {path}")
    return snapshot_type(data, hashlib.sha256(raw).hexdigest())


def add_provider_entry(
    path: Path,
    entry_name: str,
    fields: Mapping[str, Any],
    *,
    expected_fingerprint: str | None,
) -> None:
    """确认文件仍与提示开始时的快照一致后添加条目。"""
    _add_entry(
        path, entry_name, fields, expected_fingerprint=expected_fingerprint,
        resource="providers", read_snapshot=read_providers,
        file_error=ProviderConfigFileError,
        conflict_error=ProviderConfigConflictError,
        exists_error=ProviderEntryExistsError,
    )


def delete_provider_entry(
    path: Path,
    entry_name: str,
    *,
    expected_fingerprint: str | None,
) -> None:
    """确认文件仍与选择条目时的快照一致后删除条目。"""
    _delete_entry(
        path, entry_name, expected_fingerprint=expected_fingerprint,
        resource="providers", read_snapshot=read_providers,
        file_error=ProviderConfigFileError,
        conflict_error=ProviderConfigConflictError,
        missing_error=ProviderEntryMissingError,
    )


def add_model_entry(
    path: Path,
    entry_name: str,
    fields: Mapping[str, Any],
    *,
    expected_fingerprint: str | None,
) -> None:
    """确认文件仍与提示开始时的快照一致后添加模型条目。"""
    _add_entry(
        path, entry_name, fields, expected_fingerprint=expected_fingerprint,
        resource="models", read_snapshot=read_models,
        file_error=ModelConfigFileError,
        conflict_error=ModelConfigConflictError,
        exists_error=ModelEntryExistsError,
    )


def delete_model_entry(
    path: Path,
    entry_name: str,
    *,
    expected_fingerprint: str | None,
) -> None:
    """确认文件仍与选择条目时的快照一致后删除模型条目。"""
    _delete_entry(
        path, entry_name, expected_fingerprint=expected_fingerprint,
        resource="models", read_snapshot=read_models,
        file_error=ModelConfigFileError,
        conflict_error=ModelConfigConflictError,
        missing_error=ModelEntryMissingError,
    )


def _add_entry(
    path: Path,
    entry_name: str,
    fields: Mapping[str, Any],
    *,
    expected_fingerprint: str | None,
    resource: str,
    read_snapshot: Callable[[Path], Any],
    file_error: type[Exception],
    conflict_error: type[Exception],
    exists_error: type[Exception],
) -> None:
    path = Path(path)
    _ensure_parent(path.parent, resource, file_error)
    with _file_lock(path, resource, file_error, conflict_error):
        snapshot = read_snapshot(path)
        _assert_same_version(snapshot, expected_fingerprint, resource, conflict_error)
        if entry_name in snapshot.data:
            noun = "Provider" if resource == "providers" else "Model"
            raise exists_error(f"{noun} entry {entry_name!r} already exists in {path}")
        snapshot.data[entry_name] = dict(fields)
        _write_snapshot(
            path, snapshot.data, expected_fingerprint,
            resource=resource, read_snapshot=read_snapshot,
            file_error=file_error, conflict_error=conflict_error,
        )


def _delete_entry(
    path: Path,
    entry_name: str,
    *,
    expected_fingerprint: str | None,
    resource: str,
    read_snapshot: Callable[[Path], Any],
    file_error: type[Exception],
    conflict_error: type[Exception],
    missing_error: type[Exception],
) -> None:
    path = Path(path)
    with _file_lock(path, resource, file_error, conflict_error):
        snapshot = read_snapshot(path)
        _assert_same_version(snapshot, expected_fingerprint, resource, conflict_error)
        if entry_name not in snapshot.data:
            noun = "Provider" if resource == "providers" else "Model"
            raise missing_error(f"{noun} entry {entry_name!r} no longer exists in {path}")
        del snapshot.data[entry_name]
        _write_snapshot(
            path, snapshot.data, expected_fingerprint,
            resource=resource, read_snapshot=read_snapshot,
            file_error=file_error, conflict_error=conflict_error,
        )


def _assert_same_version(
    snapshot: Any,
    expected_fingerprint: str | None,
    resource: str,
    conflict_error: type[Exception],
) -> None:
    if snapshot.fingerprint != expected_fingerprint:
        raise conflict_error(
            f"The {resource} file changed during this interaction. "
            "The file was left untouched; run the command again.")


def _write_snapshot(
    path: Path,
    data: Any,
    expected_fingerprint: str | None,
    *,
    resource: str,
    read_snapshot: Callable[[Path], Any],
    file_error: type[Exception],
    conflict_error: type[Exception],
) -> None:
    """序列化到同目录临时文件，再原子替换目标文件。"""
    current = read_snapshot(path)
    _assert_same_version(current, expected_fingerprint, resource, conflict_error)
    try:
        from io import StringIO

        output = StringIO()
        _round_trip_yaml().dump(data, output)
        serialized = output.getvalue().encode("utf-8")
    except Exception:
        raise file_error(
            f"Could not serialize {resource} file {path}; the original was left untouched") from None

    old_mode: int | None = None
    if path.exists():
        try:
            old_mode = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            raise file_error(
                f"Cannot inspect {resource} file permissions {path}: {exc.strerror or exc}") from None

    temp_path: Path | None = None
    open_fd: int | None = None
    try:
        open_fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(temp_name)
        mode = 0o600 if old_mode is None else old_mode
        if hasattr(os, "fchmod"):
            os.fchmod(open_fd, mode)
        else:
            os.chmod(temp_path, mode)
        stream = os.fdopen(open_fd, "wb")
        open_fd = None  # 文件对象接管并负责关闭描述符
        with stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())

        # 序列化后、替换前再次检查文件版本。
        _assert_same_version(
            read_snapshot(path), expected_fingerprint, resource, conflict_error)
        os.replace(temp_path, path)
        temp_path = None
        _fsync_directory(path.parent)
    except file_error:
        raise
    except OSError as exc:
        raise file_error(
            f"Could not safely write {resource} file {path}: {exc.strerror or exc}; "
            "the original was left untouched") from None
    finally:
        if open_fd is not None:
            os.close(open_fd)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


@contextmanager
def _file_lock(
    path: Path,
    resource: str,
    file_error: type[Exception],
    conflict_error: type[Exception],
) -> Iterator[None]:
    """用 advisory lock 文件串行化遵循同一约定的配置命令写入。"""
    lock_path = path.with_name(f".{path.name}.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise file_error(
            f"Cannot create {resource} lock file {lock_path}: {exc.strerror or exc}") from None

    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            deadline = time.monotonic() + 10
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise conflict_error(
                            f"Another configuration command is updating {path}") from None
                    time.sleep(0.05)
        else:
            import fcntl

            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise conflict_error(
                            f"Another configuration command is updating {path}") from None
                    time.sleep(0.05)
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _ensure_parent(
    directory: Path,
    resource: str,
    file_error: type[Exception],
) -> None:
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    if current.exists() and not current.is_dir():
        raise file_error(f"{resource.title()} parent path is not a directory: {current}")
    for item in reversed(missing):
        try:
            item.mkdir(mode=0o700)
        except FileExistsError:
            if not item.is_dir():
                raise file_error(
                    f"{resource.title()} parent path is not a directory: {item}") from None
        except OSError as exc:
            raise file_error(
                f"Cannot create {resource} directory {item}: {exc.strerror or exc}") from None


def _round_trip_yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.allow_duplicate_keys = False
    yaml.preserve_quotes = True
    return yaml


def _fsync_directory(directory: Path) -> None:
    """平台支持时同步目录，以持久化文件重命名。"""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _empty_mapping() -> Any:
    # 使用 ruamel.yaml round-trip 模式采用的映射类型。
    from ruamel.yaml.comments import CommentedMap

    return CommentedMap()
