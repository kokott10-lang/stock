"""
백테스트 공용 로직 (Streamlit 앱과 Flask 앱이 함께 사용)

- 데이터: FinanceDataReader (종목 목록이 안 되면 pykrx 로 대체)
- 엔진:   backtesting.py
"""
import logging
import math

import pandas as pd
import FinanceDataReader as fdr
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------
def load_listing() -> pd.DataFrame:
    """KRX 전 종목 (Code, Name, Market)."""
    df = None
    # 1) FinanceDataReader KRX 전체  2) KOSPI+KOSDAQ 따로  3) pykrx
    for markets in (["KRX"], ["KOSPI", "KOSDAQ"]):
        try:
            parts = []
            for m in markets:
                raw = fdr.StockListing(m)
                part = raw[["Code", "Name"]].copy()
                part["Market"] = raw["Market"] if "Market" in raw else m
                parts.append(part)
            df = pd.concat(parts)
            break
        except Exception as e:  # noqa: BLE001
            log.warning("FinanceDataReader 종목 목록(%s) 실패: %s", markets, e)
    if df is None or df.empty:
        from pykrx import stock

        rows = []
        for market in ("KOSPI", "KOSDAQ"):
            for code in stock.get_market_ticker_list(market=market):
                rows.append((code, stock.get_market_ticker_name(code), market))
        df = pd.DataFrame(rows, columns=["Code", "Name", "Market"])
        if df.empty:
            raise RuntimeError("종목 목록을 불러오지 못했습니다")
    df["Code"] = df["Code"].astype(str).str.zfill(6)
    return df.drop_duplicates("Code").reset_index(drop=True)


def search_listing(df: pd.DataFrame, q: str, limit: int = 20) -> pd.DataFrame:
    """종목명/코드 검색. 정확히 일치 → 앞부분 일치 → 포함 순."""
    q = q.strip()
    if not q:
        return df.iloc[0:0]
    ql = q.lower()
    name_l = df["Name"].str.lower()
    hit = df[name_l.str.contains(ql, regex=False) | df["Code"].str.startswith(q)]
    nl = name_l[hit.index]
    rank = (nl != ql).astype(int) * 2 + (~nl.str.startswith(ql)).astype(int)
    hit = hit.assign(_r=rank, _l=hit["Name"].str.len()).sort_values(["_r", "_l"])
    return hit[["Code", "Name", "Market"]].head(limit).reset_index(drop=True)


