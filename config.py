"""
AI Trading Bot - Configuration
Hisse senedi odaklı al-sat stratejisi.
Tüm ayarlar bu dosyada merkezi olarak yönetilir.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# ALPACA API AYARLARI
# ============================================================
# ALPACA_KEY_PREFIX: Docker Compose'dan hangi key setinin
# kullanilacagini belirler (LIVE / PAPER / bos=fallback)
_key_prefix = os.getenv("ALPACA_KEY_PREFIX", "")
TRADING_MODE = os.getenv("TRADING_MODE", "paper")  # "paper" veya "live"

# Anahtar secimi: ALPACA_KEY_PREFIX (Docker Compose) > TRADING_MODE (lokal fallback).
# Boylece prefix tanimsizken bile paper modda PAPER, live modda LIVE anahtari yuklenir
# (mod/endpoint ile anahtar tutarli olur; yanlis anahtar -> 401 hatasi onlenir).
_use_live = (_key_prefix == "LIVE") or (not _key_prefix and TRADING_MODE == "live")
_use_paper = (_key_prefix == "PAPER") or (not _key_prefix and TRADING_MODE != "live")

if _use_live:
    ALPACA_API_KEY = os.getenv("ALPACA_LIVE_API_KEY", "") or os.getenv("ALPACA_API_KEY", "")
    ALPACA_SECRET_KEY = os.getenv("ALPACA_LIVE_SECRET_KEY", "") or os.getenv("ALPACA_SECRET_KEY", "")
elif _use_paper:
    ALPACA_API_KEY = os.getenv("ALPACA_PAPER_API_KEY", "") or os.getenv("ALPACA_API_KEY", "")
    ALPACA_SECRET_KEY = os.getenv("ALPACA_PAPER_SECRET_KEY", "") or os.getenv("ALPACA_SECRET_KEY", "")
else:
    # Fallback: eski tekli key (lokal gelistirme icin)
    ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
    ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
BOT_MODE = os.getenv("BOT_MODE", "both")  # "long_only", "short_only", "both"

# Haber & Veri API'leri
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
MARKETAUX_TOKEN = os.getenv("MARKETAUX_TOKEN", "")

# Alpaca base URLs
ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_URL = "https://api.alpaca.markets"

def get_base_url():
    return ALPACA_PAPER_URL if TRADING_MODE == "paper" else ALPACA_LIVE_URL

# ============================================================
# STATE DOSYALARI — LIVE / PAPER İZOLASYONU
# ------------------------------------------------------------
# Live ve paper bot aynı makinede aynı anda çalıştığında, state
# dosyalarının (positions, kill_switch, pdt, wash_sale, agent_perf)
# birbirine karışmasını önlemek için her mod kendi alt dizinine yazar.
# Önceki davranış: tüm dosyalar tek klasörde paylaşılıyordu → live ve
# paper birbirinin day-trade sayacını / kill durumunu / wash-sale
# bloğunu kirletiyordu (mükerrer kayıt kanıtlandı).
# ============================================================
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# KALICILIK: state'i kalıcı bir volume'a yaz, redeploy'da SİLİNMESİN. Coolify/VPS'te
# docker-compose'da named volume `/app/state_paper`'a mount edilince zaten _BASE_DIR
# altına denk gelir (env gerekmez). Alternatif: STATE_VOLUME_PATH env'i ile kök override
# (örn tek volume /data'ya mount). Hiçbiri yoksa eski davranış — geriye birebir uyumlu.
_STATE_ROOT = (
    os.getenv("STATE_VOLUME_PATH")              # genel (Coolify/VPS: opsiyonel kök override)
    or _BASE_DIR                                 # volume yok → eski davranış
)
STATE_DIR = os.path.join(_STATE_ROOT, "state_live" if TRADING_MODE == "live" else "state_paper")
try:
    os.makedirs(STATE_DIR, exist_ok=True)
except Exception:
    STATE_DIR = _BASE_DIR  # Yazılamıyorsa eski davranışa düş (container fallback)

def state_path(filename: str) -> str:
    """Live/paper'a göre izole edilmiş state dosyası yolu döndürür."""
    return os.path.join(STATE_DIR, filename)

