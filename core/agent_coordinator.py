"""
Agent Coordinator — Multi-Agent Karar Mimarisi

5 uzman ajan bağımsız analiz yapar, Coordinator ağırlıklı
oylama ile nihai BUY/SELL/HOLD kararını verir.

Ajanlar:
  1. TechAgent  — Teknik göstergeler (RSI, MACD, Ichimoku...)
  2. FundAgent  — Temel analiz (MCap, Volume, Supply...)
  3. SentAgent  — Duyarlılık (FinBERT + Fear&Greed)
  4. SocialAgent — Sosyal medya (Reddit, X, Trends, Whale)
  5. RiskAgent  — Risk yönetimi (ATR, drawdown, korelasyon)

Kurallar:
  - Çoğunluk: ≥3 ajan aynı yönde olmalı
  - Risk vetosu: RiskAgent SELL → BUY yapılamaz
"""
from typing import Dict, List, Optional
from utils.logger import logger


class AgentVote:
    """Tek bir ajanın oy sonucu."""
    def __init__(self, agent_name: str, signal: str, confidence: float, reasoning: str):
        self.agent_name = agent_name
        self.signal = signal  # BUY, SELL, HOLD
        self.confidence = confidence  # 0-100
        self.reasoning = reasoning

    def to_dict(self):
        return {
            "agent": self.agent_name,
            "signal": self.signal,
            "confidence": round(self.confidence, 1),
            "reasoning": self.reasoning,
        }


class TechAgent:
    """Teknik analiz ajanı — RSI, MACD, Ichimoku, ADX, OBV, Fibonacci, Divergence.
    
    v4.2 Fix: Bağımsız sinyal puanlama sistemi.
    Her gösterge kendi puanını verir, toplam puan sinyal ve güven belirler.
    Önceki bug: tech_score=0 → confidence=0 → her zaman %0 dönüyordu.
    """
    
    NAME = "TechAgent"
    
    def analyze(self, tech_data: Dict) -> AgentVote:
        # Bağımsız puanlama — her gösterge katkı sağlar
        indie_score = 0  # -100 to +100 arası
        reasons = []
        
        # === RSI (ağırlık: yüksek) ===
        rsi = tech_data.get("rsi", 50)
        if rsi < 25:
            indie_score += 25
            reasons.append(f"RSI={rsi:.0f} aşırı satım 🟢")
        elif rsi < 30:
            indie_score += 18
            reasons.append(f"RSI={rsi:.0f} oversold")
        elif rsi < 40:
            indie_score += 8
        elif rsi > 80:
            indie_score -= 25
            reasons.append(f"RSI={rsi:.0f} aşırı alım 🔴")
        elif rsi > 72:
            indie_score -= 18
            reasons.append(f"RSI={rsi:.0f} overbought")
        elif rsi > 65:
            indie_score -= 5
        
        # === MACD (ağırlık: orta-yüksek) ===
        macd_signal = tech_data.get("macd_signal", "NEUTRAL")
        if macd_signal == "BULLISH":
            indie_score += 15
            reasons.append("MACD=BULLISH")
        elif macd_signal == "BEARISH":
            indie_score -= 15
            reasons.append("MACD=BEARISH")
        
        # === Ichimoku (ağırlık: orta) ===
        ichimoku = tech_data.get("ichimoku_signal", "NEUTRAL")
        if ichimoku == "BULLISH":
            indie_score += 12
            reasons.append("Ichimoku=BULLISH")
        elif ichimoku == "BEARISH":
            indie_score -= 12
            reasons.append("Ichimoku=BEARISH")
        
        # === ADX Trend Gücü (ağırlık: düşük-orta) ===
        adx = tech_data.get("adx", 0)
        if adx > 30:
            indie_score += 8 if indie_score > 0 else -8  # Mevcut yönü güçlendir
            reasons.append(f"ADX={adx:.0f} güçlü trend")
        elif adx > 25:
            indie_score += 5 if indie_score > 0 else -5
        
        # === EMA Trend (ağırlık: orta) ===
        ema_trend = tech_data.get("ema_trend", "NEUTRAL")
        if ema_trend == "BULLISH":
            indie_score += 10
        elif ema_trend == "BEARISH":
            indie_score -= 10
        
        # === Bollinger Bands (ağırlık: düşük) ===
        bb_position = tech_data.get("bb_position", "MIDDLE")
        if bb_position == "BELOW":
            indie_score += 8
            reasons.append("BB alt bant altı")
        elif bb_position == "ABOVE":
            indie_score -= 8
            reasons.append("BB üst bant üstü")
        
        # === Orijinal tech_score'u da dahil et (ağırlık: düşük) ===
        orig_score = tech_data.get("tech_score", 0)
        indie_score += orig_score * 0.3
        
        # === Sinyal ve Güven Hesapla ===
        indie_score = max(-100, min(100, indie_score))
        
        if indie_score >= 12:
            signal = "BUY"
        elif indie_score <= -12:
            signal = "SELL"
        else:
            signal = "HOLD"
        
        # Güven: minimum %15 base + sinyal gücüne göre artar
        confidence = 15 + min(abs(indie_score) * 0.85, 85)
        confidence = min(confidence, 100)
        
        return AgentVote(
            self.NAME, signal, confidence,
            " | ".join(reasons) if reasons else "Nötr teknik görünüm"
        )


