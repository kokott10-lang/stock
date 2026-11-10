"""
주식 백테스트 - Streamlit 버전 (Streamlit Community Cloud 배포용)

로컬 실행:  streamlit run streamlit_app.py
"""
import os
import tempfile
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import backtest_core as core

st.set_page_config(page_title="주식 백테스트", page_icon="📈", layout="wide")


@st.cache_data(ttl=60 * 60 * 24, show_spinner="종목 목록 불러오는 중…")
def get_listing():
    return core.load_listing()


@st.cache_data(ttl=60 * 60, show_spinner="시세 불러오는 중…")
def get_prices(code, start, end):
    return core.load_prices(code, start, end)


def trade_markers(curve: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """거래 내역 → 자산 곡선 위에 찍을 매수/매도 점 (같은 날 여러 건은 하나로 합침)"""
    rows = []
    for _, t in trades.iterrows():
        rows.append(("매수", t["매수일"], t["매수가"], t["수량"], None))
        if isinstance(t["매도일"], str):
            rows.append(("매도", t["매도일"], t["매도가"], t["수량"], t["수익률(%)"]))
    if not rows:
        return pd.DataFrame(columns=["날짜", "구분", "자산", "가격", "수량", "수익률(%)"])
    m = pd.DataFrame(rows, columns=["구분", "날짜", "가격", "수량", "수익률(%)"])
    m["날짜"] = pd.to_datetime(m["날짜"])
    m = (m.groupby(["날짜", "구분"], as_index=False)
          .agg(가격=("가격", "mean"), 수량=("수량", "sum"), **{"수익률(%)": ("수익률(%)", "mean")}))
    eq = curve["Equity"]
    m["자산"] = eq.reindex(m["날짜"], method="ffill").values
    return m


def equity_chart(curve: pd.DataFrame, trades: pd.DataFrame) -> alt.Chart:
    line_df = (curve[["Equity", "BuyHold"]]
               .rename(columns={"Equity": "전략 자산", "BuyHold": "단순 보유"})
               .rename_axis("날짜").reset_index()
               .melt("날짜", var_name="구분", value_name="자산"))
    lines = alt.Chart(line_df).mark_line(strokeWidth=1.8).encode(
        x=alt.X("날짜:T", title=None),
        y=alt.Y("자산:Q", title="자산(원)", axis=alt.Axis(format=",.0f"), scale=alt.Scale(zero=False)),
        color=alt.Color("구분:N", title=None,
                        scale=alt.Scale(domain=["전략 자산", "단순 보유"], range=["#2f6fed", "#9aa3ad"]),
                        legend=alt.Legend(orient="top")),
        strokeDash=alt.StrokeDash("구분:N", legend=None,
                                  scale=alt.Scale(domain=["전략 자산", "단순 보유"], range=[[1, 0], [4, 3]])),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N",
                 alt.Tooltip("자산:Q", format=",.0f")],
    )
    m = trade_markers(curve, trades)
    if m.empty:
        return lines.properties(height=380)
    pts = alt.Chart(m).mark_point(filled=True, size=110, opacity=0.95, stroke="white", strokeWidth=0.8).encode(
        x="날짜:T",
        y="자산:Q",
        shape=alt.Shape("구분:N", scale=alt.Scale(domain=["매수", "매도"],
                                                  range=["triangle-up", "triangle-down"]), legend=None),
        fill=alt.Fill("구분:N", scale=alt.Scale(domain=["매수", "매도"], range=["#d63b3b", "#2463d1"]),
                      legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N",
                 alt.Tooltip("가격:Q", format=",.0f", title="가격(원)"), alt.Tooltip("수량:Q", title="수량(주)"),
                 alt.Tooltip("수익률(%):Q", format="+.2f")],
    )
    return alt.layer(lines, pts).resolve_scale(color="independent").properties(height=380).interactive(bind_y=False)


def fmt_pct(v):
    return "-" if v is None else f"{v:+.2f}%"


# ---------------------------------------------------------------------------
# 사이드바: 설정
# ---------------------------------------------------------------------------
st.sidebar.header("설정")

listing, listing_errs = None, []
try:
    listing, listing_errs = get_listing()
except Exception as e:  # noqa: BLE001
    listing_errs = [str(e)]

code, name = None, None
q = st.sidebar.text_input("종목 · ETF 검색", placeholder="예: 현대차, 005380, 나스닥100, S&P500, QQQ")
if listing_errs:
    st.sidebar.caption("⚠️ 불러오지 못한 목록: " + ", ".join(listing_errs) + " — 종목코드(6자리)나 티커를 직접 입력하면 됩니다.")
