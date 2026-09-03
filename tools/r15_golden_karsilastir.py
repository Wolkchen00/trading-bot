"""R15 altin cikti karsilastirmasi (elle dogrulama araci).

`tests/fixtures/r15_golden.json` R15 ONCESI davranistir. Bu arac mevcut kodu
ayni senaryolarla kosar ve farki gosterir.

    py tools/r15_golden_karsilastir.py            # mevcut config ile
    SOCIAL_AGENT_ENABLED=true py tools/...        # ajan acikken (birebir ayni olmali)
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_enable import enabled_agents, is_agent_enabled
from tools.r15_golden_uret import GOLDEN_PATH, uret


def main() -> int:
    beklenen = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    mevcut = uret()

    social_acik = is_agent_enabled("SocialAgent")
    print()
    print("=" * 64)
    print(f"  SocialAgent etkin mi : {social_acik}")
    print(f"  Etkin ajanlar        : {', '.join(enabled_agents())}")
    print("=" * 64)

    fark = 0
    for ad in sorted(beklenen["senaryolar"]):
        once = beklenen["senaryolar"][ad]["beklenen"]
        sonra = mevcut["senaryolar"][ad]["beklenen"]
        if once != sonra:
            fark += 1
            print(f"\nFARK: {ad}")
            for k in sorted(set(once) | set(sonra)):
                if once.get(k) != sonra.get(k):
                    print(f"    {k}: {once.get(k)} -> {sonra.get(k)}")

    toplam = len(beklenen["senaryolar"])
    print(f"\nSENARYO: {toplam - fark}/{toplam} birebir ayni, {fark} farkli")

    ga = beklenen["agirlik_gecis_siniri"]
    gb = mevcut["agirlik_gecis_siniri"]
    print("\nAGIRLIK GECIS SINIRI (MIN_TRADES_FOR_EVAL = 5):")
    for k in sorted(ga):
        durum = "ayni" if ga[k] == gb[k] else "FARKLI"
        print(f"  {k}: {durum}")
        if ga[k] != gb[k]:
            print(f"      once : {ga[k]}")
            print(f"      sonra: {gb[k]}")

    if social_acik:
        print("\nBEKLENTI: ajan ACIK -> her sey birebir ayni olmali.")
        return 0 if fark == 0 and ga == gb else 1

    print("\nBEKLENTI: ajan KAPALI -> sinyal/guven/ws ayni, agirlik vektorunden")
    print("SocialAgent SUZULMUS olmali (payi dagitilmamis).")
    return 0 if fark == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