# ============================================================
# HİSSE TANIMI — STOCK_IDS (tüm modüller buradan import eder)
# ============================================================
STOCK_IDS = {
    # Mega Cap
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corp.",
    "GOOGL": "Alphabet Inc.",
    "AMZN": "Amazon.com Inc.",
    "NVDA": "NVIDIA Corp.",
    "META": "Meta Platforms Inc.",
    "TSLA": "Tesla Inc.",
    # Growth
    "AMD": "Advanced Micro Devices",
    "SOFI": "SoFi Technologies",
    "PLTR": "Palantir Technologies",
    "COIN": "Coinbase Global",
    "SQ": "Block Inc.",
    "SHOP": "Shopify Inc.",
    "CRWD": "CrowdStrike Holdings",
    # Momentum
    "RIVN": "Rivian Automotive",
    "NIO": "NIO Inc.",
    "LCID": "Lucid Group",
    "MARA": "Marathon Digital",
    "RIOT": "Riot Platforms",
    "SMCI": "Super Micro Computer",
    # Ters ETF'ler (piyasa dususunde LONG alarak kar)
    "SQQQ": "ProShares UltraPro Short QQQ",   # NASDAQ 3x ters
    "SH": "ProShares Short S&P500",            # S&P 1x ters
    "SPXS": "Direxion Daily S&P 3x Bear",      # S&P 3x ters
    # Piyasa Endeksleri (rejim tespiti icin)
    "SPY": "SPDR S&P 500 ETF",
    "QQQ": "Invesco QQQ Trust",
}

# Hisse arama terimleri (haber & sosyal medya)
STOCK_SEARCH_TERMS = {
    "AAPL": ["apple", "iphone", "tim cook", "apple earnings"],
    "MSFT": ["microsoft", "azure", "satya nadella", "windows", "copilot"],
    "GOOGL": ["google", "alphabet", "youtube", "gemini ai", "search"],
    "AMZN": ["amazon", "aws", "prime", "bezos", "jassy"],
    "NVDA": ["nvidia", "gpu", "jensen huang", "ai chips", "cuda"],
    "META": ["meta", "facebook", "instagram", "zuckerberg", "metaverse"],
    "TSLA": ["tesla", "elon musk", "ev", "cybertruck", "autopilot"],
    "AMD": ["amd", "ryzen", "radeon", "lisa su", "epyc"],
    "SOFI": ["sofi", "student loans", "fintech", "noto"],
    "PLTR": ["palantir", "data analytics", "karp", "government contract"],
    "COIN": ["coinbase", "crypto exchange", "sec coinbase"],
    "SQ": ["block", "square", "cash app", "dorsey"],
    "SHOP": ["shopify", "ecommerce", "tobi lutke"],
    "CRWD": ["crowdstrike", "cybersecurity", "george kurtz"],
    "RIVN": ["rivian", "electric truck", "r1t", "r1s"],
    "NIO": ["nio", "chinese ev", "william li"],
    "LCID": ["lucid", "lucid air", "ev sedan"],
    "MARA": ["marathon digital", "bitcoin mining"],
    "RIOT": ["riot platforms", "crypto mining"],
    "SMCI": ["super micro", "supermicro", "ai server"],
    # Ters ETF'ler
    "SQQQ": ["sqqq", "short nasdaq", "bear etf", "nasdaq put"],
    "SH": ["short sp500", "bear sp500", "market hedge"],
    "SPXS": ["spxs", "3x bear", "sp500 short etf"],
    # Endeksler
    "SPY": ["spy", "sp500", "s&p 500", "market index"],
    "QQQ": ["qqq", "nasdaq 100", "tech index"],
}

# Jeopolitik anahtar kelimeler — AKILLI AJAN SİSTEMİ
# Keyword'ler haberi BULUR, FinBERT haberi ANLAR.
# Severity seviyeleri:
#   CRITICAL (3 puan) = Doğrudan piyasa etkisi, FinBERT onayı gerekmez
#   HIGH (2 puan)     = Önemli olay, FinBERT negatif onaylarsa sayılır
#   ELEVATED (1 puan) = Bağlama bağlı, SADECE FinBERT negatif derse sayılır
GEOPOLITICAL_KEYWORDS = {
    "bearish_critical": [
        # Doğrudan piyasa çökertenler — FinBERT onayı GEREKMEZ
        "nuclear weapon", "nuclear strike", "tactical nuke", "nuclear threat",
        "ceasefire violat", "ceasefire collapse", "broke ceasefire",
        "invasion", "ground offensive", "war declared",
        "strait of hormuz closed", "oil embargo", "pipeline attack",
        "bank run", "systemic risk", "sovereign default",
    ],
    "bearish_high": [
        # Önemli olaylar — FinBERT negatif onaylarsa 2 puan
        "war escalat", "military strike", "missile", "airstrike",
        "bombing", "retaliati", "drone attack", "drone strike",
        "artillery", "shelling", "resumed attack", "resumed fighting",
        "iran attack", "iran strike", "houthi", "red sea attack",
        "hezbollah", "gaza escalat", "lebanon strike",
        "china taiwan", "taiwan strait", "south china sea",
        "ukraine escalat", "russia attack", "nato escalat",
        "oil surge", "oil spike", "oil supply cut",
        "energy crisis", "gas shortage", "supply disruption",
        "debt default", "bank failure", "credit crisis",
        "liquidity crisis", "terror attack", "mass casualt",
    ],
    "bearish_elevated": [
        # Bağlama bağlı — SADECE FinBERT negatif derse 1 puan
        # "tariff lifted" = pozitif, "tariff war" = negatif — FinBERT ayırt eder
        "sanctions", "tariff", "trade war", "export ban",
        "chip ban", "tech ban", "economic warfare",
        "recession", "blockade", "embargo",
        "north korea", "korean peninsula", "china sanction",
        "iran israel", "iran nuclear", "gulf tension",
        "west bank", "opec cut", "debt ceiling",
        "contagion", "hostage", "terrorist",
    ],
    "bullish": [
        "ceasefire", "ceasefire agreed", "ceasefire hold",
        "peace deal", "peace agreement", "peace talks progress",
        "trade agreement", "trade deal", "sanctions lifted",
        "sanctions eased", "diplomati", "negotiations resume",
        "de-escalat", "troops withdraw", "withdrawal",
        "rate cut", "stimulus", "infrastructure bill",
        "oil drop", "oil price fall", "opec increase",
        "ceasefire extended", "hostage release", "prisoner swap",
        "tariff removed", "tariff reduced", "tariff exemption",
        "tariff relief", "trade progress", "tariff pause",
    ],
}

