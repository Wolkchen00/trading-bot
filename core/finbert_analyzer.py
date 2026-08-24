"""
FinBERT Analyzer — Finansal Domain NLP Sentiment Analizi (ONNX Runtime)

ONNX Runtime ile ProsusAI/finbert modeli kullanılır.
PyTorch'a gerek YOKTUR — RAM kullanımı ~150MB (PyTorch ile ~1.5GB'dı).

Katmanlar:
  1. ONNX FinBERT (en doğru — ~%85 accuracy)
  2. VADER fallback (orta — ~%65 accuracy)
  3. Basit kelime sayma (son çare)
"""
import os
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("TradingBot")

# ============================================================
# ONNX Runtime (PyTorch yerine)
# ============================================================
ONNX_AVAILABLE = False
try:
    import onnxruntime as ort
    import numpy as np
    ONNX_AVAILABLE = True
except ImportError:
    logger.info("onnxruntime yuklu degil, VADER fallback kullanilacak")

# Tokenizer (tokenizers kütüphanesi — hafif, torch gerektirmez)
TOKENIZER_AVAILABLE = False
_tokenizer = None
try:
    from tokenizers import Tokenizer
    TOKENIZER_AVAILABLE = True
except ImportError:
    logger.info("tokenizers yuklu degil, VADER fallback kullanilacak")

# VADER fallback
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False

# Model cache dizini
MODEL_CACHE_DIR = Path(os.getenv("FINBERT_CACHE_DIR", "/app/models/finbert"))
ONNX_MODEL_PATH = MODEL_CACHE_DIR / "model.onnx"
TOKENIZER_PATH = MODEL_CACHE_DIR / "tokenizer.json"

# Label mapping (FinBERT output sırası)
FINBERT_LABELS = ["positive", "negative", "neutral"]


def softmax(logits):
    """Numpy softmax."""
    exp = np.exp(logits - np.max(logits, axis=-1, keepdims=True))
    return exp / np.sum(exp, axis=-1, keepdims=True)


