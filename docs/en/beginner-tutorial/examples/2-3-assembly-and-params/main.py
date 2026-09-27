"""Entry point of the parameter & setup() demo subproject: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # provide before mount: assembly-affecting values are provided before mounting
    # (chained semantics: look up along the parent chain)
    runtime.provide("timezone", "Asia/Shanghai")
    # CLI --key value passes through launch verbatim into main(**kwargs);
    # main then decides which parameters go to mount (→ creation pipeline → setup(**args))
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
