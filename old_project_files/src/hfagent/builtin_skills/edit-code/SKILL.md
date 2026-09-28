---
name: Edit code
description: Use when changing an existing file without a full rewrite
---

1. Call `read_file` on the target path before editing.
2. Prefer `edit_file` for a small unique patch. If you are adding a feature or touching most of the file, call `write_file` with the complete new file instead.
3. If `edit_file` fails (old_text not found), re-read and `write_file` the full file — do not announce another attempt without calling the tool.
4. **package.json / dependencies:** to *add* a package, insert a new line — never replace an unrelated dependency with the one you want. Prefer `write_file` for the whole `package.json` when changing several deps. After edits, `run_command` `npm install` then the project tests.
5. Remote hosts: for anything beyond a trivial one-line change, rewrite the full file with `ssh_run` + heredoc (`cat > path <<'EOF'`). Do not chain `sed` fixes for syntax errors.
6. Verify with `read_file` / `run_command` / `ssh_run` (`python3 -m py_compile` when applicable), then summarize what changed. Do not stop at max steps mid-fix — leave a short status if you must stop.
