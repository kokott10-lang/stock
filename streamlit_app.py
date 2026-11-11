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

import importlib

import backtest_core as core

# Streamlit Cloud 는 새 코드를 받으면 이 파일만 다시 읽고, 이미 불러온 backtest_core 는
# 예전 것을 계속 쓰는 경우가 있다 → 파일이 바뀌었으면 다시 불러온다.
_core_mtime = os.path.getmtime(core.__file__)
if getattr(core, "_LOADED_MTIME", None) != _core_mtime:
    core = importlib.reload(core)
    core._LOADED_MTIME = _core_mtime

try:
    from streamlit_searchbox import st_searchbox
except ImportError:  # requirements.txt 의 streamlit-searchbox 가 없을 때
    st_searchbox = None

st.set_page_config(page_title="주식 백테스트", page_icon="📈", layout="wide")
# 사이드바를 조금 넓혀 포트폴리오 구성표(이름·코드·비중)가 한눈에 들어오게
st.markdown("<style>section[data-testid='stSidebar']{min-width:400px;}</style>", unsafe_allow_html=True)


@st.cache_data(ttl=60 * 60 * 24, show_spinner="종목 목록 불러오는 중…")
def get_listing(version: str):
    # version 이 바뀌면(지수·ETF 목록 수정 시) 캐시를 새로 만든다
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


def crisis_frame(curve: pd.DataFrame) -> pd.DataFrame:
    """차트 기간과 겹치는 급락 구간 (고점~저점)"""
    lo, hi = curve.index.min(), curve.index.max()
    rows = []
    for name_, peak, trough, *_ in core.CRISES:
        a, b = pd.Timestamp(peak), pd.Timestamp(trough)
        if b >= lo and a <= hi:
            rows.append({"위기": name_, "시작": max(a, lo), "끝": min(b, hi),
                         "기간": f"{peak} ~ {trough}"})
    return pd.DataFrame(rows)


def crisis_layer(curve: pd.DataFrame):
    cf = crisis_frame(curve)
    if cf.empty:
        return None
    return alt.Chart(cf).mark_rect(color="#e8590c", opacity=0.12).encode(
        x="시작:T", x2="끝:T", tooltip=["위기:N", alt.Tooltip("기간:N", title="고점 ~ 저점")])


def equity_chart(curve: pd.DataFrame, trades: pd.DataFrame, log: bool = False,
                 crises: bool = True) -> alt.Chart:
    line_df = (curve[["Equity", "BuyHold"]]
               .rename(columns={"Equity": "전략 자산", "BuyHold": "단순 보유"})
               .rename_axis("날짜").reset_index()
               .melt("날짜", var_name="구분", value_name="자산"))
    lines = alt.Chart(line_df).mark_line(strokeWidth=1.8).encode(
        x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%Y-%m")),
        y=alt.Y("자산:Q", title="자산(원)", axis=alt.Axis(format=",.0f"),
                scale=alt.Scale(type="log", nice=False) if log else alt.Scale(zero=False)),
        color=alt.Color("구분:N", title=None,
                        scale=alt.Scale(domain=["전략 자산", "단순 보유"], range=["#2f6fed", "#9aa3ad"]),
                        legend=alt.Legend(orient="top")),
        strokeDash=alt.StrokeDash("구분:N", legend=None,
                                  scale=alt.Scale(domain=["전략 자산", "단순 보유"], range=[[1, 0], [4, 3]])),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N",
                 alt.Tooltip("자산:Q", format=",.0f")],
    )
    band = crisis_layer(curve) if crises else None
    if band is not None:
        lines = alt.layer(band, lines)
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


def fmt_krw(v, sign=False):
    """큰 금액을 억/만 단위로 (예: 3.06억, 4,500만)"""
    if v is None:
        return "-"
    sg = ("+" if v > 0 else "-" if v < 0 else "") if sign else ("-" if v < 0 else "")
    a = abs(v)
    if a >= 1e8:
        return f"{sg}{a / 1e8:,.2f}억"
    if a >= 1e4:
        return f"{sg}{a / 1e4:,.0f}만"
    return f"{sg}{a:,.0f}"


