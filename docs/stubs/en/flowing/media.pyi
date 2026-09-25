"""``flowing.media`` — normalize tool outputs and represent media values.

.. rubric:: Overview

The media carriers :class:`Image`, :class:`File`, :class:`Audio`, and
:class:`Video` describe rich values returned by tools. Bare bytes and absolute
``Path`` objects can also be normalized. :class:`MediaConverter` and
:func:`register_media_converter` let the framework convert supported
third-party objects into one of those carriers. The public API is also
re-exported from :mod:`flowing.tool`.

:func:`normalize_output` is the asynchronous normalization step. It may read
files and converts carriers, bytes, paths, and registered objects into
message-level media blocks. It examines only the top-level value and, for a
top-level list or tuple, its immediate members; it does not recursively walk
nested containers. :func:`output_to_blocks` is the synchronous shaping step
that turns normalized values into message content blocks.

Carrier type takes precedence over MIME routing: ``Image`` always produces an
``ImageBlock`` and ``File`` always produces a ``FileBlock``, even when the MIME
type suggests another category. For unforced bytes or paths, MIME is chosen
from an explicit value, then a path suffix, then recognized byte signatures;
unknown MIME types become ``FileBlock`` values. Synthesized filenames use the
first 12 characters of a SHA-256 digest and a MIME-derived extension, or
``.bin`` when no extension can be inferred.

.. rubric:: Behavior

All carriers require either ``data`` or ``path``. A path must be absolute.
String data is base64 and is decoded during normalization, not during
carrier construction.

Normalization preserves basic Python values and existing allowed blocks.
Top-level tuples become lists. Unsupported values and forbidden
``ToolCallBlock`` or ``ThinkingBlock`` values raise ``ValueError``. Sequence
conversion is shallow, but the forbidden-block contract applies at any depth
and to every input shape. A deeply nested non-JSON value may instead fail
later when shaped as a ``StructBlock``.

.. seealso::

    :mod:`flowing.tool`
        Tool result types and public re-exports of this module's API.
    :class:`flowing.tool.ToolResult`
        The result object whose ``output`` is normalized and shaped.
    :mod:`flowing.message`
        Message-level content block definitions.
"""
from __future__ import annotations
from collections.abc import Callable
import os
from pathlib import Path
from typing import Any
from flowing.message import ContentBlock, MediaBlock

class Image:
    """A carrier for image data returned by a tool.

    Use this carrier when the tool must specify a MIME type or filename, or
    must force image-block handling. Otherwise a tool may return bytes or an
    absolute ``Path`` and let normalization infer the metadata. A registered
    third-party converter may also return an ``Image`` carrier.

    Construction raises ``ValueError`` when both ``data`` and ``path`` are
    absent, or when ``path`` is relative. The image carrier always selects an
    ``ImageBlock``; the MIME type does not change the selected block class.

    .. seealso:: :func:`normalize_output`, :class:`flowing.message.ImageBlock`.
    """
    data: bytes | str | None
    """Raw bytes or a strict base64 string. At least one of ``data`` and
    ``path`` must be supplied.
    """
    path: str | os.PathLike | None
    """Absolute filesystem path. Relative paths raise ``ValueError`` during
    carrier construction.
    """
    mime_type: str | None
    """Explicit MIME type. When omitted, normalization tries the path suffix
    and then recognized byte signatures.
    """
    name: str | None
    """Explicit filename. When omitted, normalization uses the path basename
    or synthesizes a digest-based filename.
    """

class File:
    """A carrier that forces file-block handling.

    ``File(path="report.png")`` still produces a ``FileBlock`` rather than
    an ``ImageBlock``; its MIME type may nevertheless be inferred as
    ``image/png``. Fields and validation follow :class:`Image`.
    """
    data: bytes | str | None
    path: str | os.PathLike | None
    mime_type: str | None
    name: str | None

class Audio:
    """A carrier that forces audio-block handling.

    Its MIME type and filename follow the same rules as :class:`Image`, but
    the carrier type selects an ``AudioBlock`` even when MIME inference is
    absent or indicates another category.
    """
    data: bytes | str | None
    path: str | os.PathLike | None
    mime_type: str | None
    name: str | None

class Video:
    """A carrier that forces video-block handling.

    Its MIME type and filename follow the same rules as :class:`Image`, but
    the carrier type selects a ``VideoBlock`` regardless of MIME routing.
    """
    data: bytes | str | None
    path: str | os.PathLike | None
    mime_type: str | None
    name: str | None

