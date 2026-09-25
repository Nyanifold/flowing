"""The ``web`` command and Flowing's bundled default frontend assets.

.. rubric:: Overview

This module defines the ``web`` subcommand and the frontend asset boundary
between the web interface and Flowing. The bundled page is a chat UI that uses
public APIs to send messages, display streamed output, inspect snapshots, and
select, switch, or create Agents.

The framework provides the HTTP API through ``flowing serve``. Whether to
include a frontend, and how that frontend looks, are interface-layer choices.
Replacing or removing this frontend does not change any endpoint in
:data:`flowing.interfaces.serve.SERVE_ENDPOINTS`; ``web`` is a convenience
combination of ``serve`` and these frontend assets.

.. rubric:: Bundled frontend

The default frontend supports ongoing conversations. It submits messages to
``POST /agents/<agent-id>/message`` and displays generation through the SSE
endpoint ``GET /agents/<agent-id>/stream``. The UI first indicates that a
response is being generated, then displays streamed deltas and the completed
response. Tool calls and steer messages are visible through ``message``
events. The page also displays snapshots, lets users select or switch Agents
from the ``GET /agents`` list, and can create an Agent through
``POST /agents``. It has an Agent sidebar with recent-reply previews and
separate ``Records`` and ``Tree`` views. The Records view renders the message
tree, indenting only at branch points; the first message in each branch can
be collapsed, and each message has a preview. The Tree view lets users select
a node to switch branches. Message bodies are rendered as Markdown with GFM;
thinking and tool-call/result cards can be collapsed, and expanded tool data
uses JSON highlighting. The input area shows context usage, the header
provides model selection, and the input accepts both messages and REPL
commands through the server's observation and control endpoints.

Root selection is client-side; the server endpoint does not select a root.
When the page loads, it lists root Agents with their IDs, last-reply previews,
and modification times, using the same data as the REPL's ``/agents`` command.
If there are multiple roots, the page initially selects the most recently
modified one and keeps the current target ID visible. If there are no roots,
it shows an empty state with an option to create an Agent.

The frontend communicates with the Runtime only through the closed HTTP API.
It does not open a separate message channel or call internal Agent methods,
so HTTP-submitted messages follow the same message path as CLI input. This
module does not build or package frontend assets: they are ready when the
module is imported. ``GET /`` and ``GET /assets/*`` read fields from
:class:`FrontendAssets`. Streaming uses the server's SSE endpoint, not
WebSockets.

.. rubric:: Usage example

.. code-block:: console

    $ flowing web .
    # Open http://127.0.0.1:8000/ in a browser to start a conversation.

.. rubric:: See also

:func:`cmd_web` is the only consumer of these assets and serves them at
``GET /`` and ``GET /assets/*``.
:data:`flowing.interfaces.serve.SERVE_ENDPOINTS` lists the closed HTTP API
consumed by the frontend.
"""
from dataclasses import dataclass
from flowing.runtime import Runtime

@dataclass(frozen=True)
class FrontendAssets:
    """Bundle the entry-point HTML and static resource mapping.

    This immutable value object contains everything that the ``web`` command
    mounts for the frontend. ``GET /`` returns :attr:`index_html`; the web
    command looks up ``GET /assets/<name>`` in :attr:`assets`.

    .. rubric:: Usage example

    .. code-block:: python

        from flowing.interfaces.web import get_frontend_assets

        assets = get_frontend_assets()
        html = assets.index_html
        js = assets.assets["app.js"]

    .. rubric:: Behavior notes

    - ``index_html`` is a non-empty, complete HTML document.
    - Keys in ``assets`` are relative paths under ``/assets/``: they have no
      leading slash and contain no ``..`` path component. Values are resource
      bytes.
    - The web command maps a missing key to ``404``; this class does not create
      error responses.

    .. rubric:: See also

    :func:`get_frontend_assets` and :func:`cmd_web`
    """
    assets: dict[str, bytes]
    """Mapping from static resource paths to their bytes.

    The web layer uses this in-memory mapping to serve ``GET /assets/*`` and
    does not read the filesystem for each request. Keys are exact relative
    paths under ``/assets/``; they have no leading slash and contain no
    ``..`` component. Lookup does not enumerate directories, and the mapping
    is immutable after construction.

    .. rubric:: See also

    :attr:`index_html`
    """
    index_html: bytes
    """The complete HTML document returned as the body of ``GET /``.

    The entry page is a non-empty string and is self-contained except for any
    static paths it references. Each such path must begin with ``/assets/``
    and resolve to a key in :attr:`assets`; otherwise the asset package is
    incomplete.

    .. rubric:: See also

    :attr:`assets`
    """

