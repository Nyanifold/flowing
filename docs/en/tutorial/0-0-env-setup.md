# 0-0 · Environment Setup

## Prerequisites

This chapter has no prerequisites because it is the first chapter of the tutorial.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| flowing sub-project | A flowing sub-project is an agent application unit with one directory, an entry point named `main.py`, and declarative agent definitions in `.fya` files; chapter 0-1 introduces the project structure. |
| `flowing` CLI | The `flowing` CLI is the command-line tool installed with the package, and this chapter uses its `--help` option to check the installation. |
| provider | A provider connects an application to a model service; this chapter covers obtaining its API key, while chapter 0-1 covers its configuration. |

## Goals

Install the Python runtime, the flowing package, and a model API key —
the prerequisites of 0-1. By the end of this chapter, your machine meets
three conditions: Python ≥ 3.13, the `flowing` command available, and
`DEEPSEEK_API_KEY` exported into the environment.

## Main text

### Install uv and the Python environment

flowing requires Python ≥ 3.13 and uses uv to manage the environment
(uv is a Python package and environment manager). Install uv (skip if
already installed):

```console
$ curl -LsSf https://astral.sh/uv/install.sh | sh
$ uv --version
uv 0.8.11
```

Create the environment and install the dependencies:

```console
$ mkdir my-flowing
$ cd my-flowing
$ uv venv --python 3.13
$ uv pip install flowing-agent
```

The `flowing-agent` package provides two things: the `flowing` Python
package (the programming interface) and the `flowing` command-line tool
(the CLI). This chapter only verifies the latter; the division of labor
between the two is covered in 0-1.

### Obtain a model API key

Take DeepSeek as the example: register an account on the DeepSeek open
platform and create an API key.

**Credentials discipline**: the key is always held in an environment
variable — never hard-coded, never written into any project file — the
`export` in this chapter only takes effect in the current shell; every
new shell needs it exported again (the 0-1 configuration injects it into
the framework via the `{{ env.X }}` template, which also never lands on
disk):

```console
$ export DEEPSEEK_API_KEY='YOUR_DEEPSEEK_API_KEY'
```

Replace `YOUR_DEEPSEEK_API_KEY` with your own credential; do not write the real value into documentation or project configuration.

### Self-check

The CLI is a closed subcommand set (`run` / `repl` / `cli` /
`repl-debug` / `serve` / `web` / `test` / `compile`; the full comparison
is in 6-1) with no top-level `--help` switch; each subcommand has a
working `--help`:

```console
$ uv run flowing repl --help
usage: flowing repl <path> [-f <main file>] [--key value ...]
```

Seeing the usage line means the environment is ready. (Bare `flowing` or
`flowing --help` prints the usage and exits with code 2 — the CLI
convention for a usage error; see 6-1.)

## Out of scope

- This chapter does not explain how to write the three model-connection files
  (`providers.yaml`, `models.yaml`, and `model-tags.yaml`); chapter 0-1 introduces them.
- This chapter only mentions assembly-time handling of the `{{ env.X }}` credential
  template; chapter 6-3 explains missing-variable warnings and related behavior.
- This chapter does not cover multiple providers or the file-search precedence for
  the three configuration files; chapter 6-3 covers those operational details.

## Main example

The complete command sequence from a bare machine to a ready environment
(version numbers and installation output may vary by environment):

```console
# 1. Install uv, unless it is already installed.
$ curl -LsSf https://astral.sh/uv/install.sh | sh
$ uv --version
uv 0.8.11

# 2. Create and enter a project directory, then create the Python environment.
$ mkdir my-flowing
$ cd my-flowing
$ uv venv --python 3.13
$ uv run python --version
Python 3.13.6

# 3. Install flowing.
$ uv pip install flowing-agent

# 4. Obtain an API key from the DeepSeek open platform and export it.
$ export DEEPSEEK_API_KEY='YOUR_DEEPSEEK_API_KEY'

# 5. Check that the CLI works.
$ uv run flowing repl --help
usage: flowing repl <path> [-f <main file>] [--key value ...]
```

Before running the export command, replace `YOUR_DEEPSEEK_API_KEY` with your own credential and keep it only in the environment variable.

This chapter does not create an Agent project. The next chapter gives the
complete project contents needed for its example.

## Summary

1. The environment requires Python ≥ 3.13, uv, and `uv pip install flowing-agent`.
2. Credentials stay in environment variables such as `DEEPSEEK_API_KEY` and are never written to disk.
3. Run `flowing repl --help` to check the CLI; the command has no top-level `--help` option.
