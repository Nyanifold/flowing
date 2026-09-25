"""The API-only HTTP server command, ``serve``.

The ``flowing.interfaces`` package documents the shared option, exit-code,
and signal-handling conventions.
"""
from aiohttp import web
from flowing.agent import Agent
from flowing.runtime import Runtime

SERVE_ENDPOINTS: tuple[str, ...]
"""The closed HTTP endpoint set exposed by the API-only ``serve`` command.

The fixed set keeps this cold-start observation surface predictable; it does
not accept runtime route registration. Paths outside the set return ``404``.
The routes use collection-style REST paths: ``GET /agents`` lists the
collection, ``POST /agents`` creates an Agent with a server-assigned ID, and
``/agents/<agent-id>/...`` addresses a member.

The tuple contains the complete route list. The principal message and
observation routes are:

.. list-table::
   :header-rows: 1

   * - Route
     - Behavior
   * - ``POST /agents/<agent-id>/message``
     - Submit a USER message to the specified Agent and wait for its turn.
   * - ``GET /agents``
     - List pool records, including inactive Agents, with reply previews and modification times.
   * - ``POST /agents``
     - Create a root Agent, or resume an existing recorded Agent when its ID is supplied.
   * - ``GET /agents/<agent-id>/messages``
     - Return the current-head message chain for rendering a conversation.
   * - ``GET /agents/<agent-id>/stream``
     - Stream main-turn text, thinking, appended messages, and turn completion as SSE.
   * - ``POST /agents/<agent-id>/cancel``
     - Cooperatively cancel the current turn; return ``idle`` when there is no active turn.
   * - ``POST /agents/<agent-id>/abort``
     - Abort the current turn without cancelling the Agent's queue consumer.
   * - ``POST /agents/<agent-id>/pause`` and ``POST /agents/<agent-id>/resume``
     - Pause or resume the Agent; ``?recursive=true`` applies the operation recursively.
   * - ``POST /agents/<agent-id>/rewind``
     - Fork the Agent's message tree at the supplied message ID and move the current head.
   * - ``GET /agents/<agent-id>/model`` and ``PATCH /agents/<agent-id>/model``
     - Read or change the Agent's model tag.
   * - ``GET /agents/<agent-id>/models``
     - Return the current model tag and available model tags.
   * - ``GET /agents/<agent-id>/status``
     - Return the Agent snapshot fields used for status and context usage.
   * - ``GET /agents/<agent-id>/tasks``
     - List the Agent's background tasks.
   * - ``POST /agents/<agent-id>/tasks/<task-id>/cancel``
     - Cancel a background task and report whether it was found.
   * - ``GET /agents/<agent-id>/export``
     - Export the current message chain as JSON records or Markdown.
   * - ``GET /agents/<agent-id>/tree``
     - Return the full message tree, root nodes, branch previews, and current head.
   * - ``POST /agents/<agent-id>/command``
     - Run a slash command from an HTTP request and return its output lines.
   * - ``PATCH /agents/<agent-id>``
     - Update the Agent's display name.
   * - ``GET /help``
     - Return the endpoint list.
   * - ``GET /snapshot``
     - Return the read-only Runtime snapshot.
   * - ``GET /healthz``
     - Return ``{"status": "ok"}``; this is a liveness check, not a deep health check.

The API has no default or currently selected Agent. Clients choose a target
using an ID from ``GET /agents``; switching Agents is a client-side change,
not a server endpoint.

.. rubric:: See also

:func:`cmd_serve` and :data:`flowing.interfaces.web.WEB_EXTRA_ENDPOINTS`
"""

_SERVE_HOOK_POINTS: tuple[str, ...]
"""Hook points observed by an SSE connection.

The connection removes this owner's subscriptions from all of these hook
points when the client disconnects or the Agent is destroyed.
"""

_SSE_HEARTBEAT_INTERVAL: float
"""Interval in seconds between SSE keepalive comments when no event arrives.

The heartbeat also detects a disconnected client.
"""

def _install_signal_handlers(runtime) -> None:
    """Bridge SIGINT and SIGTERM to graceful Runtime shutdown for ``serve``.

    This is a no-op outside the main thread and on platforms without signal
    support.
    """

def _json(data: object, status: int = 200) -> web.Response:
    """Create a JSON response without escaping non-ASCII text.

    Values that are not natively JSON-serializable, such as ``datetime``, use
    ``str(value)`` as a fallback.
    """

def _error(status: int, message: str) -> web.Response:
    """Create an error response whose body always has the form ``{"error": ...}``."""