# Eski uyumluluk: tüm bearish keyword'leri tek listede
GEOPOLITICAL_KEYWORDS["bearish"] = (
    GEOPOLITICAL_KEYWORDS["bearish_critical"] +
    GEOPOLITICAL_KEYWORDS["bearish_high"] +
    GEOPOLITICAL_KEYWORDS["bearish_elevated"]
)

# ============================================================
# SEKTÖR HARİTASI (korelasyon koruması için)
# ============================================================
SECTOR_MAP = {
    # Teknoloji
    "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology",
    "META": "Technology", "NVDA": "Semiconductors", "AMD": "Semiconductors",
    "SMCI": "Semiconductors", "CRWD": "Cybersecurity",
    # E-Ticaret / İnternet
    "AMZN": "E-Commerce", "SHOP": "E-Commerce",
    # Fintech
    "SOFI": "Fintech", "SQ": "Fintech", "COIN": "Fintech",
    # EV / Otomotiv
    "TSLA": "EV", "RIVN": "EV", "NIO": "EV", "LCID": "EV",
    # Kripto Madenciliği
    "MARA": "CryptoMining", "RIOT": "CryptoMining",
    # Data / AI
    "PLTR": "Data_AI",
    # Ters ETF'ler
    "SQQQ": "InverseETF", "SH": "InverseETF", "SPXS": "InverseETF",
    # Endeksler (trade edilmez, rejim tespiti icin)
    "SPY": "Index", "QQQ": "Index",
}

# ============================================================
# ⚠️ LEGACY CONFIG — Sadece _legacy/ modüller tarafından kullanılıyor.
# Ana bot STOCK_CONFIG kullanır. Bu ayarlar geriye uyumluluk için duruyor.
# ============================================================
RISK_CONFIG = {
    "max_risk_per_trade_pct": 0.02,     # Tek işlemde max %2 risk
    "max_daily_loss_pct": 0.03,          # Günlük max %3 kayıp
    "max_position_size_pct": 0.30,       # Tek pozisyon max %30 sermaye
    "max_open_positions": 3,             # Max 3 açık pozisyon
    "risk_reward_ratio": 2.0,            # Min risk/ödül oranı (1:2)
    "trailing_stop_pct": 0.03,           # Trailing stop %3
    "min_confidence_score": 35,          # Min sinyal güven puanı (%35 — önceki %50 çok yüksekti)
}

# ============================================================
# KOMİSYON / FEE AYARLARI
# ============================================================
COMMISSION_CONFIG = {
    # Alpaca hisse senedi: komisyon YOK!
    "stock_commission_per_share": 0.0,
    "stock_commission_pct": 0.0,
    "stock_min_commission": 0.0,

    # Düzenleyici ücretler (çok küçük — sadece satışta)
    "sec_fee_per_dollar": 0.0000278,
    "finra_taf_per_share": 0.000166,

    # Slippage tahmini
    "estimated_slippage_pct": 0.001,

    # Minimum kâr eşiği
    "min_profit_after_fees": True,
}

# ============================================================
# TEKNİK ANALİZ AYARLARI
# ============================================================
TECHNICAL_CONFIG = {
    # RSI
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    
    # EMA
    "ema_fast": 9,
    "ema_medium": 21,
    "ema_slow": 50,
    
    # MACD
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    
    # Bollinger Bands
    "bb_period": 20,
    "bb_std_dev": 2.0,
    
    # ATR (stop-loss hesabı için)
    "atr_period": 14,
    "atr_multiplier": 1.5,
    
    # VWAP
    "vwap_bounce_threshold": 0.005,
}

