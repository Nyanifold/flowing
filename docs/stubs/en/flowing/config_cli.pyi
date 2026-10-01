"""The standalone ``flowing-config`` command-line entry for Provider and model configuration."""

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser, independent of the ``flowing`` Runtime subcommand set.

    The parser exposes two resources, ``providers`` and ``models``, each with
    ``add``, ``list``, and ``delete`` operations. Every operation accepts an
    optional positional ``path`` naming the configuration file or its
    directory; when omitted, the Runtime configuration path is used after
    prompting.
    """
    ...


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``flowing-config`` configuration command.

    Parses ``argv`` and dispatches to the requested resource and operation.
    End of input cancels the current operation and exits with ``0``;
    ``KeyboardInterrupt`` exits with ``130``; a Provider or model
    configuration file error is printed to stderr and exits with ``2``.

    :param argv: Argument sequence; ``None`` parses ``sys.argv``.
    :return: The process exit code.
    """
    ...