async def _error_middleware(request: web.Request, handler) -> web.Response:
    """Map uncaught exceptions to JSON ``500`` responses.

    This includes signals such as ``Intercepted`` that inherit directly from
    ``Exception``. It prevents unhandled failures from becoming HTML error
    pages. ``web.HTTPException`` is re-raised unchanged for ``aiohttp`` to
    handle.
    """

async def _resolve_agent(runtime: Runtime, agent_id: str) -> Agent | web.Response:
    """Resolve an Agent ID for an endpoint that targets a specific Agent.

    An ID absent from both the live-instance table and the pool is unknown
    and produces ``404``. An inactive pool record is restored through
    ``get_agent``; if restoration raises, this helper produces ``500`` with
    an error summary. A live node that is not an ``Agent``, such as a
    Workflow, also produces ``404``. On success, return the ``Agent``; on
    failure, return a ready-to-send ``web.Response`` that the caller can
    recognize with ``isinstance``.
    """

def _build_app(runtime: Runtime, extra_routes: tuple[tuple[str, str, object], ...] = ()) -> web.Application:
    """Build the shared HTTP application used by ``serve`` and ``web``.

    The registered route keys must match :data:`SERVE_ENDPOINTS` exactly; a
    construction-time assertion enforces this closed endpoint set. The
    ``extra_routes`` argument carries only ``web`` additions such as
    ``GET /`` and ``GET /assets/{name}``; ``serve`` passes an empty tuple.
    Each route is registered from a ``(method, aiohttp path template,
    handler)`` tuple.
    """

async def _serve_runtime(runtime: Runtime, app: web.Application, host: str, port: int) -> int:
    """Bind the HTTP server and tie its lifetime to the Runtime.

    If binding ``host:port`` fails, the function reports the error to stderr,
    calls ``runtime.shutdown()``, and returns
    :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`. The signal handler is
    already installed and the Runtime is active, so shutdown does not depend
    on the HTTP application having started. On success, the function awaits
    the Runtime until shutdown, then calls ``runner.cleanup()`` to cancel
    remaining connections. In-progress POST requests and SSE streams are
    therefore closed without a guarantee that they return a result. The
    function then returns :data:`flowing.interfaces.EXIT_OK`.
    """

