{{ fullname | escape | underline}}

.. automodule:: {{ fullname }}
{% if fullname == "flowing.builtins" %}   :members: register_builtins
{% elif fullname == "flowing.interfaces" %}   :members: parse_kv_args
{% elif fullname == "flowing.plugins" %}   :members: Plugin
{% elif fullname not in [
  "flowing",
  "flowing._unstable",
  "flowing.composables",
  "flowing.plugins.clipboard",
  "flowing.plugins.comm",
  "flowing.plugins.cron",
  "flowing.plugins.skills",
  "flowing.plugins.workflow",
  "flowing.providers",
  "flowing.tool",
] %}   :members:
{% endif %}   :show-inheritance:

{%- block modules %}
{%- if modules %}
.. rubric:: Modules

.. autosummary::
   :toctree:
   :recursive:
{% for item in modules %}
   {{ item }}
{%- endfor %}
{% endif %}
{%- endblock %}