# ============================================================
# STOCK BOT ANA KONFİGÜRASYON
# ============================================================
STOCK_CONFIG = {
    # === HİSSE HAVUZU ===
    "symbols": list(STOCK_IDS.keys()),

    # === Pozisyon ağırlıkları (tier bazlı) ===
    "tier_weights": {
        # Mega cap — %40
        "AAPL": 0.40, "MSFT": 0.40, "GOOGL": 0.40, "AMZN": 0.40,
        "NVDA": 0.40, "META": 0.40, "TSLA": 0.35,
        # Growth — %35
        "AMD": 0.35, "SOFI": 0.30, "PLTR": 0.30, "COIN": 0.30,
        "SQ": 0.30, "SHOP": 0.30, "CRWD": 0.30,
        # Momentum — %25
        "RIVN": 0.25, "NIO": 0.25, "LCID": 0.25,
        "MARA": 0.25, "RIOT": 0.25, "SMCI": 0.25,
    },
    "default_tier_weight": 0.20,

    # === RISK YÖNETİMİ ===
    "max_risk_per_trade_pct": 0.03,
    "max_position_pct": 0.20,              # %20 equity per pozisyon (de-risk: edge kanıtlanana dek)
    "max_position_usd": 200,               # Paper default (PAPER_AGGRESSIVE override eder)
    "live_max_position_usd": 300,          # LIVE: sert tavan $300/trade
    # LIVE GÜVENE-GÖRE BOYUT (İhsan kararı 2026-07-02): Kelly negatifken sizer %5
    # tabana düşüp $25'lik işlemler üretiyordu. Artık sinyal güven puanı boyutu
    # belirler: normal sinyal $100-200, çok güvenilir $300. Kelly/damping BYPASS;
    # tavanlar: fixed_position_max_pct × equity + live_max_position_usd + eldeki nakit.
    # Format: [min_güven, $boyut] — güveni karşılayan EN YÜKSEK bant seçilir.
    # v4.8 yön-farkında güven + v4.9 KAYNAK-REMAP: v4.8'in Monte Carlo tahmini
    # (yeni≈eski×0.83) gerçek oy dağılımında TUTMADI — 06-07 Tem canlı veride
    # ham |ws| max 15'e sıkıştı (NVDA 3'lü BUY ~32), 50 eşiği hiç ulaşılamadı =
    # canlı hisse motoru fiilen kapalıydı. v4.9: koordinatör güveni kaynağında
    # ×2.0 ölçeklenir (agent_coordinator.decide) → bu bantlar yeniden anlamlı:
    # NVDA-tipi güçlü mutabakat (ws~33 + çoğunluk) ≈ 79 → $150-200; zayıf 2'li
    # oylar (ws 10-15) ≈ 20-30 → hiçbir banda giremez. Çelişkili sinyaller yön-
    # farkında formülde zaten ölür (2v2 → ws≈0).
    "live_conf_position_bands": [
        [50, 100],                          # eşiği yeni geçen sinyal → $100
        [60, 150],
        [70, 200],
        [80, 300],                          # çok güvenilir (≈eski 90+) → $300
    ],
    "live_fixed_position_usd": 0,          # eski düz-sabit mod (0=kapalı; bantlar öncelikli)
    "fixed_position_max_pct": 0.62,        # boyut equity'nin %62'sini aşamaz — $487 hesapta $300
                                           # bandına izin verir; drawdown'da otomatik küçülür
    "max_open_positions": 3,               # 3 pozisyona çeşitlenme (konsantrasyon riski azalt)
    "cash_reserve_pct": 0.10,              # %10 nakit rezerv (sabit boyut modunda sermaye deploy edilsin)
    "equity_floor_pct": 0.85,              # Hesap %85'ine düşerse yeni giriş dur (~%15 DD koruması)

    # === INDEX PARKING (boştaki nakit → SPY beta) ===
    # v4.8.2 — LIVE'DA AÇIK (İhsan kararı 2026-07-05 "hemen aç"): regime deneyi
    # SPY açığının ~9 puanının nakit sürüklemesi olduğunu gösterdi (−11.5→−2.8).
    # Katkı eklenmeyecek (tek sermaye) → boş nakdin çalışması tek yapısal düzeltme.
    # Rezerv etkileşimi: %30 rezerv (~$146 @ $487) likit kalır; executor'ın %10
    # nakit rezerviyle birlikte ilk alım ~$97-100'e kırpılabilir — bilinçli kabul;
    # alım rezervi eritirse parking ertesi gün SPY satıp rezervi tamamlar (günde 1
    # rebalance → aynı-gün AL-SAT yok → PDT güvenli; floor ihlalinde park yapılmaz).
    "index_parking_enabled": True,
    "index_parking_symbol": "SPY",
    "index_parking_reserve_pct": 0.30,     # equity'nin %30'u likit kalsın (trade buying-power)
    "index_parking_min_trade_usd": 50,     # bu altı rebalance yapma (gereksiz emir/PDT yok)
    "index_parking_allow_live": True,      # LIVE opt-in (İhsan onayı 2026-07-05)

    # === STOP/PROFIT HEDEFLERİ (backtest sonrası optimize) ===
    "stop_loss_pct": 0.04,                  # %4 stop-loss (3% çok dar)
    "stop_loss_max_pct": 0.06,              # %6 max stop
    "atr_stop_multiplier": 1.8,             # ATR çarpanı (1.5 çok sıkı)
    # v4.8 DİNAMİK TP: gerçek TP = clamp(planlanan_SL × min_rr_ratio,
    # take_profit_pct, take_profit_max_pct). Eski sabit TP %8 + ATR bazlı SL
    # kombinasyonu R:R'yi yapısal olarak 2.0'a sabitliyordu → R:R gate fiilen
    # "ATR ≤ %2.22" filtresine dönüşmüştü (yüksek-ATR hisselerde hiç alım yok).
    # Şimdi SL genişledikçe TP de orantılı uzar; oran her işlemde korunur.
    "take_profit_pct": 0.08,                # TABAN TP — dinamik TP bunun altına inmez
    "take_profit_max_pct": 0.12,            # dinamik TP tavanı (SL %6 × 2.0 = %12'ye izin)
    "trailing_stop_pct": 0.04,              # %4 trailing stop
    "partial_profit_pct": 0.05,             # %5'de yarısını sat

    # === SINYAL EŞİKLERİ ===
    # v4.8: 60 → 50. v4.9: 50 KALDI ama artık remap'li ölçekte okunur
    # (conf = |ws|×çarpanlar×2.0): 50 ≈ ham ws 25 = net, çok-ajanlı mutabakat.
    # Remap'siz dönemde (06-07 Tem) bu eşiğe hiçbir sinyal ulaşamamıştı.
    "min_confidence_score": 50,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "min_volume_ratio": 1.3,
    "trend_ema_period": 50,

    # === GATE FİLTRELERİ ===
    "ema200_trend_gate": True,
    "time_filter_enabled": True,            # Piyasa saatleri kontrolü
    "earnings_gate_enabled": True,          # Earnings koruma
    "volatility_filter_enabled": True,
    "max_atr_pct": 0.05,                    # ATR > %5 ise alım yapma
    "bb_proximity_pct": 0.01,               # BB bant yakınlık eşiği (%1)

    # === SEKTÖR KORELASYON KORUMASI ===
    "max_positions_per_sector": 2,           # Aynı sektörde max 2 pozisyon

    # === KAYIP SERİSİ KORUYUCU ===
    "loss_streak_enabled": True,
    "loss_streak_warn": 2,                  # 2 ardışık zarar → güven yükselt
    "loss_streak_halt": 4,                  # 4 ardışık zarar → 1 gün alım yasağı
    "loss_streak_halt_hours": 24,
    "loss_streak_elevated_conf": 70,
    "coin_filter_enabled": True,            # Hisse bazlı ardışık zarar filtresi
    "coin_max_consecutive_losses": 3,

    # === R:R GATE ===
    # v4.8: gate artık executor'ın GERÇEK planladığı SL/TP ile oranı ölçer
    # (dinamik TP sayesinde oran normalde sağlanır; gate yalnız take_profit_max_pct
    # tavanı orana izin vermediğinde bloklar). Volatilite koruması bu gate'in işi
    # DEĞİL — onu max_atr_pct (VOL GATE) yapar.
    "rr_gate_enabled": True,
    "min_rr_ratio": 2.0,

    # === MULTI-TIMEFRAME ===
    "multi_tf_enabled": True,

    # === SIGNAL QUEUE (pullback girişi) — v4.8 ===
    # Uzamış girişlerde (RSI yüksek / BB üstü / VWAP primli) hemen almak yerine
    # %1.5 pullback bekler (2 saat, gelmezse iptal). Paper-first: canlıda kapalı,
    # paper'da PAPER_AGGRESSIVE açar. Kanıt birikirse live'a alınır.
    "pullback_queue_enabled": False,

    # === BREAK-EVEN STOP ===
    "breakeven_enabled": True,
    "breakeven_trigger_pct": 0.025,
    "breakeven_offset_pct": 0.003,

    # === PDT AYARLARI ===
    "max_day_trades_per_week": 2,
    "pdt_equity_threshold": 25000,

    # === ZAMANLAMA ===
    "scan_interval_seconds": 30,            # Her 30 saniyede tara
    "min_interval_high_conf": 10,           # %65+ güven: 10dk
    "min_interval_med_conf": 20,            # %55-64 güven: 20dk
    "min_interval_low_conf": 30,            # %50-54 güven: 30dk
    "sell_cooldown_seconds": 300,            # 5 dakika satış cooldown (swing trade)

    # === KILL SWITCH (ana botun OKUDUĞU değerler — KillSwitch buradan beslenir) ===
    # %5: sabit $250 boyutta 2 tam stop (~%4.1 equity) normal strateji akışıdır,
    # %3'te kill bunu keserdi. %5 yine günü ~$24 kayıpla sert keser ($487 hesap).
    "max_daily_loss_pct": 0.05,             # Sert kill: gün -%5 → TÜM pozisyonları kapat
    "max_consecutive_errors": 3,            # 3 ardışık API hatası → kill (önceki 5; de-risk)

    # === ZAMANLAMA SABİTLERİ ===
    "error_retry_sleep": 30,
    "heartbeat_interval": 30,
    "status_report_interval": 5,
    "min_position_close_usd": 5.0,

    # === KOMİSYON (HİSSE = $0) ===
    "commission_pct": 0.0,
    "min_trade_value": 10.0,
}

