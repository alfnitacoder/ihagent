"""run_tests: detect a framework, or smoke-check and offer a scaffold."""

from __future__ import annotations

from .base import Tool


class RunTests(Tool):
    name = "run_tests"
    description = (
        "Detect the project's test framework (pytest, jest, mocha, vitest, "
        "npm test, go test) and run it. If none is found, run a smoke check: "
        "python -m py_compile, npm run build when a build script exists, and "
        "curl a local server that is already listening. scaffold=true writes "
        "a minimal smoke test when one is not already there. Requires approval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root. Defaults to the current directory.",
            },
            "scaffold": {
                "type": "boolean",
                "description": "Write a minimal smoke test if the project has none.",
            },
        },
    }
    needs_approval = True

    def preview(self, path: str = ".", scaffold: bool = False) -> str:
        action = "write a smoke-test scaffold and run checks" if scaffold else "detect and run tests"
        return f"{action} in {path or '.'}"

    def run(self, path: str = ".", scaffold: bool = False) -> str:
        from ..assist.testing import run_project_tests

        return run_project_tests(path or ".", scaffold=bool(scaffold))
