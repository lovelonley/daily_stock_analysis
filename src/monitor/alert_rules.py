"""分级告警规则引擎 — 复用 RiskEngine 阈值体系 + 时序规则"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Deque, List, Optional, Tuple


class AlertLevel(IntEnum):
    """告警级别"""
    NORMAL = 0   # 正常 (绿色)
    WATCH = 1    # 关注 (白色)
    WARNING = 2  # 警告 (黄色)
    DANGER = 3   # 危险 (红色)


# 级别显示配置
LEVEL_DISPLAY = {
    AlertLevel.NORMAL:  ("🟢", "NORMAL ", "\033[32m"),   # 绿
    AlertLevel.WATCH:   ("🟡", "WATCH  ", "\033[37m"),   # 白
    AlertLevel.WARNING: ("🟡", "WARNING", "\033[33m"),   # 黄
    AlertLevel.DANGER:  ("🔴", "DANGER ", "\033[31m"),   # 红
}
RESET = "\033[0m"


@dataclass
class MarketSnapshot:
    """全市场快照数据"""
    timestamp: datetime
    total_stocks: int = 0           # 有效交易股票数
    limit_down_count: int = 0       # 跌停家数
    limit_up_count: int = 0         # 涨停家数
    median_pct_change: float = 0.0  # 全市场中位数涨跌幅
    decline_ratio: float = 0.0      # 下跌家数占比
    csi300_pct_change: float = 0.0  # 沪深300涨跌幅
    shanghai_pct_change: float = 0.0  # 上证涨跌幅
    # 涨停板炸板监控 (Tier2)
    early_limit_up_count: Optional[int] = None  # 前半小时涨停家数
    # ── 时序派生指标 (由 AlertEngine 填充) ──
    limit_down_delta_10min: Optional[int] = None   # 10分钟跌停增量
    scissors_gap: Optional[float] = None           # 指数-中位数剪刀差


@dataclass
class AlertResult:
    """告警结果"""
    level: AlertLevel
    reasons: List[str]
    snapshot: MarketSnapshot
    confirmed: bool = False  # 连续2轮同级别触发


# ── 阈值常量 (与 RiskEngine 对齐) ──────────────────────────

# Tier 1: 踩踏级
T1_LIMIT_DOWN_COUNT = 150
T1_LIMIT_DOWN_RATIO = 0.20
T1_MEDIAN_DROP = -0.045
T1_CSI300_DROP = -0.05
T1_LIMIT_DOWN_ACCEL = 30   # 10分钟跌停增量 ≥30 → 踩踏级

# Tier 2: 压力级
T2_LIMIT_DOWN_COUNT = 50
T2_MEDIAN_DROP = -0.025
T2_DECLINE_RATIO = 0.80
T2_CSI300_DROP = -0.03
T2_LIMIT_UP_COLLAPSE_EARLY = 30  # 前半小时涨停>30
T2_LIMIT_UP_COLLAPSE_NOW = 10    # 当前<10 → 炸板
T2_LIMIT_DOWN_ACCEL = 15          # 10分钟跌停增量 ≥15 → 压力级
T2_SCISSORS_GAP = 0.015           # 剪刀差 >1.5% 且中位数 <-2%
T2_SCISSORS_MEDIAN = -0.020       # 剪刀差配合中位数阈值

# Tier 3: 关注级
T3_LIMIT_DOWN_COUNT = 20
T3_MEDIAN_DROP = -0.015
T3_CSI300_DROP = -0.02

# 历史窗口
HISTORY_WINDOW_SEC = 660  # 11分钟 (略大于10分钟，确保覆盖)
HISTORY_MAX_SIZE = 200    # 环形缓冲最大条目


class AlertEngine:
    """分级告警引擎 — 静态阈值 + 时序规则 + 确认机制"""

    def __init__(self):
        self._history: Deque[MarketSnapshot] = deque(maxlen=HISTORY_MAX_SIZE)
        # 确认机制：连续 N 轮同级别告警
        self._prev_level: AlertLevel = AlertLevel.NORMAL
        self._streak: int = 0

    def evaluate(self, snap: MarketSnapshot) -> AlertResult:
        """
        逐条检查 Tier1/2/3 规则（含时序规则），返回最高告警级别和触发原因列表。
        快照自动入历史队列，时序派生指标自动填充。
        """
        # 填充时序派生指标
        self._enrich_snapshot(snap)
        self._history.append(snap)

        tier1 = self._check_tier1(snap)
        tier2 = self._check_tier2(snap)
        tier3 = self._check_tier3(snap)

        if tier1:
            level = AlertLevel.DANGER
            reasons = tier1 + tier2 + tier3
        elif tier2:
            level = AlertLevel.WARNING
            reasons = tier2 + tier3
        elif tier3:
            level = AlertLevel.WATCH
            reasons = tier3
        else:
            level = AlertLevel.NORMAL
            reasons = []

        # 确认机制
        if level >= AlertLevel.WARNING and level == self._prev_level:
            self._streak += 1
        else:
            self._streak = 1 if level >= AlertLevel.WARNING else 0
        self._prev_level = level

        confirmed = self._streak >= 2

        return AlertResult(level=level, reasons=reasons, snapshot=snap, confirmed=confirmed)

    def _enrich_snapshot(self, snap: MarketSnapshot):
        """从历史队列计算时序派生指标，填充到 snap"""
        # 跌停加速度：与 ~10 分钟前的快照比较
        old_snap = self._find_snapshot_ago(snap.timestamp, seconds=600)
        if old_snap is not None:
            snap.limit_down_delta_10min = snap.limit_down_count - old_snap.limit_down_count
        # 剪刀差：上证涨跌幅 - 全市场中位数涨跌幅
        snap.scissors_gap = snap.shanghai_pct_change - snap.median_pct_change

    def _find_snapshot_ago(self, now: datetime, seconds: int) -> Optional[MarketSnapshot]:
        """在历史队列中找到距 now 约 seconds 秒的快照（最近匹配）"""
        if not self._history:
            return None
        target = now - timedelta(seconds=seconds)
        best = None
        best_diff = float("inf")
        for snap in self._history:
            diff = abs((snap.timestamp - target).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best = snap
        # 只接受误差在 2 分钟内的匹配
        if best is not None and best_diff <= 120:
            return best
        return None

    def _check_tier1(self, snap: MarketSnapshot) -> List[str]:
        """Tier 1: 踩踏级 (红色)"""
        reasons = []
        if snap.limit_down_count >= T1_LIMIT_DOWN_COUNT:
            ratio = snap.limit_down_count / max(snap.total_stocks, 1)
            reasons.append(
                f"跌停>={T1_LIMIT_DOWN_COUNT}家: {snap.limit_down_count}({ratio:.0%})"
            )
        if snap.total_stocks > 0:
            ratio = snap.limit_down_count / snap.total_stocks
            if ratio >= T1_LIMIT_DOWN_RATIO and snap.limit_down_count < T1_LIMIT_DOWN_COUNT:
                reasons.append(f"跌停比例>={T1_LIMIT_DOWN_RATIO:.0%}: {ratio:.1%}")
        if snap.median_pct_change < T1_MEDIAN_DROP:
            reasons.append(
                f"中位数<{T1_MEDIAN_DROP:.1%}: {snap.median_pct_change:.2%}"
            )
        if snap.csi300_pct_change < T1_CSI300_DROP:
            reasons.append(
                f"沪深300<{T1_CSI300_DROP:.0%}: {snap.csi300_pct_change:.2%}"
            )
        # 时序规则: 跌停加速度
        if (
            snap.limit_down_delta_10min is not None
            and snap.limit_down_delta_10min >= T1_LIMIT_DOWN_ACCEL
        ):
            reasons.append(
                f"跌停加速: 10min+{snap.limit_down_delta_10min}家"
            )
        return reasons

    def _check_tier2(self, snap: MarketSnapshot) -> List[str]:
        """Tier 2: 压力级 (黄色)"""
        reasons = []
        if snap.limit_down_count >= T2_LIMIT_DOWN_COUNT:
            reasons.append(f"跌停>={T2_LIMIT_DOWN_COUNT}家: {snap.limit_down_count}")
        if snap.median_pct_change < T2_MEDIAN_DROP:
            reasons.append(
                f"中位数<{T2_MEDIAN_DROP:.1%}: {snap.median_pct_change:.2%}"
            )
        if snap.decline_ratio > T2_DECLINE_RATIO:
            reasons.append(f"下跌>{T2_DECLINE_RATIO:.0%}: {snap.decline_ratio:.0%}")
        if snap.csi300_pct_change < T2_CSI300_DROP:
            reasons.append(
                f"沪深300<{T2_CSI300_DROP:.0%}: {snap.csi300_pct_change:.2%}"
            )
        # 涨停板炸板
        if (
            snap.early_limit_up_count is not None
            and snap.early_limit_up_count >= T2_LIMIT_UP_COLLAPSE_EARLY
            and snap.limit_up_count < T2_LIMIT_UP_COLLAPSE_NOW
        ):
            reasons.append(
                f"涨停炸板: {snap.early_limit_up_count}→{snap.limit_up_count}"
            )
        # 时序规则: 跌停加速度 (Tier2 级)
        if (
            snap.limit_down_delta_10min is not None
            and snap.limit_down_delta_10min >= T2_LIMIT_DOWN_ACCEL
            and snap.limit_down_delta_10min < T1_LIMIT_DOWN_ACCEL
        ):
            reasons.append(
                f"跌停加速: 10min+{snap.limit_down_delta_10min}家"
            )
        # 时序规则: 指数-中位数剪刀差 (掩护撤退)
        if (
            snap.scissors_gap is not None
            and snap.scissors_gap > T2_SCISSORS_GAP
            and snap.median_pct_change < T2_SCISSORS_MEDIAN
        ):
            reasons.append(
                f"掩护撤退: 剪刀差{snap.scissors_gap:+.2%} 中位数{snap.median_pct_change:+.2%}"
            )
        return reasons

    def _check_tier3(self, snap: MarketSnapshot) -> List[str]:
        """Tier 3: 关注级 (白色)"""
        reasons = []
        if snap.limit_down_count >= T3_LIMIT_DOWN_COUNT:
            reasons.append(f"跌停>={T3_LIMIT_DOWN_COUNT}家: {snap.limit_down_count}")
        if snap.median_pct_change < T3_MEDIAN_DROP:
            reasons.append(
                f"中位数<{T3_MEDIAN_DROP:.1%}: {snap.median_pct_change:.2%}"
            )
        if snap.csi300_pct_change < T3_CSI300_DROP:
            reasons.append(
                f"沪深300<{T3_CSI300_DROP:.0%}: {snap.csi300_pct_change:.2%}"
            )
        return reasons


def format_alert_line(result: AlertResult) -> str:
    """格式化单行告警输出 (带 ANSI 颜色 + 确认标记)"""
    snap = result.snapshot
    level = result.level
    icon, label, color = LEVEL_DISPLAY[level]
    ts = snap.timestamp.strftime("%H:%M:%S")

    limit_down_pct = ""
    if snap.total_stocks > 0 and snap.limit_down_count > 0:
        ratio = snap.limit_down_count / snap.total_stocks
        limit_down_pct = f"({ratio:.0%})"

    # 加速度标注
    accel_tag = ""
    if snap.limit_down_delta_10min is not None and snap.limit_down_delta_10min > 0:
        accel_tag = f" +{snap.limit_down_delta_10min}"

    # 确认标记
    confirm_tag = " ⚡确认" if result.confirmed else ""

    line = (
        f"{color}[{ts}] {icon} {label}{confirm_tag}{RESET}  "
        f"跌停:{snap.limit_down_count}{limit_down_pct}{accel_tag}  "
        f"涨停:{snap.limit_up_count}  "
        f"中位数:{snap.median_pct_change:+.2%}  "
        f"300:{snap.csi300_pct_change:+.1%}  "
        f"下跌:{snap.decline_ratio:.0%}"
    )

    if result.reasons:
        line += f"\n           ├─ {', '.join(result.reasons)}"

    return line
