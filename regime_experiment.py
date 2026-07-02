"""
Regime Experiment — rejim-koşullu katılım modlarını walk-forward ile A/B test eder.

SORU: Rejim-koşullu katılım (veya index-overlay) OOS'ta SPY açığını kapatıyor mu?
Körlemesine "rejim filtresi ekledim" demek yerine ÖLÇER. Her modu aynı kayan
pencerelerde koşar, ortalama alpha / SPY'ı geçme oranı / Sharpe karşılaştırır.

Modlar (backtest.py BT_REGIME_MODE):
  off     — rejim etkisi yok (temiz baseline)
  base    — mevcut davranış (BEAR'da buy_conf+10, short-10)
  flat    — BEAR'da yeni LONG açma (risk-off)
  scale   — pozisyon boyutunu rejime göre ölçekle (bull/bear mult)
  overlay — boştaki nakit BEAR-dışı SPY getirisi kazanır (beta capture)
            → "aktif trading SPY'ın ÜSTÜNE alpha katıyor mu?" testi

BT_CACHE=1 ile veri bir kez çekilir, modlar arası paylaşılır (≈5× hız).

Kullanım:
    py -X utf8 regime_experiment.py                 # paper-aggr, 2 pencere
    py -X utf8 regime_experiment.py --live 6 6 3    # live config, 6ay×3 pencere
"""
import os
import sys
import json

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

os.environ["BT_CACHE"] = "1"  # modlar arası veri paylaşımı (deneyi hızlandırır)
from walk_forward import run_window  # noqa: E402

MODES = ["off", "base", "flat", "scale", "overlay"]


def run_mode(mode, n, window, step, capital, use_pa):
    os.environ["BT_REGIME_MODE"] = mode
    windows = []
    for i in range(n):
        offset = i * step * 30
        r = run_window(offset, window, capital, use_pa)
        if r.get("ok"):
            windows.append(r)
    if not windows:
        return None
    alphas = [w["alpha_pct"] for w in windows]
    rets = [w["return_pct"] for w in windows]
    return {
        "n": len(windows),
        "beat_spy": sum(1 for a in alphas if a > 0),
        "mean_alpha": float(np.mean(alphas)),
        "worst_alpha": float(np.min(alphas)),
        "mean_return": float(np.mean(rets)),
        "mean_sharpe": float(np.mean([w["sharpe"] for w in windows])),
        "windows": windows,
    }


