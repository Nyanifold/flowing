"""One-shot process execution and startup smoke-check commands.

The shared exit-code conventions are documented in the
``flowing.interfaces`` package.
"""
def _install_signal_handlers(runtime) -> None:
    """Bridge SIGINT and SIGTERM to graceful Runtime shutdown for ``run``.

    The handler only starts ``shutdown()`` and returns immediately; the event
    loop performs the cleanup. It is a no-op outside the main thread and on
    platforms without signal support.
    """

async def cmd_run(path: str, main_file: str | None = None, **kwargs: str | bool) -> int:
    """Launch a project and keep the process alive while awaiting its Runtime.

    This is the simplest long-running command. After launch, it waits for
    messages from any source, including Cron, communication extensions, and
    embedding applications using ``enqueue_message()``. ``repl`` and
    ``serve`` add an interaction layer or HTTP server on top of this lifecycle.

    .. rubric:: Usage example

    .. code-block:: console

        $ flowing run . --workspace_root /ws --debug

    .. rubric:: Behavior notes

    1. The command awaits ``launch(path, main_file=main_file, **kwargs)``.
       ``launch`` registers the ``@`` context, imports the project's ``main``
       file (default ``@/main.py`` or the file selected by CLI option ``-f``),
       and calls ``main``. When it returns, Agents are active and the message
       trees and queues are ready; turn loops are waiting for messages.
    2. The command installs handlers for SIGINT and SIGTERM through
       :func:`_install_signal_handlers`.
    3. It awaits the Runtime until shutdown begins.
    4. After the Runtime wakes, the command returns
       :data:`flowing.interfaces.EXIT_OK`.

    The command does not read input from stdin; use ``repl`` for interactive
    input. A project that does not mount any nodes is valid: the Runtime stays
    idle and waits for shutdown. If ``launch`` raises, the command prints an
    error summary to stderr and returns
    :data:`flowing.interfaces.EXIT_RUNTIME_ERROR` instead of waiting for a
    signal.

    :param path: Project path as a filesystem path.
    :param main_file: Optional replacement ``main`` file, supplied by CLI
        option ``-f``.
    :param kwargs: ``--key value`` arguments passed through to ``launch`` and
        the project's ``main`` function.
    :return: :data:`flowing.interfaces.EXIT_OK` after normal shutdown, or
        :data:`flowing.interfaces.EXIT_RUNTIME_ERROR` if ``launch`` fails.

    .. rubric:: See also

    :func:`flowing.runtime.launch` and
    :meth:`flowing.runtime.Runtime.shutdown`.
    :func:`_install_signal_handlers` bridges signals to shutdown.
    """

async def cmd_test(path: str, main_file: str | None = None, **kwargs: str | bool) -> int:
    """Launch a project, verify that its snapshot is serializable, then shut it down.

    The ``test`` command is a zero-configuration startup smoke check. It
    launches the project, calls ``runtime.snapshot()``, and then shuts down.
    Project-specific assertions belong in user-written code that uses the
    embedding API, for example ``launch()``, ``query()``, and ``snapshot()``
    inside a test framework; the command does not define those policies.

    .. rubric:: Usage example

    .. code-block:: console

        $ flowing test . && echo "project boots"
        $ flowing test . -f ./tests/main_test.py

    A project may define its own behavioral test separately:

    .. code-block:: python

        async def test_root_agent():
            runtime = await launch("/path/to/project")
            agent = await runtime.get_agent("root")
            result = await agent.query("Hello")
            assert result.status in ("completed", "error")
            await runtime.shutdown()

    .. rubric:: Behavior notes

    - If ``launch(path, main_file=main_file, **kwargs)`` raises, for example
      because project ``main`` raises or cannot be imported, the command
      prints an error summary to stderr and returns
      :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`.
    - The smoke assertion passes when ``runtime.snapshot()`` does not raise
      and its result can be serialized as JSON. It does not assert any
      application-specific fields.
    - The command calls ``runtime.shutdown()`` before returning
      :data:`flowing.interfaces.EXIT_OK`. If the snapshot assertion fails, it
      still attempts to shut down the Runtime before exiting.
    - The command neither discovers nor runs project test files. It does not
      mock the LLM. A project can launch without an available Provider when
      that Provider is created lazily and never called.

    :param path: Project path as a filesystem path.
    :param main_file: Optional replacement ``main`` file, supplied by CLI
        option ``-f``.
    :param kwargs: ``--key value`` arguments passed through to ``launch`` and
        the project's ``main`` function.
    :return: :data:`flowing.interfaces.EXIT_OK` if launch and shutdown succeed,
        or :data:`flowing.interfaces.EXIT_RUNTIME_ERROR` if launch or the
        snapshot assertion fails.

    .. rubric:: See also

    :func:`flowing.runtime.launch`,
    :meth:`flowing.runtime.Runtime.snapshot`, and
    :meth:`flowing.agent.Agent.query`.
    """
