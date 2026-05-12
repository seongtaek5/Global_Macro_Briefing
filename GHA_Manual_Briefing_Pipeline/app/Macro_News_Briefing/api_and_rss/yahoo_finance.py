from datetime import datetime, timezone, timedelta, time as dtime
from zoneinfo import ZoneInfo
import yfinance as yf
import logging
import numpy as np

def _to_float(value) -> float:
    if hasattr(value, "iloc"):
        return float(value.iloc[0])
    return float(value)

def _infer_exchange_tz(ticker: str):
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return ZoneInfo("Asia/Seoul")
    if ticker.endswith(".L"):
        return ZoneInfo("Europe/London")
    if ticker.endswith(".PA"):
        return ZoneInfo("Europe/Paris")
    return timezone.utc

def _fmt_utc_minute(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def prices_and_pct_diff_reaction(
    ticker: str,
    publish_time: datetime,
    now_time: datetime,
    interval: str = "1m",
):
    """게시 시각과 기준 시각 사이의 가격 반응을 계산해 반환한다.

    입력:
    - ticker: 종목 티커
    - publish_time: 기사 게시 시각
    - now_time: 비교 기준 시각
    - interval: 다운로드 간격

    출력:
    - (시작 시점, 시작가, 종료 시점, 종료가, 수익률%) 튜플 또는 None/UNLISTED

    동작:
    - yfinance 분봉 데이터를 조회하고 구간 시작·종료 바의 종가로 퍼센트 변화를 계산한다.
    """
    if not ticker or ticker == "UNLISTED":
        return "UNLISTED"

    if publish_time.tzinfo is None or now_time.tzinfo is None:
        raise ValueError("publish_time and now_time must be timezone-aware")

    publish_time = publish_time.astimezone(timezone.utc)
    now_time = now_time.astimezone(timezone.utc)

    t_start, t_end = sorted([publish_time, now_time])

    start_dt = t_start - timedelta(days=2)
    end_dt   = t_end + timedelta(days=1)

    df = yf.download(
        tickers=ticker,
        start=start_dt,
        end=end_dt,
        interval=interval,
        prepost=True,
        auto_adjust=False,
        progress=False,
    )

    if df is None or df.empty:
        logging.info(f"[YF] Empty DataFrame for {ticker}")
        return None

    idx = df.index
    if getattr(idx, "tz", None) is None:
        idx_utc = idx.tz_localize(timezone.utc)
    else:
        idx_utc = idx.tz_convert(timezone.utc)

    idx_vals = idx_utc.values  # numpy datetime64[ns]

    t_start64 = np.datetime64(t_start, "ns")
    t_end64   = np.datetime64(t_end, "ns")

    i1 = np.searchsorted(idx_vals, t_start64, side="left")
    if i1 >= len(idx_vals):
        logging.info(f"[YF] No bar after publish_time for {ticker}")
        return None
    t1_bar = idx_utc[i1]
    p1 = float(df["Close"].iloc[i1])

    i2 = np.searchsorted(idx_vals, t_end64, side="right") - 1
    if i2 < 0:
        logging.info(f"[YF] No bar before now_time for {ticker}")
        return None
    t2_bar = idx_utc[i2]
    p2 = float(df["Close"].iloc[i2])

    if p1 == 0:
        return None

    pct = (p2 - p1) / p1 * 100.0
    logging.info(f"[PICK] {ticker} p1={p1} at {t1_bar} | p2={p2} at {t2_bar} | pct={pct:.6f}%")

    return t1_bar, p1, t2_bar, p2, pct


def last_full_day_change(
    ticker: str,
    now_time: datetime,
    close_hour_local: int = 16,
):
    """최근 완료된 일봉 기준 전일 대비 변동률을 계산해 반환한다.

    입력:
    - ticker: 종목 티커
    - now_time: 기준 시각
    - close_hour_local: 거래소 현지 마감 시각(시)

    출력:
    - (마감 바 시각, 전일 종가, 당일 종가, 수익률%) 튜플 또는 None/UNLISTED

    동작:
    - 일봉 데이터를 조회하고 미완성 당일 바를 제외한 최신 완료 바로 변동률을 계산한다.
    """
    if not ticker or ticker == "UNLISTED":
        return "UNLISTED"

    if now_time.tzinfo is None:
        raise ValueError("now_time must be timezone-aware")

    now_time = now_time.astimezone(timezone.utc)

    start_dt = now_time - timedelta(days=10)
    end_dt = now_time + timedelta(days=1)

    df = yf.download(
        tickers=ticker,
        start=start_dt,
        end=end_dt,
        interval="1d",
        prepost=False,
        auto_adjust=False,
        progress=False,
    )

    if df is None or df.empty:
        logging.info(f"[YF] Empty daily DataFrame for {ticker}")
        return None

    idx = df.index
    if getattr(idx, "tz", None) is None:
        exchange_tz = _infer_exchange_tz(ticker)
        idx_local = idx.tz_localize(exchange_tz)
    else:
        idx_local = idx.tz_convert(idx.tz)
        exchange_tz = idx_local.tz or timezone.utc
    now_local = now_time.astimezone(exchange_tz)

    last_i = len(df) - 1
    last_bar_time = idx_local[last_i]

    if last_bar_time.date() == now_local.date() and now_local.time() < dtime(close_hour_local, 0):
        last_i -= 1
        if last_i < 0:
            logging.info(f"[YF] No completed daily bar for {ticker}")
            return None
        last_bar_time = idx_local[last_i]

    close_p = _to_float(df["Close"].iloc[last_i])
    if close_p == 0:
        return None

    prev_i = last_i - 1
    if prev_i < 0:
        logging.info(f"[YF] No previous daily bar for {ticker}")
        return None
    prev_close = _to_float(df["Close"].iloc[prev_i])
    if prev_close == 0:
        return None

    pct = (close_p - prev_close) / prev_close * 100.0

    logging.info(
        f"[DAY] {ticker} prev_close={prev_close} close={close_p} at {last_bar_time} | pct={pct:.6f}%"
    )
    return last_bar_time, prev_close, close_p, pct
