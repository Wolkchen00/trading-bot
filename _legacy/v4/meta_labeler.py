"""
Meta-Labeler — López de Prado ikincil-model katmanı (Faz 1: analiz + altyapı).

FİKİR: Coordinator YÖN (side) üretmeye devam etsin. Üstüne ikincil bir model koy:
"bu sinyal kazanacak mı?" olasılığını tahmin etsin. Düşük olasılıkları BAS,
yüksekleri büyüt. Bu, dün teşhis ettiğimiz "düşük-güvenli aşırı işlem = zarar"
problemine birebir reçete: precision'ı artırır, false-positive'leri süpürür.

Bu dosya BAĞIMSIZDIR (yalnız numpy + stdlib) ve CANLIYA BAĞLI DEĞİLDİR. Şu an:
  1. agent_performance.json'dan per-trade dataset kurar
     (5 ajanın signal+confidence'ı = özellik, WIN/LOSS = etiket)
  2. numpy-only lojistik regresyon + K-fold AUC ile sinyal gücünü ölçer
  3. Eşik/precision-coverage tablosu basar (meta-labeling'in değerini gösterir)
  4. Modeli meta_model.json'a kaydeder

WIRE-ETME KAPISI: OOF AUC > ~0.55 ve yeterli veri olduğunda, MetaLabeler.predict_proba'yı
stock_bot karar yoluna trade-gate / size-çarpanı olarak bağlarız. AUC ≈ 0.50 ise
ajan oyları win/loss'u öngörmüyor demektir — bağlamaya değmez (dürüst sonuç da budur).

Kullanım:
    py -X utf8 meta_labeler.py                          # state_paper/agent_performance.json
    py -X utf8 meta_labeler.py state_live/agent_performance.json
    py -X utf8 meta_labeler.py --min 30                 # min örnek eşiğini düşür
"""
import os
import sys
import json
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