# ============================================================
# SHORT SELLING AYARLARI
# ============================================================
_is_paper = TRADING_MODE == "paper"

SHORT_CONFIG = {
    # === ANA KONTROL ===
    "short_enabled": True,               # Short sistemi aktif
    "short_paper_only": True,            # Sadece paper'da short (canli icin False yap)

    # === POZISYON — Paper: agresif, Live: muhafazakar ===
    "short_max_positions": 3 if _is_paper else 1,
    "short_max_position_pct": 0.20 if _is_paper else 0.15,
    "short_max_position_usd": 2000 if _is_paper else 100,
    "short_max_exposure_pct": 0.40 if _is_paper else 0.25,

    # === STOP / PROFIT (ters yon) ===
    "short_stop_loss_pct": 0.04,         # %4 stop
    "short_stop_loss_max_pct": 0.06,     # %6 max stop
    "short_take_profit_pct": 0.06,       # %6 take-profit
    "short_trailing_stop_pct": 0.035,    # %3.5 trailing
    "short_partial_profit_pct": 0.04,    # %4'de yarisini cover

    # === SINYAL ESIKLERI — Paper: daha dusuk esik ===
    "short_min_confidence": 40 if _is_paper else 38,
    "short_min_sell_score": 40 if _is_paper else 45,
    "short_atr_stop_multiplier": 2.0,

    # === SQUEEZE KORUMASI ===
    "short_squeeze_protection": True,
    "squeeze_volume_threshold": 3.0,
    "squeeze_price_threshold": 0.05,
    "squeeze_consecutive_days": 3,

    # === KARA LISTE — Ters ETF'ler short yapilmaz (zaten ters) ===
    "short_blacklist": [
        "GME", "AMC",                    # Meme stocks
        "RIVN", "LCID",                  # Dusuk float
        "MARA", "RIOT",                  # Kripto/volatil
        "NVDA",                          # AI boom — short yapilamaz
        "AMD",                           # AI chip — guclu uptrend
        "SMCI",                          # AI server — cok volatil
        "SQQQ", "SH", "SPXS",           # Ters ETF — short yapma
        "SPY", "QQQ",                    # Endeksler — short yapma
    ],

    # === FILTRELER ===
    "short_ema200_gate": True,
    "short_earnings_gate": True,
    "short_volume_min_ratio": 1.5,
    "short_max_rsi": 75,

    # === BREAK-EVEN ===
    "short_breakeven_enabled": True,
    "short_breakeven_trigger_pct": 0.02,
    "short_breakeven_offset_pct": 0.003,
}