def render_dca(r):
    res, s_ = r["res"], r["res"]["summary"]
    cv = res["curve"]
    st.subheader(f"{r['name']} ({r['code']}) · {r['strategy']}")
    st.caption(f"{s_['시작']} ~ {s_['끝']} · 매수 {s_['매수 횟수']}회 · 매도 없음(마지막 날 평가금액 기준)")
    if r.get("period") and tuple(r["period"]) != (start, end):
        st.warning(f"사이드바 기간({start} ~ {end})이 이 결과와 다릅니다. **백테스트 실행**을 누르면 새 기간으로 다시 계산합니다.")
    if r.get("note"):
        st.info(r["note"])

    k = st.columns(4)
    k[0].metric("총 투자원금", fmt_krw(s_["총 투자원금"]), help=f"{s_['총 투자원금']:,.0f}원")
    k[1].metric("최종 평가금액", fmt_krw(s_["최종 평가금액"]), help=f"{s_['최종 평가금액']:,.0f}원")
    k[2].metric("총 손익", fmt_krw(s_["총 손익"], sign=True), help=f"{s_['총 손익']:+,.0f}원")
    k[3].metric("누적 수익률", fmt_pct(s_["누적 수익률(%)"]))
    k = st.columns(4)
    k[0].metric("연환산 수익률 (XIRR)", fmt_pct(s_["연환산 수익률(%)"]),
                help="돈이 들어간 시점까지 반영한 연평균 수익률")
    diff = s_["거치식 수익률(%)"] - s_["누적 수익률(%)"]
    k[1].metric("같은 총액 거치식이었다면", fmt_pct(s_["거치식 수익률(%)"]),
                delta=f"적립식보다 {diff:+.1f}%p", delta_color="normal",
                help="같은 총액을 첫날 한 번에 투자했다면의 누적 수익률")
    k[2].metric("최저 평가손익률", fmt_pct(s_["최저 평가손익률(%)"]), help=f"{s_['최저 평가손익일']} 기준, 원금 대비")
    k[3].metric("평균단가(원)", f"{s_['평균단가']:,.0f}",
                delta=f"현재가 {s_['마지막 종가']:,.0f} ({(s_['마지막 종가'] / s_['평균단가'] - 1) * 100:+.0f}%)")

    st.markdown("#### 원금과 평가금액")
    tc = st.columns(2)
    show_lump = tc[0].toggle("같은 총액 거치식 함께 보기", value=False)
    dlog = tc[1].toggle("로그 스케일", value=r["bars"] > 252 * 5, key="dca_log")
    yscale = alt.Scale(type="log", nice=False) if dlog else alt.Scale()
    cols_ = ["원금", "평가금액"] + (["거치식"] if show_lump else [])
    ld = cv[cols_].rename_axis("날짜").reset_index().melt("날짜", var_name="구분", value_name="금액")
    palette = {"원금": "#9aa3ad", "평가금액": "#2f6fed", "거치식": "#e8590c"}
    dashes = {"원금": [4, 3], "평가금액": [1, 0], "거치식": [2, 2]}
    sc = alt.Scale(domain=cols_, range=[palette[c] for c in cols_])
    lines = alt.Chart(ld).mark_line(strokeWidth=1.8).encode(
        x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%Y-%m")),
        y=alt.Y("금액:Q", title="금액(원)", axis=alt.Axis(format=",.0f"), scale=yscale),
        color=alt.Color("구분:N", scale=sc, legend=alt.Legend(orient="top", title=None)),
        strokeDash=alt.StrokeDash("구분:N", legend=None,
                                  scale=alt.Scale(domain=cols_, range=[dashes[c] for c in cols_])),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N", alt.Tooltip("금액:Q", format=",.0f")],
    )
    band = crisis_layer(cv.rename(columns={"평가금액": "Equity"}))
    chart = alt.layer(band, lines) if band is not None else lines
    st.altair_chart(chart.properties(height=360).interactive(bind_y=False), use_container_width=True)
    st.caption("회색 점선 = 넣은 돈(원금), 파랑 = 평가금액 · 주황 음영 = 역사적 급락 구간")

    st.markdown("#### 평균단가와 주가")
    ad = cv[["평균단가", "종가"]].dropna().rename_axis("날짜").reset_index().melt(
        "날짜", var_name="구분", value_name="가격")
    st.altair_chart(alt.Chart(ad).mark_line(strokeWidth=1.6).encode(
        x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%Y-%m")),
        y=alt.Y("가격:Q", title="가격(원)", axis=alt.Axis(format=",.0f"),
                scale=alt.Scale(type="log", nice=False) if dlog else alt.Scale(zero=False)),
        color=alt.Color("구분:N", scale=alt.Scale(domain=["종가", "평균단가"], range=["#9aa3ad", "#d63b3b"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N", alt.Tooltip("가격:Q", format=",.0f")],
    ).properties(height=280), use_container_width=True)
    st.caption("주가가 빨간 평균단가선 아래에 있는 구간은 평가손실 상태입니다. 하락장에서 꾸준히 사면 평균단가가 내려갑니다.")

    with st.expander(f"매수 내역 ({len(res['buys'])}회)"):
        st.dataframe(res["buys"], hide_index=True, use_container_width=True,
                     column_config={c: st.column_config.NumberColumn(format="%,d")
                                    for c in ["매수가", "평균단가", "누적 원금", "평가금액", "이월 현금"]})

    # 적립 시작 시점별 분석
    st.markdown("#### 적립 시작 시점별 분석")
    st.caption("기간 안의 매 시작 시점마다 같은 금액을 N년간 적립했다면 어땠는지, "
               "같은 기간 처음에 한 번에 넣은 거치식과 비교합니다. (비용 제외, 소수 단위 근사)")
    close = cv["종가"]
    rows, frames = [], {}
    for yrs in (1, 3, 5, 10, 20):
        rd = core.rolling_dca(close, yrs, r.get("freq", "M"))
        if len(rd) < 3:
            continue
        frames[f"{yrs}년"] = rd
        for col in ("적립식", "거치식"):
            v = rd[col]
            rows.append({"적립 기간": f"{yrs}년", "구분": col, "중앙값(%)": v.median(), "최저(%)": v.min(),
                         "최고(%)": v.max(), "플러스 확률(%)": (v > 0).mean() * 100, "표본 수": len(v)})
    if not rows:
        st.info("기간이 너무 짧습니다. 시작일을 앞당기면(예: 기간 바로가기 → 최근 10년) 분석할 수 있습니다.")
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 column_config={c: st.column_config.NumberColumn(format="%.1f")
                                for c in ["중앙값(%)", "최저(%)", "최고(%)", "플러스 확률(%)"]})
    h = st.selectbox("그래프로 볼 적립 기간", list(frames), index=min(1, len(frames) - 1))
    rd = frames[h]
    long = rd.rename_axis("시작").reset_index().melt("시작", var_name="구분", value_name="수익률(%)")
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#999", strokeDash=[3, 3]).encode(
        y=alt.Y("y:Q", title="수익률(%)"))
    st.altair_chart((alt.Chart(long).mark_line(strokeWidth=1.6).encode(
        x=alt.X("시작:T", title="적립 시작", axis=alt.Axis(format="%Y-%m")),
        y=alt.Y("수익률(%):Q", title="수익률(%)"),
        color=alt.Color("구분:N", scale=alt.Scale(domain=["적립식", "거치식"], range=["#2f6fed", "#e8590c"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("시작:T", format="%Y-%m"), "구분:N", alt.Tooltip("수익률(%):Q", format="+.1f")],
    ) + zero).properties(height=300), use_container_width=True)
    c = st.columns(2)
    c[0].metric(f"{h} 적립식이 거치식보다 나았던 비율", f"{(rd['적립식'] > rd['거치식']).mean() * 100:.0f}%")
    c[1].metric(f"{h} 적립식 손실 확률", f"{(rd['적립식'] < 0).mean() * 100:.0f}%")


def render_portfolio(r):
    res, s_ = r["res"], r["res"]["summary"]
    names = r["names"]
    label = lambda c: f"{names.get(c, c)}"  # noqa: E731
    w = res["weights"]
    st.subheader(f"포트폴리오 · 리밸런싱 {core.REBALANCE[r['reb']]}"
                 + (f" · {'매월' if r.get('cfreq') == 'M' else '매년'} {r['contrib']:,.0f}원 적립"
                    if r["contrib"] else ""))
    st.caption(" · ".join(f"{label(c)} {v * 100:.0f}%" for c, v in w.items()))
    st.caption(f"{s_['시작']} ~ {s_['끝']}")
    if r.get("period") and tuple(r["period"]) != (start, end):
        st.warning(f"사이드바 기간({start} ~ {end})이 이 결과와 다릅니다. **백테스트 실행**을 누르면 새 기간으로 다시 계산합니다.")
    if r.get("note"):
        st.info(r["note"])

    k = st.columns(4)
    k[0].metric("최종 평가금액", fmt_krw(s_["최종 평가금액"]), help=f"{s_['최종 평가금액']:,.0f}원")
    k[1].metric("총 손익", fmt_krw(s_["총 손익"], sign=True),
                delta=f"원금 {fmt_krw(s_['총 투자원금'])} 대비 {s_['원금 대비 수익률(%)']:+.1f}%", delta_color="off")
    k[2].metric("연환산 수익률", fmt_pct(s_["연환산 수익률(%)"]),
                help="적립이 있으면 돈이 들어간 시점까지 반영한 XIRR, 없으면 CAGR")
    k[3].metric("최대 낙폭 (MDD)", fmt_pct(s_["최대 낙폭(%)"]), help=f"{s_['최대 낙폭일']} 기준")
    k = st.columns(4)
    k[0].metric("변동성 (연)", f"{s_['변동성(%)']:.1f}%")
    k[1].metric("샤프 지수", "-" if s_["샤프"] is None else f"{s_['샤프']:.2f}")
    k[2].metric("최악의 해", fmt_pct(s_["최악의 해(%)"]), help=f"{s_['최악의 해']}년")
    k[3].metric("리밸런싱 / 거래비용", f"{s_['리밸런싱 횟수']:.0f}회 / {fmt_krw(s_['거래비용 합계'])}")

    cv = res["curve"]
    st.markdown("#### 성장 곡선 (시작 = 100)")
    tc = st.columns(3)
    show_assets = tc[0].toggle("개별 자산도 보기", value=False)
    plog = tc[1].toggle("로그 스케일", value=len(cv) > 252 * 5, key="pf_log")
    show_cr = tc[2].toggle("급락 구간 표시", value=True, key="pf_cr")
    g = pd.DataFrame({f"포트폴리오 (리밸런싱 {core.REBALANCE[r['reb']]})": cv["NAV"] * 100})
    if r["reb"] != "N":
        g["같은 비중 · 리밸런싱 안 함"] = cv["리밸런싱 안 함"] * 100
    if show_assets:
        for c in res["asset_nav"].columns:
            g[label(c)] = res["asset_nav"][c] * 100
    gl = g.rename_axis("날짜").reset_index().melt("날짜", var_name="구분", value_name="지수")
    order = list(g.columns)
    palette = ["#2f6fed", "#e8590c", "#9aa3ad", "#2b8a3e", "#ae3ec9", "#f59f00", "#1098ad", "#c92a2a", "#5c940d"]
    lines = alt.Chart(gl).mark_line(strokeWidth=1.6).encode(
        x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%Y-%m")),
        y=alt.Y("지수:Q", title="시작 = 100",
                scale=alt.Scale(type="log", nice=False) if plog else alt.Scale(zero=False)),
        color=alt.Color("구분:N", sort=order, scale=alt.Scale(domain=order, range=palette[:len(order)]),
                        legend=alt.Legend(orient="top", title=None, columns=3, labelLimit=320)),
        size=alt.condition(alt.datum["구분"] == order[0], alt.value(2.6), alt.value(1.2)),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N", alt.Tooltip("지수:Q", format=",.1f")],
    )
    band = crisis_layer(cv.rename(columns={"NAV": "Equity"})) if show_cr else None
    st.altair_chart((alt.layer(band, lines) if band is not None else lines)
                    .properties(height=380).interactive(bind_y=False), use_container_width=True)
    st.caption("적립금 효과를 뺀 '운용 성과'만 비교한 곡선입니다(시간가중). 주황 음영 = 역사적 급락 구간.")

    st.markdown("#### 낙폭 (고점 대비 하락률)")
    ddf = pd.DataFrame({"포트폴리오": core._drawdown(cv["NAV"]),
                        "리밸런싱 안 함": core._drawdown(cv["리밸런싱 안 함"])})
    if r["reb"] == "N":
        ddf = ddf[["포트폴리오"]]
    ddl = ddf.rename_axis("날짜").reset_index().melt("날짜", var_name="구분", value_name="낙폭(%)")
    st.altair_chart(alt.Chart(ddl).mark_area(opacity=0.35, line=True).encode(
        x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%Y-%m")),
        y=alt.Y("낙폭(%):Q", stack=None),
        color=alt.Color("구분:N", scale=alt.Scale(domain=["포트폴리오", "리밸런싱 안 함"],
                                                  range=["#2f6fed", "#e8590c"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N", alt.Tooltip("낙폭(%):Q", format=".1f")],
    ).properties(height=220), use_container_width=True)

    if r["contrib"]:
        st.markdown("#### 원금과 평가금액")
        ad = cv[["원금", "평가금액"]].rename_axis("날짜").reset_index().melt("날짜", var_name="구분", value_name="금액")
        st.altair_chart(alt.Chart(ad).mark_line().encode(
            x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%Y-%m")),
            y=alt.Y("금액:Q", axis=alt.Axis(format=",.0f")),
            color=alt.Color("구분:N", scale=alt.Scale(domain=["원금", "평가금액"], range=["#9aa3ad", "#2f6fed"]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N", alt.Tooltip("금액:Q", format=",.0f")],
        ).properties(height=260), use_container_width=True)

    st.markdown("#### 비교")
    cmp_ = res["compare"].copy()
    cmp_["구분"] = [x if "단독" not in x else f"{label(x.replace(' 단독 보유', ''))} 단독 보유" for x in cmp_["구분"]]
    st.dataframe(cmp_, hide_index=True, use_container_width=True,
                 column_config={c: st.column_config.NumberColumn(format="%.2f") for c in cmp_.columns if c != "구분"})
    st.caption("같은 기간 각 자산만 들고 있었을 때와 비교합니다. 리밸런싱의 효과는 보통 '수익률은 비슷하거나 약간 낮아도 낙폭·변동성이 줄어드는' 형태로 나타납니다.")

    tab1, tab2, tab3 = st.tabs(["연도별 수익률", "비중 변화", "급락 구간별"])
    with tab1:
        y = res["yearly"].rename(columns={c: label(c) for c in w.index})
        yl = y.melt("연도", value_vars=["포트폴리오", "리밸런싱 안 함"], var_name="구분", value_name="수익률(%)")
        st.altair_chart(alt.Chart(yl).mark_bar().encode(
            x=alt.X("연도:O", title=None, axis=alt.Axis(labelAngle=0)),
            xOffset=alt.XOffset("구분:N", sort=["포트폴리오", "리밸런싱 안 함"]),
            y="수익률(%):Q",
            color=alt.Color("구분:N", scale=alt.Scale(domain=["포트폴리오", "리밸런싱 안 함"],
                                                      range=["#2f6fed", "#e8590c"]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["연도:O", "구분:N", alt.Tooltip("수익률(%):Q", format="+.1f")],
        ).properties(height=280), use_container_width=True)
        st.dataframe(y, hide_index=True, use_container_width=True,
                     column_config={c: st.column_config.NumberColumn(format="%+.1f") for c in y.columns if c != "연도"})
    with tab2:
        parts = res["parts"].div(res["parts"].sum(axis=1), axis=0) * 100
        parts = parts.rename(columns={c: label(c) for c in parts.columns})
        pm = parts.resample("ME").last() if len(parts) > 400 else parts
        pl = pm.rename_axis("날짜").reset_index().melt("날짜", var_name="자산", value_name="비중(%)")
        st.altair_chart(alt.Chart(pl).mark_area().encode(
            x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%Y-%m")),
            y=alt.Y("비중(%):Q", stack="zero", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("자산:N", legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("날짜:T", format="%Y-%m"), "자산:N", alt.Tooltip("비중(%):Q", format=".1f")],
        ).properties(height=280), use_container_width=True)
        st.caption("리밸런싱 사이에 오른 자산의 비중이 커졌다가, 리밸런싱 날 목표 비중으로 돌아갑니다.")
    with tab3:
        rows = []
        series = {"포트폴리오": cv["NAV"], "리밸런싱 안 함": cv["리밸런싱 안 함"]}
        series.update({label(c): res["asset_nav"][c] for c in res["asset_nav"].columns if c != core.CASH})
        for _, c in crisis_frame(cv).iterrows():
            row = {"위기": c["위기"], "기간": c["기간"]}
            for nm, sr in series.items():
                seg = sr.loc[c["시작"]:c["끝"]]
                row[nm] = (seg.min() / seg.iloc[0] - 1) * 100 if len(seg) > 1 else None
            rows.append(row)
        if rows:
            st.caption("급락 구간(고점~저점) 동안 각 구성의 최대 하락률(%)입니다.")
            cr = pd.DataFrame(rows)
            st.dataframe(cr, hide_index=True, use_container_width=True,
                         column_config={c: st.column_config.NumberColumn(format="%.1f")
                                        for c in cr.columns if c not in ("위기", "기간")})
        else:
            st.info("이 기간에는 표시할 급락 구간이 없습니다.")


def fmt_pct(v):
    return "-" if v is None else f"{v:+.2f}%"


# ---------------------------------------------------------------------------
# 사이드바: 설정
# ---------------------------------------------------------------------------
st.sidebar.header("설정")

listing, listing_errs = None, []
try:
    listing, listing_errs = get_listing(core.listing_version())
except Exception as e:  # noqa: BLE001
    listing_errs = [str(e)]

EMPTY = pd.DataFrame(columns=["Code", "Name", "Market", "_key"])


def asset_name(c: str) -> str:
    """코드 → 보기 좋은 이름 (예: SPY → SPDR S&P500)"""
    c = str(c).strip()
    if c.upper() == core.CASH:
        return "현금"
    if c.upper() in core.INDICES:
        return core.INDICES[c.upper()][0]
    if listing is not None:
        hit = listing.loc[listing["Code"].str.upper() == c.upper(), "Name"]
        if not hit.empty:
            return hit.iloc[0]
    return c


def search_options(term: str):
    """입력할 때마다 호출되는 자동완성 후보: (보여줄 이름, (코드, 이름))"""
    if not term or not term.strip():
        return []
    hits = core.search_listing(listing if listing is not None else EMPTY, term, limit=15)
    return [(f"{r.Name}  ·  {r.Code}  ·  {r.Market}", (r.Code, r.Name)) for r in hits.itertuples()]


code, name = None, None
with st.sidebar:
    if st_searchbox is not None:
        picked = st_searchbox(
            search_options,
            label="종목 · ETF 검색",
            placeholder="예: 현대차, 나스닥100, S&P500, 코스피 지수, QQQ",
            key="stock_search",
            debounce=200,
        )
    else:  # 부품이 없으면 일반 입력창 + 목록으로 대체
        q = st.text_input("종목 · ETF 검색", placeholder="예: 현대차, 나스닥100, S&P500, 코스피 지수, QQQ")
        opts = search_options(q)
        picked = None
        if opts:
            i = st.selectbox("검색 결과", range(len(opts)), format_func=lambda k: opts[k][0])
            picked = opts[i][1]
        elif q:
            st.caption("검색 결과가 없습니다.")
    if picked:
        code, name = picked
        st.success(f"선택: {name} ({code})")
    if listing_errs:
        st.caption("⚠️ 불러오지 못한 목록: " + ", ".join(listing_errs)
                   + " — 6자리 종목코드나 미국 티커를 직접 입력해도 됩니다.")
    if listing is not None:
        n = listing["Market"].value_counts()
        st.caption(f"검색 가능: 국내 주식 {n.drop(['ETF', '미국 ETF', '지수'], errors='ignore').sum():,} · "
                   f"ETF {n.get('ETF', 0):,} · 미국 ETF {n.get('미국 ETF', 0)} · 지수 {n.get('지수', 0)}")
    if st.button("🔄 종목 목록 새로고침", help="검색이 이상하면 눌러서 목록을 다시 불러오세요."):
        get_listing.clear()
        st.rerun()

today = date.today()
MIN_DATE = date(1990, 1, 1)

# 기간 바로가기: 고르면 시작일/종료일이 자동으로 바뀜
PRESETS = {
    "최근 3년": (today - timedelta(days=365 * 3), today),
    "최근 5년": (today - timedelta(days=365 * 5), today),
    "최근 10년": (today - timedelta(days=365 * 10), today),
    "2000년부터": (date(2000, 1, 1), today),
    "가능한 전체 (1990년~)": (MIN_DATE, today),
}
for name_, _peak, _trough, s_, e_ in core.CRISES:
    PRESETS[f"📉 {name_} ({_peak[:4]})"] = (date.fromisoformat(s_), min(date.fromisoformat(e_), today))

if "start_date" not in st.session_state:
    st.session_state["start_date"], st.session_state["end_date"] = PRESETS["최근 3년"]


def apply_preset():
    """바로가기 선택 → 시작일/종료일 채우고, 결과가 이미 있으면 새 기간으로 자동 재실행"""
    key = st.session_state.get("preset")
    if key in PRESETS:
        st.session_state["start_date"], st.session_state["end_date"] = PRESETS[key]
        if st.session_state.get("result"):
            st.session_state["auto_run"] = True


def on_manual_date():
    """날짜를 직접 바꾸면 바로가기 표시를 '직접 선택'으로 (같은 바로가기를 다시 고를 수 있게)"""
    st.session_state["preset"] = "직접 선택"


st.sidebar.selectbox("기간 바로가기", ["직접 선택"] + list(PRESETS), key="preset", on_change=apply_preset,
                     help="고르면 시작일·종료일이 자동으로 바뀝니다. 위기 구간은 위기 전부터 회복기까지 들어갑니다.")
start = st.sidebar.date_input("시작일", key="start_date", min_value=MIN_DATE, max_value=today,
                              on_change=on_manual_date)
end = st.sidebar.date_input("종료일", key="end_date", min_value=MIN_DATE, max_value=today,
                            on_change=on_manual_date)
mode = st.sidebar.radio("투자 방식", ["거치식", "적립식", "포트폴리오"], horizontal=True,
                        help="거치식: 한 종목에 처음 한 번 투자 · 적립식: 매월/매년 같은 금액을 사서 보유 · "
                             "포트폴리오: 여러 자산을 비율대로 섞고 주기적으로 리밸런싱")
skey, info, params, cash = "buyhold", core.STRATEGIES["buyhold"], {}, 0
pf_weights, pf_ok = {}, False
if mode == "거치식":
    c1, c2 = st.sidebar.columns(2)
    cash = c1.number_input("초기 자금(원)", min_value=100_000, value=10_000_000, step=1_000_000)
    commission = c2.number_input("거래비용(편도 %)", min_value=0.0, value=0.1, step=0.01, format="%.2f")

    skey = st.sidebar.selectbox("전략", list(core.STRATEGIES), format_func=lambda k: core.STRATEGIES[k]["name"])
    info = core.STRATEGIES[skey]
    st.sidebar.caption(info["desc"])
    cols = st.sidebar.columns(2)
    for j, (k, label, default) in enumerate(info["params"]):
        params[k] = cols[j % 2].number_input(label, value=float(default), format="%g", key=f"{skey}_{k}")
elif mode == "포트폴리오":
    PF_COLS = ["이름", "코드", "비중(%)"]

    def with_names(df):
        """코드 → 이름 칸 채우기"""
        df = df.copy()
        df["이름"] = [asset_name(c) if isinstance(c, str) and c.strip() else "" for c in df["코드"]]
        return df[PF_COLS]

    def preset_df(key):
        return with_names(pd.DataFrame([(c, float(v)) for c, v in core.PORTFOLIO_PRESETS[key].items()],
                                       columns=["코드", "비중(%)"]))

    def apply_pf_preset():
        k = st.session_state.get("pf_preset")
        if k in core.PORTFOLIO_PRESETS:
            st.session_state["pf_df"] = preset_df(k)
            st.session_state.pop("pf_editor", None)

    if "pf_df" not in st.session_state or "이름" not in st.session_state["pf_df"]:
        st.session_state["pf_df"] = preset_df(next(iter(core.PORTFOLIO_PRESETS)))
    st.sidebar.selectbox("예시 포트폴리오", list(core.PORTFOLIO_PRESETS), key="pf_preset", on_change=apply_pf_preset,
                         help="고르면 아래 구성표가 바뀝니다. 표에서 코드·비중을 직접 고치거나 행을 추가/삭제할 수 있습니다.")
    pf_edit = st.sidebar.data_editor(
        st.session_state["pf_df"], key="pf_editor", num_rows="dynamic", hide_index=True, use_container_width=True,
        column_order=PF_COLS, disabled=["이름"],
        column_config={"이름": st.column_config.TextColumn(width=124, help="코드를 입력하면 자동으로 채워집니다"),
                       "코드": st.column_config.TextColumn(width=70, help="종목코드·티커·지수코드, 현금은 CASH"),
                       "비중(%)": st.column_config.NumberColumn(width=82, min_value=0.0, max_value=100.0,
                                                              step=1.0, format="%g")})
    # 코드를 새로 넣거나 고치면 이름 칸을 다시 채운다
    named = with_names(pf_edit)
    if not named["이름"].fillna("").equals(pf_edit["이름"].fillna("")):
        st.session_state["pf_df"] = named
        st.session_state.pop("pf_editor", None)
        st.rerun()
    if code and st.sidebar.button(f"＋ 검색한 '{name}' 구성에 추가", use_container_width=True):
        st.session_state["pf_df"] = with_names(pd.concat(
            [pf_edit, pd.DataFrame([(code, 10.0)], columns=["코드", "비중(%)"])], ignore_index=True))
        st.session_state.pop("pf_editor", None)
        st.rerun()
    pf_rows = pf_edit.dropna()
    pf_rows = pf_rows[(pf_rows["코드"].astype(str).str.strip() != "") & (pf_rows["비중(%)"] > 0)]
    pf_weights = {str(c).strip().upper() if not str(c).strip().isdigit() else str(c).strip(): float(v)
                  for c, v in zip(pf_rows["코드"], pf_rows["비중(%)"])}
    wsum = sum(pf_weights.values())
    pf_ok = bool(wsum) and abs(wsum - 100) < 0.01
    if wsum:
        if pf_ok:
            st.sidebar.caption(f"✅ 비중 합계 100% · 자산 {len(pf_weights)}개")
        else:
            st.sidebar.warning(f"⚠️ 비중 합계가 **{wsum:g}%** 입니다 ({wsum - 100:+g}%p). "
                               "합계가 100%가 되도록 표의 비중을 고쳐 주세요. 100%가 되기 전에는 실행할 수 없습니다.")

            def fit_100():
                rows = st.session_state["pf_df_live"].copy()
                mask = rows["비중(%)"].fillna(0) > 0
                total = rows.loc[mask, "비중(%)"].sum()
                rows.loc[mask, "비중(%)"] = (rows.loc[mask, "비중(%)"] / total * 100).round(1)
                gap = round(100 - rows.loc[mask, "비중(%)"].sum(), 1)
                if gap and mask.any():  # 반올림 오차는 가장 큰 비중에 더함
                    i = rows.loc[mask, "비중(%)"].idxmax()
                    rows.loc[i, "비중(%)"] = round(rows.loc[i, "비중(%)"] + gap, 1)
                st.session_state["pf_df"] = rows
                st.session_state.pop("pf_editor", None)

            st.session_state["pf_df_live"] = pf_edit
            st.sidebar.button("⚖️ 비율은 유지하고 합계만 100%로 맞추기", on_click=fit_100, use_container_width=True)
        with st.sidebar.popover("ⓘ 구성 자세히", use_container_width=True):
            st.markdown("\n".join(f"- **{asset_name(c)}** `{c}` — {v:g}%" for c, v in pf_weights.items()))
            st.caption(f"합계 {wsum:g}%")
    else:
        st.sidebar.warning("구성표에 코드와 비중을 입력하세요.")
    pf_reb = st.sidebar.radio("리밸런싱", list(core.REBALANCE), format_func=core.REBALANCE.get, horizontal=True,
                              help="정해진 주기의 첫 거래일에 목표 비중으로 되돌립니다.")
    cash = st.sidebar.number_input("초기 금액(원)", min_value=100_000, value=10_000_000, step=1_000_000)
    pf_cfreq = st.sidebar.radio("추가 적립", ["Y", "M"], format_func={"Y": "매년", "M": "매월"}.get,
                                horizontal=True, key="pf_cfreq")
    pf_contrib = st.sidebar.number_input(
        f"{'매년' if pf_cfreq == 'Y' else '매월'} 추가 적립(원)", min_value=0,
        value=0, step=1_000_000 if pf_cfreq == "Y" else 100_000, key=f"pf_contrib_{pf_cfreq}",
        help=f"0이면 처음 금액만. 넣으면 {'매년' if pf_cfreq == 'Y' else '매월'} 첫 거래일에 목표 비중대로 나눠 삽니다.")
    c1, c2 = st.sidebar.columns(2)
    commission = c1.number_input("거래비용(편도 %)", min_value=0.0, value=0.1, step=0.01, format="%.2f")
    pf_cash_rate = c2.number_input("현금(CASH) 연 이자율(%)", min_value=0.0, value=3.0, step=0.5, format="%g")
else:
    dca_freq = st.sidebar.radio("적립 주기", list(core.DCA_FREQS), format_func=core.DCA_FREQS.get,
                                horizontal=True)
    c1, c2 = st.sidebar.columns(2)
    dca_amount = c1.number_input("회당 금액(원)", min_value=10_000, step=100_000,
                                 value=500_000 if dca_freq == "M" else 6_000_000, key=f"dca_amt_{dca_freq}")
    commission = c2.number_input("거래비용(편도 %)", min_value=0.0, value=0.1, step=0.01, format="%.2f")
    dca_initial = st.sidebar.number_input("시작할 때 일시금(원, 선택)", min_value=0, value=0, step=1_000_000)
    st.sidebar.selectbox("전략", ["매수 후 보유 (적립식 고정)"], disabled=True,
                         help="적립식은 정해진 날마다 사서 팔지 않고 끝까지 보유하는 방식으로만 계산합니다. "
                              "분할 익절·이동평균 등 매매 전략은 거치식에서 쓸 수 있습니다.")
    st.sidebar.caption(f"📌 **매수 후 보유** — {core.DCA_FREQS[dca_freq]} 첫 거래일 종가로 사서 팔지 않고 끝까지 보유합니다. "
                       "1주 단위로 사고 남은 돈은 다음 매수로 넘깁니다(지수는 소수 단위).")

can_run = (bool(pf_weights) and pf_ok) if mode == "포트폴리오" else code is not None
run = st.sidebar.button("백테스트 실행", type="primary", use_container_width=True, disabled=not can_run)
run = (run or st.session_state.pop("auto_run", False)) and can_run

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
            if mode == "포트폴리오":
                prices, notes = {}, set()
                for c in pf_weights:
                    if c == core.CASH:
                        continue
                    try:
                        prices[c] = get_prices(c, start.isoformat(), end.isoformat())["Close"]
                    except Exception as e:  # noqa: BLE001
                        raise RuntimeError(f"{asset_name(c)} 시세를 불러오지 못했습니다: {e}") from e
                    if core.is_foreign(c):
                        notes.add("미국 자산은 일별 원/달러 환율로 원화 환산했습니다(환율 변동 포함).")
                    if c.upper() in core.INDICES:
                        notes.add("지수는 운용보수·배당이 빠진 값입니다.")
                if core.CASH in pf_weights:
                    idx = (pd.DataFrame(prices).index if prices
                           else pd.bdate_range(start, end))
                    prices[core.CASH] = core.cash_series(idx, float(pf_cash_rate))
                res = core.run_portfolio(prices, pf_weights, float(cash), float(pf_contrib), pf_reb,
                                         float(commission), contrib_freq=pf_cfreq)
                firsts = {c: sr.dropna().index[0] for c, sr in prices.items() if c != core.CASH}
                latest = max(firsts.values()) if firsts else None
                late = ([asset_name(c) for c, f in firsts.items() if f >= latest - pd.Timedelta(days=10)]
                        if latest is not None and latest > pd.Timestamp(start) + pd.Timedelta(days=10) else [])
                if late:
                    notes.add("데이터가 늦게 시작하는 자산(" + ", ".join(late) + ") 때문에 시작일이 "
                              + res["summary"]["시작"] + "로 밀렸습니다.")
                st.session_state["result"] = dict(mode="portfolio", res=res, period=(start, end), reb=pf_reb,
                                                  contrib=float(pf_contrib), cfreq=pf_cfreq, note=" ".join(sorted(notes)) or None,
                                                  names={c: asset_name(c) for c in pf_weights})
                df = None
            else:
                df = get_prices(code, start.isoformat(), end.isoformat())
            is_index = code.upper() in core.INDICES if code else False
            if mode == "포트폴리오":
                pass
            elif mode == "적립식":
                res = core.run_dca(df, float(dca_amount), dca_freq, float(commission),
                                   float(dca_initial), fractional=is_index)
                label = f"적립식 매수 후 보유 · {core.DCA_FREQS[dca_freq]} {dca_amount:,.0f}원"
                if dca_initial:
                    label += f" (+ 일시금 {dca_initial:,.0f}원)"
                st.session_state["result"] = dict(mode="dca", res=res, name=name, code=code, strategy=label,
                                                  bars=len(df), note=core.price_note(code), period=(start, end),
                                                  freq=dca_freq)
            else:
                plot_path = os.path.join(tempfile.gettempdir(), f"bt_{code}_{skey}.html")
                res = core.run_backtest(df, skey, float(cash), float(commission), params, plot_path,
                                        fractional=True if is_index else None)
                plot_html = None
                if res["plot"]:
                    with open(res["plot"], encoding="utf-8") as f:
                        plot_html = f.read()
                st.session_state["result"] = dict(mode="lump", res=res, name=name, code=code, strategy=info["name"],
                                                  bars=len(df), plot_html=plot_html, note=core.price_note(code),
                                                  period=(start, end))
        except Exception as e:  # noqa: BLE001
            st.error(f"실행 실패: {e}")

r = st.session_state.get("result")
if not r:
    st.info("왼쪽에서 종목이나 ETF를 검색해 선택하고 **백테스트 실행**을 누르세요. (모바일은 왼쪽 위 ☰ 메뉴)")
    st.stop()

if r.get("mode") == "dca":
    render_dca(r)
    st.stop()
if r.get("mode") == "portfolio":
    render_portfolio(r)
    st.stop()

s = r["res"]["summary"]
st.subheader(f"{r['name']} ({r['code']}) · {r['strategy']}")
st.caption(f"{s.get('Start')} ~ {s.get('End')} · {r['bars']}거래일")
if r.get("period") and tuple(r["period"]) != (start, end):
    st.warning(f"사이드바 기간({start} ~ {end})이 이 결과와 다릅니다. **백테스트 실행**을 누르면 새 기간으로 다시 계산합니다.")
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
t1_, t2_ = st.columns(2)
log_scale = t1_.toggle("로그 스케일 (기간이 길 때 비교하기 쉬움)", value=r["bars"] > 252 * 5)
show_crises = t2_.toggle("급락 구간 표시", value=True)
st.altair_chart(equity_chart(r["res"]["curve"], trades, log=log_scale, crises=show_crises),
                use_container_width=True)
st.caption("▲ 빨강 = 매수, ▼ 파랑 = 매도 · 주황 음영 = 역사적 급락 구간(고점~저점, 대략) · "
           "점이나 음영에 마우스를 올리면 내용이 보입니다.")

cf = crisis_frame(r["res"]["curve"])
if show_crises and not cf.empty:
    eqc = r["res"]["curve"]
    rows = []
    for _, c in cf.iterrows():
        seg = eqc.loc[c["시작"]:c["끝"]]
        if len(seg) < 2:
            continue
        after = eqc.loc[c["시작"]:]
        row = {"위기": c["위기"], "기간": c["기간"]}
        for col, who in (("Equity", "전략"), ("BuyHold", "단순 보유")):
            start_v = seg[col].iloc[0]
            row[f"{who} 낙폭(%)"] = (seg[col].min() / start_v - 1) * 100
            rec = after[after[col] >= start_v]
            rec = rec[rec.index > seg[col].idxmin()]
            row[f"{who} 회복"] = rec.index[0].strftime("%Y-%m") if not rec.empty else "미회복"
        rows.append(row)
    if rows:
        with st.expander(f"급락 구간별 성과 ({len(rows)}개)", expanded=True):
            st.caption("낙폭 = 급락 시작일 대비 그 구간 최저 자산. 회복 = 급락 시작일 자산을 다시 넘은 달.")
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                         column_config={c: st.column_config.NumberColumn(format="%.1f")
                                        for c in ["전략 낙폭(%)", "단순 보유 낙폭(%)"]})

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

# ---------------------------------------------------------------------------
# 투자기간 분석
# ---------------------------------------------------------------------------
st.markdown("#### 투자기간 분석")
curve_all = r["res"]["curve"]
COLORS = alt.Scale(domain=["전략", "단순 보유"], range=["#2f6fed", "#9aa3ad"])
tab1, tab2, tab3 = st.tabs(["보유기간별", "연도별", "매수 시점별"])

with tab1:
    ht = core.horizon_table(curve_all)
    if ht.empty:
        st.info("기간이 너무 짧아 분석할 수 없습니다. 시작일을 앞당겨 보세요.")
    else:
        st.caption("기간 안의 **모든 날짜에 샀다고 가정**하고, 정해진 기간만큼 보유했을 때의 수익률 분포입니다. "
                   "막대는 최저~최고, 점은 중앙값입니다.")
        order = [h for h, _ in core.HORIZONS if h in set(ht["보유기간"])]
        base = alt.Chart(ht).encode(
            x=alt.X("보유기간:N", sort=order, title=None, axis=alt.Axis(labelAngle=0)),
            xOffset=alt.XOffset("구분:N", sort=["전략", "단순 보유"]),
            color=alt.Color("구분:N", scale=COLORS, legend=alt.Legend(orient="top", title=None)),
        )
        tips = ["보유기간", "구분", alt.Tooltip("중앙값(%):Q", format="+.1f"),
                alt.Tooltip("최저(%):Q", format="+.1f"), alt.Tooltip("최고(%):Q", format="+.1f"),
                alt.Tooltip("플러스 확률(%):Q", format=".0f")]
        rng = base.mark_rule(strokeWidth=6, opacity=0.35).encode(
            y=alt.Y("최저(%):Q", title="수익률(%)"), y2="최고(%):Q", tooltip=tips)
        med = base.mark_point(filled=True, size=90).encode(y="중앙값(%):Q", tooltip=tips)
        zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#999", strokeDash=[3, 3]).encode(y=alt.Y("y:Q", title="수익률(%)"))
        st.altair_chart((rng + med + zero).properties(height=340), use_container_width=True)

        if not any(h in order for h in ("3년", "5년")):
            st.caption("💡 3년·5년 보유 분석을 보려면 시작일을 더 앞으로 당겨 보세요.")
        st.dataframe(
            ht, hide_index=True, use_container_width=True,
            column_config={c: st.column_config.NumberColumn(format="%.1f")
                           for c in ht.columns if c.endswith("(%)")},
        )

with tab2:
    yt = core.yearly_table(curve_all)
    st.caption("달력 기준 연도별 수익률입니다. 첫해와 마지막 해는 데이터가 있는 기간만 계산합니다.")
    ylong = yt.melt("연도", value_vars=["전략", "단순 보유"], var_name="구분", value_name="수익률(%)")
    bars = alt.Chart(ylong).mark_bar().encode(
        x=alt.X("연도:O", title=None, axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset("구분:N", sort=["전략", "단순 보유"]),
        y=alt.Y("수익률(%):Q"),
        color=alt.Color("구분:N", scale=COLORS, legend=alt.Legend(orient="top", title=None)),
        tooltip=["연도:O", "구분:N", alt.Tooltip("수익률(%):Q", format="+.2f")],
    )
    st.altair_chart(bars.properties(height=300), use_container_width=True)
    st.dataframe(yt, hide_index=True, use_container_width=True,
                 column_config={c: st.column_config.NumberColumn(format="%+.2f") for c in ["전략", "단순 보유", "차이(%p)"]})

with tab3:
    avail = [(h, d) for h, d in core.HORIZONS if len(curve_all) >= d + core.MIN_SAMPLES]
    if not avail:
        st.info("기간이 너무 짧아 분석할 수 없습니다.")
    else:
        labels = [h for h, _ in avail]
        h = st.selectbox("보유기간", labels, index=labels.index("1년") if "1년" in labels else len(labels) - 1)
        days = dict(avail)[h]
        rf = core.rolling_frame(curve_all, days)
        st.caption(f"x축 날짜에 사서 **{h}** 보유했다면 얻었을 수익률입니다. 0선 아래는 손실 구간입니다.")
        long = rf.rename_axis("매수일").reset_index().melt("매수일", var_name="구분", value_name="수익률(%)")
        line = alt.Chart(long).mark_line(strokeWidth=1.6).encode(
            x=alt.X("매수일:T", title="매수일", axis=alt.Axis(format="%Y-%m")),
            y=alt.Y("수익률(%):Q", title="수익률(%)"),
            color=alt.Color("구분:N", scale=COLORS, legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("매수일:T", format="%Y-%m-%d"), "구분:N",
                     alt.Tooltip("수익률(%):Q", format="+.2f")],
        )
        zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#999", strokeDash=[3, 3]).encode(y=alt.Y("y:Q", title="수익률(%)"))
        st.altair_chart((line + zero).properties(height=320).interactive(bind_y=False), use_container_width=True)
        c = st.columns(3)
        c[0].metric(f"전략 {h} 플러스 확률", f"{(rf['전략'] > 0).mean() * 100:.0f}%")
        c[1].metric(f"단순 보유 {h} 플러스 확률", f"{(rf['단순 보유'] > 0).mean() * 100:.0f}%")
        c[2].metric("전략이 더 나았던 비율", f"{(rf['전략'] > rf['단순 보유']).mean() * 100:.0f}%")

st.caption("※ 전략의 기간별 수익률은 전체 백테스트 자산 곡선에서 잘라 계산한 값이라, 그 날짜에 전략을 새로 시작한 결과와는 조금 다를 수 있습니다.")

if r["plot_html"]:
    with st.expander("backtesting.py 상세 차트 (캔들 · 매매 시점 · 지표)"):
        components.html(r["plot_html"], height=820, scrolling=True)
