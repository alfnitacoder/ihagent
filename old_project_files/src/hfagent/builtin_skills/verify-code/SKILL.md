---
name: Verify code
description: Use after writing or editing code to prove it works
---

1. Run the smallest meaningful check: unit test, `python -m py_compile`, linter, or a one-shot script.
2. Use `run_command` locally or `ssh_run` on a named remote host.
3. If the check fails, diagnose from the real tool output, fix with `edit_file` / `write_file` / `ssh_run`, and re-run the check. Keep looping until it passes or you hit a hard environmental blocker.
4. For web apps specifically: always run the test suite (or a smoke test you add) after creating/changing the app; fix failures before finishing.
5. Never claim tests passed unless a tool observation shows it.
