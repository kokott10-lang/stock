"""
백테스트 공용 로직 (Streamlit 앱과 Flask 앱이 함께 사용)

- 데이터: FinanceDataReader (종목 목록이 안 되면 pykrx 로 대체)
- 엔진:   backtesting.py
"""
import logging
import math
import re

import pandas as pd
import FinanceDataReader as fdr
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------
# 미국 주요 ETF (FinanceDataReader 의 해외 ETF 목록 기능이 중단되어 직접 관리)
US_ETFS = [
    ("QQQ", "Invesco QQQ (나스닥100)"),
    ("QQQM", "Invesco QQQM (나스닥100)"),
    ("TQQQ", "ProShares UltraPro QQQ (나스닥100 3배)"),
    ("QLD", "ProShares Ultra QQQ (나스닥100 2배)"),
    ("SQQQ", "ProShares UltraPro Short QQQ (나스닥100 -3배)"),
    ("SPY", "SPDR S&P500"),
    ("VOO", "Vanguard S&P500"),
    ("IVV", "iShares Core S&P500"),
    ("SPLG", "SPDR Portfolio S&P500"),
    ("UPRO", "ProShares UltraPro S&P500 (3배)"),
    ("SSO", "ProShares Ultra S&P500 (2배)"),
    ("DIA", "SPDR 다우존스 (Dow Jones)"),
    ("IWM", "iShares 러셀2000 (Russell 2000)"),
    ("VTI", "Vanguard 미국 전체시장 (Total Stock Market)"),
    ("VT", "Vanguard 전세계 주식 (Total World)"),
    ("SCHD", "Schwab 미국 배당 (Dividend Equity)"),
    ("JEPI", "JPMorgan 프리미엄 인컴 (커버드콜)"),
    ("JEPQ", "JPMorgan 나스닥 프리미엄 인컴 (커버드콜)"),
    ("SOXX", "iShares 반도체 (Semiconductor)"),
    ("SMH", "VanEck 반도체 (Semiconductor)"),
    ("SOXL", "Direxion 반도체 3배 (Semiconductor Bull 3X)"),
    ("XLK", "Technology Select Sector (기술주)"),
    ("VGT", "Vanguard 정보기술 (IT)"),
    ("XLE", "Energy Select Sector (에너지)"),
    ("XLF", "Financial Select Sector (금융)"),
    ("ARKK", "ARK Innovation (혁신기업)"),
    ("EFA", "iShares 선진국 (MSCI EAFE)"),
    ("EEM", "iShares 신흥국 (MSCI Emerging Markets)"),
    ("EWY", "iShares 한국 (MSCI South Korea)"),
    ("TLT", "iShares 미국채 20년+ (장기채)"),
    ("IEF", "iShares 미국채 7-10년 (중기채)"),
    ("SHY", "iShares 미국채 1-3년 (단기채)"),
    ("BND", "Vanguard 미국 종합채권 (Total Bond)"),
    ("TMF", "Direxion 미국채 20년+ 3배"),
    ("GLD", "SPDR 금 (Gold)"),
    ("IAU", "iShares 금 (Gold)"),
    ("SLV", "iShares 은 (Silver)"),
    ("USO", "United States Oil (원유)"),
    ("VNQ", "Vanguard 리츠 (REIT)"),
    ("IBIT", "iShares 비트코인 (Bitcoin Trust)"),
]

# 국내 주요 ETF (네이버 ETF 목록을 못 받아올 때를 대비한 기본 목록 · 이름은 바뀌었을 수 있음)
KR_ETFS = [
    ("069500", "KODEX 200"),
    ("102110", "TIGER 200"),
    ("278530", "KODEX 200TR"),
    ("122630", "KODEX 레버리지"),
    ("114800", "KODEX 인버스"),
    ("252670", "KODEX 200선물인버스2X"),
    ("229200", "KODEX 코스닥150"),
    ("233740", "KODEX 코스닥150레버리지"),
    ("251340", "KODEX 코스닥150선물인버스"),
    ("133690", "TIGER 미국나스닥100"),
    ("379810", "KODEX 미국나스닥100TR"),
    ("367380", "ACE 미국나스닥100"),
    ("360750", "TIGER 미국S&P500"),
    ("379800", "KODEX 미국S&P500TR"),
    ("360200", "ACE 미국S&P500"),
    ("381180", "TIGER 미국필라델피아반도체나스닥"),
    ("458730", "TIGER 미국배당다우존스"),
    ("446720", "SOL 미국배당다우존스"),
    ("091160", "KODEX 반도체"),
    ("305720", "KODEX 2차전지산업"),
    ("132030", "KODEX 골드선물(H)"),
    ("261240", "KODEX 미국달러선물"),
]

