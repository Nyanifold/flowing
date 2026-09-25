This example is an offline demo with no persisted final form.

Run command (see the info box at the top of Chapter 2-2 of the tutorial):
    uv run python demo_messages.py

demo_messages.py constructs Message objects offline and prints their structure
(no model calls, no Runtime created); running it produces no .flowing state
directory.

Verified by an actual run (2026-09-21): exit=0, the output is byte-for-byte
identical to the recorded transcript demo_output.txt, and the project directory
holds no state artifacts.
