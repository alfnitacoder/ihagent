---
name: Web app test-and-fix
description: Use when creating, improving, or changing a web app — discover layout, run tests, curl the live server, fix failures
---

1. Start with `list_dir` on `.` and `src` (if present). Read `package.json` and bundler config. Never assume `src/index.js` exists.
2. If a `read_file` fails with FileNotFoundError, do not retry that path. List directories or `write_file` the missing entrypoint the config expects.
3. After code changes, `run_command` the tests. If none exist, add a minimal test for the new behavior, then run it.
4. On failure: read the error, fix with `edit_file` / `write_file`, re-run. Repeat until pass.
5. **Is the server up?** Prefer curl:
   `run_command` → `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/`
   (use the port from webpack/vite config). Codes like 200/304 mean reuse that URL.
6. Start a dev server at most once: `run_command` with `background=true` (`npm start` / `webpack serve`). Do not start again on 3001/3002/etc.
7. **Curl-test the page:**
   - `curl -sS -o /tmp/hfagent-page.html -w '%{http_code}' http://127.0.0.1:3000/`
   - Confirm status is 200 (or expected).
   - `grep` / `read_file` `/tmp/hfagent-page.html` for expected markers (title, heading, `#root`).
   - For APIs: `curl -sS http://127.0.0.1:PORT/api/...`
8. If curl fails or content is wrong: fix code, wait briefly, curl again. Do not open a second server.
9. Do not finish while unit tests or curl checks fail / were skipped.
