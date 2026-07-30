"""
数字钥匙云推标定参数解析脚本。

解析云推 TLV 标定数据（hex 字符串），输出蓝牙无感解闭锁的各策略参数。
所有 RSSI 阈值均为有符号 dbm（负值，越接近 0 信号越强）。

节点编号约定（下标含义，贯穿所有策略，对应 NodeType 枚举 rssi_manager.h:27）：
  0 = 主节点(MASTER)   1 = 前保从节点(SLAVE1)
  2 = 后保从节点(SLAVE2)   3 = 左侧从节点(SLAVE3)
  4 = 右侧从节点(SLAVE4)

返回 result（OrderedDict）各字段说明：

bleUnlockThred: list[int] | None
    基础蓝牙解锁门限（5 节点，负 dbm）。
    配置：每个节点设一个 RSSI 门限，表示该节点信号强到什么程度就算"进入解锁区"。
    触发：任意一个有效节点的 RSSI 大于其门限 -> 钥匙定位在解锁区；连续停留解锁区
    达稳定时间（基础数据 inUnlockStab，×0.1s）后真正执行解锁。
    配 0：该节点不参与解锁判定。
    配得越高（越接近 0）：解锁越严格，需要钥匙更靠近节点才解锁。

bleLockThred: list[int] | None
    基础蓝牙闭锁门限（5 节点，负 dbm）。
    配置：每个节点设一个 RSSI 门限，用于划分缓冲区与闭锁区。
    触发：若某节点 RSSI 大于其闭锁门限但无节点达到解锁门限 -> 缓冲区；
    所有节点 RSSI 均不大于各自闭锁门限 -> 闭锁区。连续在闭锁区达 inLockStab 且
    连续离开解锁区达 outUnlockStab（基础数据，×0.1s）后执行闭锁。
    配 0：该节点不参与闭锁判定。
    配得越高：进入闭锁区越早，闭锁越积极。

mstUnlock: list[int] | None
    主节点单独解锁门限（5 节点，负 dbm）。额外策略 Parent01/子tag=1。
    作用：让钥匙靠近车但没到任何从节点解锁门限时，仅凭主节点信号也能解锁。
    配置：下标 i 对应"当前信号最强的节点是 i"这一场景，该值是该场景下主节点 RSSI
    需超过的门限：
      mstUnlock[0]：主节点自己最强时（钥匙在车中心附近），主节点 RSSI 需 > 此值
      mstUnlock[1]：前保从节点最强时（钥匙在前保附近），主节点 RSSI 需 > 此值
      mstUnlock[2]：后保从节点最强时（钥匙在后保附近），主节点 RSSI 需 > 此值
      mstUnlock[3]：左侧从节点最强时，主节点 RSSI 需 > 此值
      mstUnlock[4]：右侧从节点最强时，主节点 RSSI 需 > 此值
    触发：非闭锁区时，取信号最强节点 maxIdx；若主节点 RSSI > mstUnlock[maxIdx]
    -> 解锁（不要求任何从节点达到解锁门限）。主节点单独解锁受 500ms 时限约束，
    超时则该次连接不再凭主节点解锁。
    配 0：该场景下主节点不参与单独解锁。
    配得越高：主节点单独解锁越严格（需主节点信号更强）。

quickLock: dict | None
    快速闭锁策略参数。额外策略 Parent01/子tag=3。仅在缓冲区生效，钥匙快速移动时
    提前判定闭锁。每节点配一对偏移（单位 db）：
      weakFront/weakRear/weakFl/weakFr : 4 个从节点的弱信号容限
        含义：该节点 RSSI 需 ≤ 闭锁门限 + weakOffset 才算"信号弱到可闭锁"
      strongMst/strongFront/strongRear/strongFl/strongFr : 各节点对其余节点的
        强信号下限。含义：其余节点 RSSI 需 ≤ 各自闭锁门限 - strongOffset
        （即其余节点必须足够弱）
    触发：缓冲区中某个高于闭锁门限的节点，若其信号在 weakOffset 容限内，且其余
    所有节点信号均低于各自闭锁门限减 strongOffset -> 立即判为闭锁区。
    主节点 weakOffset 固定为 0（不可配）。
    配 0：weakOffset 或 strongOffset 为 0 时，该节点不参与快速闭锁判定。
    配得越高（weakOffset）：该节点越容易被视为"弱信号"，快速闭锁越积极；
    配得越高（strongOffset）：对其余节点信号要求越严，快速闭锁越保守。
      reserve : 保留

quickUnlock: dict | None
    快速解锁策略参数。额外策略 Parent01/子tag=4。仅 4 从节点场景，用于钥匙沿 45°
    斜向绕车移动时的快速解锁。字段：
      unlockTime : 区域变化检测时间窗。生效值 = unlockTime × 200ms
      frontToFr / frontToFl / rearToFl / rearToFr : 右前/左前/左后/右后 4 个角象限
        的解锁门限补偿值（单位 db）
    触发：检测信号最强的两个相邻从节点对是否发生切换（钥匙沿 45° 方向移动）；
    在切换后 unlockTime 时间窗内，若对应象限的角点从节点 RSSI ≥ 解锁门限 - 补偿值
    -> 解锁。例如 frontToFr=5，表示右前象限角点节点只需达到"解锁门限 - 5db"即可解锁。
    配 0：unlockTime=0 整个策略禁用；某象限补偿值=0 表示该象限无补偿（需达满解锁门限）。
    配得越高（unlockTime）：检测窗口越长，越容易捕获到斜向移动；
    配得越高（补偿值）：该象限越容易解锁（角点信号要求越低）。
      reserve : 保留

mstThanSlave: dict | None
    主节点强于从节点识别策略参数。额外策略 Parent01/子tag=5。字段：
      diff    : 主从信号差值。生效值 = diff × 2 dbm
      reserve : 保留
    触发：主节点须为所有节点中最强，且所有节点信号有效；当主节点 RSSI ≥ 最强从节点
    RSSI + diff×2 -> 解锁。例如 diff=3，主节点比最强从节点强 6db 即解锁。
    配 0：策略禁用。
    配得越高：要求主节点比从节点强得更多才解锁，策略越保守。

bevelAngle: dict | None
    斜角度信号补偿解锁参数。额外策略 Parent02/子tag=3。仅 4 从节点场景，用于车辆
    斜向进入时的解锁补偿。字段（单位 db，命名 offset<节点><方向>）：
      offsetRFR : 右前节点-右侧方向补偿    offsetRFF : 右前节点-前方向补偿
      offsetLFL : 左前节点-左侧方向补偿    offsetLFF : 左前节点-前方向补偿
      offsetLBL : 左后节点-左侧方向补偿    offsetLBB : 左后节点-后方向补偿
      offsetRBR : 右后节点-右侧方向补偿    offsetRBB : 右后节点-后方向补偿
    触发：对 4 个角象限（右前/左前/左后/右后）各取一个侧边节点（左 SLAVE3 或右 SLAVE4）
    和一个前/后节点（前 SLAVE1 或后 SLAVE2），若这两个节点 RSSI 各自加上其补偿值后
    均超过该节点解锁门限 -> 解锁。例如 offsetRFR=4、offsetRFF=4：右前象限，
    若 SLAVE4.rssi+4 > 解锁门限[4] 且 SLAVE1.rssi+4 > 解锁门限[1] -> 解锁。
    生效前提：每个节点 offset + 闭锁门限 < 解锁门限，否则整个策略不启用。
    配 0：该方向无补偿（需达满解锁门限）。
    配得越高：该方向越容易解锁（角点信号要求越低）。

各字段在云推数据中缺失时为 None。
"""

