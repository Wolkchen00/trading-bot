"""
AI Trading Bot Dashboard - Streamlit Web Arayüzü
Gerçek zamanlı P&L, pozisyonlar, sinyaller ve kontrol paneli.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import json
import os
import sys

# Proje root'unu path'e ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import RISK_CONFIG, SCANNER_CONFIG, STRATEGY_CONFIG, DASHBOARD_CONFIG
from core.order_executor import OrderExecutor
from core.portfolio_tracker import PortfolioTracker
from core.stock_scanner import StockScanner
from core.data_fetcher import DataFetcher
from core.technical_analysis import TechnicalAnalysis

# ============================================================
# SAYFA AYARI
# ============================================================
st.set_page_config(
    page_title="🤖 AI Trading Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS
st.markdown("""
<style>
    .stMetric {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        padding: 15px;
        border-radius: 12px;
        border: 1px solid #0f3460;
    }
    .stMetric label { color: #a0a0b0 !important; }
    .stMetric [data-testid="stMetricValue"] { font-size: 1.8rem !important; }
    div[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f0f1a 0%, #1a1a2e 100%);
    }
    .main { background-color: #0a0a14; }
    h1, h2, h3 { color: #e0e0ff !important; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# VERİ YÜKLEME
# ============================================================
@st.cache_resource
def get_executor():
    try:
        return OrderExecutor()
    except Exception:
        return None


@st.cache_resource
def get_tracker():
    return PortfolioTracker()


@st.cache_resource
def get_data_fetcher():
    try:
        return DataFetcher()
    except Exception:
        return None


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.title("🤖 AI Trading Bot")
    st.markdown("---")

    # Mod göstergesi
    mode = os.getenv("TRADING_MODE", "paper")
    if mode == "paper":
        st.success("📝 PAPER TRADING (Demo)")
    else:
        st.error("🔴 LIVE TRADING (Gerçek)")

    st.markdown("---")

    # Navigasyon
    page = st.radio(
        "📊 Sayfa Seç",
        [
            "🏠 Ana Panel",
            "📊 Pozisyonlar",
            "📈 Hisse Tarayıcı",
            "🔍 Teknik Analiz",
            "📜 İşlem Geçmişi",
            "🧪 Backtest",
            "⚙️ Ayarlar",
        ],
    )

    st.markdown("---")
    st.caption(f"Son güncelleme: {datetime.now().strftime('%H:%M:%S')}")

    if st.button("🔄 Yenile", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()


# ============================================================
# ANA PANEL
# ============================================================
if page == "🏠 Ana Panel":
    st.title("🏠 Ana Panel")

    executor = get_executor()
    tracker = get_tracker()

    if executor:
        account = executor.get_account()
        if account:
            # Hesap metrikleri
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric(
                    "💰 Toplam Değer",
                    f"${account['equity']:,.2f}",
                    f"${account['daily_pnl']:,.2f}",
                )
            with col2:
                st.metric("💵 Nakit", f"${account['cash']:,.2f}")
            with col3:
                st.metric("🛒 Alım Gücü", f"${account['buying_power']:,.2f}")
            with col4:
                pnl_color = "🟢" if account["daily_pnl"] >= 0 else "🔴"
                st.metric(
                    f"{pnl_color} Günlük P&L",
                    f"${account['daily_pnl']:,.2f}",
                )

            st.markdown("---")

            # Açık pozisyonlar özet
            positions = executor.get_positions()
            if positions:
                st.subheader(f"📊 Açık Pozisyonlar ({len(positions)})")
                pos_df = pd.DataFrame(positions)
                pos_df["unrealized_pnl"] = pos_df["unrealized_pnl"].apply(
                    lambda x: f"${x:,.2f}"
                )
                st.dataframe(pos_df, use_container_width=True, hide_index=True)
            else:
                st.info("📭 Açık pozisyon yok")

            # İşlem istatistikleri
            stats = tracker.get_overall_stats()
            if stats["total_trades"] > 0:
                st.subheader("📈 Performans İstatistikleri")
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Toplam İşlem", stats["total_trades"])
                with col2:
                    st.metric("Win Rate", f"{stats['win_rate']:.1f}%")
                with col3:
                    st.metric("Toplam P&L", f"${stats['total_pnl']:,.2f}")
                with col4:
                    st.metric("Profit Factor", f"{stats['profit_factor']:.2f}")
        else:
            st.warning("⚠️ Hesap bilgisi alınamadı. API key'lerinizi kontrol edin.")
    else:
        st.warning(
            "⚠️ Alpaca bağlantısı kurulamadı.\n\n"
            "`.env` dosyasına API key'lerinizi ekleyin:\n"
            "```\nALPACA_API_KEY=your_key\nALPACA_SECRET_KEY=your_secret\n```"
        )


# ============================================================
# POZİSYONLAR
# ============================================================
elif page == "📊 Pozisyonlar":
    st.title("📊 Pozisyonlar")

    executor = get_executor()
    if executor:
        positions = executor.get_positions()
        if positions:
            for pos in positions:
                pnl = pos["unrealized_pnl"]
                pnl_pct = pos["unrealized_pnl_pct"]
                color = "🟢" if pnl >= 0 else "🔴"

                with st.expander(
                    f"{color} {pos['symbol']} | {pos['qty']} hisse | "
                    f"P&L: ${pnl:,.2f} ({pnl_pct:+.2f}%)"
                ):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Giriş Fiyat", f"${pos['avg_entry']:,.2f}")
                    with col2:
                        st.metric("Güncel Fiyat", f"${pos['current_price']:,.2f}")
                    with col3:
                        st.metric("Piyasa Değeri", f"${pos['market_value']:,.2f}")

                    col_a, col_b = st.columns(2)
                    with col_a:
                        if st.button(f"🔴 {pos['symbol']} Sat", key=f"sell_{pos['symbol']}"):
                            executor.sell_all(pos["symbol"])
                            st.success(f"✅ {pos['symbol']} satıldı!")
                            st.rerun()
                    with col_b:
                        if st.button(f"⚡ %50 Sat", key=f"partial_{pos['symbol']}"):
                            executor.sell_partial(pos["symbol"], 0.5)
                            st.success(f"✅ {pos['symbol']} %50 satıldı!")
                            st.rerun()
        else:
            st.info("📭 Açık pozisyon yok")

        # Acil kontroller
        st.markdown("---")
        st.subheader("🚨 Acil Kontroller")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("❌ TÜM EMİRLERİ İPTAL ET", type="secondary", use_container_width=True):
                executor.cancel_all_orders()
                st.warning("Tüm emirler iptal edildi!")
        with col2:
            if st.button("🚨 TÜM POZİSYONLARI KAPAT", type="primary", use_container_width=True):
                executor.close_all_positions()
                st.error("Tüm pozisyonlar kapatıldı!")
                st.rerun()


# ============================================================
# HİSSE TARAYICI
# ============================================================
elif page == "📈 Hisse Tarayıcı":
    st.title("📈 Hisse Tarayıcı (Momentum Scanner)")

    data_fetcher = get_data_fetcher()
    if data_fetcher:
        if st.button("🔎 Tara", use_container_width=True):
            with st.spinner("Hisseler taranıyor..."):
                scanner = StockScanner(data_fetcher)
                movers = scanner.scan_movers()

            if movers:
                st.success(f"✅ {len(movers)} momentum hisse bulundu!")
                df = pd.DataFrame(movers)
                df["change_pct"] = df["change_pct"].apply(lambda x: f"{x:+.1f}%")
                df["volume"] = df["volume"].apply(lambda x: f"{x:,}")
                df["price"] = df["price"].apply(lambda x: f"${x:.2f}")
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.warning("Filtre kriterlerini karşılayan hisse bulunamadı.")
    else:
        st.warning("⚠️ Bağlantı kurulamadı")


# ============================================================
# TEKNİK ANALİZ
# ============================================================
elif page == "🔍 Teknik Analiz":
    st.title("🔍 Teknik Analiz")

    symbol = st.text_input("Hisse Kodu", value="AAPL").upper()

    data_fetcher = get_data_fetcher()
    if data_fetcher and symbol:
        if st.button("📊 Analiz Et"):
            with st.spinner(f"{symbol} analiz ediliyor..."):
                ta = TechnicalAnalysis()
                df = data_fetcher.get_historical_yfinance(symbol, "3mo", "1d")

                if not df.empty:
                    df = ta.calculate_all(df)

                    # Fiyat grafiği
                    fig = go.Figure()
                    fig.add_trace(go.Candlestick(
                        x=df.index,
                        open=df["open"],
                        high=df["high"],
                        low=df["low"],
                        close=df["close"],
                        name="Fiyat",
                    ))
                    if "ema_fast" in df.columns:
                        fig.add_trace(go.Scatter(
                            x=df.index, y=df["ema_fast"],
                            name="EMA 9", line=dict(color="cyan", width=1),
                        ))
                    if "ema_medium" in df.columns:
                        fig.add_trace(go.Scatter(
                            x=df.index, y=df["ema_medium"],
                            name="EMA 21", line=dict(color="orange", width=1),
                        ))
                    fig.update_layout(
                        title=f"{symbol} - Fiyat Grafiği",
                        template="plotly_dark",
                        height=500,
                        xaxis_rangeslider_visible=False,
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    # RSI grafiği
                    if "rsi" in df.columns:
                        fig_rsi = go.Figure()
                        fig_rsi.add_trace(go.Scatter(
                            x=df.index, y=df["rsi"],
                            name="RSI", line=dict(color="yellow"),
                        ))
                        fig_rsi.add_hline(y=70, line_dash="dash", line_color="red")
                        fig_rsi.add_hline(y=30, line_dash="dash", line_color="green")
                        fig_rsi.update_layout(
                            title="RSI (14)",
                            template="plotly_dark",
                            height=250,
                        )
                        st.plotly_chart(fig_rsi, use_container_width=True)

                    # Sinyal verisi
                    signal_data = ta.get_signal_data(df)
                    if signal_data:
                        st.subheader("📊 Son Bar Verileri")
                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            rsi_val = signal_data["rsi"]
                            st.metric("RSI", f"{rsi_val:.1f}",
                                     delta="Aşırı Alım" if rsi_val > 70 else "Aşırı Satım" if rsi_val < 30 else "Normal")
                        with col2:
                            st.metric("MACD Hist", f"{signal_data['macd_hist']:.4f}")
                        with col3:
                            st.metric("ATR", f"${signal_data['atr']:.2f}")
                        with col4:
                            st.metric("Rel. Volume", f"{signal_data['relative_volume']:.1f}x")
                else:
                    st.error(f"{symbol} verisi bulunamadı")


# ============================================================
# İŞLEM GEÇMİŞİ
# ============================================================
elif page == "📜 İşlem Geçmişi":
    st.title("📜 İşlem Geçmişi")

    tracker = get_tracker()
    trades = tracker.trades

    if trades:
        df = pd.DataFrame(trades)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # P&L grafik
        daily_pnl = tracker.get_daily_pnl_history()
        if daily_pnl:
            pnl_df = pd.DataFrame(daily_pnl)
            fig = px.bar(
                pnl_df, x="date", y="pnl",
                title="Günlük P&L",
                color="pnl",
                color_continuous_scale=["red", "gray", "green"],
                template="plotly_dark",
            )
            st.plotly_chart(fig, use_container_width=True)

        # İstatistikler
        stats = tracker.get_overall_stats()
        st.subheader("📊 Genel İstatistikler")
        st.json(stats)
    else:
        st.info("📭 Henüz işlem geçmişi yok")


# ============================================================
# BACKTEST
# ============================================================
elif page == "🧪 Backtest":
    st.title("🧪 Strateji Backtest")

    col1, col2, col3 = st.columns(3)
    with col1:
        symbol = st.text_input("Hisse", value="AAPL").upper()
    with col2:
        period = st.selectbox("Periyot", ["3mo", "6mo", "1y", "2y"], index=1)
    with col3:
        capital = st.number_input("Başlangıç Sermaye ($)", value=10000, step=1000)

    if st.button("🚀 Backtest Çalıştır", use_container_width=True):
        with st.spinner(f"{symbol} backtest çalıştırılıyor..."):
            from backtesting.backtester import Backtester
            bt = Backtester(initial_capital=capital)
            result = bt.run(symbol, period)

        if "error" not in result:
            # Sonuç metrikleri
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                color = "normal" if result["total_pnl"] >= 0 else "inverse"
                st.metric("Toplam P&L", f"${result['total_pnl']:,.2f}",
                         f"{result['total_return_pct']:+.1f}%")
            with col2:
                st.metric("Win Rate", f"{result['win_rate']:.1f}%")
            with col3:
                st.metric("Profit Factor", f"{result['profit_factor']:.2f}")
            with col4:
                st.metric("Sharpe Ratio", f"{result['sharpe_ratio']:.2f}")

            # Equity curve
            if result.get("equity_curve"):
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    y=result["equity_curve"],
                    mode="lines",
                    name="Portföy Değeri",
                    line=dict(color="cyan", width=2),
                    fill="tozeroy",
                    fillcolor="rgba(0,200,255,0.1)",
                ))
                fig.update_layout(
                    title="Equity Curve",
                    template="plotly_dark",
                    height=400,
                    yaxis_title="Değer ($)",
                )
                st.plotly_chart(fig, use_container_width=True)

            # İşlem detayları
            trades = result.get("trades", [])
            if trades:
                st.subheader("📝 İşlem Detayları")
                st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)
        else:
            st.error(f"Backtest hatası: {result.get('error', 'Unknown')}")


# ============================================================
# AYARLAR
# ============================================================
elif page == "⚙️ Ayarlar":
    st.title("⚙️ Ayarlar")

    tab1, tab2, tab3 = st.tabs(["🛡️ Risk", "🔎 Tarayıcı", "📊 Strateji"])

    with tab1:
        st.subheader("Risk Yönetimi Ayarları")
        st.json(RISK_CONFIG)

    with tab2:
        st.subheader("Hisse Tarayıcı Ayarları")
        st.json(SCANNER_CONFIG)

    with tab3:
        st.subheader("Strateji Ayarları")
        st.json(STRATEGY_CONFIG)

    st.info("💡 Ayarları değiştirmek için `config.py` dosyasını düzenleyin.")
