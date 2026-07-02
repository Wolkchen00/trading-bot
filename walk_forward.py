"""
Walk-Forward Harness — Edge'in REJİMLER ARASI tutarlılığını ölçer.

Tek bir backtest seni kandırır (bir pencere şans olabilir — bkz. P1 +%8.28
sonra P3 -%9.68). Bu harness aynı stratejiyi KAYAN out-of-sample pencerelerde
çalıştırır ve "SPY'ı KAÇ pencerede geçti?" sorusuna cevap verir. Bu, memory'deki
graduation kapısının ölçülebilir hâlidir: harness OOS'ta SPY'ı tutarlı geçmeden
LIVE agresifleştirilmez.

backtest.py'ı DEĞİŞTİRMEZ — mevcut BT_END_OFFSET env hook'unu kullanır.

Kullanım:
    py -X utf8 walk_forward.py                 # paper-aggressive config, 4×6ay
    py -X utf8 walk_forward.py --live          # LIVE (konservatif) config
    py -X utf8 walk_forward.py 6 3 5           # window=6ay, step=3ay, 5 pencere
    py -X utf8 walk_forward.py --live 6 6 4

NOT: Her pencere Alpaca'dan veri çeker (birkaç dakika sürebilir). Tam CPCV
(combinatorial purged CV) bir sonraki adım; bu, dürüst ve uygulanabilir
walk-forward versiyonudur.
"""
import io
import os
import sys
import json
import contextlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

# Windows konsolunda Türkçe/emoji çökmesin
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from backtest import BacktestEngine


def _sharpe(daily_equity):
    """backtest.py ile aynı basitleştirilmiş Sharpe."""
    if len(daily_equity) <= 2:
        return 0.0
    s = pd.Series([d["equity"] for d in daily_equity])
    r = s.pct_change().dropna()
    if r.std() > 0:
        return float((r.mean() / r.std()) * np.sqrt(252))
    return 0.0


def run_window(offset_days, window_months, capital, use_paper_aggressive):
    """Tek pencere çalıştır, metrikleri döndür (verbose çıktıyı yut)."""
    os.environ["BT_END_OFFSET"] = str(offset_days)
    engine = BacktestEngine(
        initial_capital=capital, use_paper_aggressive=use_paper_aggressive
    )
    # backtest.run() çok print eder — yut, biz kendi özetimizi basacağız
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink):
            engine.run(months=window_months)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    spy = engine.spy_buyhold_pct
    if spy is None or engine.total_trades == 0:
        return {"ok": False, "error": "veri yok / 0 trade", "spy": spy,
                "trades": engine.total_trades}

    total_return = engine.total_pnl / engine.initial_capital * 100
    pf = engine.gross_profit / max(engine.gross_loss, 1e-9)
    win_rate = engine.winning_trades / max(engine.total_trades, 1) * 100

    end_d = date.today() - timedelta(days=offset_days)
    start_d = end_d - timedelta(days=window_months * 30)

    return {
        "ok": True,
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
        "return_pct": round(total_return, 2),
        "spy_pct": round(spy, 2),
        "alpha_pct": round(total_return - spy, 2),
        "pf": round(pf, 2),
        "sharpe": round(_sharpe(engine.daily_equity), 2),
        "max_dd_pct": round(engine.max_drawdown * 100, 2),
        "trades": engine.total_trades,
        "win_rate": round(win_rate, 1),
    }