class MediaConverter:
    """A registration record that converts one third-party type into a carrier.

    The ``module`` and ``qualname`` fields identify a Python class by its full
    module and qualified name. During normalization, the framework compares
    that name against the returned object's class MRO, so instances of
    subclasses can match a converter registered for a base class. Matching
    uses names rather than importing or retaining the third-party class, so a
    converter can be registered before its library is imported.

    The converter must return an ``Image``, ``File``, ``Audio``, or ``Video``
    carrier.

    .. seealso:: :func:`register_media_converter`, :func:`normalize_output`.
    """
    module: str
    """The third-party class's module name, such as ``"PIL.Image"``."""
    qualname: str
    """The class's qualified name, such as ``"Image"``."""
    convert: Callable[[Any], Image | File | Audio | Video]
    """Callable that converts a matching object to one of the four media
    carrier types.
    """

def register_media_converter(tp_or_module: type | str, qualname: str | None = None, convert: Callable[[Any], Image | File | Audio | Video] | None = None) -> None:
    """Register a process-wide converter for a third-party Python type.

    After registration, ``normalize_output`` checks returned objects against
    the registered module and qualified name, searching the object's
    class MRO so subclasses can match. Passing a class stores its
    ``__module__`` and ``__qualname__``; in that form, omit ``qualname``.
    Alternatively, pass the module name and qualified name as strings.

    Registrations are global to the imported module and shared across
    Runtime instances. There is no per-Runtime isolation or public unregister
    operation. Registrations persist across tests and Runtime instances; tests
    that need isolation must clean up the module-level registry themselves.
    Register each module/qualified-name pair only once.

    .. rubric:: Example

    .. code-block:: python

        from flowing.tool import Image, register_media_converter

        class PlotImage:
            def render_png(self) -> bytes:
                ...

        def to_image(plot: PlotImage) -> Image:
            return Image(data=plot.render_png(), mime_type="image/png")

        register_media_converter(PlotImage, convert=to_image)

    :param tp_or_module: A Python class, or the module name of the class to
        match.
    :param qualname: Qualified class name; required when ``tp_or_module`` is
        a string and omitted when it is a class.
    :param convert: Converter returning one of the four carrier types.
    :raises ValueError: The pair is already registered, a class is supplied
        together with ``qualname``, or any required registration value is
        missing.
    """
    ...

async def normalize_output(value: Any) -> Any:
    """Normalize one tool result value or its top-level sequence members.

    Basic values (including dictionaries, lists, tuples, dataclass instances,
    and Pydantic models) pass through. Allowed content blocks also pass
    through. A top-level list or tuple is examined one level deep: tuples
    become lists, basic members are retained, and non-basic members are
    converted individually. Carriers, bytes, absolute ``Path`` objects, and
    matching registered objects become media blocks. Carrier classes force
    their corresponding block type; unforced bytes and paths use MIME
    routing.

    This operation is asynchronous because it may read a file. It is
    idempotent for normalized values: existing blocks and basic values remain
    unchanged, so the tool and Agent cleanup paths can both call it. Sequence
    conversion inspects each top-level member without recursively converting
    nested containers. Forbidden ``ToolCallBlock`` and ``ThinkingBlock``
    values are rejected at any depth and in any input shape.

    :param value: Raw value returned by a tool or stored in ``ToolResult.output``.
    :return: The same basic value, an allowed content block, or a list whose
        convertible top-level members have become blocks.
    :raises ValueError: A directly encountered ``ToolCallBlock`` or
        ``ThinkingBlock`` is forbidden, a relative bare ``Path`` is supplied,
        base64 carrier data is invalid, or a non-basic value has no registered
        conversion. File-reading errors propagate from the filesystem.

    .. seealso:: :func:`output_to_blocks`, :class:`MediaConverter`.
    """
    ...

def output_to_blocks(output: Any, *, error: str | None = None) -> list[ContentBlock]:
    """Shape a normalized tool output into message content blocks.

    This synchronous operation performs no file I/O. Its input is expected to
    be a normalized form produced by :func:`normalize_output` (as it is after
    ``Agent.tool_call``). ``None`` becomes an empty block list; strings become
    text blocks; scalar values become JSON-encoded text blocks that can be
    parsed back; dictionaries, dataclass instances, Pydantic
    models, and sequences without content blocks become one structured block.
    A single content block is passed through. If a top-level list or tuple
    contains at least one content block, each member becomes a block in the
    original order: existing blocks pass through, strings become text,
    scalars become JSON-encoded text, and structured values become structured
    blocks. Nested containers are not recursively traversed here.

    If ``error`` is not ``None``, a text block containing it is appended after
    the output blocks, including when the output is ``None``.

    :param output: A normalized tool result value.
    :param error: Optional error text to append as the last block.
    :return: Message content blocks in their output order.
    :raises ValueError: A structured block cannot validate nested data that is
        not JSON-compatible.

    .. seealso:: :func:`normalize_output`, :class:`flowing.tool.ToolResult`.
    """
    ...