class FundAgent:
    """Temel analiz ajanı — P/E, EPS, Revenue, Margin, Analist hedef."""
    
    NAME = "FundAgent"
    
    def analyze(self, fund_data: Dict) -> AgentVote:
        score = fund_data.get("fundamental_score", 0)
        confidence = min(abs(score) * 2, 100)
        
        reasons = []
        
        # P/E oranı
        pe = fund_data.get("metrics", {}).get("pe_ratio", 0)
        if pe > 0 and pe < 15:
            reasons.append(f"P/E düşük ({pe:.1f}) — değer fırsatı")
        elif pe > 40:
            reasons.append(f"P/E yüksek ({pe:.1f}) — pahalı")
        
        # EPS
        eps = fund_data.get("metrics", {}).get("eps", 0)
        if eps > 0:
            reasons.append(f"EPS pozitif ({eps:.2f})")
        elif eps < 0:
            reasons.append(f"EPS negatif ({eps:.2f}) — zarar")
        
        # Profit margin
        margin = fund_data.get("metrics", {}).get("profit_margin", 0)
        if margin > 0.15:
            reasons.append(f"Kâr marjı güçlü ({margin:.0%})")
        
        if score >= 10:
            signal = "BUY"
        elif score <= -10:
            signal = "SELL"
        else:
            signal = "HOLD"
        
        return AgentVote(
            self.NAME, signal, confidence,
            " | ".join(reasons) if reasons else "Nötr temel görünüm"
        )


class SentAgent:
    """Duyarlılık ajanı — FinBERT/VADER + Fear&Greed + Haberler."""
    
    NAME = "SentAgent"
    
    def analyze(self, sent_data: Dict) -> AgentVote:
        news_score = sent_data.get("news_score", 0)
        fg_value = sent_data.get("fear_greed_value", 50)
        fg_signal = sent_data.get("fear_greed_signal", "NEUTRAL")
        
        confidence = min(abs(news_score) * 1.5, 100)
        
        reasons = []
        
        if fg_value < 25:
            reasons.append(f"Fear&Greed={fg_value} EXTREME FEAR (contrarian BUY)")
        elif fg_value > 75:
            reasons.append(f"Fear&Greed={fg_value} EXTREME GREED (contrarian SELL)")
        
        sentiment_label = sent_data.get("sentiment_label", "NEUTRAL")
        if sentiment_label not in ("NEUTRAL",):
            reasons.append(f"Haber sentiment: {sentiment_label}")
        
        # Contrarian mantık + haber skoru
        combined = news_score
        if fg_signal in ("STRONG_BUY", "BUY"):
            combined += 10
        elif fg_signal in ("STRONG_SELL", "SELL"):
            combined -= 10
        
        if combined >= 12:
            signal = "BUY"
        elif combined <= -12:
            signal = "SELL"
        else:
            signal = "HOLD"
        
        return AgentVote(
            self.NAME, signal, confidence,
            " | ".join(reasons) if reasons else "Nötr sentiment"
        )


