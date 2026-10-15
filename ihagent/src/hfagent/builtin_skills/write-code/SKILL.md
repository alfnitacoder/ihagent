---
name: Write code
description: Use when creating a new source file or rewriting a file from scratch
---

1. Call `list_dir` / `grep` / `read_file` first if the path or surrounding style is unclear.
2. Call `write_file` with the full intended content in the **same turn** — do not say "I'll write the file" in prose.
3. For React/web apps, rewrite the whole component file, then fix the entrypoint (`App.js` / `main`) so it imports and renders that component.
4. After writing a web app (or web feature): run the project's tests with `run_command`. If they fail, fix and re-run until they pass. If no tests exist, add a minimal smoke/unit test for what you built, then run it.
5. After writing other code, verify with `read_file` or `run_command` (syntax check / tests). Prefer a real check over claiming success.
6. Remote hosts (`on <host>`): rewrite the whole file with one `ssh_run` using a quoted heredoc, e.g.
   `cat > /root/file.py <<'EOF'` … `EOF`
   Then verify with `python3 -m py_compile /root/file.py` (or the language equivalent).
7. Never use `sed` / `echo | tee -a` to patch Python syntax errors — rewrite the full file.
8. Finish with a short factual summary of paths changed and how you verified (include test command + pass/fail).
