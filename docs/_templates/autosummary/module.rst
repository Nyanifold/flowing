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

{% if fullname == "flowing.providers" %}
包级 re-export 的交叉引用桩（canonical 指向定义模块，内容不重复渲染）：

.. py:class:: Provider
   :canonical: flowing.providers.provider.Provider

.. py:class:: ProviderResponse
   :canonical: flowing.providers.provider.ProviderResponse

.. py:class:: ProviderDelta
   :canonical: flowing.providers.provider.ProviderDelta

.. py:class:: Usage
   :canonical: flowing.providers.provider.Usage

{% endif %}
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