class SocialAgent:
    """Sosyal medya ajanı — Reddit (r/stocks, r/wallstreetbets), X/Twitter."""
    
    NAME = "SocialAgent"
    
    def analyze(self, social_data: Dict) -> AgentVote:
        score = social_data.get("social_score", 0)
        confidence = min(abs(score) * 2, 100)
        
        reasons = []
        
        reddit_posts = social_data.get("reddit_posts", 0)
        if reddit_posts > 10:
            reasons.append(f"Reddit: {reddit_posts} post")
        
        x_tweets = social_data.get("x_tweets", 0)
        if x_tweets > 5:
            x_sent = social_data.get("x_sentiment", 0)
            reasons.append(f"X: {x_tweets} tweet (sent:{x_sent:.2f})")
        
        if social_data.get("wsb_hype", False):
            reasons.append("🚀 WSB HYPE!")
            score += 5  # WSB hype ek puan
        
        mentions_trend = social_data.get("mentions_trend", "STABLE")
        if mentions_trend == "UP":
            reasons.append("Sosyal bahsediş artışta")
        
        if score >= 10:
            signal = "BUY"
        elif score <= -10:
            signal = "SELL"
        else:
            signal = "HOLD"
        
        return AgentVote(
            self.NAME, signal, confidence,
            " | ".join(reasons) if reasons else "Nötr sosyal aktivite"
        )


class RiskAgent:
    """
    Risk yönetim ajanı — ATR, drawdown, pozisyon limiti, VIX, jeopolitik.
    
    ÖNEMLİ: RiskAgent'ın SELL oyu → BUY'ı veto eder!
    """
    
    NAME = "RiskAgent"
    
    def analyze(self, risk_data: Dict) -> AgentVote:
        reasons = []
        risk_score = 0
        short_boost = 0  # VIX bazli short guven artisi
        
        # Günlük kayıp kontrolü
        daily_pnl_pct = risk_data.get("daily_pnl_pct", 0)
        if daily_pnl_pct < -2.0:
            risk_score -= 30
            reasons.append(f"⚠️ Günlük kayıp: {daily_pnl_pct:.1f}%")
        
        # Açık pozisyon sayısı
        open_positions = risk_data.get("open_positions", 0)
        max_positions = risk_data.get("max_positions", 2)
        if open_positions >= max_positions:
            risk_score -= 25
            reasons.append(f"Max pozisyon dolu: {open_positions}/{max_positions}")
        
        # ATR volatilite
        atr_pct = risk_data.get("atr_pct", 0)
        if atr_pct > 5:
            risk_score -= 15
            reasons.append(f"Yüksek volatilite ATR={atr_pct:.1f}%")
        
        # Korelasyon riski → VIX bazlı piyasa riski
        vix = risk_data.get("vix", 0)
        if vix > 35:
            risk_score -= 25
            short_boost += 20  # Panik = short icin cok iyi
            reasons.append(f"VIX PANİK: {vix:.1f} (SHORT+{short_boost})")
        elif vix > 25:
            risk_score -= 15
            short_boost += 10  # Yuksek korku = short firsati
            reasons.append(f"VIX yüksek: {vix:.1f} (SHORT+{short_boost})")
        
        # Jeopolitik risk (petrol, savaş haberleri)
        geo_risk = risk_data.get("geopolitical_risk", "NORMAL")
        if geo_risk == "HIGH":
            risk_score -= 20
            short_boost += 10
            reasons.append("⚠️ Jeopolitik risk YÜKSEK")
        elif geo_risk == "ELEVATED":
            risk_score -= 10
            reasons.append("Jeopolitik risk yükselmekte")
        
        # Petrol spike (Hürmüz Boğazı)
        oil_signal = risk_data.get("oil_signal", "STABLE")
        if oil_signal == "SPIKE":
            risk_score -= 15
            reasons.append("🛢️ Petrol SPIKE — Hürmüz Boğazı riski!")
        
        # Equity floor kontrolü
        equity_floor_hit = risk_data.get("equity_floor_hit", False)
        if equity_floor_hit:
            risk_score -= 50
            reasons.append("🛑 EQUITY FLOOR! Bot durmalı!")
        
        # Sinyal belirle — v4.8: RiskAgent FRENDİR, GAZ DEĞİL.
        # Eski davranış: risk normalken BUY oyu veriyordu → her taramada bedava
        # +1 BUY oyu (çoğunluk 3/5'i fiilen 2/4'e indiriyordu) + güven şişmesi.
        # Artık risk-normal = HOLD (nötr); SELL vetosu aynen korunur.
        if risk_score <= -30:
            signal = "SELL"  # VETO!
        elif risk_score <= -15:
            signal = "HOLD"
        else:
            signal = "HOLD"  # Risk uygun → nötr; alım gerekçesi diğer ajanların işi

        confidence = min(abs(risk_score) + 30, 100)
        
        vote = AgentVote(
            self.NAME, signal, confidence,
            " | ".join(reasons) if reasons else "Risk seviyeleri normal"
        )
        vote.short_boost = short_boost  # Coordinator'a ilet
        return vote