AGENTS = ["TechAgent", "FundAgent", "SentAgent", "SocialAgent", "RiskAgent"]
SIGNAL_MAP = {"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0, "SHORT": -1.0}
DEFAULT_MIN_SAMPLES = 40   # altında: eğitme, "daha çok kapalı işlem gerek" de
MODEL_FILE = "meta_model.json"


# ============================================================
# DATASET — agent_performance.json → per-trade satırlar
# ============================================================
def build_dataset(path):
    """(symbol, timestamp) ile grupla → her işlem bir satır.

    Returns: X (n×d), y (n,), feature_names, meta(list of dict)
    """
    if not os.path.exists(path):
        return None, None, None, None, f"dosya yok: {path}"

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # (symbol, ts) -> satır
    rows = {}
    for agent, preds in data.items():
        for p in preds:
            outcome = p.get("actual_outcome")
            if outcome not in ("WIN", "LOSS"):
                continue  # yalnız kapanmış, yönlü işlemler
            key = (p.get("symbol"), p.get("timestamp"))
            row = rows.setdefault(key, {
                "symbol": p.get("symbol"),
                "ts": p.get("timestamp"),
                "coordinator_signal": p.get("coordinator_signal", "BUY"),
                "label": 1 if outcome == "WIN" else 0,
                "pnl": p.get("pnl", 0),
                "agents": {},
            })
            row["agents"][agent] = {
                "signal": p.get("predicted_signal", "HOLD"),
                "confidence": float(p.get("confidence", 0) or 0),
            }

    if not rows:
        return None, None, None, None, "kapanmış (WIN/LOSS) işlem kaydı yok"

    feature_names = []
    for a in AGENTS:
        feature_names += [f"{a}_dir", f"{a}_conf"]
    feature_names += ["is_long"]

    X, y, meta = [], [], []
    for key, row in rows.items():
        feat = []
        for a in AGENTS:
            av = row["agents"].get(a)
            if av:
                feat.append(SIGNAL_MAP.get(av["signal"], 0.0))
                feat.append(av["confidence"] / 100.0)
            else:
                feat += [0.0, 0.0]  # ajan oyu yoksa nötr
        is_long = 1.0 if row["coordinator_signal"] in ("BUY", "LONG") else 0.0
        feat.append(is_long)
        X.append(feat)
        y.append(row["label"])
        meta.append({"symbol": row["symbol"], "ts": row["ts"], "pnl": row["pnl"]})

    return np.array(X, float), np.array(y, float), feature_names, meta, None


# ============================================================
# numpy-only LOJİSTİK REGRESYON (bağımlılıksız)
# ============================================================
def _standardize(X, mean=None, std=None):
    if mean is None:
        mean = X.mean(axis=0)
        std = X.std(axis=0)
    std_safe = np.where(std < 1e-9, 1.0, std)
    return (X - mean) / std_safe, mean, std_safe


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def train_logreg(X, y, l2=1.0, lr=0.1, iters=2000, seed=42):
    """Standardize + bias + GD. Döner: dict(weights, bias, mean, std)."""
    Xs, mean, std = _standardize(X)
    n, d = Xs.shape
    rng = np.random.default_rng(seed)
    w = rng.normal(0, 0.01, d)
    b = 0.0
    for _ in range(iters):
        p = _sigmoid(Xs @ w + b)
        err = p - y
        grad_w = Xs.T @ err / n + (l2 / n) * w
        grad_b = err.mean()
        w -= lr * grad_w
        b -= lr * grad_b
    return {"weights": w.tolist(), "bias": float(b),
            "mean": mean.tolist(), "std": std.tolist()}


def predict_proba_raw(model, X):
    w = np.array(model["weights"]); b = model["bias"]
    mean = np.array(model["mean"]); std = np.array(model["std"])
    Xs = (X - mean) / np.where(std < 1e-9, 1.0, std)
    return _sigmoid(Xs @ w + b)


# ============================================================
# DEĞERLENDİRME — AUC (Mann-Whitney) + K-fold OOF
# ============================================================
def auc_score(y, p):
    """Tie-averaged rank AUC. Tek sınıf varsa nan."""
    y = np.asarray(y); p = np.asarray(p)
    n_pos = int((y == 1).sum()); n_neg = int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), float)
    sp = p[order]
    i = 0
    while i < len(sp):
        j = i
        while j + 1 < len(sp) and sp[j + 1] == sp[i]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-indeksli ortalama rank
        ranks[order[i:j + 1]] = avg
        i = j + 1
    sum_pos = ranks[y == 1].sum()
    return float((sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def kfold_oof(X, y, k, l2=1.0):
    """OOF olasılıkları üret (her fold dışarıda tahmin edilir)."""
    n = len(y)
    rng = np.random.default_rng(7)
    idx = rng.permutation(n)
    oof = np.zeros(n)
    folds = np.array_split(idx, k)
    for f in folds:
        test = f
        train = np.setdiff1d(idx, test)
        if len(np.unique(y[train])) < 2:
            oof[test] = y[train].mean() if len(train) else 0.5
            continue
        m = train_logreg(X[train], y[train], l2=l2)
        oof[test] = predict_proba_raw(m, X[test])
    return oof


def threshold_table(y, p, thresholds=(0.50, 0.55, 0.60, 0.65, 0.70)):
    """Meta-labeling değeri: eşik t'de coverage (işlem yüzdesi) ve precision (win-rate)."""
    y = np.asarray(y); p = np.asarray(p)
    base = y.mean() * 100
    out = [("baz (hepsini al)", 100.0, base, len(y))]
    for t in thresholds:
        take = p >= t
        cov = take.mean() * 100
        prec = (y[take].mean() * 100) if take.any() else float("nan")
        out.append((f"p>={t:.2f}", cov, prec, int(take.sum())))
    return out


# ============================================================
# WIRE-ETMEYE HAZIR API (canlıya henüz bağlı değil)
# ============================================================
class MetaLabeler:
    """meta_model.json yükle → bir kararın win olasılığını ver. İleride stock_bot
    karar yoluna: take = proba >= gate; size_mult = clip(proba/0.5, ...)."""

    def __init__(self, model_file=MODEL_FILE):
        self.model = None
        self.feature_names = None
        if os.path.exists(model_file):
            with open(model_file, "r", encoding="utf-8") as f:
                blob = json.load(f)
            self.model = blob["model"]
            self.feature_names = blob["feature_names"]

    @staticmethod
    def features_from_votes(votes, coordinator_signal):
        """Coordinator decide() çıktısındaki votes listesinden özellik vektörü kur.
        (canlı wiring için — build_dataset ile aynı düzen)."""
        by = {v.get("agent"): v for v in votes}
        feat = []
        for a in AGENTS:
            v = by.get(a)
            if v:
                feat.append(SIGNAL_MAP.get(v.get("signal", "HOLD"), 0.0))
                feat.append(float(v.get("confidence", 0) or 0) / 100.0)
            else:
                feat += [0.0, 0.0]
        feat.append(1.0 if coordinator_signal in ("BUY", "LONG") else 0.0)
        return np.array([feat], float)

    def predict_proba(self, votes, coordinator_signal):
        if self.model is None:
            return None
        X = self.features_from_votes(votes, coordinator_signal)
        return float(predict_proba_raw(self.model, X)[0])


# ============================================================
# CLI
# ============================================================
def main():
    args = list(sys.argv[1:])
    min_samples = DEFAULT_MIN_SAMPLES
    if "--min" in args:
        i = args.index("--min")
        min_samples = int(args[i + 1]); del args[i:i + 2]
    path = args[0] if args else os.path.join("state_paper", "agent_performance.json")

    print("=" * 72)
    print("  🧬 META-LABELER — ajan oyları win/loss'u öngörüyor mu?")
    print(f"  Veri: {path}")
    print("=" * 72)

    X, y, fnames, meta, err = build_dataset(path)
    if err:
        print(f"\n  ⚠️ {err}")
        print("  → Bot kapanmış işlem ürettikçe (paper-track) bu dosya birikir.")
        print("    Railway'deki state_paper/agent_performance.json'ı buraya çekip")
        print("    tekrar çalıştır. Altyapı hazır; yalnız VERİ bekliyor.")
        return

    n = len(y)
    n_pos = int(y.sum()); n_neg = n - n_pos
    print(f"\n  Kapanmış işlem (satır):  {n}")
    print(f"  WIN / LOSS:              {n_pos} / {n_neg}  (baz win-rate {y.mean()*100:.1f}%)")
    print(f"  Özellik sayısı:          {len(fnames)}")

    if n < min_samples or n_pos < 5 or n_neg < 5:
        need = max(min_samples - n, 0)
        print(f"\n  ⏳ Güvenilir eğitim için veri YETERSİZ "
              f"(min {min_samples}, her sınıftan ≥5).")
        if need:
            print(f"     ~{need} kapalı işlem daha gerek.")
        print("     Altyapı + AUC/threshold pipeline hazır; veri gelince otomatik anlamlı olur.")
        return

    # K-fold OOF AUC (dürüst, out-of-sample)
    k = int(min(5, n_pos, n_neg))
    k = max(k, 2)
    oof = kfold_oof(X, y, k)
    auc = auc_score(y, oof)

    print(f"\n  {'─'*40}")
    print(f"  📈 {k}-fold OOF AUC: {auc:.3f}")
    if auc >= 0.58:
        verdict = "✅ GÜÇLÜ sinyal — wire etmeye değer (trade-gate/size)."
    elif auc >= 0.54:
        verdict = "⚠️ ZAYIF ama var — daha çok veri + özellik ile denenebilir."
    else:
        verdict = "❌ Sinyal yok (≈0.5) — ajan oyları win/loss öngörmüyor; wire etme."
    print(f"  KARAR: {verdict}")

    print(f"\n  {'─'*40}")
    print("  Eşik tablosu (OOF) — meta-labeling false-positive'i nasıl süpürür:")
    print(f"  {'eşik':<18}{'coverage':>10}{'precision(WR)':>16}{'işlem':>8}")
    for label, cov, prec, cnt in threshold_table(y, oof):
        pr = f"{prec:.1f}%" if prec == prec else "—"
        print(f"  {label:<18}{cov:>9.1f}%{pr:>16}{cnt:>8}")

    # Tam veriyle final model eğit + kaydet
    model = train_logreg(X, y)
    blob = {"model": model, "feature_names": fnames,
            "n_samples": n, "oof_auc": round(auc, 3), "k_folds": k}
    with open(MODEL_FILE, "w", encoding="utf-8") as f:
        json.dump(blob, f, indent=2, ensure_ascii=False)
    print(f"\n  📁 {MODEL_FILE} kaydedildi (n={n}, AUC={auc:.3f})")

    # Özellik önemi (|standardize edilmiş ağırlık|)
    w = np.abs(np.array(model["weights"]))
    top = np.argsort(w)[::-1][:6]
    print("\n  En etkili özellikler:")
    for i in top:
        print(f"    {fnames[i]:<18} |w|={w[i]:.3f}")


if __name__ == "__main__":
    main()