class FinBERTAnalyzer:
    """
    FinBERT tabanlı finansal metin duygu analizi — ONNX Runtime ile.
    
    PyTorch GEREKMEZ. RAM: ~150MB (PyTorch ile ~1.5GB'dı).
    
    Kullanım:
        fb = FinBERTAnalyzer()
        result = fb.analyze("Tesla stock surges 10%")
        # {'label': 'positive', 'score': 0.95, 'confidence': 0.95, 'source': 'finbert'}
    """

    MODEL_NAME = "ProsusAI/finbert"
    
    def __init__(self):
        self.session = None
        self.tokenizer = None
        self.vader = None
        self.model_loaded = False
        self._load_attempts = 0
        self._max_load_attempts = 2
        
        # Hisse senedi-spesifik kelime ağırlıkları (FinBERT sonucunu ayarlar)
        self.stock_boost = {
            # Güçlü bullish kelimeler
            "upgrade": 0.12, "outperform": 0.10, "buy rating": 0.12,
            "beat estimate": 0.15, "earnings beat": 0.15, "revenue beat": 0.12,
            "raised guidance": 0.15, "strong buy": 0.12, "bullish": 0.10,
            "fda approv": 0.15, "acquisition": 0.08, "buyback": 0.10,
            "dividend increase": 0.10, "all-time high": 0.08,
            "breakout": 0.10, "rally": 0.10, "surge": 0.10,
            # Güçlü bearish kelimeler
            "downgrade": -0.12, "sell rating": -0.12, "underperform": -0.10,
            "earnings miss": -0.15, "revenue miss": -0.12,
            "lowered guidance": -0.15, "restructur": -0.10,
            "layoff": -0.10, "recall": -0.12, "lawsuit": -0.10,
            "sec investig": -0.15, "fraud": -0.18, "bankruptcy": -0.20,
            "tariff": -0.10, "sanctions": -0.12, "crash": -0.15,
            "bearish": -0.10, "default": -0.15,
        }
        
        # Model yüklemeyi dene
        self._load_model()
        
        # VADER fallback
        if VADER_AVAILABLE and not self.model_loaded:
            self.vader = SentimentIntensityAnalyzer()
            self._add_stock_lexicon()
            logger.info("FinBERT yuklenemedi, VADER fallback aktif")

    def _load_model(self):
        """ONNX FinBERT modelini yükle."""
        if not ONNX_AVAILABLE:
            logger.info("ONNX Runtime yuklu degil, FinBERT devre disi")
            return
        
        if not TOKENIZER_AVAILABLE:
            logger.info("tokenizers paketi yuklu degil, FinBERT devre disi")
            return
        
        if self._load_attempts >= self._max_load_attempts:
            return
            
        self._load_attempts += 1
        
        try:
            # ONNX model dosyası var mı kontrol et
            if not ONNX_MODEL_PATH.exists():
                logger.info(f"ONNX model bulunamadi: {ONNX_MODEL_PATH}")
                logger.info("Model indiriliyor... (ilk sefer, ~300MB)")
                self._download_and_convert_model()
            
            if not ONNX_MODEL_PATH.exists():
                logger.warning("ONNX model indirilemedi")
                return
            
            # Tokenizer dosyası var mı kontrol et
            if not TOKENIZER_PATH.exists():
                logger.info(f"Tokenizer bulunamadi: {TOKENIZER_PATH}")
                logger.info("Tokenizer indiriliyor...")
                self._download_tokenizer()
            
            if not TOKENIZER_PATH.exists():
                logger.warning("Tokenizer indirilemedi, FinBERT devre disi")
                return
            
            logger.info(f"FinBERT ONNX modeli yukleniyor...")
            start = time.time()
            
            # ONNX Runtime session
            sess_options = ort.SessionOptions()
            sess_options.inter_op_num_threads = 1
            sess_options.intra_op_num_threads = 2
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            
            self.session = ort.InferenceSession(
                str(ONNX_MODEL_PATH),
                sess_options,
                providers=['CPUExecutionProvider']
            )
            
            # Tokenizer yükle (sadece fast tokenizer — torch gerektirmez)
            self._load_tokenizer()
            
            if self.tokenizer is None:
                logger.warning("Tokenizer yuklenemedi, ONNX iptal")
                self.session = None
                return
            
            elapsed = time.time() - start
            self.model_loaded = True
            logger.info(f"✅ FinBERT ONNX basariyla yuklendi ({elapsed:.1f}sn)")
            
        except Exception as e:
            logger.warning(f"FinBERT ONNX yukleme hatasi: {e}")
            import traceback
            logger.debug(f"FinBERT traceback: {traceback.format_exc()}")
            self.model_loaded = False

    def _load_tokenizer(self):
        """Tokenizer'ı yükle — sadece fast tokenizer (torch gerektirmez)."""
        try:
            if TOKENIZER_PATH.exists():
                # Hızlı tokenizer (tokenizers kütüphanesi — torch gerektirmez)
                self.tokenizer = Tokenizer.from_file(str(TOKENIZER_PATH))
                self._tokenizer_type = "fast"
                logger.info("Tokenizer lokal cache'den yuklendi (fast)")
                return
        except Exception as e:
            logger.warning(f"Lokal tokenizer yuklenemedi: {e}")
        
        # Tokenizer dosyası yoksa veya yüklenemiyorsa indir
        try:
            self._download_tokenizer()
            if TOKENIZER_PATH.exists():
                self.tokenizer = Tokenizer.from_file(str(TOKENIZER_PATH))
                self._tokenizer_type = "fast"
                logger.info("Tokenizer indirildi ve yuklendi")
                return
        except Exception as e:
            logger.warning(f"Tokenizer indirme/yukleme hatasi: {e}")
        
        self.tokenizer = None
        logger.warning("Tokenizer yuklenemedi — FinBERT devre disi")

    def _download_tokenizer(self):
        """Tokenizer dosyasını HuggingFace Hub'dan indir."""
        try:
            from huggingface_hub import hf_hub_download
            MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            for f in ["tokenizer.json", "vocab.txt", "special_tokens_map.json"]:
                try:
                    hf_hub_download(
                        repo_id="jonngan/finbert-onnx",
                        filename=f,
                        local_dir=str(MODEL_CACHE_DIR),
                    )
                except Exception:
                    pass
            logger.info("Tokenizer dosyalari indirildi")
        except ImportError:
            logger.warning("huggingface_hub yuklu degil, tokenizer indirilemez")

    def _download_and_convert_model(self):
        """FinBERT ONNX modelini indir (pre-exported — PyTorch gerekmez)."""
        try:
            MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            
            # Pre-exported ONNX model indir (EN HAFİF — PyTorch gerekmez)
            try:
                from huggingface_hub import hf_hub_download
                # Doğrulanmış ONNX repo: jonngan/finbert-onnx (440MB, 795+ download)
                onnx_repo = "jonngan/finbert-onnx"
                hf_hub_download(
                    repo_id=onnx_repo,
                    filename="model.onnx",
                    local_dir=str(MODEL_CACHE_DIR),
                )
                logger.info(f"Pre-exported ONNX model indirildi ({onnx_repo})")
                
                # Tokenizer'ı da aynı ONNX repo'dan indir (uyumlu versiyon)
                for f in ["tokenizer.json", "vocab.txt", "special_tokens_map.json", "config.json"]:
                    try:
                        hf_hub_download(repo_id=onnx_repo, filename=f, local_dir=str(MODEL_CACHE_DIR))
                    except Exception:
                        pass
                return
            except Exception as e:
                logger.warning(f"Pre-exported model indirilemedi: {e}")
            
            # Yöntem 2: optimum ile export (transformers + optimum gerekir)
            try:
                from optimum.onnxruntime import ORTModelForSequenceClassification
                model = ORTModelForSequenceClassification.from_pretrained(
                    self.MODEL_NAME,
                    export=True,
                    cache_dir=str(MODEL_CACHE_DIR),
                )
                model.save_pretrained(str(MODEL_CACHE_DIR))
                logger.info("ONNX model optimum ile export edildi")
                return
            except ImportError:
                pass
            
            # NOT: PyTorch/torch artık KULLANILMIYOR.
            # Container'da sadece onnxruntime + tokenizers var.
            # Model mutlaka Dockerfile'da pre-download edilmeli.
            logger.warning(
                "ONNX model olusturulamadi. "
                "Dockerfile'da pre-build adimi ile model indirilmeli. "
                "(jonngan/finbert-onnx reposu)"
            )
            
        except Exception as e:
            logger.warning(f"Model indirme hatasi: {e}")

    def _tokenize(self, text: str) -> dict:
        """Metni tokenize et — tokenizer tipine göre."""
        if self._tokenizer_type == "fast":
            # tokenizers kütüphanesi
            encoded = self.tokenizer.encode(text, add_special_tokens=True)
            input_ids = np.array([encoded.ids[:512]], dtype=np.int64)
            attention_mask = np.array([encoded.attention_mask[:512]], dtype=np.int64)
            return {"input_ids": input_ids, "attention_mask": attention_mask}
        else:
            # transformers AutoTokenizer
            encoded = self.tokenizer(
                text, 
                return_tensors="np",
                padding=True,
                truncation=True,
                max_length=512
            )
            return {
                "input_ids": encoded["input_ids"].astype(np.int64),
                "attention_mask": encoded["attention_mask"].astype(np.int64),
            }

    def _add_stock_lexicon(self):
        """VADER'a hisse senedi kelimeleri ekle (fallback için)."""
        if not self.vader:
            return
        stock_words = {
            "upgrade": 2.5, "downgrade": -2.5, "outperform": 2.0,
            "underperform": -2.0, "overweight": 1.5, "underweight": -1.5,
            "bullish": 2.0, "bearish": -2.0, "rally": 2.0, "surge": 2.5,
            "crash": -3.0, "plunge": -2.5, "tumble": -2.0,
            "buyback": 1.5, "dividend": 1.5, "guidance": 1.0,
            "beat": 2.0, "miss": -2.0, "layoff": -2.0, "restructuring": -1.5,
            "acquisition": 1.5, "merger": 1.0, "bankruptcy": -3.5,
            "fda": 1.0, "approved": 2.0, "rejected": -2.5,
            "breakout": 2.5, "selloff": -2.5, "correction": -1.5,
            "momentum": 1.5, "overbought": -1.0, "oversold": 1.0,
        }
        self.vader.lexicon.update(stock_words)

    def analyze(self, text: str) -> Dict:
        """
        Metni analiz et — ONNX FinBERT veya VADER ile.

        ``score`` işaretli duygu skorudur: negatif duygu negatif, pozitif duygu
        pozitif değer taşır; 0 nötrdür. ``confidence`` ise işaretsiz model
        güvenidir (FinBERT için ``raw_score``).
        
        Returns:
            {
                'label': 'positive' | 'negative' | 'neutral',
                'score': float (-1.0 to 1.0, işaretli duygu skoru),
                'confidence': float (0.0 to 1.0, işaretsiz büyüklük),
                'source': 'finbert' | 'vader'
            }
        """
        if not text or not text.strip():
            return {
                "label": "neutral",
                "score": 0.0,
                "confidence": 0.0,
                "source": "none",
            }

        # ONNX FinBERT ile analiz
        if self.model_loaded and self.session and self.tokenizer:
            return self._analyze_finbert(text)
        
        # VADER fallback
        if self.vader:
            return self._analyze_vader(text)
        
        # Hiçbiri yoksa basit analiz
        return self._analyze_simple(text)

    def _analyze_finbert(self, text: str) -> Dict:
        """ONNX FinBERT ile derin duygu analizi."""
        try:
            # Tokenize
            inputs = self._tokenize(text[:512])
            
            # ONNX inference
            outputs = self.session.run(None, inputs)
            logits = outputs[0]
            
            # Softmax → olasılıklar
            probs = softmax(logits)[0]
            
            # En yüksek olasılık
            pred_idx = int(np.argmax(probs))
            label = FINBERT_LABELS[pred_idx]
            raw_score = float(probs[pred_idx])
            
            # Label → score dönüşümü
            if label == "positive":
                score = raw_score
            elif label == "negative":
                score = -raw_score
            else:
                score = 0.0
            
            # Hisse senedi boost uygula
            boost = self._get_stock_boost(text)
            score = max(-1.0, min(1.0, score + boost))
            
            # Boost sonrası label güncelle
            if score > 0.1:
                label = "positive"
            elif score < -0.1:
                label = "negative"
            else:
                label = "neutral"
            
            return {
                "label": label,
                "score": round(score, 4),  # İşaretli duygu skoru
                "confidence": round(raw_score, 4),  # İşaretsiz büyüklük
                "source": "finbert",
            }
            
        except Exception as e:
            logger.debug(f"FinBERT ONNX analiz hatasi: {e}")
            # Fallback to VADER
            if self.vader:
                return self._analyze_vader(text)
            return self._analyze_simple(text)

    def _analyze_vader(self, text: str) -> Dict:
        """VADER ile kural tabanlı duygu analizi (fallback)."""
        scores = self.vader.polarity_scores(text)
        compound = scores["compound"]
        
        if compound >= 0.15:
            label = "positive"
        elif compound <= -0.15:
            label = "negative"
        else:
            label = "neutral"
        
        return {
            "label": label,
            "score": round(compound, 4),
            "confidence": round(abs(compound), 4),
            "source": "vader",
        }

    def _analyze_simple(self, text: str) -> Dict:
        """En basit kelime sayma analizi (son çare)."""
        text_lower = text.lower()
        
        bullish = ["surge", "rally", "moon", "bullish", "breakout", "ath",
                    "adoption", "upgrade", "pump", "accumulate", "buy"]
        bearish = ["crash", "dump", "scam", "rug", "hack", "bearish",
                    "ban", "liquidation", "sell", "collapse", "fear"]
        
        bull_count = sum(1 for w in bullish if w in text_lower)
        bear_count = sum(1 for w in bearish if w in text_lower)
        
        score = (bull_count - bear_count) * 0.2
        score = max(-1.0, min(1.0, score))
        
        if score > 0.1:
            label = "positive"
        elif score < -0.1:
            label = "negative"
        else:
            label = "neutral"
        
        return {
            "label": label,
            "score": round(score, 4),
            "confidence": round(abs(score), 4),
            "source": "simple",
        }

    def _get_stock_boost(self, text: str) -> float:
        """Hisse senedi-spesifik kelimeler için ek boost."""
        text_lower = text.lower()
        boost = 0.0
        for word, value in self.stock_boost.items():
            if word in text_lower:
                boost += value
        return max(-0.3, min(0.3, boost))

    def analyze_batch(self, texts: List[str]) -> List[Dict]:
        """Birden fazla metni toplu analiz et."""
        return [self.analyze(text) for text in texts if text]

    def is_available(self) -> bool:
        """FinBERT modeli yüklü mü?"""
        return self.model_loaded

    def get_status(self) -> Dict:
        """Analyzer durumunu döndür."""
        return {
            "finbert_loaded": self.model_loaded,
            "vader_available": self.vader is not None,
            "active_source": "finbert-onnx" if self.model_loaded else ("vader" if self.vader else "simple"),
            "onnx_available": ONNX_AVAILABLE,
        }
