"""Helper entry: ``python -m hfagent.iheye``.

ihagent spawns this. Running it by hand with no parent pid just exits.
"""

from __future__ import annotations

import argparse
import sys

from .host import HostInfo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iheye", description="IHAgent floating eye overlay")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--position", required=True)
    parser.add_argument("--host-kind", default="unknown")
    parser.add_argument("--host-name", default="")
    parser.add_argument("--host-bundle", default="")
    parser.add_argument("--host-pid", type=int, default=0)
    parser.add_argument("--ancestors", default="")
    parser.add_argument("--tty", default="")
    parser.add_argument("--track", default="1", help="1 to follow the pointer")
    parser.add_argument("--summary", default="1", help="1 to show the completion card")
    return parser


def host_from_args(args: argparse.Namespace) -> HostInfo:
    ancestors: list[int] = []
    for part in str(args.ancestors or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ancestors.append(int(part))
        except ValueError:
            continue
    pid = int(args.host_pid or 0)
    return HostInfo(
        kind=args.host_kind or "unknown",
        app_name=args.host_name or "",
        bundle_id=args.host_bundle or "",
        pid=pid or None,
        ancestor_pids=tuple(ancestors),
        tty=args.tty or "",
        source="args",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.platform != "darwin":
        print("iheye overlay is macOS-only", file=sys.stderr)
        return 0
    from .overlay_app import run_overlay

    return int(run_overlay(args, host_from_args(args)) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
