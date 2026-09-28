"""``flowing.providers.bedrock`` —— AWS Bedrock 上的 Anthropic 模型 adapter。

.. rubric:: 功能介绍

本模块承载经 AWS Bedrock 调用 Anthropic Messages 格式的实现
:class:`BedrockProvider`，import 期经 :func:`register_provider` 进程级
注册（``name="bedrock"``）。家族叙述见
:mod:`flowing.providers.anthropic_messages`。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.provider import ProviderConfigField, register_provider


@register_provider
class BedrockProvider(AnthropicMessagesProvider):
    """AWS Bedrock 上的 Anthropic 模型 adapter（``name="bedrock"``）。

    .. rubric:: 功能介绍

    经 AWS Bedrock 调用 Anthropic Messages 格式的实现；消息格式复用
    基类，差异集中在凭证来源。随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"bedrock"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        bedrock:
          adapter: bedrock
          base_url: https://bedrock.example.com   # 本 adapter 无官方默认端点，必填
          aws_session_token: "{{env.AWS_SESSION_TOKEN}}"   # adapter 自读字段

    .. rubric:: 行为要点

    - 本 adapter 无官方默认端点：条目必须提供 ``base_url``。
    - 凭证优先级：config 显式 ``aws_session_token`` > 基类 ``api_key``。
    - 凭证同样遵守“不进消息 / ``_provided`` / 落盘”的安全边界。
    - 当前传输层复用基类的普通 HTTP 形态（未实现 AWS SigV4 签名）。

    .. seealso::

        :meth:`flowing.providers.Provider.get_credential` 凭证覆写点。
        :class:`AnthropicMessagesProvider` 格式实现来源。
    """

    name: ClassVar[str] = "bedrock"
    config_fields: ClassVar[tuple[ProviderConfigField, ...]] = (
        ProviderConfigField(name="base_url", prompt="API 端点"),
        ProviderConfigField(
            name="aws_session_token",
            prompt="AWS Session Token",
            default="",
            sensitive=True,
            persist_default=False,
        ),
    )
    """Bedrock 端点必填，并可额外提供 AWS Session Token。"""

    def get_credential(self) -> str | None:
        """凭证覆写点：config 显式 ``aws_session_token`` 优先，否则回退基类 ``api_key``。

        .. rubric:: 行为要点

        - 优先级：``aws_session_token`` > 基类 ``api_key``。
        - 当前不实现完整 AWS credential chain（环境变量 / 实例元数据）
          与 SigV4 签名传输——``_post`` 复用基类普通 HTTP 形态。
        - 安全边界：返回值禁止写入消息、``_provided``、日志与任何落盘
          文件。
        """
        return self.config.get("aws_session_token") or super().get_credential()