def main():
    args = list(sys.argv[1:])
    use_pa = True
    if "--live" in args:
        use_pa = False
        args.remove("--live")
    window = int(args[0]) if len(args) > 0 else 6
    step = int(args[1]) if len(args) > 1 else 6
    n = int(args[2]) if len(args) > 2 else 2
    capital = float(args[3]) if len(args) > 3 else 100000.0

    mode_str = "LIVE (konservatif)" if not use_pa else "PAPER AGGRESSIVE"
    print("=" * 78)
    print("  🧪 REGIME EXPERIMENT — rejim-koşullu katılım OOS'ta yardım ediyor mu?")
    print(f"  Config: {mode_str} | {window}ay pencere × {n} | adım {step}ay | "
          f"{len(MODES)} mod")
    print("=" * 78)

    results = {}
    for mode in MODES:
        print(f"\n  ▶ mod='{mode}' çalışıyor ({n} pencere)...", flush=True)
        res = run_mode(mode, n, window, step, capital, use_pa)
        if res is None:
            print("      ⚠️ geçerli pencere yok, atlandı")
            continue
        results[mode] = res
        print(f"      SPY'ı geçen {res['beat_spy']}/{res['n']} | "
              f"ort.alpha {res['mean_alpha']:+.2f}% | "
              f"en kötü {res['worst_alpha']:+.2f}% | "
              f"ort.getiri {res['mean_return']:+.2f}% | "
              f"Sharpe {res['mean_sharpe']:+.2f}")

    if not results:
        print("\n  ❌ Hiç sonuç yok — veri/bağlantı sorunu.")
        return

    # ---- Karşılaştırma tablosu ----
    print("\n" + "=" * 78)
    print("  📊 MOD KARŞILAŞTIRMASI (out-of-sample, ortalama)")
    print("=" * 78)
    print(f"  {'mod':<10}{'SPY-geçen':>11}{'ort.alpha':>12}{'en-kötü':>11}"
          f"{'ort.getiri':>12}{'Sharpe':>9}")
    print("  " + "─" * 64)
    # mean_alpha'ya göre sırala (yüksek = iyi)
    ordered = sorted(results.items(), key=lambda kv: kv[1]["mean_alpha"], reverse=True)
    for mode, r in ordered:
        print(f"  {mode:<10}{r['beat_spy']}/{r['n']:<9}{r['mean_alpha']:>+11.2f}%"
              f"{r['worst_alpha']:>+10.2f}%{r['mean_return']:>+11.2f}%"
              f"{r['mean_sharpe']:>+9.2f}")

    # ---- Yorum / öneri ----
    # ÖNEMLİ: overlay bir DEPLOYABLE rejim modu DEĞİL — "boştaki nakdi index'te tut"
    # benchmark'ı (aktif trading SPY'a değer katıyor mu testi). Deployable modları
    # (off/base/flat/scale) kendi aralarında kıyasla; overlay'i ayrı yorumla.
    DEPLOYABLE = ["off", "base", "flat", "scale"]
    deployable = {m: results[m] for m in DEPLOYABLE if m in results}
    overlay = results.get("overlay")
    bm = (max(deployable.items(), key=lambda kv: kv[1]["mean_alpha"])[0]
          if deployable else None)  # en iyi deployable mod (JSON + öneri için)
    print(f"\n  {'─' * 64}")

    if deployable:
        best_dep = max(deployable.items(), key=lambda kv: kv[1]["mean_alpha"])
        bm, br = best_dep
        base = deployable.get("base")
        beat_any = any(r["beat_spy"] > 0 for r in deployable.values())
        print(f"  DEPLOYABLE REJIM MODLARI (en iyi: '{bm}' alpha {br['mean_alpha']:+.2f}%):")
        if not beat_any:
            print("    • Hiçbiri SPY'ı geçmiyor (0 pencere).")
        if base and bm != "base" and (br["mean_alpha"] - base["mean_alpha"]) > 1.0:
            print(f"    • '{bm}' base'i {br['mean_alpha']-base['mean_alpha']:+.2f}% geçti "
                  f"— değerlendirmeye değer (kademeli + paper'da doğrula).")
        else:
            print("    • Rejim-koşullu katılım belirgin EDGE KATMIYOR.")
        # flat/scale base'e eşitse uyar: bu pencerelerde tetiklenmemiş olabilir
        if base and results.get("flat", {}).get("mean_alpha") == base["mean_alpha"] \
                and results.get("scale", {}).get("mean_alpha") == base["mean_alpha"]:
            print("    • UYARI: flat==scale==base → bu pencerelerde BEAR-günü long'u yok "
                  "(windows muhtemelen BULL). flat/scale'i test etmek için bear-içeren "
                  "pencere gerek (daha eski/uzun offset).")

    if overlay is not None:
        oa = overlay["mean_alpha"]
        if oa > 1.0:
            verdict = "✅ aktif trading SPY'ın ÜSTÜNE alpha katıyor — strateji değerli."
        elif oa > -4.0:
            verdict = ("≈ index (hafif geride). Boşluğun çoğu NAKİTTE oturma fırsat "
                       "maliyetiymiş; aktif kısım ~sıfır/negatif katıyor → nakdi index'te "
                       "tut, aktif trading'i küçük tut + edge'i kanıtla.")
        else:
            verdict = ("❌ aktif trading DEĞER YOK EDİYOR — saf index al-tut daha iyi.")
        print(f"\n  OVERLAY BENCHMARK (alpha {oa:+.2f}%): {verdict}")
    print(f"  {'─' * 64}")

    # ---- Kaydet ----
    out = {
        "config_mode": "live" if not use_pa else "paper_aggressive",
        "window_months": window, "step_months": step, "n_windows": n,
        "best_deployable_mode": bm,
        "overlay_alpha": (overlay["mean_alpha"] if overlay else None),
        "summary": {m: {k: v for k, v in r.items() if k != "windows"}
                    for m, r in results.items()},
        "detail": results,
    }
    try:
        with open("regime_experiment_results.json", "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print("\n  📁 regime_experiment_results.json kaydedildi")
    except Exception as e:
        print(f"\n  JSON kayıt hatası: {e}")


if __name__ == "__main__":
    main()