# ============================================================
# PIYASA REJIM TESPITI (Bear/Bull Mode)
# ============================================================
MARKET_REGIME_CONFIG = {
    "enabled": True,
    "benchmark_symbol": "SPY",           # S&P 500 ETF
    "ema_period": 200,                   # EMA200 altinda = BEAR
    "bear_short_conf_reduction": 10,     # Bear modda short esigi -10 puan
    "bear_buy_conf_increase": 10,        # Bear modda BUY icin +10 puan gerektir
    "inverse_etf_symbols": ["SQQQ", "SH", "SPXS"],
    "index_symbols": ["SPY", "QQQ"],     # Trade edilmez, sadece analiz
}

# ============================================================
# ZAMANLAMA AYARLARI (US Eastern Time)
# ============================================================
SCHEDULE_CONFIG = {
    "market_open": "09:30",
    "market_close": "16:00",
    "scan_interval_seconds": 30,
    "pre_market_scan": "09:00",
    "stop_trading_time": "15:45",
    "timezone": "US/Eastern",
}

# ============================================================
# LOGLAMA AYARLARI
# ============================================================
LOG_CONFIG = {
    "log_dir": "logs",
    "log_level": "INFO",
    "trade_history_file": "trade_history.json",
    "max_log_files": 30,
}

