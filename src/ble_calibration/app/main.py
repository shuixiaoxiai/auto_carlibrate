"""Command-line application shell used before the Qt UI is introduced."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from ..domain.enums import DIRECTION_LABELS, NODE_LABELS
from ..domain.schema import PROJECT_SCHEMA_VERSION
from ..mock.generator import main as generate_mock_main
from ..version import __version__


def _installed_version(package: str) -> Optional[str]:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def application_info() -> Dict[str, Any]:
    return {
        "name": "BLE Calibration",
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "project_schema": PROJECT_SCHEMA_VERSION,
        "directions": list(DIRECTION_LABELS),
        "nodes": list(NODE_LABELS),
        "dependencies": {
            "python-can": _installed_version("python-can"),
            "zlgcan": _installed_version("zlgcan"),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ble-calibration",
        description="汽车数字钥匙 BLE 标定工具应用入口",
    )
    subparsers = parser.add_subparsers(dest="command")

    info_parser = subparsers.add_parser("info", help="显示应用和运行环境信息")
    info_parser.add_argument("--json", action="store_true", help="以 JSON 输出")

    subparsers.add_parser(
        "generate-mock",
        add_help=False,
        help="生成确定性的八方向 Mock CAN 数据",
    )
    return parser


def _print_info(as_json: bool) -> None:
    info = application_info()
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return
    print(f"{info['name']} {info['version']}")
    print(f"Python: {info['python']}  Platform: {info['platform']}")
    print(f"Directions: {', '.join(info['directions'])}")
    print(f"Nodes: {', '.join(info['nodes'])}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "generate-mock":
        return generate_mock_main(arguments[1:])

    parser = build_parser()
    args = parser.parse_args(arguments or ["info"])
    if args.command == "info":
        _print_info(args.json)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
