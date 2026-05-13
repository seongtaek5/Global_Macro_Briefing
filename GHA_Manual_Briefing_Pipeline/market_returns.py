from __future__ import annotations

from typing import Iterable

import pandas as pd
import yfinance as yf


MARKET_SECTIONS: dict[str, list[tuple[str, str]]] = {
    "EQUITIES": [
        ("S&P 500", "^GSPC"),
        ("NASDAQ 100", "^NDX"),
        ("Dow Jones", "^DJI"),
        ("KOSPI", "^KS11"),
        ("Nikkei 225", "^N225"),
        ("Euro Stoxx 50", "^STOXX50E"),
        ("Brazil Bovespa", "^BVSP"),
        ("India Nifty 50", "^NSEI"),
        ("Vietnam VN Index", "^VNINDEX"),
    ],
    "COMMODITIES & CRYPTO": [
        ("Gold", "GC=F"),
        ("Silver", "SI=F"),
        ("WTI Crude", "CL=F"),
        ("Brent Crude", "BZ=F"),
        ("Natural Gas", "NG=F"),
        ("Copper", "HG=F"),
        ("Corn", "ZC=F"),
        ("Wheat", "ZW=F"),
        ("BTC/USDT", "BTC-USD"),
        ("ETH/USDT", "ETH-USD"),
    ],
    "CURRENCY": [
        ("Dollar Index", "DX-Y.NYB"),
        ("EUR/USD", "EURUSD=X"),
        ("USD/JPY", "USDJPY=X"),
        ("USD/KRW", "USDKRW=X"),
        ("GBP/USD", "GBPUSD=X"),
        ("USD/CNY", "CNY=X"),
    ],
}

RETURN_WINDOWS = ["1D", "1M", "3M", "6M", "12M", "YTD"]


def _extract_close(prices: pd.DataFrame | pd.Series, tickers: Iterable[str]) -> pd.DataFrame:
    ticker_list = list(tickers)
    if isinstance(prices, pd.Series):
        return prices.to_frame(name=ticker_list[0])

    if prices.empty:
        return pd.DataFrame(columns=ticker_list)

    if isinstance(prices.columns, pd.MultiIndex):
        if "Close" in prices.columns.get_level_values(0):
            close = prices["Close"].copy()
        else:
            close = prices.xs("Close", axis=1, level=0, drop_level=False)
            close.columns = close.columns.get_level_values(-1)
        return close

    if "Close" in prices.columns and len(ticker_list) == 1:
        return prices[["Close"]].rename(columns={"Close": ticker_list[0]})

    return prices


def load_market_prices(tickers: tuple[str, ...], period: str = "2y") -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()

    prices = yf.download(
        tickers=list(tickers),
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    close = _extract_close(prices, tickers)
    if close.empty:
        return pd.DataFrame()

    close.index = pd.to_datetime(close.index)
    close = close.sort_index()
    return close


def _price_asof(series: pd.Series, target: pd.Timestamp) -> float | None:
    hist = series.dropna()
    if hist.empty:
        return None

    sliced = hist.loc[:target]
    if sliced.empty:
        return None

    val = float(sliced.iloc[-1])
    return val if pd.notna(val) else None


def _window_start(now: pd.Timestamp, window: str) -> pd.Timestamp:
    if window == "1D":
        return now - pd.DateOffset(days=7)
    if window == "1M":
        return now - pd.DateOffset(months=1)
    if window == "3M":
        return now - pd.DateOffset(months=3)
    if window == "6M":
        return now - pd.DateOffset(months=6)
    if window == "12M":
        return now - pd.DateOffset(years=1)
    return pd.Timestamp(year=now.year, month=1, day=1)


def calc_return(series: pd.Series, window: str, as_of: pd.Timestamp | None = None) -> float | None:
    if window == "1D":
        hist = series.dropna()
        if len(hist) < 2:
            return None
        end_px = float(hist.iloc[-1])
        prev_px = float(hist.iloc[-2])
        if prev_px == 0:
            return None
        return (end_px / prev_px - 1.0) * 100.0

    now = (as_of or pd.Timestamp.today()).normalize()
    end_px = _price_asof(series, now)
    start_px = _price_asof(series, _window_start(now, window))

    if end_px is None or start_px is None or start_px == 0:
        return None

    return (end_px / start_px - 1.0) * 100.0


def get_market_returns(
    sections: dict[str, list[tuple[str, str]]] | None = None,
    windows: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    section_map = sections or MARKET_SECTIONS
    return_windows = windows or RETURN_WINDOWS

    tickers = tuple(ticker for items in section_map.values() for _, ticker in items)
    prices = load_market_prices(tickers)

    out: dict[str, pd.DataFrame] = {}
    for section_name, instruments in section_map.items():
        rows: list[dict[str, object]] = []
        for display_name, ticker in instruments:
            row: dict[str, object] = {"Instrument": display_name, "Ticker": ticker}
            if ticker in prices.columns:
                s = prices[ticker]
                for window in return_windows:
                    row[window] = calc_return(s, window)
            else:
                for window in return_windows:
                    row[window] = None
            rows.append(row)

        out[section_name] = pd.DataFrame(rows, columns=["Instrument", "Ticker", *return_windows])

    return out


def get_ts_mom_zscore_heatmap(
    sections: dict[str, list[tuple[str, str]]] | None = None,
    lookback_months: int = 24,
) -> dict[str, pd.DataFrame]:
    section_map = sections or MARKET_SECTIONS

    tickers = tuple(ticker for items in section_map.values() for _, ticker in items)
    prices = load_market_prices(tickers, period="5y")
    if prices.empty:
        return {}

    monthly = prices.resample("M").last().dropna(how="all")
    if monthly.empty:
        return {}

    month_idx = monthly.index[-lookback_months:]
    month_cols = [d.strftime("%y-%m") for d in month_idx]

    out: dict[str, pd.DataFrame] = {}
    for section_name, instruments in section_map.items():
        rows: list[dict[str, object]] = []

        for display_name, ticker in instruments:
            row: dict[str, object] = {"Instrument": display_name, "Ticker": ticker}
            if ticker in monthly.columns:
                series = monthly[ticker].dropna()
                mom_1y = series.pct_change(12)
                mean_1y = mom_1y.rolling(12, min_periods=12).mean()
                std_1y = mom_1y.rolling(12, min_periods=12).std(ddof=0).replace(0, pd.NA)
                z = (mom_1y - mean_1y) / std_1y
                z = z.reindex(month_idx)
                for label, value in zip(month_cols, z.tolist()):
                    row[label] = value if pd.notna(value) else None
            else:
                for label in month_cols:
                    row[label] = None

            rows.append(row)

        out[section_name] = pd.DataFrame(rows, columns=["Instrument", "Ticker", *month_cols])

    return out
