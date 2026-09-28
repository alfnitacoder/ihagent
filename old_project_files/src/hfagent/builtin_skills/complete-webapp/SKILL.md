---
name: Complete a web app
description: Use when asked to finish, complete, create, improve, or add features to a React/JS web app
---

1. **Discover first — never assume paths.** Call `list_dir` on `.`, then `src` if it exists. Read `package.json` and bundler config (`webpack.config.js`, `vite.config.js`, etc.). Only then `read_file` entrypoints that actually appear (or create ones the config requires).
2. Do **not** blindly open `src/index.js` / `src/App.js` / `src/main.jsx`. If a path is missing, call `list_dir` or create the file with `write_file` — never retry the same failed `read_file`.
3. **Design is part of “improve/create”.** Apply `frontend-design`: pick one aesthetic, rewrite CSS + markup with expressive fonts and atmosphere. Do not leave cream/terracotta/system-font boilerplate.
4. Implement features with `write_file` (full file) or `edit_file`. Do not stop at "I'll edit…".
5. Entry must render the real component; wire imports in the real entrypoint.
6. Keep state in the component (and `localStorage` when asked). Re-read files you changed.
7. **Always run tests before claiming done.** Prefer the project's script (`npm test`, etc.). If none exist, add a small focused test / smoke script and run it.
8. If a test fails: diagnose from real `run_command` output, fix, re-run until green.
9. **Dev server:** curl the port first; reuse if up; start at most once with `background=true`.
10. **Smoke-test with curl** for status + expected HTML markers; also confirm stylesheet/fonts are linked.
11. Never say done while tests fail, design is still generic AI defaults, or a missing path loop continues.
