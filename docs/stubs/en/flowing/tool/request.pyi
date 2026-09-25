"""``flowing.tool.request`` — ``RequestTool``, a zero-code HTTP/HTTPS tool declared in a ``.fya`` file.

The public type is re-exported from ``flowing.tool``.
"""
from typing import Any, Literal
from flowing.tool.core import Tool, ToolDefinition

def _auth_headers(auth: dict[str, Any]) -> dict[str, str]: ...

class RequestTool(Tool):
    """Build and send an HTTP request from declared arguments.

    .. rubric:: Overview

    A ``type: request`` declaration must provide ``url`` and ``args``. The tool
    maps arguments to the URL path, query string, or request body. The
    ``query`` and ``body`` settings can explicitly override the default
    mapping. Like :class:`flowing.tool.CliTool`, this tool turns a common
    integration into declarative configuration. Credentials are supplied
    through ``{{ env.X }}`` templates and are not included in messages or
    persisted state.

    .. rubric:: Example

    .. code-block:: yaml

        # create-order/TOOL.fya
        name: create-order
        type: request
        description: "Create an order after the user confirms the cart."
        url: https://api.example.com/v1/orders
        method: POST
        headers:
          Authorization: "Bearer {{ env.SHOP_API_KEY }}"
        auth:
          type: api_key
          header: X-API-Key
          value: "{{ env.API_KEY }}"
        args:
          user_id:
            type: string
            description: User ID.
          items:
            type: array
            description: Items in the cart.
          currency: CNY                  # Sugar syntax: a literal becomes {type: string, default: CNY}.
        output:
          type: object
          properties:
            order_id:
              type: string
            total_amount:
              type: number
        timeout: 30
        expected_status: [200, 201]

    .. rubric:: Behavior

    - The URL template is rendered from the arguments. Path arguments are not
      URL-escaped, are the declaration author's responsibility with respect to
      URL structure, and are excluded from the body and query.
    - Other arguments go to a JSON body for ``POST``, ``PUT``, and ``PATCH``
      requests, or to the query string for ``GET`` and ``DELETE`` requests.
      The ``query`` and ``body`` settings can explicitly override the default
      mapping.
    - If ``auth`` and ``headers`` define the same header, the header generated
      from ``auth`` takes precedence.
    - A response is parsed as JSON by default. When ``output`` is declared,
      fields are selected according to its schema and unrelated fields are
      ignored. A non-JSON response falls back to text.
    - A response status code outside ``expected_status`` produces an
      ``error`` result containing the status code and a response summary; it
      does not raise an exception to the caller.

    :raises flowing.errors.MissingSchemaError: The ``.fya`` declaration omits
        ``args``.

    .. seealso:: :class:`flowing.tool.CliTool` for another zero-code tool.
    """
    def __init__(self, *, definition: ToolDefinition, url: str, method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST", headers: dict[str, str] | None = None, auth: dict[str, Any] | None = None, query: list[str] | None = None, body: list[str] | None = None, expected_status: list[int] | None = None, timeout: float = 30.0) -> None:
        """Create an HTTP request tool.

        :param definition: The tool declaration. ``args`` is required, and
            ``output_schema`` corresponds to the ``output:`` field in the
            ``.fya`` declaration.
        :param url: The endpoint URL. Jinja2 templates can supply path
            arguments.
        :param method: The HTTP method. The default is ``POST``.
        :param headers: Static request headers. Values support templates.
        :param auth: Authentication shorthand for ``basic``, ``bearer``, or
            ``api_key`` authentication. Generated authentication headers take
            precedence over conflicting values in ``headers``.
        :param query: Argument names that must be sent in the query string.
        :param body: Argument names that must be sent in the JSON body.
        :param expected_status: Accepted response status codes. ``None`` uses
            ``[200, 201]``.
        :param timeout: Request timeout in seconds. The default is 30 seconds.
        """
        ...

    async def execute(self, **kwargs: Any) -> Any:
        """Build the request from arguments and return the response payload.

        .. rubric:: Behavior

        - The URL template is rendered from the arguments. Path arguments are
          not URL-escaped; the declaration author is responsible for the URL
          structure. Path arguments are excluded from the body and query.
        - The ``query`` and ``body`` settings explicitly override the default
          mapping for other arguments. Remaining arguments follow the method
          default: ``POST``, ``PUT``, and ``PATCH`` use the JSON body; ``GET``
          and ``DELETE`` use the query string.
        - Authentication headers take precedence over conflicting values in
          ``headers``.
        - A response status outside ``expected_status`` raises an exception
          containing the status code and response summary. ``Tool.__call__``
          converts it to a ``ToolResult`` with ``status="error"`` rather than
          propagating it to the caller.
        - Responses are parsed as JSON by default. If
          ``definition.output_schema`` is declared, fields are selected
          according to its schema and unrelated fields are ignored. A
          non-JSON response falls back to text.
        """
        ...
