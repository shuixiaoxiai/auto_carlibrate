"""Command-line application shell used before the Qt UI is introduced."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from ..analysis import EightDirectionRecomputeService
from ..can.mock_source import MockCanSource
from ..can.recording import JsonlFrameRecorder
from ..capture.worker import CaptureWorker
from ..cloud import CloudCodecError, CloudParameters, decode_cloud
from ..domain import CalibrationProject
from ..domain.enums import DIRECTION_LABELS, NODE_LABELS
from ..domain.schema import PROJECT_SCHEMA_VERSION
from ..mock.generator import main as generate_mock_main
from ..replay import ReplayService
from ..session.demo import replay_manifest_session
from ..storage import ProjectRepository, StoredProject
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
    subparsers.add_parser(
        "capture-mock",
        add_help=False,
        help="按指定速度回放并采集 Mock CAN JSONL",
    )
    subparsers.add_parser(
        "session-demo",
        add_help=False,
        help="使用 manifest 无界面执行八方向记录会话",
    )
    subparsers.add_parser(
        "cloud-decode",
        add_help=False,
        help="解码云推 HEX",
    )
    subparsers.add_parser(
        "cloud-encode",
        add_help=False,
        help="修改已存在参数并编码云推 HEX",
    )
    subparsers.add_parser(
        "project-demo",
        add_help=False,
        help="创建、关闭、重开并离线重算一个 Mock 项目",
    )
    subparsers.add_parser(
        "gui",
        add_help=False,
        help="启动八方向桌面界面",
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


def _capture_mock_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="ble-calibration capture-mock",
        description="无界面回放和采集 Mock CAN 数据",
    )
    parser.add_argument("--input", type=Path, required=True, help="输入 CAN JSONL")
    parser.add_argument(
        "--speed",
        type=float,
        default=0.0,
        help="回放倍速；1 为原速，10 为十倍速，0 为最快",
    )
    parser.add_argument("--loop", action="store_true", help="循环回放")
    parser.add_argument("--max-frames", type=int, default=None, help="达到帧数后停止")
    parser.add_argument("--output", type=Path, default=None, help="可选采集输出 JSONL")
    args = parser.parse_args(argv)

    if args.loop and args.max_frames is None:
        parser.error("--loop requires --max-frames")
    if args.output is not None and args.output.resolve() == args.input.resolve():
        parser.error("--output must differ from --input")

    source = MockCanSource(args.input, speed=args.speed, loop=args.loop)
    recorder = None if args.output is None else JsonlFrameRecorder(args.output)
    worker = CaptureWorker(source, recorder=recorder, max_frames=args.max_frames)
    worker.start()
    worker.join()
    if worker.last_error is not None:
        print(f"采集失败: {worker.last_error}", file=sys.stderr)
        return 1
    print(f"采集完成: 帧数={worker.frame_count}")
    return 0


def _session_demo_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="ble-calibration session-demo",
        description="将 Mock CAN 与 manifest 跑过真实方向会话状态机",
    )
    parser.add_argument("--input", type=Path, required=True, help="输入 CAN JSONL")
    parser.add_argument("--manifest", type=Path, required=True, help="方向 manifest")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args(argv)
    try:
        _, summary = replay_manifest_session(args.input, args.manifest)
    except (OSError, ValueError, KeyError) as error:
        print(f"会话失败: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"会话完成: 方向={summary['direction_count']} "
            f"完整={summary['complete_count']} "
            f"不完整={summary['incomplete_count']}"
        )
    return 0 if summary["incomplete_count"] == 0 else 2


def _cloud_decode_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="ble-calibration cloud-decode",
        description="解码数字钥匙云推参数",
    )
    parser.add_argument("hex", help="云推 HEX；可以包含空格和换行")
    args = parser.parse_args(argv)
    try:
        document = decode_cloud(args.hex)
    except CloudCodecError as error:
        print(f"解码失败: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            document.parameters.to_legacy_dict(),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cloud_encode_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="ble-calibration cloud-encode",
        description="修改源数据中已存在的字段并编码回云推 HEX",
    )
    parser.add_argument("hex", help="原始云推 HEX")
    parser.add_argument("--unlock", type=int, nargs=5, help="5 节点解锁阈值")
    parser.add_argument("--lock", type=int, nargs=5, help="5 节点闭锁阈值")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="STRATEGY.FIELD=VALUE",
        help="修改策略半字节，例如 quickLock.weakFront=2",
    )
    args = parser.parse_args(argv)

    strategy_updates: Dict[str, Dict[str, int]] = {}
    try:
        for expression in args.set:
            path, value_text = expression.split("=", 1)
            strategy, field = path.split(".", 1)
            strategy_updates.setdefault(strategy, {})[field] = int(value_text)
        document = decode_cloud(args.hex).with_updates(
            unlock_thresholds=args.unlock,
            lock_thresholds=args.lock,
            strategy_updates=strategy_updates,
        )
    except (CloudCodecError, ValueError) as error:
        print(f"编码失败: {error}", file=sys.stderr)
        return 1
    print(document.encode_hex())
    return 0


def _project_demo_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="ble-calibration project-demo",
        description="验证项目保存、重开、离线回放和八方向重算闭环",
    )
    parser.add_argument("--input", type=Path, required=True, help="输入 CAN JSONL")
    parser.add_argument("--manifest", type=Path, required=True, help="方向 manifest")
    parser.add_argument("--database", type=Path, required=True, help="SQLite 项目库")
    parser.add_argument("--name", default="Mock 八方向项目", help="项目名称")
    args = parser.parse_args(argv)

    try:
        controller, _ = replay_manifest_session(args.input, args.manifest)
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        thresholds = manifest["reference_thresholds"]
        parameters = CloudParameters(
            unlock_thresholds=tuple(int(value) for value in thresholds["unlock"]),
            lock_thresholds=tuple(int(value) for value in thresholds["lock"]),
        )
        project = CalibrationProject(
            name=args.name,
            directions=tuple(controller.records),
        )
        stored = StoredProject(
            project=project,
            capture_path=str(args.input.resolve()),
            capture_format="jsonl",
        )

        with ProjectRepository(args.database) as repository:
            repository.save_project(stored)

        with ProjectRepository(args.database) as repository:
            reopened = repository.load_project(project.project_id)
            datasets = ReplayService().rebuild_project(reopened)
            result = EightDirectionRecomputeService().recompute(
                parameters,
                datasets,
                use_actual_action_times=True,
            )
            repository.save_analysis(project.project_id, result, cloud_hex=None)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"项目闭环失败: {error}", file=sys.stderr)
        return 1

    print(
        f"项目闭环完成: ID={project.project_id} "
        f"方向={len(datasets)} 重算={result.elapsed_ms:.2f}ms "
        f"闭锁优秀率={result.lock_summary.excellent_rate_percent:.1f}% "
        f"解锁优秀率={result.unlock_summary.excellent_rate_percent:.1f}%"
    )
    return 0


def _gui_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="ble-calibration gui",
        description="启动 Windows 风格的八方向 BLE 标定桌面界面",
    )
    parser.add_argument("--input", type=Path, help="可选 Mock CAN JSONL")
    parser.add_argument("--manifest", type=Path, help="与 JSONL 对应的 manifest")
    parser.add_argument("--seed", type=int, default=20260730, help="内置 Mock 随机种子")
    parser.add_argument(
        "--manual-mock",
        action="store_true",
        help="启动空项目，按测试人员操作手动记录各方向",
    )
    parser.add_argument(
        "--replay-speed",
        type=float,
        default=10.0,
        help="手动 Mock 模式的数据播放倍速",
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="项目 SQLite；默认使用用户数据目录",
    )
    parser.add_argument("--project-id", help="直接打开指定项目 ID")
    parser.add_argument(
        "--project-name",
        default="新建八方向标定",
        help="手动 Mock 新项目名称",
    )
    parser.add_argument("--screenshot", type=Path, help="保存启动界面截图后退出")
    parser.add_argument(
        "--parameters-hidden",
        action="store_true",
        help="启动时隐藏 What-if 参数，用于查看宽幅图表",
    )
    parser.add_argument(
        "--quit-after-ms",
        type=int,
        default=None,
        help="自动退出时间，仅用于自动化验收",
    )
    parser.add_argument(
        "--automation-report",
        type=Path,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if (args.input is None) != (args.manifest is None):
        parser.error("--input and --manifest must be provided together")
    if args.manual_mock and args.input is not None:
        parser.error("--manual-mock cannot be combined with --input")
    if args.project_id and args.input is not None:
        parser.error("--project-id cannot be combined with --input")
    if args.replay_speed < 0:
        parser.error("--replay-speed cannot be negative")
    try:
        from ..ui.application import run_ui

        return run_ui(
            frame_path=args.input,
            manifest_path=args.manifest,
            seed=args.seed,
            screenshot_path=args.screenshot,
            quit_after_ms=args.quit_after_ms,
            parameters_hidden=args.parameters_hidden,
            manual_mock=args.manual_mock,
            replay_speed=args.replay_speed,
            database_path=args.database,
            project_id=args.project_id,
            project_name=args.project_name,
            automation_report_path=args.automation_report,
        )
    except (ImportError, OSError, ValueError, KeyError) as error:
        print(f"界面启动失败: {error}", file=sys.stderr)
        return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "generate-mock":
        return generate_mock_main(arguments[1:])
    if arguments and arguments[0] == "capture-mock":
        return _capture_mock_main(arguments[1:])
    if arguments and arguments[0] == "session-demo":
        return _session_demo_main(arguments[1:])
    if arguments and arguments[0] == "cloud-decode":
        return _cloud_decode_main(arguments[1:])
    if arguments and arguments[0] == "cloud-encode":
        return _cloud_encode_main(arguments[1:])
    if arguments and arguments[0] == "project-demo":
        return _project_demo_main(arguments[1:])
    if arguments and arguments[0] == "gui":
        return _gui_main(arguments[1:])

    parser = build_parser()
    args = parser.parse_args(arguments or ["info"])
    if args.command == "info":
        _print_info(args.json)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