# 검색어 별칭: 이름에 이 패턴이 있으면 별칭으로도 검색되게
ALIASES = [
    (r"(?<![0-9])200(?![0-9])", "코스피200 kospi200"),
    (r"나스닥|nasdaq|qqq", "나스닥100 nasdaq100 나스닥"),
    (r"s&p|sp500|s&p500", "s&p500 sp500 에스앤피500 에스엔피500"),
    (r"코스닥150", "kosdaq150"),
    (r"다우|dow", "다우존스 dowjones"),
    (r"반도체|semiconductor", "반도체 semiconductor"),
]


def _norm(text: str) -> str:
    """검색용 정규화: 소문자, 공백·기호 제거"""
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def is_foreign(code: str) -> bool:
    """6자리 숫자가 아니면 해외(미국) 종목으로 간주"""
    return not (code.isdigit() and len(code) == 6)


def load_listing():
    """
    검색용 전체 목록 (Code, Name, Market, _key) 과 실패한 항목 설명 목록을 반환.
    국내 주식 / 국내 ETF / 미국 ETF 를 각각 따로 불러와, 일부가 실패해도 나머지는 사용.
    """
    parts, errors = [], []

    # 1) 국내 주식: FinanceDataReader KRX → KOSPI+KOSDAQ → pykrx
    stocks = None
    for markets in (["KRX"], ["KOSPI", "KOSDAQ"]):
        try:
            ps = []
            for m in markets:
                raw = fdr.StockListing(m)
                part = raw[["Code", "Name"]].copy()
                part["Market"] = raw["Market"] if "Market" in raw else m
                ps.append(part)
            stocks = pd.concat(ps)
            break
        except Exception as e:  # noqa: BLE001
            log.warning("FinanceDataReader 종목 목록(%s) 실패: %s", markets, e)
    if stocks is None or stocks.empty:
        try:
            from pykrx import stock

            rows = []
            for market in ("KOSPI", "KOSDAQ"):
                for code in stock.get_market_ticker_list(market=market):
                    rows.append((code, stock.get_market_ticker_name(code), market))
            stocks = pd.DataFrame(rows, columns=["Code", "Name", "Market"])
        except Exception as e:  # noqa: BLE001
            log.warning("pykrx 종목 목록 실패: %s", e)
    if stocks is not None and not stocks.empty:
        parts.append(stocks)
    else:
        errors.append("국내 주식 목록")

    # 2) 국내 ETF (네이버 전체 목록, 실패해도 아래 기본 목록은 항상 포함)
    try:
        raw = fdr.StockListing("ETF/KR")
        etf = raw[["Symbol", "Name"]].rename(columns={"Symbol": "Code"})
        etf["Market"] = "ETF"
        parts.append(etf)
    except Exception as e:  # noqa: BLE001
        log.warning("국내 ETF 목록 실패: %s", e)
        errors.append("국내 ETF 전체 목록(주요 ETF만 검색됨)")
    parts.append(pd.DataFrame([(c, n, "ETF") for c, n in KR_ETFS], columns=["Code", "Name", "Market"]))

    # 3) 미국 ETF (고정 목록)
    parts.append(pd.DataFrame([(c, n, "미국 ETF") for c, n in US_ETFS], columns=["Code", "Name", "Market"]))

    df = pd.concat(parts, ignore_index=True)
    df["Code"] = df["Code"].astype(str)
    kr = ~df["Market"].eq("미국 ETF")
    df.loc[kr, "Code"] = df.loc[kr, "Code"].str.zfill(6)
    df = df.drop_duplicates("Code").reset_index(drop=True)

    def key(row):
        extra = " ".join(a for pat, a in ALIASES if re.search(pat, row.Name, re.I))
        return _norm(f"{row.Name} {row.Code} {extra}")
    df["_key"] = [key(r) for r in df.itertuples()]
    return df, errors