WEB_EXTRA_ENDPOINTS: tuple[str, ...]
"""The closed set of routes that ``web`` adds to ``serve``.

- ``GET /`` returns ``get_frontend_assets().index_html``.
- ``GET /assets/*`` returns the matching resource from
  ``get_frontend_assets().assets``.

The frontend is an interface-layer choice and does not change any API in
:data:`flowing.interfaces.serve.SERVE_ENDPOINTS`. The page submits messages
through ``POST /agents/<agent-id>/message`` and cannot open a separate
message channel around the server API.

.. rubric:: See also

:func:`cmd_web` and :func:`get_frontend_assets`
"""

def _install_signal_handlers(runtime) -> None:
    """Bridge SIGINT and SIGTERM to graceful Runtime shutdown for ``web``.

    This is a no-op outside the main thread and on platforms without signal
    support.
    """

def get_frontend_assets() -> FrontendAssets:
    """Return the bundled frontend asset package.

    This is the only entry point used by ``web`` to obtain its default
    frontend. The assets come from the repository's built ``webui`` output:
    one ``webui-dist/index.html`` file with JavaScript and CSS inlined. The
    bundled page has a light theme based on the light palette of the Kimi Code
    web interface. It includes an Agent sidebar with recent-reply previews;
    ``Records`` and ``Tree`` views for the message tree; Markdown rendering
    with GFM; collapsible thinking and tool-call/result cards with JSON
    highlighting; a context-usage indicator; model selection; and an input
    field for messages and REPL commands sent through the server's observation
    and control endpoints.

    In the bundled single-file layout, ``assets`` is empty and every
    ``GET /assets/*`` request returns ``404``.

    .. rubric:: Usage example

    .. code-block:: python

        assets = get_frontend_assets()
        # GET / returns assets.index_html.
        # GET /assets/<name> looks up assets.assets[name]; a missing name is 404.

    .. rubric:: Behavior notes

    - This is a synchronous, read-only operation on files in the packaged
      frontend directory. It performs no network activity.
    - Repeated calls return equivalent assets; they may return the same cached
      object.
    - The function takes no arguments and does not read project configuration.
    - If ``webui-dist/index.html`` is missing, the function raises
      :class:`FileNotFoundError`. This indicates a packaging defect; the
      function does not silently fall back to another frontend.

    .. rubric:: See also

    :class:`FrontendAssets` defines the returned fields.
    :func:`cmd_web` serves the entry page and static-resource routes.
    :data:`WEB_EXTRA_ENDPOINTS` lists the routes added by ``web``.
    """

def _asset_content_type(name: str) -> str:
    """Infer a static resource's Content-Type from its filename suffix.

    Unknown suffixes use a generic binary-stream content type.
    """

async def cmd_web(path: str, main_file: str | None = None, *, host: str = "127.0.0.1", port: int = 8000, **kwargs: str | bool) -> int:
    """Run the HTTP API server and mount the bundled frontend.

    ``web`` shares the HTTP server and all
    :data:`flowing.interfaces.serve.SERVE_ENDPOINTS` with
    :func:`flowing.interfaces.serve.cmd_serve`. It adds the
    :data:`WEB_EXTRA_ENDPOINTS` routes ``GET /`` for the entry page and
    ``GET /assets/*`` for static resources. The assets come from
    :func:`get_frontend_assets`. Replacing or removing the frontend leaves the
    HTTP API unchanged; the frontend has only one message-submission route,
    ``POST /agents/<agent-id>/message``.

    .. rubric:: Usage example

    .. code-block:: console

        $ flowing web .
        # Open http://127.0.0.1:8000/ in a browser to start a conversation.

    .. rubric:: Behavior notes

    All server lifecycle and endpoint behavior is inherited from
    :func:`flowing.interfaces.serve.cmd_serve`. The additional routes behave
    as follows:

    - ``GET /`` returns ``200`` with ``get_frontend_assets().index_html`` and
      ``Content-Type: text/html``.
    - ``GET /assets/<name>`` looks up an exact key in
      ``get_frontend_assets().assets``. A match returns ``200`` and the
      corresponding bytes. A missing key, including a missing packaged asset,
      returns ``404``; it does not terminate the server.

    The frontend does not open another message channel; it communicates with
    the Runtime through ``POST /agents/<agent-id>/message``. The CLI does not
    build or package frontend assets; they are prepared by
    ``flowing.interfaces.web``.

    :param path: Project path as a filesystem path.
    :param main_file: Optional replacement ``main`` file, supplied by CLI
        option ``-f``.
    :param host: Listening address; defaults to ``127.0.0.1``.
    :param port: Listening port; defaults to ``8000``.
    :param kwargs: ``--key value`` arguments passed through to ``launch`` and
        the project's ``main`` function.
    :return: :data:`flowing.interfaces.EXIT_OK` after normal shutdown, or
        :data:`flowing.interfaces.EXIT_RUNTIME_ERROR` if launch or port
        binding fails.

    .. rubric:: See also

    :func:`flowing.interfaces.serve.cmd_serve` and
    :data:`WEB_EXTRA_ENDPOINTS`.
    :func:`get_frontend_assets` is the only source of frontend assets.
    """