def load_prices(code: str, start: str, end: str) -> pd.DataFrame:
    df = fdr.DataReader(code, start, end)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df = df[(df[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]  # 거래정지일 제거
    return df


# ---------------------------------------------------------------------------
# 지표
# ---------------------------------------------------------------------------
def SMA(values, n):
    return pd.Series(values).rolling(n).mean()


def SMA_early(values, n):
    """초반 n일 미만 구간도 값이 나오는 이동평균 (전략이 첫날부터 동작하도록)"""
    return pd.Series(values).rolling(n, min_periods=1).mean()


def RSI(values, n=14):
    s = pd.Series(values)
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


# ---------------------------------------------------------------------------
# 전략
# ---------------------------------------------------------------------------
class BuyHold(Strategy):
    """첫날 사서 끝까지 보유 (비교 기준)"""

    def init(self):
        pass

    def next(self):
        if not self.position:
            self.buy()


class SmaCross(Strategy):
    """골든크로스 매수 / 데드크로스 매도"""

    n1 = 20
    n2 = 60

    def init(self):
        self.s1 = self.I(SMA, self.data.Close, self.n1, name=f"SMA{self.n1}")
        self.s2 = self.I(SMA, self.data.Close, self.n2, name=f"SMA{self.n2}")

    def next(self):
        if crossover(self.s1, self.s2):
            self.buy()
        elif crossover(self.s2, self.s1):
            self.position.close()


class RsiReversal(Strategy):
    """RSI 과매도 탈출 시 매수, 과매수 도달 시 매도"""

    n = 14
    lower = 30
    upper = 70

    def init(self):
        self.rsi = self.I(RSI, self.data.Close, self.n, name=f"RSI{self.n}")

    def next(self):
        if not self.position and crossover(self.rsi, self.lower):
            self.buy()
        elif self.position and self.rsi[-1] >= self.upper:
            self.position.close()


class SplitExit(Strategy):
    """
    분할 익절 + 추적 손절
      - 진입: 시작일 매수, (재진입 옵션) 전량 청산 후 골든크로스 시 재매수
      - 1차 익절: 진입가 대비 +tp1% 도달 → 최초 수량의 p1% 매도
      - 2차 익절: +tp2% 도달 → 최초 수량의 p2% 매도
      - 1차 익절 이후: 보유 중 최고가 대비 -trail% 하락 → 잔량 전량 매도
      - 손절: 진입가 대비 -stop% 하락 → 전량 매도
    """

    tp1 = 20.0
    tp2 = 40.0
    p1 = 30.0
    p2 = 30.0
    trail = 10.0
    stop = 10.0
    reentry = 1
    n1 = 20
    n2 = 60

    def init(self):
        self.s1 = self.I(SMA_early, self.data.Close, self.n1, name=f"SMA{self.n1}")
        self.s2 = self.I(SMA_early, self.data.Close, self.n2, name=f"SMA{self.n2}")
        self.entered_once = False
        self._reset()

    def _reset(self):
        self.entry = None
        self.peak = 0.0
        self.stage = 0

    def next(self):
        price = self.data.Close[-1]

        if not self.position:
            if self.entry is not None:  # 직전 봉에서 전량 청산됨
                self._reset()
            if not self.entered_once or (self.reentry and crossover(self.s1, self.s2)):
                self.buy()
                self.entered_once = True
            return

        if self.entry is None:  # 매수 체결 직후 진입가 기록
            self.entry = self.trades[0].entry_price
            self.peak = self.entry
        self.peak = max(self.peak, self.data.High[-1])
        gain = price / self.entry - 1

        if gain <= -self.stop / 100:
            self.position.close()
            return
        if self.stage == 0 and gain >= self.tp1 / 100:
            self.position.close(portion=min(self.p1 / 100, 1))
            self.stage = 1
            return
        if self.stage == 1 and gain >= self.tp2 / 100:
            left = 1 - self.p1 / 100
            self.position.close(portion=min(self.p2 / 100 / left, 1) if left > 0 else 1)
            self.stage = 2
            return
        if self.stage >= 1 and price <= self.peak * (1 - self.trail / 100):
            self.position.close()


# 화면에 보여줄 전략 정보: (키, 이름, 클래스, 설명, [(파라미터, 라벨, 기본값), ...])
STRATEGIES = {
    "split": dict(
        name="분할 익절 + 추적 손절", cls=SplitExit,
        desc="시작일에 매수 → +1차% 에서 일부, +2차% 에서 일부 익절 → 1차 익절 후엔 고점 대비 "
             "추적손절%, 진입가 대비 손절% 하락 시 전량 매도. 재진입을 켜면 청산 후 골든크로스에서 다시 매수.",
        params=[("tp1", "1차 익절 (%)", 20), ("p1", "1차 매도 비중 (%)", 30),
                ("tp2", "2차 익절 (%)", 40), ("p2", "2차 매도 비중 (%)", 30),
                ("trail", "추적 손절 (고점 대비 %)", 10), ("stop", "손절 (진입가 대비 %)", 10),
                ("reentry", "골든크로스 재진입 (1=켬, 0=끔)", 1),
                ("n1", "단기 이평 (일)", 20), ("n2", "장기 이평 (일)", 60)],
    ),
    "sma": dict(
        name="이동평균 골든/데드크로스", cls=SmaCross,
        desc="단기 이동평균이 장기 이동평균을 위로 뚫으면 매수, 아래로 뚫으면 매도.",
        params=[("n1", "단기 이평 (일)", 20), ("n2", "장기 이평 (일)", 60)],
    ),
    "rsi": dict(
        name="RSI 역추세", cls=RsiReversal,
        desc="RSI 가 과매도선을 위로 넘어서면 매수, 과매수선 이상이면 매도.",
        params=[("n", "RSI 기간 (일)", 14), ("lower", "과매도선", 30), ("upper", "과매수선", 70)],
    ),
    "buyhold": dict(
        name="매수 후 보유 (비교용)", cls=BuyHold,
        desc="첫날 전액 매수 후 끝까지 보유. 다른 전략과 비교하는 기준선.", params=[],
    ),
}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
SUMMARY_KEYS = [
    "Start", "End", "Duration", "Exposure Time [%]", "Equity Final [$]",
    "Equity Peak [$]", "Return [%]", "Buy & Hold Return [%]", "Return (Ann.) [%]",
    "Volatility (Ann.) [%]", "Sharpe Ratio", "Max. Drawdown [%]",
    "Max. Drawdown Duration", "# Trades", "Win Rate [%]", "Best Trade [%]",
    "Worst Trade [%]", "Avg. Trade [%]", "Profit Factor",
]


def to_plain(v):
    """JSON/화면 표시에 쓸 수 있는 값으로 변환 (NaN → None)"""
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, pd.Timedelta):
        return v.days
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return None if math.isnan(f) or math.isinf(f) else f


def run_backtest(df: pd.DataFrame, strategy: str, cash: float, commission_pct: float,
                 params: dict | None = None, plot_path: str | None = None) -> dict:
    """
    백테스트 실행. 결과:
      summary  주요 지표 dict
      curve    DataFrame(Equity, BuyHold, Close) - 날짜 인덱스
      trades   DataFrame(매수일, 매도일, 수량, 매수가, 매도가, 손익, 수익률%)
      plot     plot_path 를 줬다면 상세 차트 HTML 경로 (실패 시 None)
    """
    info = STRATEGIES[strategy]
    Strat = info["cls"]
    if len(df) < 30:
        raise ValueError("기간 내 시세 데이터가 너무 적습니다 (30거래일 이상 필요).")
    if cash < df["Close"].iloc[0]:
        raise ValueError("초기 자금이 첫날 주가보다 적어 1주도 살 수 없습니다.")

    clean = {}
    for k, v in (params or {}).items():
        if hasattr(Strat, k) and not k.startswith("_"):
            clean[k] = type(getattr(Strat, k))(float(v))

    kw = dict(cash=cash, commission=commission_pct / 100, exclusive_orders=False)
    try:
        bt = Backtest(df, Strat, finalize_trades=True, **kw)
    except TypeError:  # 구버전 backtesting.py
        bt = Backtest(df, Strat, **kw)
    stats = bt.run(**clean)

    summary = {k: to_plain(stats.get(k)) for k in SUMMARY_KEYS if k in stats}
    eq = stats["_equity_curve"]["Equity"]
    curve = pd.DataFrame({
        "Equity": eq,
        "BuyHold": (cash * df["Close"] / df["Close"].iloc[0]).reindex(eq.index),
        "Close": df["Close"].reindex(eq.index),
    })

    t = stats["_trades"]
    trades = pd.DataFrame({
        "매수일": t["EntryTime"].dt.strftime("%Y-%m-%d"),
        "매도일": t["ExitTime"].dt.strftime("%Y-%m-%d"),
        "수량": t["Size"].astype(int),
        "매수가": t["EntryPrice"].round(0),
        "매도가": t["ExitPrice"].round(0),
        "손익(원)": t["PnL"].round(0),
        "수익률(%)": (t["ReturnPct"] * 100).round(2),
    }).reset_index(drop=True)

    plot = None
    if plot_path:
        try:
            bt.plot(filename=plot_path, open_browser=False, resample=False)
            plot = plot_path
        except Exception as e:  # noqa: BLE001
            log.warning("상세 차트 생성 실패: %s", e)

    return {"summary": summary, "curve": curve, "trades": trades, "plot": plot}