import sys
import json
from collections import OrderedDict

DK_NODE_NUM = 5
BASE_LEN = 35  # sizeof(BaseCloudDataVer01)


def signed(b):
    return b - 256 if b > 127 else b


def parse_nibbles(val, names):
    out = OrderedDict()
    ni = 0
    for b in val:
        if ni >= len(names):
            break
        out[names[ni]] = (b >> 4) & 0x0F
        ni += 1
        if ni >= len(names):
            break
        out[names[ni]] = b & 0x0F
        ni += 1
    return out


QUICK_LOCK_FIELDS = ["weakFront", "weakRear", "weakFl", "weakFr",
                     "strongMst", "strongFront", "strongRear", "strongFl", "strongFr", "reserve"]
QUICK_UNLOCK_FIELDS = ["unlockTime", "frontToFr", "frontToFl",
                       "rearToFl", "rearToFr", "reserve"]
MST_THAN_SLAVE_FIELDS = ["diff", "reserve"]
BEVEL_ANGLE_FIELDS = ["offsetRFR", "offsetRFF", "offsetLFL", "offsetLFF",
                      "offsetLBL", "offsetLBB", "offsetRBR", "offsetRBB"]


def parse_cloud(hex_str):
    data = bytes.fromhex(hex_str.replace(' ', '').replace('\n', ''))

    result = OrderedDict()
    result["bleUnlockThred"] = [signed(b) for b in data[1:6]]
    result["bleLockThred"] = [signed(b) for b in data[6:11]]
    result["mstUnlock"] = None
    result["quickLock"] = None
    result["quickUnlock"] = None
    result["mstThanSlave"] = None
    result["bevelAngle"] = None

    idx = BASE_LEN + 1  # 跳过 extraVer

    block_end = idx
    while idx + 1 < len(data):
        hl_tag = data[idx]
        hl_len = data[idx + 1]
        block_end = idx + 2 + hl_len
        if hl_tag != 0:  # 0 = 高配
            idx = block_end
            continue
        idx += 2
        break

    while idx < block_end:
        head = data[idx]
        p_tag = head & 0x07
        p_len = (head >> 3) & 0x1F
        p_end = idx + 1 + p_len
        idx += 1
        if p_tag == 0:  # Parent01
            while idx < p_end:
                shead = data[idx]
                s_tag = shead & 0x07
                s_len = (shead >> 3) & 0x1F
                s_val = data[idx + 1: idx + 1 + s_len]
                if s_tag == 1:  # CLOUD_SUB_TAG_MST_UNLOCK
                    result["mstUnlock"] = [signed(b) for b in s_val[:DK_NODE_NUM]]
                elif s_tag == 3:  # CLOUD_SUB_TAG_QUICK_LOCK
                    result["quickLock"] = parse_nibbles(s_val, QUICK_LOCK_FIELDS)
                elif s_tag == 4:  # CLOUD_SUB_TAG_QUICK_UNLOCK
                    result["quickUnlock"] = parse_nibbles(s_val, QUICK_UNLOCK_FIELDS)
                elif s_tag == 5:  # CLOUD_SUB_TAG_MST_THAN_SLAVE
                    result["mstThanSlave"] = parse_nibbles(s_val, MST_THAN_SLAVE_FIELDS)
                idx += 1 + s_len
        elif p_tag == 1:  # Parent02
            while idx < p_end:
                shead = data[idx]
                s_tag = shead & 0x07
                s_len = (shead >> 3) & 0x1F
                s_val = data[idx + 1: idx + 1 + s_len]
                if s_tag == 3:  # CLOUD_SUB_TAG_BEVEL_ANGLE
                    result["bevelAngle"] = parse_nibbles(s_val, BEVEL_ANGLE_FIELDS)
                idx += 1 + s_len
        else:
            idx = p_end
            continue

    return result


if __name__ == "__main__":
    hex_str = sys.argv[1] if len(sys.argv) > 1 else \
        "00C2C7CABEC1BEC4C6BBBE0332143C1E37282828285F50505050000000002D14141B1B010030B029FFFF0000001A4644442B03000300001C4400000D059908535914143211000000000000002333000000221D9C9C00"
    print(json.dumps(parse_cloud(hex_str), ensure_ascii=False))
