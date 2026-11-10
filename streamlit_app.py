"""
주식 백테스트 - Streamlit 버전 (Streamlit Community Cloud 배포용)

로컬 실행:  streamlit run streamlit_app.py
"""
import os
import tempfile
from datetime import date, timedelta

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


def fmt_pct(v):
    return "-" if v is None else f"{v:+.2f}%"


# ---------------------------------------------------------------------------
# 사이드바: 설정
# ---------------------------------------------------------------------------
st.sidebar.header("설정")

listing, listing_err = None, None
try:
    listing = get_listing()
except Exception as e:  # noqa: BLE001
    listing_err = str(e)

code, name = None, None
if listing is not None:
    q = st.sidebar.text_input("종목 검색", placeholder="종목명 또는 코드 (예: 현대차, 005380)")
    if q:
        hits = core.search_listing(listing, q)
        if hits.empty:
            st.sidebar.caption("검색 결과가 없습니다.")
        else:
            labels = [f"{r.Name}  ({r.Code} · {r.Market})" for r in hits.itertuples()]
            i = st.sidebar.selectbox("검색 결과", range(len(labels)), format_func=lambda k: labels[k])
            code, name = hits.loc[i, "Code"], hits.loc[i, "Name"]
else:
    st.sidebar.warning("종목 목록을 불러오지 못했습니다. 6자리 종목코드를 직접 입력하세요.")
    with st.sidebar.expander("오류 내용"):
        st.code(listing_err)
    raw = st.sidebar.text_input("종목코드", placeholder="005380")
    if raw.strip().isdigit() and len(raw.strip()) == 6:
        code = name = raw.strip()

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
                                              bars=len(df), plot_html=plot_html)
        except Exception as e:  # noqa: BLE001
            st.error(f"실행 실패: {e}")

r = st.session_state.get("result")
if not r:
    st.info("왼쪽에서 종목을 검색해 선택하고 **백테스트 실행**을 누르세요. (모바일은 왼쪽 위 ☰ 메뉴)")
    st.stop()

s = r["res"]["summary"]
st.subheader(f"{r['name']} ({r['code']}) · {r['strategy']}")
st.caption(f"{s.get('Start')} ~ {s.get('End')} · {r['bars']}거래일")

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

st.markdown("#### 자산 곡선")
curve = r["res"]["curve"][["Equity", "BuyHold"]].rename(columns={"Equity": "전략 자산", "BuyHold": "단순 보유"})
st.line_chart(curve, color=["#2f6fed", "#9aa3ad"])

trades = r["res"]["trades"]
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