if q:
    hits = core.search_listing(listing, q) if listing is not None else core.search_listing(
        pd.DataFrame(columns=["Code", "Name", "Market", "_key"]), q)
    if hits.empty:
        st.sidebar.caption("검색 결과가 없습니다.")
    else:
        labels = [f"{r.Name}  ({r.Code} · {r.Market})" for r in hits.itertuples()]
        i = st.sidebar.selectbox("검색 결과", range(len(labels)), format_func=lambda k: labels[k])
        code, name = hits.loc[i, "Code"], hits.loc[i, "Name"]

today = date.today()
start = st.sidebar.date_input("시작일", today - timedelta(days=365 * 3), max_value=today)
end = st.sidebar.date_input("종료일", today, max_value=today)
c1, c2 = st.sidebar.columns(2)
cash = c1.number_input("초기 자금(원)", min_value=100_000, value=10_000_000, step=1_000_000)
commission = c2.number_input("거래비용(편도 %)", min_value=0.0, value=0.1, step=0.01, format="%.2f")

skey = st.sidebar.selectbox("전략", list(core.STRATEGIES), format_func=lambda k: core.STRATEGIES[k]["name"])
info = core.STRATEGIES[skey]
st.sidebar.caption(info["desc"])
params = {}
cols = st.sidebar.columns(2)
for j, (k, label, default) in enumerate(info["params"]):
    params[k] = cols[j % 2].number_input(label, value=float(default), format="%g", key=f"{skey}_{k}")

run = st.sidebar.button("백테스트 실행", type="primary", use_container_width=True, disabled=code is None)

# ---------------------------------------------------------------------------
# 본문
# ---------------------------------------------------------------------------
st.title("📈 주식 백테스트")
st.caption("FinanceDataReader 시세 + backtesting.py 엔진 · 과거 성과가 미래 수익을 보장하지 않습니다.")

if run:
    if start >= end:
        st.error("시작일이 종료일보다 앞이어야 합니다.")
    else:
        try:
            df = get_prices(code, start.isoformat(), end.isoformat())
            plot_path = os.path.join(tempfile.gettempdir(), f"bt_{code}_{skey}.html")
            res = core.run_backtest(df, skey, float(cash), float(commission), params, plot_path)
            plot_html = None
            if res["plot"]:
                with open(res["plot"], encoding="utf-8") as f:
                    plot_html = f.read()
            st.session_state["result"] = dict(res=res, name=name, code=code, strategy=info["name"],
                                              bars=len(df), plot_html=plot_html, note=core.price_note(code))
        except Exception as e:  # noqa: BLE001
            st.error(f"실행 실패: {e}")

r = st.session_state.get("result")
if not r:
    st.info("왼쪽에서 종목이나 ETF를 검색해 선택하고 **백테스트 실행**을 누르세요. (모바일은 왼쪽 위 ☰ 메뉴)")
    st.stop()

s = r["res"]["summary"]
st.subheader(f"{r['name']} ({r['code']}) · {r['strategy']}")
st.caption(f"{s.get('Start')} ~ {s.get('End')} · {r['bars']}거래일")
if r.get("note"):
    st.info(r["note"])

k = st.columns(4)
k[0].metric("전략 수익률", fmt_pct(s.get("Return [%]")))
k[1].metric("단순 보유 수익률", fmt_pct(s.get("Buy & Hold Return [%]")))
k[2].metric("연환산 수익률", fmt_pct(s.get("Return (Ann.) [%]")))
k[3].metric("최대 낙폭 (MDD)", fmt_pct(s.get("Max. Drawdown [%]")))
k = st.columns(4)
k[0].metric("최종 자산(원)", f"{s.get('Equity Final [$]') or 0:,.0f}")
k[1].metric("거래 횟수", f"{int(s.get('# Trades') or 0)}")
wr, sh = s.get("Win Rate [%]"), s.get("Sharpe Ratio")
k[2].metric("승률", "-" if wr is None else f"{wr:.1f}%")
k[3].metric("샤프 지수", "-" if sh is None else f"{sh:.2f}")

trades = r["res"]["trades"]

st.markdown("#### 자산 곡선")
st.altair_chart(equity_chart(r["res"]["curve"], trades), use_container_width=True)
st.caption("▲ 빨강 = 매수, ▼ 파랑 = 매도 · 점에 마우스를 올리면 거래 내용이 보입니다.")

st.markdown(f"#### 거래 내역 ({len(trades)}건)")
st.dataframe(
    trades,
    hide_index=True,
    use_container_width=True,
    column_config={
        "매수가": st.column_config.NumberColumn(format="%,d"),
        "매도가": st.column_config.NumberColumn(format="%,d"),
        "손익(원)": st.column_config.NumberColumn(format="%,d"),
        "수익률(%)": st.column_config.NumberColumn(format="%.2f"),
    },
)

if r["plot_html"]:
    with st.expander("backtesting.py 상세 차트 (캔들 · 매매 시점 · 지표)"):
        components.html(r["plot_html"], height=820, scrolling=True)