async def cmd_serve(path: str, main_file: str | None = None, *, host: str = "127.0.0.1", port: int = 8000, **kwargs: str | bool) -> int:
    """Launch a project and expose its Runtime through the API-only HTTP server.

    The command serves the closed endpoint set in :data:`SERVE_ENDPOINTS`.
    The full list is available from ``GET /help``. The set covers message
    submission, Agent listing and creation, message views, SSE streaming,
    turn and model controls, tasks, exports, message trees, help, snapshots,
    and health checks. It does not serve a frontend.

    CLI options ``-a <host>`` and ``-p <port>`` belong to this command. The
    CLI removes them before invoking project ``main``; defaults are
    ``127.0.0.1`` and ``8000``.

    HTTP requests are one of the message-entry paths into the Runtime.
    Message requests use public APIs such as ``agent.query()`` and
    ``runtime.create_agent()``, making them equivalent to input from the CLI,
    Cron, or communication extensions. To observe background events inside
    the server process, applications can attach observation handlers through
    ``agent.hooks``; the endpoint set itself does not provide a push channel
    for those events.

    .. rubric:: Usage example

    .. code-block:: console

        $ flowing serve . -p 9000

        $ curl -X POST localhost:9000/agents -d '{}'
        {"agent_id": "agent-..."}
        $ curl localhost:9000/agents
        [{"agent_id": "agent-...", "name": null, "last_reply": "...", "modified_at": "..."}]
        $ curl -X POST localhost:9000/agents/agent-.../message -d '{"text": "Hello"}'
        {"message_id": "msg-...", "final_text": "..."}
        $ curl localhost:9000/agents/agent-.../messages
        [{"id": "msg-...", "kind": "user", ...}, ...]
        $ curl -X POST localhost:9000/agents/agent-.../cancel
        {"status": "cancelled"}
        $ curl localhost:9000/snapshot
        {...}
        $ curl localhost:9000/healthz
        {"status": "ok"}

    .. rubric:: Behavior notes

    The command awaits ``launch(path, main_file=main_file, **kwargs)`` and
    installs its signal handlers. If launch fails, it prints an error summary
    to stderr and returns :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`. It
    then binds ``host:port`` and awaits the Runtime until shutdown. The
    shutdown behavior matches :func:`flowing.interfaces.run.cmd_run`.

    The documented endpoint contracts are:

    - ``POST /agents/<agent-id>/message`` accepts a request body of
      ``{"text": str}``. It passes the string to
      :meth:`flowing.agent.Agent.query`, which packages the USER message,
      enqueues it, and waits for the turn result. On success it returns
      ``200 {"message_id": str, "final_text": str}``. ``message_id`` is the
      first message ID in the turn, or an empty string if the turn appended no
      message. An inactive or
      destroyed Agent with a pool record is restored before submission. The
      API has no implicit default Agent; the target ID is required.
    - ``GET /agents`` returns ``200`` and a list of all Agent pool records,
      including inactive sessions. Each item contains ``agent_id``,
      ``agent_type``, ``parent_agent_id``, ``created_at``, ``active``,
      ``name``, ``last_reply``, and ``modified_at``. ``name`` may be ``null``;
      ``modified_at`` is an ISO-formatted timestamp or ``null``. The listing
      and lazy-read behavior match the REPL's ``/agents`` command.
    - ``POST /agents`` accepts ``{"agent_type"?: str, "args"?: dict}``; an
      empty body is valid. If the body includes ``id``, the endpoint resumes
      the existing recorded Agent with that ID and returns ``200
      {"agent_id": str}``; an unknown ID returns ``404``. Without ``id``, it
      calls ``runtime.create_agent()`` and returns ``201 {"agent_id": str}``.
      If ``agent_type`` is omitted, the project-default root Agent type is
      used, matching the REPL's first unbound message. If the pool has no root
      record, the endpoint returns ``400``. A non-string ``agent_type`` or
      non-object ``args`` returns ``400``. A failed creation does not leave a
      partially registered instance.
    - ``GET /agents/<agent-id>/messages`` returns ``200`` with the messages
      on the chain obtained by following parents from the current head. This
      is suitable for rendering a conversation; the output is ordered from
      the oldest reachable message to the current head. If a parent ID is
      missing from the in-memory message map, traversal stops at that break.
      Inactive Agents are restored through ``get_agent`` first.
    - ``GET /agents/<agent-id>/stream`` opens an SSE connection that subscribes
      to the target Agent's hooks and emits events as they occur. Text from
      ``on_provider_delta`` from the main turn is sent as ``event: delta``;
      thinking deltas are sent as ``event: thinking`` when the adapter streams
      them. Each delta payload contains ``message_id`` and ``text``. Side
      queries are excluded. New messages appended by ``on_turn_append`` are
      sent as ``event: message`` with the serialized message record, including
      tool calls and results or injected steer messages. ``after_turn`` is
      sent as ``event: turn_end`` with the ``TurnResult.status``. A comment
      heartbeat is written every second without events. Disconnecting or
      destroying the Agent removes the subscription and ends the stream. An
      SSE stream can remain open while a corresponding ``POST /message`` waits
      for the turn result.
    - ``POST /agents/<agent-id>/cancel`` calls ``agent.cancel()`` to
      cooperatively cancel the active turn and returns
      ``200 {"status": "cancelled"}``. If no turn is active, it is
      idempotent and returns ``200 {"status": "idle"}``.
    - ``GET /snapshot`` returns ``200`` with the read-only JSON serialization
      of ``runtime.snapshot()``.
    - ``GET /healthz`` returns ``200 {"status": "ok"}``. This is a liveness
      check for the HTTP process and Runtime, not a deeper health check.

    Error and edge-case behavior:

    - An Agent ID absent from both the live-instance table and pool returns
      ``404 {"error": "unknown agent"}`` from the message, messages, cancel,
      and stream endpoints. An inactive but recorded Agent is restored instead
      of returning ``404``. A live non-Agent node such as a Workflow also
      returns ``404``.
    - Malformed JSON or a message body without a string ``text`` field returns
      ``400``.
    - ``POST /agents`` returns ``400`` for an unregistered ``agent_type`` and
      ``500`` if Agent setup raises. A failed setup leaves no partial pool
      registration.
    - A path outside the endpoint set returns ``404``.
    - If the port cannot be bound, the command prints the error to stderr,
      shuts down an already launched Runtime, and returns
      :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`.
    - If shutdown begins during a turn, in-progress POST connections are
      cancelled as the Runtime is destroyed; the server does not guarantee a
      response for those requests.

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

    :data:`SERVE_ENDPOINTS` and
    :func:`flowing.interfaces.web.cmd_web`.
    :meth:`flowing.agent.Agent.query` and
    :meth:`flowing.runtime.Runtime.snapshot`.
    """