def main():
    args = list(sys.argv[1:])
    use_paper_aggressive = True
    if "--live" in args:
        use_paper_aggressive = False
        args.remove("--live")

    window_months = int(args[0]) if len(args) > 0 else 6
    step_months = int(args[1]) if len(args) > 1 else 6      # 6 = örtüşmeyen OOS
    n_windows = int(args[2]) if len(args) > 2 else 4
    capital = float(args[3]) if len(args) > 3 else 100000.0

    mode = "LIVE (konservatif)" if not use_paper_aggressive else "PAPER AGGRESSIVE"
    print("=" * 74)
    print("  🔁 WALK-FORWARD HARNESS — edge tutarlı mı?")
    print(f"  Config: {mode} | pencere: {window_months}ay | adım: {step_months}ay "
          f"| {n_windows} pencere")
    print(f"  Geriye kapsam: ~{window_months + step_months * (n_windows - 1)} ay")
    print("=" * 74)

    results = []
    for i in range(n_windows):
        offset = i * step_months * 30
        print(f"\n  [{i+1}/{n_windows}] offset={offset}g çalışıyor "
              f"(veri çekiliyor, bekleyin)...", flush=True)
        r = run_window(offset, window_months, capital, use_paper_aggressive)
        if not r.get("ok"):
            print(f"      ⚠️ atlandı: {r.get('error')}")
            continue
        results.append(r)
        beat = "✅ SPY+" if r["alpha_pct"] > 0 else "❌ SPY-"
        print(f"      {r['start']}→{r['end']} | "
              f"Getiri {r['return_pct']:+.2f}% | SPY {r['spy_pct']:+.2f}% | "
              f"Alpha {r['alpha_pct']:+.2f}% {beat} | PF {r['pf']:.2f} | "
              f"Sharpe {r['sharpe']:+.2f} | DD {r['max_dd_pct']:.1f}% | "
              f"{r['trades']} trade (WR {r['win_rate']:.0f}%)")

    if not results:
        print("\n  ❌ Hiç geçerli pencere yok — veri/bağlantı sorunu olabilir.")
        return

    # ---- Agregasyon ----
    n = len(results)
    alphas = [r["alpha_pct"] for r in results]
    rets = [r["return_pct"] for r in results]
    beat_spy = sum(1 for a in alphas if a > 0)
    profitable = sum(1 for x in rets if x > 0)
    frac_beat = beat_spy / n
    mean_alpha = float(np.mean(alphas))
    std_alpha = float(np.std(alphas))
    worst_alpha = float(np.min(alphas))
    mean_sharpe = float(np.mean([r["sharpe"] for r in results]))

    print("\n" + "=" * 74)
    print("  📊 WALK-FORWARD ÖZET (out-of-sample)")
    print("=" * 74)
    print(f"  Geçerli pencere:        {n}")
    print(f"  SPY'ı geçen:            {beat_spy}/{n}  ({frac_beat*100:.0f}%)")
    print(f"  Kârlı (getiri>0):       {profitable}/{n}")
    print(f"  Ortalama alpha:         {mean_alpha:+.2f}%")
    print(f"  Alpha std (kararlılık): {std_alpha:.2f}%  (düşük=tutarlı)")
    print(f"  En kötü pencere alpha:  {worst_alpha:+.2f}%")
    print(f"  Ortalama Sharpe:        {mean_sharpe:+.2f}")

    # ---- Verdict — graduation kapısı ----
    print(f"\n  {'─' * 50}")
    if frac_beat >= 0.7 and worst_alpha > -2.0:
        verdict = ("✅ GEÇTI — edge OOS'ta tutarlı. LIVE'ı KÜÇÜK adımlarla "
                   "agresifleştirmek savunulabilir (yine de kademeli).")
    elif frac_beat >= 0.5:
        verdict = ("⚠️ KARARSIZ — bazı pencerelerde geçiyor ama tutarsız/regime-"
                   "bağımlı. Agresifleştirme; önce edge'i sağlamlaştır (meta-label).")
    else:
        verdict = ("❌ KALDI — OOS'ta SPY'ı geçmiyor. De-risk/index duruşu rasyonel; "
                   "LIVE long_only + küçük kalsın. Paper'da öğrenmeye devam.")
    print(f"  KARAR: {verdict}")
    print(f"  {'─' * 50}")

    # ---- Kaydet ----
    out = {
        "config_mode": "live" if not use_paper_aggressive else "paper_aggressive",
        "window_months": window_months, "step_months": step_months,
        "n_windows_requested": n_windows, "n_windows_valid": n,
        "beat_spy": beat_spy, "frac_beat_spy": round(frac_beat, 3),
        "mean_alpha_pct": round(mean_alpha, 2),
        "std_alpha_pct": round(std_alpha, 2),
        "worst_alpha_pct": round(worst_alpha, 2),
        "mean_sharpe": round(mean_sharpe, 2),
        "windows": results,
    }
    try:
        with open("walk_forward_results.json", "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print("\n  📁 walk_forward_results.json kaydedildi")
    except Exception as e:
        print(f"\n  JSON kayıt hatası: {e}")


if __name__ == "__main__":
    main()