class AgentCoordinator:
    """
    5 uzman ajanın kararlarını birleştiren koordinatör.
    
    Ağırlıklar:
      TechAgent:   %25
      FundAgent:   %20
      SentAgent:   %20
      SocialAgent: %15
      RiskAgent:   %20
    
    Kurallar:
      1. Çoğunluk: ≥3 ajan aynı yönde → işlem
      2. Risk vetosu: RiskAgent SELL → BUY engellenir
    """

    WEIGHTS = {
        "TechAgent": 0.25,
        "FundAgent": 0.20,
        "SentAgent": 0.20,
        "SocialAgent": 0.15,
        "RiskAgent": 0.20,
    }

    def __init__(self):
        self.tech_agent = TechAgent()
        self.fund_agent = FundAgent()
        self.sent_agent = SentAgent()
        self.social_agent = SocialAgent()
        self.risk_agent = RiskAgent()
        
        self.last_decision = None
        logger.info("Agent Coordinator baslatildi — 5 uzman ajan aktif")

    def decide(self, symbol: str, 
               tech_data: Dict, fund_data: Dict,
               sent_data: Dict, social_data: Dict,
               risk_data: Dict) -> Dict:
        """
        Tüm ajanlardan oy al ve nihai kararı ver.
        
        Returns:
            {
                'signal': 'BUY' | 'SELL' | 'HOLD',
                'confidence': float (0-100),
                'votes': [AgentVote],
                'majority': bool,
                'risk_veto': bool,
                'reasoning': str
            }
        """
        # 1. Her ajandan oy al
        votes = [
            self.tech_agent.analyze(tech_data),
            self.fund_agent.analyze(fund_data),
            self.sent_agent.analyze(sent_data),
            self.social_agent.analyze(social_data),
            self.risk_agent.analyze(risk_data),
        ]
        
        # 2. Oyları say
        buy_count = sum(1 for v in votes if v.signal == "BUY")
        sell_count = sum(1 for v in votes if v.signal == "SELL")
        hold_count = sum(1 for v in votes if v.signal == "HOLD")
        
        # 3. Ağırlıklı skor hesapla
        weighted_score = 0

        for vote in votes:
            weight = self.WEIGHTS.get(vote.agent_name, 0.15)
            signal_value = {"BUY": 1, "SELL": -1, "HOLD": 0}[vote.signal]
            weighted_score += signal_value * weight * vote.confidence
        
        # 4. Risk vetosu kontrolü
        risk_vote = votes[4]  # RiskAgent her zaman son
        risk_veto = False
        
        if risk_vote.signal == "SELL":
            risk_veto = True
        
        # 5. Çoğunluk kontrolü
        majority = False
        
        if buy_count >= 3:
            preliminary_signal = "BUY"
            majority = True
        elif sell_count >= 3:
            preliminary_signal = "SELL"
            majority = True
        elif weighted_score > 15:
            preliminary_signal = "BUY"
        elif weighted_score < -15:
            preliminary_signal = "SELL"
        else:
            preliminary_signal = "HOLD"
        
        # 6. Risk vetosu uygula
        final_signal = preliminary_signal
        if risk_veto and preliminary_signal == "BUY":
            final_signal = "HOLD"
            logger.warning(
                f"  ⚠️ {symbol} RiskAgent VETO! BUY -> HOLD "
                f"({risk_vote.reasoning})"
            )
        
        # 7. Güven hesapla — v4.8: YÖN-FARKINDA güven.
        # Eski formül TÜM ajanların güvenini yön fark etmeksizin topluyordu:
        # karşı yönde oy veren ajanın güveni bile nihai BUY güvenine EKLENIYORDU
        # (pozisyon boyutu bantları bu sayıya bağlı → çelişkili sinyalde büyük
        # pozisyon riski). |weighted_score| tam istenen ölçü: aynı yön güven katar,
        # karşı yön düşer, HOLD sulandırır (katkı 0). Tam mutabakat sinyallerinde
        # eski ve yeni değer hemen hemen aynıdır — 60/70/80/90 bantları anlamını korur.
        confidence = abs(weighted_score)
        if majority:
            confidence *= 1.2  # Çoğunluk = daha güvenli
        if risk_veto:
            confidence *= 0.5  # Veto = güven düşer

        # VIX bazli short boost (SELL sinyalinde guven artisi)
        short_boost = getattr(risk_vote, "short_boost", 0)
        if final_signal == "SELL" and short_boost > 0:
            confidence += short_boost
            logger.info(f"  📉 {symbol} VIX SHORT BOOST: +{short_boost} puan")

        confidence = min(confidence, 100)
        
        # 8. Gerekçe oluştur
        vote_summary = f"BUY:{buy_count} SELL:{sell_count} HOLD:{hold_count}"
        reasoning_parts = [
            f"Oylama: {vote_summary}",
            f"Ağırlıklı skor: {weighted_score:.1f}",
        ]
        if majority:
            reasoning_parts.append("Çoğunluk sağlandı")
        if risk_veto:
            reasoning_parts.append(f"RİSK VETO: {risk_vote.reasoning}")
        
        result = {
            "signal": final_signal,
            "confidence": round(confidence, 1),
            "weighted_score": round(weighted_score, 1),
            "votes": [v.to_dict() for v in votes],
            "buy_count": buy_count,
            "sell_count": sell_count,
            "hold_count": hold_count,
            "majority": majority,
            "risk_veto": risk_veto,
            "reasoning": " | ".join(reasoning_parts),
        }
        
        self.last_decision = result
        
        # Log
        logger.info(
            f"  Coordinator {symbol}: {final_signal} "
            f"(guven:{confidence:.0f}%) "
            f"[{vote_summary}] "
            f"{'COGUNLUK' if majority else 'tekil'} "
            f"{'⚠️VETO' if risk_veto else ''}"
        )
        for v in votes:
            # Signal != HOLD ise agent detaylarini INFO'da goster (debug icin kritik)
            log_fn = logger.info if final_signal != "HOLD" else logger.debug
            log_fn(
                f"    {v.agent_name}: {v.signal} "
                f"({v.confidence:.0f}%) — {v.reasoning}"
            )
        
        return result