# ============================================================
# KILL SWITCH (ACİL DURUM) AYARLARI
# ⚠️ Ana bot (stock_bot.py) bu bloğu OKUMAZ — kill eşiklerini STOCK_CONFIG'den
# alır (max_daily_loss_pct / max_consecutive_errors, ~satır 370). Bu blok
# yalnızca _legacy/main.py içindir. Yanıltıcı olmaması için değerler
# STOCK_CONFIG ile AYNI tutuldu (3 hata / %3 günlük kayıp).
# ============================================================
KILL_SWITCH_CONFIG = {
    "max_consecutive_api_errors": 3,
    "max_daily_loss_pct": 0.03,
    "auto_close_positions": True,
    "kill_state_file": "kill_switch.json",
}

# ============================================================
# EMİR TİPİ AYARLARI
# ============================================================
ORDER_CONFIG = {
    "prefer_limit_orders": True,
    "limit_order_slippage_pct": 0.005,
    "min_volume_for_market_order": 100_000,
    "limit_order_timeout_minutes": 5,
}

# ============================================================
# VERİ KALİTESİ
# ============================================================
DATA_CONFIG = {
    "require_realtime_data": True,
    "max_acceptable_delay_seconds": 5,
    "warn_on_delayed_data": True,
}

# ============================================================
# OPTIONS TRADING AYARLARI (CALL / PUT)
# ============================================================
OPTIONS_CONFIG = {
    # === ANA KONTROL ===
    # v4.9: KAPATILDI. 06 Tem'de CRWD PUT churn'ü 50 dakikada -$2,170 yaktı:
    # bozuk snapshot (str-request bug'ı) yüzünden bayat close_price ($0.71) entry
    # sanılıyor, gerçek dolum $0.42, stop bid $0.20'ye karşı anında -%72 → sat →
    # cooldown yok → 60-90sn sonra tekrar al (×8 tur). Yeniden açma şartları:
    # (1) snapshot fix doğrulanmış (v4.9'da yapıldı), (2) fill-onaylı muhasebe
    # (v4.9'da yapıldı), (3) spread/likidite kapısı canlı veriyle test edilmiş,
    # (4) paper'da en az 1 hafta churn'süz gözlem. Açmadan önce PLAN.md v4.9'a bak.
    "options_enabled": False,
    "options_paper_only": True,              # Sadece paper'da aktif

    # === POZİSYON LİMİTLERİ ===
    "options_max_positions": 5,              # Max 5 açık opsiyon
    "options_max_per_symbol": 2,             # Aynı hissede max 2
    "options_max_position_usd": 1000,        # Max $1000/kontrat grubu (live'da $2000)
    "options_max_exposure_pct": 0.20,        # Sermayenin max %20'si opsiyon

    # === KONTRAT SEÇİM KRİTERLERİ ===
    "options_min_delta": 0.25,               # Min delta (çok OTM olmasın)
    "options_max_delta": 0.55,               # Max delta (çok ITM pahalı)
    "options_preferred_delta": 0.40,         # İdeal delta (ATM yakını)
    "options_min_expiry_days": 5,            # Min 5 gün vade
    "options_max_expiry_days": 21,           # Max 3 hafta (theta dengesi)
    "options_preferred_expiry_days": 10,     # İdeal 10 gün
    "options_min_open_interest": 50,         # Likidite filtresi
    "options_max_spread_pct": 0.10,          # Max %10 bid-ask spread

    # === RİSK YÖNETİMİ ===
    # v4.9: stop-out sonrası aynı underlying'e tekrar giriş yasağı (churn kilidi)
    "options_reentry_cooldown_hours": 4,
    "options_stop_loss_pct": 0.40,           # %40 zarar → kapat
    "options_take_profit_pct": 0.80,         # %80 kar → kapat
    "options_partial_profit_pct": 0.50,      # %50 karda yarısını sat
    "options_close_before_expiry_days": 1,   # Vadeye 1 gün kala kapat
    "options_max_theta_decay_pct": 0.05,     # Günlük %5+ theta kaybı → kapat

    # === SİNYAL EŞİKLERİ ===
    "options_min_confidence": 45,            # Min %45 güven (hisseden yüksek)
    "options_call_min_confidence": 45,       # CALL için min güven
    "options_put_min_confidence": 45,        # PUT için min güven

    # === KARA LİSTE (opsiyon likiditesi düşük olanlar) ===
    "options_blacklist": [
        "RIVN", "LCID", "NIO",              # Düşük hacimli EV'ler
        "MARA", "RIOT",                      # Kripto madenciliği
        "SQQQ", "SH", "SPXS",               # Ters ETF
    ],

    # === TERCİH EDİLEN (yüksek opsiyon likiditesi) ===
    "options_preferred_symbols": [
        "AAPL", "MSFT", "NVDA", "META", "AMZN",
        "GOOGL", "TSLA", "AMD", "SPY", "QQQ",
    ],
}