def search_listing(df: pd.DataFrame, q: str, limit: int = 30) -> pd.DataFrame:
    """종목명/코드/별칭 검색. 이름 정확히 일치 → 앞부분 일치 → 포함 순, 같은 순위면 짧은 이름 먼저."""
    q = q.strip()
    if not q:
        return df.iloc[0:0][["Code", "Name", "Market"]]
    nq = _norm(q)
    if not nq:
        return df.iloc[0:0][["Code", "Name", "Market"]]
    code_hit = df["Code"].str.upper().str.startswith(q.upper())
    # 띄어 쓴 단어가 모두 들어 있으면 일치 (예: "tiger 나스" → TIGER 미국나스닥100)
    tokens = [t for t in (_norm(w) for w in q.split()) if t]
    word_hit = pd.Series(True, index=df.index)
    for t in tokens:
        word_hit &= df["_key"].str.contains(t, regex=False)
    hit = df[word_hit | code_hit].copy()
    nn = hit["Name"].map(_norm)
    hit["_r"] = (nn != nq).astype(int) * 2 + (~nn.str.startswith(nq)).astype(int)
    hit.loc[hit["Code"].str.upper() == q.upper(), "_r"] = -1
    hit["_l"] = hit["Name"].str.len()
    out = hit.sort_values(["_r", "_l"])[["Code", "Name", "Market"]].head(limit).reset_index(drop=True)

    # 목록에 없는 코드도 직접 입력 가능하게: 6자리 숫자 또는 영문 티커
    raw = q.upper()
    if raw not in set(out["Code"].str.upper()):
        if (raw.isdigit() and len(raw) == 6) or (raw.isascii() and raw.isalpha() and 1 <= len(raw) <= 5):
            direct = pd.DataFrame([(raw, raw, "코드 직접 입력")], columns=["Code", "Name", "Market"])
            out = pd.concat([out, direct], ignore_index=True)
    return out


def price_note(code: str) -> str | None:
    if is_foreign(code):
        return "미국 종목은 달러 가격을 일별 원/달러 환율로 원화 환산해 계산했습니다 (환율 변동 포함)."
    return None


def _read_prices(code: str, start: str, end: str) -> pd.DataFrame:
    """
    시세 조회. 국내 종목은 네이버(기본) → 야후(.KS / .KQ) 순서로 시도.
    (해외 서버에서 실행되는 Streamlit Cloud 에서는 네이버가 막히는 경우가 있음)
    """
    sources = [code] if is_foreign(code) else [code, f"YAHOO:{code}.KS", f"YAHOO:{code}.KQ"]
    errors = []
    for src in sources:
        try:
            df = fdr.DataReader(src, start, end)
            if df is not None and not df.empty and {"Open", "High", "Low", "Close"} <= set(df.columns):
                if "Volume" not in df:
                    df["Volume"] = 0
                return df
            errors.append(f"{src}: 데이터 없음")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src}: {e}")
            log.warning("시세 조회 실패 %s: %s", src, e)
    raise RuntimeError("시세를 불러오지 못했습니다 → " + " / ".join(errors))


def load_prices(code: str, start: str, end: str) -> pd.DataFrame:
    df = _read_prices(code, start, end)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df = df[(df[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]  # 거래정지일 제거
    if is_foreign(code) and not df.empty:
        try:
            fx = fdr.DataReader("USD/KRW", start, end)["Close"]
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"원/달러 환율을 불러오지 못했습니다: {e}") from e
        fx = fx[fx > 0].reindex(df.index.union(fx.index)).ffill().bfill().reindex(df.index)
        df[["Open", "High", "Low", "Close"]] = df[["Open", "High", "Low", "Close"]].mul(fx, axis=0)
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
