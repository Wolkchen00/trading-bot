"""Alpaca FILL activities kayitlarindan fill ledger backfill araci.

Varsayilan davranis dry-run'dir. Kalici yazim yalniz acik ``--apply`` ile
yapilir. Journal kaniti olmayan order provenance'i tahmin edilmez.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.fill_ledger import read_fills, record_fill  # noqa: E402
from core.order_journal import resolve  # noqa: E402


def _load_fixture(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        payload = payload.get("activities", [])
    if not isinstance(payload, list):
        raise ValueError("fixture list veya {'activities': [...]} olmali")
    return [item for item in payload if isinstance(item, dict)]


def _fetch_activities(after: str | None, until: str | None) -> list[dict]:
    from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, get_base_url

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise RuntimeError("Alpaca API anahtarlari yok; --fixture kullanin")
    url = f"{get_base_url().rstrip('/')}/v2/account/activities/FILL"
    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    params: dict[str, Any] = {"direction": "asc", "page_size": 100}
    if after:
        params["after"] = after
    if until:
        params["until"] = until

    activities: list[dict] = []
    seen: set[str] = set()
    while True:
        response = requests.get(
            url, headers=headers, params=params, timeout=30
        )
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list):
            raise ValueError("Alpaca activities cevabi liste degil")
        new_count = 0
        for item in page:
            if not isinstance(item, dict):
                continue
            activity_id = str(item.get("id") or "")
            if activity_id and activity_id in seen:
                continue
            if activity_id:
                seen.add(activity_id)
            activities.append(item)
            new_count += 1
        if len(page) < int(params["page_size"]) or new_count == 0:
            break
        page_token = str(page[-1].get("id") or "")
        if not page_token:
            break
        params["page_token"] = page_token
    return activities


def _normalized_activity(activity: dict) -> dict:
    execution_id = str(activity.get("id") or "").strip() or None
    order_id = str(activity.get("order_id") or "").strip() or None
    side = str(activity.get("side") or "").upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError(f"gecersiz activity side: {activity.get('side')}")
    # TAHMIN YASAGI (Ihsan karar maddesi, RF-PLAN-3 R9): order journal deploy
    # ONCESI her emir icin bostur, dolayisiyla hicbir tarihsel dolumun
    # stratejisi KANITLANAMAZ -> provenance UNKNOWN, pnl_usd None kalir.
    # Backfill'in isi defteri TAMAMLAMAKTIR, gecmise PnL atfetmek degil;
    # tarihsel gerceklesen PnL R10'un isidir (broker dolumlarindan yeniden
    # kurulur). Maliyet tabani uydurmak, kaldirmaya calistigimiz sahte
    # kesinligi geri getirir. Kilit: test_r9_ledger.py::
    # test_backfill_never_guesses_pnl_or_provenance_for_unprovable_history
    provenance = resolve(order_id) if order_id else "UNKNOWN"
    return {
        "symbol": str(activity.get("symbol") or "").upper(),
        "side": side,
        "qty": abs(float(activity.get("qty") or 0)),
        "price": float(activity.get("price") or 0),
        "pnl_usd": None,
        "provenance": provenance,
        "execution_id": execution_id,
        "order_id": order_id,
        "client_order_id": (
            str(activity.get("client_order_id") or "").strip() or None
        ),
        "episode_id": None,
        "ts_utc": activity.get("transaction_time") or activity.get("date"),
        "degraded": execution_id is None,
    }


def backfill(activities: list[dict], *, apply: bool = False) -> dict:
    existing_keys = {
        str(item.get("dedupe_key")) for item in read_fills()
        if item.get("dedupe_key") is not None
    }
    pending: list[dict] = []
    skipped = 0
    seen = set(existing_keys)
    for activity in activities:
        record = _normalized_activity(activity)
        execution_id = record.get("execution_id")
        dedupe_key = execution_id or (
            f"{record.get('order_id')}|{record['side']}|"
            f"{float(record['qty'])}|{float(record['price'])}"
        )
        if dedupe_key in seen:
            skipped += 1
            continue
        seen.add(dedupe_key)
        pending.append(record)

    added = 0
    if apply:
        for record in pending:
            if record_fill(**record):
                added += 1

    distribution = Counter(record["symbol"] for record in pending)
    return {
        "to_add": len(pending),
        "added": added,
        "skipped": skipped,
        "unknown": sum(
            1 for record in pending if record["provenance"] == "UNKNOWN"
        ),
        "symbols": dict(sorted(distribution.items())),
        "apply": bool(apply),
    }


def _print_summary(summary: dict) -> None:
    mode = "APPLY" if summary["apply"] else "DRY-RUN"
    print(f"Mod: {mode}")
    print(f"Eklenecek: {summary['to_add']}")
    print(f"Eklendi: {summary['added']}")
    print(f"Atlanacak: {summary['skipped']}")
    print(f"UNKNOWN: {summary['unknown']}")
    symbols = summary["symbols"]
    rendered = ", ".join(f"{symbol}={count}" for symbol, count in symbols.items())
    print(f"Sembol dagilimi: {rendered or '-'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help="Degisiklik yazma (varsayilan).",
    )
    mode.add_argument(
        "--apply", action="store_true",
        help="Eksik activities kayitlarini fill ledger'a kalici yaz.",
    )
    parser.add_argument(
        "--fixture", help="Broker yerine yerel JSON activity fixture'i oku."
    )
    parser.add_argument("--after", help="Alpaca activities after filtresi.")
    parser.add_argument("--until", help="Alpaca activities until filtresi.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    activities = (
        _load_fixture(args.fixture)
        if args.fixture else _fetch_activities(args.after, args.until)
    )
    summary = backfill(activities, apply=args.apply)
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
