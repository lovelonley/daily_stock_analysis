"""pytdx 全市场扫描器 — 批量获取快照 + 指数数据"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import pandas as pd
from pytdx.hq import TdxHq_API

# 公共行情服务器列表 (host, port)
DEFAULT_SERVERS: List[Tuple[str, int]] = [
    ("180.153.18.170", 7709),
    ("180.153.18.171", 7709),
    ("202.108.253.130", 7709),
    ("202.108.253.131", 7709),
    ("60.191.117.167", 7709),
    ("115.238.56.198", 7709),
    ("218.75.126.9", 7709),
    ("124.160.88.183", 7709),
]

# 沪深市场代码
MARKET_SH = 1  # 上海
MARKET_SZ = 0  # 深圳


class MarketScanner:
    """pytdx 全市场扫描器"""

    def __init__(self, servers: Optional[List[Tuple[str, int]]] = None):
        self.servers = servers or DEFAULT_SERVERS
        self.api = TdxHq_API()
        self._connected = False
        self._stock_list: Optional[List[Tuple[int, str]]] = None  # [(market, code), ...]

    def connect(self) -> bool:
        """尝试连接行情服务器，失败则切换下一个"""
        for host, port in self.servers:
            try:
                self.api.connect(host, port)
                # 测试连接
                result = self.api.get_security_count(MARKET_SH)
                if result and result > 0:
                    self._connected = True
                    return True
            except Exception:
                continue
        self._connected = False
        return False

    def disconnect(self):
        """断开连接"""
        try:
            self.api.disconnect()
        except Exception:
            pass
        self._connected = False

    def _ensure_connected(self):
        """确保已连接，断线自动重连"""
        if self._connected:
            # 心跳检测：尝试一个轻量调用验证连接是否存活
            try:
                result = self.api.get_security_count(MARKET_SH)
                if result and result > 0:
                    return
            except Exception:
                pass
            # 心跳失败，标记断开并重连
            self._connected = False
        if not self.connect():
            raise ConnectionError("无法连接任何行情服务器")

    def load_stock_list(self) -> List[Tuple[int, str]]:
        """获取沪深全市场股票代码列表，缓存复用"""
        if self._stock_list is not None:
            return self._stock_list

        self._ensure_connected()
        stocks = []

        for market in (MARKET_SH, MARKET_SZ):
            count = self.api.get_security_count(market)
            if not count:
                continue
            for start in range(0, count, 1000):
                batch = self.api.get_security_list(market, start)
                if not batch:
                    continue
                for item in batch:
                    code = item["code"]
                    # 过滤：只保留 A 股主板/创业板/科创板
                    if market == MARKET_SH and code.startswith(("60", "68")):
                        stocks.append((market, code))
                    elif market == MARKET_SZ and code.startswith(("00", "30")):
                        stocks.append((market, code))

        self._stock_list = stocks
        return stocks

    def scan_all_quotes(self) -> pd.DataFrame:
        """
        批量获取全市场实时快照。

        返回 DataFrame 列: market, code, price, last_close, pct_change, volume, amount
        过滤: 剔除停牌(vol=0)、无效数据
        """
        self._ensure_connected()
        stocks = self.load_stock_list()

        all_quotes = []
        batch_size = 80  # pytdx 每批最多约 80 只

        for i in range(0, len(stocks), batch_size):
            batch = stocks[i : i + batch_size]
            try:
                result = self.api.get_security_quotes(batch)
                if result:
                    all_quotes.extend(result)
            except Exception:
                # 单批失败，尝试重连后继续
                try:
                    self.connect()
                    result = self.api.get_security_quotes(batch)
                    if result:
                        all_quotes.extend(result)
                except Exception:
                    continue

        if not all_quotes:
            return pd.DataFrame()

        df = pd.DataFrame(all_quotes)
        # 选取关键列并重命名
        cols = {
            "market": "market",
            "code": "code",
            "price": "price",
            "last_close": "last_close",
            "vol": "volume",
            "amount": "amount",
        }
        available = {k: v for k, v in cols.items() if k in df.columns}
        df = df[list(available.keys())].rename(columns=available)

        # 过滤停牌 (volume=0 或 price=0) 和异常昨收
        df = df[(df["volume"] > 0) & (df["price"] > 0) & (df["last_close"] > 0)].copy()

        # 计算涨跌幅
        df["pct_change"] = (df["price"] - df["last_close"]) / df["last_close"]

        return df.reset_index(drop=True)

    def get_index_snapshot(self) -> dict:
        """
        获取主要指数实时数据 (相对昨收涨跌幅，与股票口径统一)。

        返回: {code: {"name": str, "price": float, "pct_change": float}}
        指数代码: 000001(上证), 399001(深成), 399006(创业板), 000300(沪深300)
        """
        self._ensure_connected()

        indices = {
            "000001": ("上证指数", MARKET_SH),
            "399001": ("深证成指", MARKET_SZ),
            "399006": ("创业板指", MARKET_SZ),
            "000300": ("沪深300", MARKET_SH),
        }

        result = {}
        # 使用 get_security_quotes 获取实时报价 (含 last_close)
        quote_args = [(market, code) for code, (_, market) in indices.items()]
        try:
            quotes = self.api.get_security_quotes(quote_args)
            if quotes:
                for q in quotes:
                    code = q.get("code", "")
                    if code not in indices:
                        continue
                    name = indices[code][0]
                    price = q.get("price", 0.0)
                    last_close = q.get("last_close", 0.0)
                    pct = (price - last_close) / last_close if last_close > 0 else 0.0
                    result[code] = {
                        "name": name,
                        "price": price,
                        "pct_change": pct,
                    }
        except Exception:
            # 回退: 用日线获取
            for code, (name, market) in indices.items():
                try:
                    bars = self.api.get_index_bars(9, market, code, 0, 2)
                    if bars and len(bars) >= 2:
                        today = bars[-1]
                        yesterday = bars[-2]
                        price = today["close"]
                        last_close = yesterday["close"]
                        pct = (price - last_close) / last_close if last_close > 0 else 0.0
                        result[code] = {"name": name, "price": price, "pct_change": pct}
                except Exception:
                    continue

        return result
