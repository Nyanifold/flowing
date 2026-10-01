"""Raw read and write utilities for ``providers.yaml`` and ``models.yaml``.

These helpers back the standalone configuration command (``flowing-config``):
they resolve configuration paths, read files in round-trip mode without
expanding templates, and add or delete entries with snapshot-fingerprint
conflict protection.
"""

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


class ProviderConfigFileError(Exception):
    """A configuration path or raw ``providers.yaml`` read/write operation failed."""


class ProviderConfigConflictError(ProviderConfigFileError):
    """The file changed during interactive configuration."""


class ProviderEntryExistsError(ProviderConfigFileError):
    """The target file already contains the given entry name."""


class ProviderEntryMissingError(ProviderConfigFileError):
    """The target file no longer contains the selected entry."""


class ModelConfigFileError(Exception):
    """A configuration path or raw ``models.yaml`` read/write operation failed."""


class ModelConfigConflictError(ModelConfigFileError):
    """The model file changed during interactive configuration."""


class ModelEntryExistsError(ModelConfigFileError):
    """The target model file already contains the given entry name."""


class ModelEntryMissingError(ModelConfigFileError):
    """The target model file no longer contains the selected entry."""


@dataclass(frozen=True)
class ProvidersSnapshot:
    """A round-trip YAML mapping and the version fingerprint of its source file."""

    data: Any
    fingerprint: str | None


@dataclass(frozen=True)
class ModelsSnapshot:
    """A round-trip YAML mapping and the version fingerprint of its source file."""

    data: Any
    fingerprint: str | None