# ============================================================
# PAPER HESAP AGRESİF AYARLAR
# Paper'da daha hızlı ve agresif trade etmek için override'lar.
# Live hesapta bu ayarlar UYGULANMAZ.
# ============================================================
PAPER_AGGRESSIVE_CONFIG = {
    # === HİSSE AYARLARI (override) ===
    "max_position_usd": 5000,                # $200 → $5000
    "max_open_positions": 8,                  # 3 → 8
    # v4.9: 45 → 30. v4.8'in Monte Carlo kalibrasyonu GERÇEK oy dağılımını
    # tutturamadı: 06-07 Tem canlı veride koordinatör güveni (|ws|) max 15'te
    # kaldı, NVDA'nın 3'lü BUY mutabakatı bile ~32'ydi → 45 HİÇ ulaşılamıyordu
    # (2 günde 0 paper işlem). v4.9 remap (conf=|ws|×2.0 çarpanlar sonrası) ile
    # ölçek 0-100'e açıldı; 30 eşiği ≈ ws 15 = "koordinatörün kendi sinyal
    # tabanı" (ws>15 → BUY/SELL). Paper'ın işi ÖĞRENME VERİSİ üretmek
    # (meta_labeler WIRE kapısı 30-50 kapalı işlem bekliyor).
    "min_confidence_score": 30,
    "scan_interval_seconds": 15,              # 30 → 15 (daha sık tara)
    "stop_loss_pct": 0.05,                    # %4 → %5 (biraz daha geniş)
    "take_profit_pct": 0.06,                  # %8 → %6 (daha hızlı kar al)
    # v4.8: paper R:R hedefi 1.5 (dinamik TP = SL×1.5, tavan %10) → TP %7.5-9
    # bandında kalır, işlemler daha hızlı kapanır = öğrenme döngüsü hızlanır.
    # ESKİ BUG: TP %6 / SL taban %5 sabitken R:R gate (min 2.0) paper'da HER
    # alımı blokluyordu (maks oran 1.2) — paper hisse işlemleri aylardır ölüydü.
    "min_rr_ratio": 1.5,
    "take_profit_max_pct": 0.10,
    "sell_cooldown_seconds": 120,             # 5dk → 2dk (daha hızlı geri gir)

    # === SHORT AYARLARI (override) ===
    "short_max_positions": 4,                 # 3 → 4
    "short_max_position_usd": 4000,           # $2000 → $4000
    "short_min_confidence": 35,               # 40 → 35

    # === OPTIONS ===
    # v4.9: churn nedeniyle kapalı (OPTIONS_CONFIG.options_enabled yorumuna bak)
    "enable_options": False,
    "prefer_options_over_stock": False,

    # === INDEX PARKING (paper'da AÇIK — nakit sürüklemesini azalt, beta yakala) ===
    "index_parking_enabled": True,

    # === SIGNAL QUEUE (paper'da AÇIK — pullback girişi paper-first denenir) ===
    "pullback_queue_enabled": True,

    # === KAYIP SERİSİ (v4.10 — paper'da öğrenme akışını dondurmasın) ===
    # 08-10 Tem kanıtı: paper 2 ardışık zarardan sonra her giriş için güven ≥70
    # istiyordu (META ~96 kez bundan bloklandı) → 4 günde 1 işlem. Paper'ın işi
    # ÖRNEK ÜRETMEK (meta_labeler 30-50 kapalı işlem bekliyor) ve beklenen PF
    # ~0.96'yla zarar serisi kaçınılmaz; sermaye koruması DEĞİL (sahte para,
    # kill-switch -%5/gün freni ayrıca duruyor). LIVE değerleri DEĞİŞMEDİ
    # (warn 2 / halt 4 / 24h — İhsan'ın koruma kilidi aynen).
    "loss_streak_warn": 999,        # güven-yükseltme fiilen kapalı
    "loss_streak_halt": 6,          # 6 ardışık zarar → yine fren
    "loss_streak_halt_hours": 6,    # 24h değil 6h — öğrenme günü çöpe gitmesin
}
