# Appendix A · Environment Setup

This appendix describes the Python environment, model-service configuration, and credentials needed for the Flowing conversation examples. Each chapter contains its complete application code, input, and representative behavior inline.

## Requirements

- Python 3.13 or later.
- uv to create the virtual environment, install dependencies, and run the CLI.
- An available distribution of the `flowing-agent` package.
- Conversation examples require model-service credentials; code-only demonstrations that do not call a model do not.

The project manifest below can be used directly in a newly created practice directory. It declares the package name and does not depend on a machine-specific repository location.

**`pyproject.toml`**

```toml
[project]
name = "flowing-tutorial-example"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["flowing-agent>=0.1.0"]

[tool.uv]
package = false
```

Run these commands in that directory:

```console
uv sync
```

If the configured Python package index does not yet provide `flowing-agent`, install the package through the distribution channel you use.

## Model-Service Configuration

The examples use the DeepSeek API. The provider, model name, and tag mapping are three separate configuration layers; the API credential is read only from an environment variable.

**`providers.yaml`**

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

**`models.yaml`**

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

**`model-tags.yaml`**

```yaml
tags:
  default: deepseek-flash
```

Set your credential in the terminal used to run a conversation example:

```console
export DEEPSEEK_API_KEY=sk-your-key-here
```

Replace `sk-your-key-here` with a credential issued by the provider. Never copy a real credential into documentation, code, a configuration file, or command history. The environment variable applies only to the current shell; set it again in a new terminal.

## Running an Inline Example

Each chapter provides relative filenames, complete file contents, the input to provide, a run command, and representative output. Create the listed files together in one practice directory, then run that chapter's command from that directory. Do not change into another directory or retrieve an external input or output file. Natural-language model responses may vary with the model version, sampling, and server state; tool-call arguments, fixed script output, and the chapter's stated behavior are the checks to use.

You can type the input at the interactive prompt. When a chapter provides a one-shot command, it uses a here-document whose complete input is part of the command rather than redirected from an external file.

## Credential and Execution Boundaries

1. Pass credentials only through environment variables; never write them to a file or paste them into example text.
2. Model-service calls may incur charges and are subject to the provider's rate and quota limits.
3. Do not submit real personal data, access tokens, or information that should not be sent to the model service.
4. Model replies in this documentation are representative records used to explain control flow and are not guaranteed to reproduce verbatim.