def default_providers_path(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the default Provider configuration file, the same file ``Runtime`` uses.

    Environment paths follow the Runtime interpretation:
    ``FLOWING_PROVIDERS_PATH`` names a file, and ``FLOWING_CONFIG_HOME``
    names the directory containing ``providers.yaml``.

    :param env: Environment mapping to read; ``None`` uses ``os.environ``.
    :param home: Home directory used when no environment override applies;
        ``None`` uses ``Path.home()``.
    :return: The default ``providers.yaml`` path.
    """
    ...


def resolve_providers_path(
    path_arg: str | Path | None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve a command-line path to a ``providers.yaml`` file.

    A directory path maps to the ``providers.yaml`` beneath it; a path with a
    ``.yaml`` / ``.yml`` suffix names the file directly; a nonexistent path
    without such a suffix is treated as a directory. With no argument, the
    Runtime default file path is used directly.

    :param path_arg: File or directory path from the command line, or
        ``None`` for the Runtime default.
    :param env: Environment mapping used for the default path; ``None`` uses
        ``os.environ``.
    :param home: Home directory used for the default path; ``None`` uses
        ``Path.home()``.
    :return: The resolved ``providers.yaml`` file path.
    :raises ProviderConfigFileError: The configured path is a directory where
        a file is expected, is not a regular file or directory, or does not
        use a ``.yaml`` / ``.yml`` suffix.
    """
    ...


def default_models_path(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the default model configuration file, the same file ``Runtime`` uses.

    ``FLOWING_MODELS_PATH`` names a file, and ``FLOWING_CONFIG_HOME`` names
    the directory containing ``models.yaml``.

    :param env: Environment mapping to read; ``None`` uses ``os.environ``.
    :param home: Home directory used when no environment override applies;
        ``None`` uses ``Path.home()``.
    :return: The default ``models.yaml`` path.
    """
    ...


def resolve_models_path(
    path_arg: str | Path | None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve a command-line path to a ``models.yaml`` file.

    The mapping rules match :func:`resolve_providers_path`: directories map
    to the ``models.yaml`` beneath them, ``.yaml`` / ``.yml`` paths name the
    file directly, and nonexistent paths without such a suffix are treated as
    directories. With no argument, the Runtime default file path is used
    directly.

    :param path_arg: File or directory path from the command line, or
        ``None`` for the Runtime default.
    :param env: Environment mapping used for the default path; ``None`` uses
        ``os.environ``.
    :param home: Home directory used for the default path; ``None`` uses
        ``Path.home()``.
    :return: The resolved ``models.yaml`` file path.
    :raises ModelConfigFileError: The configured path is a directory where a
        file is expected, is not a regular file or directory, or does not use
        a ``.yaml`` / ``.yml`` suffix.
    """
    ...


def read_providers(path: Path) -> ProvidersSnapshot:
    """Read the raw ``providers.yaml`` YAML in round-trip mode without expanding environment-variable references.

    A missing file reads as an empty mapping with a ``None`` fingerprint;
    unreadable, undecodable, or non-mapping content raises
    :class:`ProviderConfigFileError`.
    """
    ...


def read_models(path: Path) -> ModelsSnapshot:
    """Read the raw ``models.yaml`` model YAML in round-trip mode without evaluating Parsable strings.

    A missing file reads as an empty mapping with a ``None`` fingerprint;
    unreadable, undecodable, or non-mapping content raises
    :class:`ModelConfigFileError`.
    """
    ...


def add_provider_entry(
    path: Path,
    entry_name: str,
    fields: Mapping[str, Any],
    *,
    expected_fingerprint: str | None,
) -> None:
    """Add an entry after verifying the file still matches the snapshot from when prompting began.

    :param path: The ``providers.yaml`` file path.
    :param entry_name: New entry name (identity); it must not already exist.
    :param fields: Field mapping stored under the entry.
    :param expected_fingerprint: Fingerprint captured before prompting began;
        a mismatch means the file changed during the interaction.
    :raises ProviderConfigConflictError: The file changed during the interaction.
    :raises ProviderEntryExistsError: The entry name already exists in the file.
    """
    ...


def delete_provider_entry(
    path: Path,
    entry_name: str,
    *,
    expected_fingerprint: str | None,
) -> None:
    """Delete an entry after verifying the file still matches the snapshot from when the entry was selected.

    :param path: The ``providers.yaml`` file path.
    :param entry_name: Entry name to delete.
    :param expected_fingerprint: Fingerprint captured when the entry was
        selected; a mismatch means the file changed during the interaction.
    :raises ProviderConfigConflictError: The file changed during the interaction.
    :raises ProviderEntryMissingError: The entry no longer exists in the file.
    """
    ...


def add_model_entry(
    path: Path,
    entry_name: str,
    fields: Mapping[str, Any],
    *,
    expected_fingerprint: str | None,
) -> None:
    """Add a model entry after verifying the file still matches the snapshot from when prompting began.

    :param path: The ``models.yaml`` file path.
    :param entry_name: New entry name (identity); it must not already exist.
    :param fields: Field mapping stored under the model entry.
    :param expected_fingerprint: Fingerprint captured before prompting began;
        a mismatch means the file changed during the interaction.
    :raises ModelConfigConflictError: The model file changed during the interaction.
    :raises ModelEntryExistsError: The entry name already exists in the file.
    """
    ...


def delete_model_entry(
    path: Path,
    entry_name: str,
    *,
    expected_fingerprint: str | None,
) -> None:
    """Delete a model entry after verifying the file still matches the snapshot from when the entry was selected.

    :param path: The ``models.yaml`` file path.
    :param entry_name: Entry name to delete.
    :param expected_fingerprint: Fingerprint captured when the entry was
        selected; a mismatch means the file changed during the interaction.
    :raises ModelConfigConflictError: The model file changed during the interaction.
    :raises ModelEntryMissingError: The entry no longer exists in the file.
    """
    ...


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
    """Serialize to a temporary file in the same directory, then atomically replace the target file.

    The file version is checked again after serialization and before the
    replacement. The original file is left untouched on failure.
    """
    ...


@contextmanager
def _file_lock(
    path: Path,
    resource: str,
    file_error: type[Exception],
    conflict_error: type[Exception],
) -> Iterator[None]:
    """Serialize configuration-command writes that follow the same convention using an advisory lock file.

    The lock is acquired non-blocking with a ten-second deadline; a timeout
    raises ``conflict_error``.
    """
    ...


def _fsync_directory(directory: Path) -> None:
    """Sync the directory when the platform supports it, persisting the file rename."""
    ...
