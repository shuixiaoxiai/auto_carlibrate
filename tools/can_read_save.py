"""独立运行的 CAN RSSI 实时读取脚本（不依赖 zcanpro）。

通过 python-can + zlgcan 包直接读取 ZLG USBCANFD-200U 设备，复用
auto_calibrate.py 的 CAN 信号字节解码逻辑。

CAN 信号解码（与 auto_calibrate.py:827 一致）：
  - 0x629 (IKM_MasterSt, 64B):  master = data[4] - 256
  - 0x62A (IKM_NodeABSt, 64B):  front = data[4] - 256, rear = data[28] - 256
  - 0x62B (IKM_NodeCDSt, 64B):  left  = data[4] - 256, right = data[28] - 256
  - 0x55A (BLE_Req_0x55A, 32B): lock_req = (data[3] >> 4) & 0x0F  (1=Unlock, 2=Lock)

用法：
  python scripts\auto_calibrate_standalone.py [选项] [--log BASE]
Ctrl+C 退出。

录制（可选）：
  --log BASE   录制所有帧到 BLF，BASE 为文件名基础（不含序号后缀）。
               实际生成: {BASE}_YYYYMMDD.blf, {BASE}_YYYYMMDD_2.blf, ...
               支持: 传目录自动创建；传 xxx.blf 自动剥离后缀。
  --max-frames N  单文件最大帧数，达到后自动换文件（默认 1000000，与 ZCANPRO 一致）。
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path

import can
from zlgcan.zlgcan import ZCANDeviceType

try:
    from .can_protocol import (
        CANID_LOCKREQ,
        CANID_MASTER,
        CANID_NODEAB,
        CANID_NODECD,
        decode_frame,
    )
except ImportError:
    from can_protocol import (
        CANID_LOCKREQ,
        CANID_MASTER,
        CANID_NODEAB,
        CANID_NODECD,
        decode_frame,
    )

# =====================================================================
# 配置常量
# =====================================================================

DEFAULT_LIBPATH = r"D:\code\auto_calibrate\library"
DEFAULT_BITRATE = 500_000
DEFAULT_DBITRATE = 2_000_000

STAT_INTERVAL_S = 5.0       # 统计打印间隔
DEFAULT_MAX_FRAMES = 1_000_000  # 单文件最大帧数（与 ZCANPRO 一致）


# =====================================================================
# 工具函数
# =====================================================================

def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


# =====================================================================
# BLF 分卷写入器（命名风格与 ZCANPRO 一致）
# =====================================================================

class BlfRotatingWriter:
    """按帧数自动分卷的 BLF 写入器。

    文件命名：
      第 1 个: {base}_{YYYYMMDD}.blf
      第 2 个: {base}_{YYYYMMDD}_2.blf
      第 3 个: {base}_{YYYYMMDD}_3.blf
      ...

    用户传入的 base 若以 .blf 结尾会自动剥离后缀；
    所在目录不存在时自动创建。
    """

    def __init__(self, base: str, channel: int, max_frames: int = DEFAULT_MAX_FRAMES):
        # 剥离 .blf 后缀
        if base.lower().endswith(".blf"):
            base = base[:-4]
        # 自动创建目录
        d = os.path.dirname(base)
        if d:
            os.makedirs(d, exist_ok=True)
        self.base = base
        self.date_str = datetime.now().strftime("%Y%m%d")
        self.channel = channel
        self.max_frames = max_frames
        self.seq = 0          # 0 → 无后缀；1 → _2；2 → _3 ...
        self.frame_count = 0
        self.writer: can.BLFWriter | None = None
        self._roll(first=True)

    def _path(self) -> str:
        if self.seq == 0:
            return f"{self.base}_{self.date_str}.blf"
        return f"{self.base}_{self.date_str}_{self.seq + 1}.blf"

    def _roll(self, first: bool = False) -> None:
        if self.writer is not None:
            self.writer.stop()
        self.writer = can.BLFWriter(self._path(), channel=self.channel)
        prev = "" if first else f"（上一文件写入 {self.frame_count} 帧）"
        log(f"录制文件: {self._path()}  {prev}".rstrip())
        self.frame_count = 0

    def write(self, msg: can.Message) -> None:
        receive = getattr(self.writer, "on_message_received", None)
        if callable(receive):
            receive(msg)
        else:
            self.writer.write(msg)
        self.frame_count += 1
        if self.frame_count >= self.max_frames:
            self.seq += 1
            self._roll()

    def stop(self) -> None:
        if self.writer is not None:
            self.writer.stop()
            self.writer = None
            log(f"录制结束: {self._path()}  共 {self.frame_count} 帧")


# =====================================================================
# 主循环
# =====================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="独立读取 ZLG USBCANFD-200U 的 CAN RSSI")
    ap.add_argument("--libpath", default=DEFAULT_LIBPATH, help=f"ZLG library 根目录（默认 {DEFAULT_LIBPATH}）")
    ap.add_argument("--device-index", type=int, default=0, help="设备索引（默认 0）")
    ap.add_argument("--channel", type=int, default=0, help="通道号（默认 0）")
    ap.add_argument("--bitrate", type=int, default=DEFAULT_BITRATE, help=f"仲裁域波特率（默认 {DEFAULT_BITRATE}）")
    ap.add_argument("--dbitrate", type=int, default=DEFAULT_DBITRATE, help=f"数据域波特率（默认 {DEFAULT_DBITRATE}）")
    ap.add_argument("--no-resistance", action="store_true", help="禁用终端电阻（默认启用）")
    ap.add_argument("--log", default=None,
                    help="录制 BLF 文件基础名（不含序号后缀）；不传则不录制")
    ap.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES,
                    help=f"单文件最大帧数，达到后自动换文件（默认 {DEFAULT_MAX_FRAMES}）")
    args = ap.parse_args()

    resistance = 0 if args.no_resistance else 1
    log(f"打开设备 USBCANFD-200U dev={args.device_index} ch={args.channel} "
        f"bitrate={args.bitrate} dbitrate={args.dbitrate} resistance={resistance}")

    try:
        bus = can.Bus(
            interface="zlgcan",
            libpath=args.libpath,
            device_type=ZCANDeviceType.ZCAN_USBCANFD_200U,
            device_index=args.device_index,
            configs=[{"bitrate": args.bitrate, "dbitrate": args.dbitrate, "resistance": resistance}],
        )
    except Exception as e:
        log(f"打开设备失败: {type(e).__name__}: {e}")
        log("请检查: 设备已插入 / ZCANPRO 已关闭 / libpath 正确 / VC++ 运行库已装")
        return 1

    log(f"设备已打开，监听通道 {args.channel}，Ctrl+C 退出")

    recorder: BlfRotatingWriter | None = None
    if args.log:
        recorder = BlfRotatingWriter(args.log, channel=args.channel, max_frames=args.max_frames)

    frame_count = 0
    rssi_count = 0
    start = time.monotonic()
    last_stat_t = start

    try:
        while True:
            msg = bus.recv(timeout=1.0)
            if msg is None or msg.channel != args.channel:
                continue

            frame_count += 1
            if recorder:
                recorder.write(msg)
            can_id = msg.arbitration_id
            data = list(msg.data)
            decoded = decode_frame(can_id, data)

            if decoded:
                rssi_count += 1
                parts = "  ".join(f"{k}={v}" for k, v in decoded.items())
                log(f"0x{can_id:03X}  dlc={len(data)}  ts={msg.timestamp:.6f}  {parts}")

            now = time.monotonic()
            if now - last_stat_t >= STAT_INTERVAL_S:
                last_stat_t = now
                rate = frame_count / max(now - start, 1e-3)
                log(f"[stat] 总帧={frame_count}  RSSI帧={rssi_count}  帧率={rate:.1f}/s")

    except KeyboardInterrupt:
        log("收到 Ctrl+C，正在退出...")
    except Exception as e:
        log(f"运行异常: {type(e).__name__}: {e}")
        return 1
    finally:
        if recorder:
            try:
                recorder.stop()
            except Exception as e:
                log(f"录制关闭异常: {e}")
        try:
            bus.shutdown()
            log("设备已关闭")
        except Exception as e:
            log(f"关闭异常: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
