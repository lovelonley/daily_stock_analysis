"""盘中实时预警主循环 — pytdx 扫描 + 分级告警 + 邮件通知"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from src.monitor.alert_rules import (
    AlertEngine,
    AlertLevel,
    AlertResult,
    MarketSnapshot,
    format_alert_line,
    LEVEL_DISPLAY,
)
from src.monitor.market_scanner import MarketScanner

logger = logging.getLogger(__name__)

# 状态持久化文件 (收盘后保存当日摘要，开盘前读取)
_STATUS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "sentinel_status.json"

LEVEL_NAMES = {
    AlertLevel.NORMAL: "normal",
    AlertLevel.WATCH: "watch",
    AlertLevel.WARNING: "warning",
    AlertLevel.DANGER: "danger",
}

# 跌停/涨停判定阈值 (pytdx 实时涨跌幅)
# 主板 ±10%，创业板(30)/科创板(68) ±20%
LIMIT_DOWN_PCT_MAIN = -0.095   # 主板: <= -9.5% 视为跌停
LIMIT_UP_PCT_MAIN = 0.095      # 主板: >= +9.5% 视为涨停
LIMIT_DOWN_PCT_20CM = -0.195   # 创业板/科创板: <= -19.5%
LIMIT_UP_PCT_20CM = 0.195      # 创业板/科创板: >= +19.5%


class LiveSentinel:
    """盘中实时预警主循环，支持 daemon 线程启动 + 邮件告警"""

    def __init__(self, interval: int = 5, servers=None):
        self.scanner = MarketScanner(servers)
        self.engine = AlertEngine()
        self.interval = interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # 邮件冷却
        self._last_alert_time: Optional[datetime] = None
        self._notify_cooldown = 600  # 10 分钟
        # 日报统计
        self._max_level = AlertLevel.NORMAL
        self._max_level_time: Optional[str] = None
        self._peak_limit_down = 0
        self._peak_limit_down_time: Optional[str] = None
        self._min_median = 999.0
        self._min_median_time: Optional[str] = None
        self._last_csi300 = 0.0
        self._max_accel: Optional[int] = None
        self._max_accel_time: Optional[str] = None
        # 涨停板炸板监控
        self._early_limit_up: Optional[int] = None
        self._early_captured = False
        # 最新告警结果 (供 API 读取)
        self._last_result: Optional[AlertResult] = None
        self._connected = False

    def start_background(self):
        """以 daemon 线程启动，供 FastAPI lifespan 调用"""
        self._thread = threading.Thread(
            target=self.run, daemon=True, name="SentinelMonitor"
        )
        self._thread.start()
        logger.info("Sentinel 盘中监控已启动 (interval=%ds)", self.interval)

    def stop(self):
        """优雅停止"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("Sentinel 盘中监控已停止")

    def get_status(self) -> Dict[str, Any]:
        """
        返回当前监控状态，供 API 端点读取。

        逻辑:
        - 未连接 / 线程未运行 → phase=offline
        - 交易时段有数据 → phase=live, 返回实时级别+快照
        - 收盘后 (>=15:01) → phase=closed, 返回当日摘要
        - 开盘前 (<9:15) → phase=pre_market, 读取上一次持久化的状态
        - 午休 (11:30-13:00) → phase=lunch_break, 返回上午最新数据
        """
        now = datetime.now()
        t = now.hour * 60 + now.minute
        base: Dict[str, Any] = {"timestamp": now.isoformat()}

        # 线程没跑或没连上
        if not self._running and not self._connected:
            # 尝试读持久化文件
            saved = self._load_saved_status()
            if saved:
                return {**base, "phase": "pre_market", **saved}
            return {**base, "phase": "offline", "level": "offline"}

        # 开盘前
        if t < 9 * 60 + 15:
            saved = self._load_saved_status()
            if saved:
                return {**base, "phase": "pre_market", **saved}
            return {**base, "phase": "pre_market", "level": "normal"}

        # 收盘后
        if t >= 15 * 60 + 1:
            return {**base, "phase": "closed", **self._daily_summary()}

        # 交易时段或午休 — 返回实时数据
        if self._last_result is not None:
            snap = self._last_result.snapshot
            phase = "live"
            if 11 * 60 + 30 < t < 13 * 60:
                phase = "lunch_break"
            return {
                **base,
                "phase": phase,
                "level": LEVEL_NAMES.get(self._last_result.level, "normal"),
                "confirmed": self._last_result.confirmed,
                "reasons": self._last_result.reasons,
                "snapshot": {
                    "limit_down": snap.limit_down_count,
                    "limit_up": snap.limit_up_count,
                    "median_pct": round(snap.median_pct_change, 4),
                    "csi300_pct": round(snap.csi300_pct_change, 4),
                    "decline_ratio": round(snap.decline_ratio, 4),
                },
                "max_level_today": LEVEL_NAMES.get(self._max_level, "normal"),
            }

        return {**base, "phase": "live", "level": "normal"}

    def _daily_summary(self) -> Dict[str, Any]:
        """当日摘要数据"""
        return {
            "level": LEVEL_NAMES.get(self._max_level, "normal"),
            "max_level_time": self._max_level_time,
            "peak_limit_down": self._peak_limit_down,
            "peak_limit_down_time": self._peak_limit_down_time,
            "min_median": round(self._min_median, 4) if self._min_median < 999 else None,
            "min_median_time": self._min_median_time,
            "last_csi300": round(self._last_csi300, 4),
        }

    def _save_status(self):
        """收盘时持久化当日状态"""
        try:
            _STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                **self._daily_summary(),
            }
            _STATUS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning("保存状态失败: %s", e)

    def _load_saved_status(self) -> Optional[Dict[str, Any]]:
        """读取持久化状态"""
        try:
            if _STATUS_FILE.exists():
                data = json.loads(_STATUS_FILE.read_text(encoding="utf-8"))
                return data
        except Exception:
            pass
        return None

    def run(self):
        """主循环 (在 daemon 线程中运行)"""
        logger.info("正在连接行情服务器...")
        if not self.scanner.connect():
            logger.error("无法连接任何行情服务器")
            return
        self._connected = True

        stocks = self.scanner.load_stock_list()
        logger.info("已加载 %d 只股票，扫描间隔: %d秒", len(stocks), self.interval)

        self._running = True

        while self._running:
            if not self._is_trading_time():
                now = datetime.now()
                # 15:01 后输出日报并退出
                if now.hour >= 15 and now.minute >= 1:
                    if self._max_level > AlertLevel.NORMAL:
                        self._log_daily_report()
                    self._save_status()
                    logger.info("收盘，监控结束。")
                    break
                # 非交易时段等待
                time.sleep(30)
                continue

            try:
                snap = self._build_snapshot()
                if snap.total_stocks == 0:
                    self._empty_count = getattr(self, '_empty_count', 0) + 1
                    if self._empty_count >= 3:
                        logger.warning("连续空数据，尝试重连...")
                        self.scanner.disconnect()
                        if not self.scanner.connect():
                            logger.error("重连失败，等待 30 秒后重试")
                            time.sleep(30)
                        self._empty_count = 0
                    continue
                self._empty_count = 0

                result = self.engine.evaluate(snap)
                self._last_result = result
                line = format_alert_line(result)
                print(line)

                # 更新日报统计
                self._update_stats(snap, result)

                # 邮件告警
                self._maybe_send_alert(result)

            except ConnectionError:
                logger.warning("连接断开，尝试重连...")
                if not self.scanner.connect():
                    logger.error("重连失败，等待 30 秒后重试")
                    time.sleep(30)
                    continue
            except Exception as e:
                logger.warning("扫描异常: %s", e)

            time.sleep(self.interval)

        self.scanner.disconnect()

    def _maybe_send_alert(self, result: AlertResult):
        """DANGER + confirmed 时发送邮件告警，带冷却机制"""
        if result.level < AlertLevel.DANGER or not result.confirmed:
            return
        now = result.snapshot.timestamp
        if (
            self._last_alert_time
            and (now - self._last_alert_time).total_seconds() < self._notify_cooldown
        ):
            return
        try:
            from src.notification_sender.email_sender import EmailSender
            sender = EmailSender()
            success = sender.send_to_email(
                content=self._build_alert_email(result),
                subject=f"A股盘中风险警报 {now.strftime('%H:%M')}",
            )
            if success:
                self._last_alert_time = now
                logger.info("风险告警邮件已发送")
            else:
                logger.warning("邮件发送返回失败")
        except Exception as e:
            logger.warning("邮件发送异常: %s", e)

    def _build_alert_email(self, result: AlertResult) -> str:
        """构建告警邮件内容 (Markdown)"""
        snap = result.snapshot
        ts = snap.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        icon, label, _ = LEVEL_DISPLAY[result.level]

        lines = [
            f"# {icon} A股盘中风险警报",
            f"**时间**: {ts}",
            f"**级别**: {label.strip()} (已确认)",
            "",
            "## 市场概况",
            f"- 跌停: **{snap.limit_down_count}** 家",
            f"- 涨停: {snap.limit_up_count} 家",
            f"- 中位数涨跌: **{snap.median_pct_change:+.2%}**",
            f"- 沪深300: {snap.csi300_pct_change:+.2%}",
            f"- 下跌占比: {snap.decline_ratio:.0%}",
        ]

        if snap.limit_down_delta_10min is not None and snap.limit_down_delta_10min > 0:
            lines.append(f"- 跌停加速: 10min +{snap.limit_down_delta_10min} 家")

        if result.reasons:
            lines.append("")
            lines.append("## 触发规则")
            for r in result.reasons:
                lines.append(f"- {r}")

        return "\n".join(lines)

    def _build_snapshot(self) -> MarketSnapshot:
        """构建全市场快照"""
        now = datetime.now()

        df = self.scanner.scan_all_quotes()
        if df.empty:
            return MarketSnapshot(timestamp=now)

        total = len(df)
        is_20cm = df["code"].str.startswith(("30", "68"))
        limit_thresh_down = pd.Series(LIMIT_DOWN_PCT_MAIN, index=df.index)
        limit_thresh_down[is_20cm] = LIMIT_DOWN_PCT_20CM
        limit_thresh_up = pd.Series(LIMIT_UP_PCT_MAIN, index=df.index)
        limit_thresh_up[is_20cm] = LIMIT_UP_PCT_20CM
        limit_down = int((df["pct_change"] <= limit_thresh_down).sum())
        limit_up = int((df["pct_change"] >= limit_thresh_up).sum())
        median_pct = float(df["pct_change"].median())
        decline_count = (df["pct_change"] < 0).sum()
        decline_ratio = decline_count / total if total > 0 else 0.0

        # 涨停板炸板: 9:30~10:30 之间持续更新早盘涨停峰值
        in_early_window = (
            (now.hour == 9 and now.minute >= 30) or
            (now.hour == 10 and now.minute <= 30)
        )
        if in_early_window:
            if self._early_limit_up is None or limit_up > self._early_limit_up:
                self._early_limit_up = limit_up
                self._early_captured = True

        # 指数数据
        indices = self.scanner.get_index_snapshot()
        csi300_pct = indices.get("000300", {}).get("pct_change", 0.0)
        shanghai_pct = indices.get("000001", {}).get("pct_change", 0.0)

        return MarketSnapshot(
            timestamp=now,
            total_stocks=total,
            limit_down_count=limit_down,
            limit_up_count=limit_up,
            median_pct_change=median_pct,
            decline_ratio=decline_ratio,
            csi300_pct_change=csi300_pct,
            shanghai_pct_change=shanghai_pct,
            early_limit_up_count=self._early_limit_up,
        )

    def _update_stats(self, snap: MarketSnapshot, result: AlertResult):
        """更新日报统计"""
        ts = snap.timestamp.strftime("%H:%M")
        if result.level > self._max_level:
            self._max_level = result.level
            self._max_level_time = ts
        if snap.limit_down_count > self._peak_limit_down:
            self._peak_limit_down = snap.limit_down_count
            self._peak_limit_down_time = ts
        if snap.median_pct_change < self._min_median:
            self._min_median = snap.median_pct_change
            self._min_median_time = ts
        self._last_csi300 = snap.csi300_pct_change
        if snap.limit_down_delta_10min is not None:
            if self._max_accel is None or snap.limit_down_delta_10min > self._max_accel:
                self._max_accel = snap.limit_down_delta_10min
                self._max_accel_time = ts

    def _log_daily_report(self):
        """输出收盘日报到日志"""
        today = datetime.now().strftime("%Y-%m-%d")
        icon, label, _ = LEVEL_DISPLAY[self._max_level]
        parts = [
            f"=== 收盘日报 [{today}] ===",
            f"最高告警: {icon} {label.strip()} ({self._max_level_time} 触发)",
            f"跌停峰值: {self._peak_limit_down}家 ({self._peak_limit_down_time})",
        ]
        if self._max_accel is not None and self._max_accel > 0:
            parts.append(f"最大加速: 10min+{self._max_accel}家 ({self._max_accel_time})")
        if self._min_median < 999:
            parts.append(f"中位数最低: {self._min_median:.2%} ({self._min_median_time})")
        parts.append(f"沪深300: {self._last_csi300:+.2%}")
        logger.info("\n".join(parts))

    def _is_trading_time(self) -> bool:
        """判断当前是否在交易时段 (9:15-11:30, 13:00-15:00)"""
        now = datetime.now()
        t = now.hour * 60 + now.minute
        morning_start = 9 * 60 + 15
        morning_end = 11 * 60 + 30
        afternoon_start = 13 * 60
        afternoon_end = 15 * 60
        return (morning_start <= t <= morning_end) or (afternoon_start <= t <= afternoon_end)
