"""Entry point of the parameters and setup() demo sub-project: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # provide before mount: values that affect assembly are provided before mounting
    # (1-5 covers the chained semantics)
    runtime.provide("timezone", "Asia/Shanghai")
    # CLI --key value passes through launch verbatim to main(**kwargs);
    # main then decides which parameters go to mount (-> creation pipeline -> setup(**args))
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
