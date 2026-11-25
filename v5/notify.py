"""Owner-facing Turkish messages and their durable delivery outbox."""

from __future__ import annotations

import json
import os
import string
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo


def _decimal(value) -> Decimal:
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value))


def fmt_money(value) -> str:
    rounded = abs(_decimal(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def fmt_pct(value) -> str:
    rounded = (abs(_decimal(value)) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return format(rounded, ".1f").replace(".", ",")


def fmt_weight(value) -> str:
    rounded = (abs(_decimal(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return format(rounded, "f")


REASON_SENTENCES = {
    "TREND_UP": "Nasdaq yükseliş eğiliminde.",
    "TREND_MIXED": "Nasdaq kararsız, bu yüzden ana payı küçük tutuyorum.",
    "TREND_DOWN": "Nasdaq düşüş eğiliminde, bu yüzden ana para nakitte bekliyor.",
    "VOL_HIGH": "Piyasa çok oynak, bu yüzden payı küçük tutuyorum.",
    "VOL_CALM": "Piyasa sakin.",
    "BAND_HOLD": "Ana pay hedefe yeterince yakın.",
    "OVERLAY_ENTER": "Bugün sert bir düşüş oldu, sonraki işlem günü için küçük bir ek alım yaptım.",
    "OVERLAY_WOULD": "Bugün sert bir düşüş oldu; gerçek emir açık olsaydı sonraki işlem günü için küçük bir ek alım yapardım.",
    "OVERLAY_HELD": "Bugün sert bir düşüş oldu, bu yüzden elimdekinin bir kısmını sonraki işlem gününe kadar tutuyorum.",
    "OVERLAY_KEEP": "Düşüş sürdü, ek payı bir işlem günü daha tutuyorum.",
    "OVERLAY_EXIT": "Düşüş için tuttuğum ek payı sattım.",
    "OVERLAY_DATA_MISSING": "Bugün gün içi fiyat aralığına ulaşamadım, bu yüzden ek alım kuralını çalıştırmadım.",
    "BREAKER_REDUCE_ONLY": "Bugün sert düşüş olduğu için yeni alım yapmadım.",
    "DOUBT_REDUCE_ONLY": "Hesap değerini doğrulayamadığım için bugün yeni alım yapmadım.",
}


TEMPLATES = {
    "CLOSE_CORRECTED": "Gün sonu özetindeki hesap değeri yanlış okunmuş ({date}). Doğru kapanış değeri {equity} dolar.",
    "DRAWDOWN_ALERT": "Hesap en yüksek değerinden yüzde {pct} aşağıda, şu an {equity} dolar. Bu risk seviyesinde geçmişte görülen en derin düşüşe geldik. Kurala göre çalışmaya devam ediyorum. Durdurmak istersen Claude'a 'botu durdur' yaz.",
    "BREAKER": "Hesap bugün yüzde {pct} düştü. Bugün yeni alım yapmıyorum. Sonraki işlem gününde normal çalışmaya dönerim. Senden bir şey beklemiyorum.",
    "FLOOR_DONE": "Hesap en yüksek değerinden yüzde {pct} düştü, şu an {equity} dolar. Elimdeki Nasdaq fonunun tamamını sattım ve durdum. Devam etmemi istersen Claude'a 'botu devam ettir' yaz.",
    "FLOOR_FLAT": "Hesap en yüksek değerinden yüzde {pct} düştü, şu an {equity} dolar. Elimde Nasdaq fonu yoktu, bu yüzden satış yapmadan durdum. Devam etmemi istersen Claude'a 'botu devam ettir' yaz.",
    "FLOOR_PENDING": "Hesap en yüksek değerinden yüzde {pct} düştü, şu an {equity} dolar. Satışa başladım ama henüz bitmedi. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Denemeye devam ediyorum.",
    "FLOOR_WAITING": "Hesap en yüksek değerinden yüzde {pct} düştü, şu an {equity} dolar. Güvenlik satışını henüz başlatamadım, çünkü {cause}. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Denemeye devam ediyorum.",
    "FLOOR_WAITING_BLIND": "Hesap en yüksek değerinden yüzde {pct} düştü, şu an {equity} dolar. Güvenlik satışını henüz başlatamadım, çünkü {cause}. Denemeye devam ediyorum.",
    "RAIL_LIQUIDATING": "Güvenlik satışı hala bitmedi. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Denemeye devam ediyorum. Claude'a 'trading botuna bak' yazarsan inceler.",
    "HOLD_NOT_READY": "Bugün karar veremedim, çünkü {cause}. Elimdekilere dokunmadım. Sonraki işlem gününde yeniden denerim.",
    "MISSED": "Bugünkü karar saatini kaçırdım. Elimdekilere dokunmadım. Sonraki işlem gününde normal devam ederim.",
    "ORDER_FAILED": "Alpaca emrimi kabul etmedi. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Claude'a 'trading botuna bak' yazarsan sebebini inceler.",
    "ORDER_UNFILLED": "Emrim gerçekleşmedi ve iptal oldu. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Sonraki işlem gününde yeniden karar veririm.",
    "ORDER_PARTIAL": "Emrimin tamamı gerçekleşmedi. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Sonraki işlem gününde duruma yeniden bakarım.",
    "CAPPED_FILLED": "Hedefin tamamı için boş nakit yetmedi; alabildiğim kadarını aldım.",
    "CAPPED_IDLE": "Hedefin tamamı için boş nakit yok.",
    "UNRESOLVED": "Son emrimin akıbetini öğrenemedim. Netleşene kadar yeni işlem yapmıyorum. Claude'a 'trading botuna bak' yazarsan inceler.",
    "FOREIGN_ASSET": "Hesapta benim almadığım bir varlık ya da emir var ({symbol}). Karışmamak için işlem yapmıyorum. Bunu sen yaptıysan Claude'a haber ver.",
    "DATA_DOUBT": "Hesap değeri tutarsız göründü. Yanlış alarm olmasın diye güvenlik satışı yapmadım, izlemeye devam ediyorum.",
    "PAUSED": "Bot duraklatılmış durumda. Elimdekiler duruyor, yeni işlem yapmıyorum.",
    "NOT_STARTED": "Bot başlayamadı. Claude'a 'trading botuna bak' yaz.",
    "WATCHDOG": "Trading botu çalışmıyor görünüyor. Claude'a 'trading botuna bak' yaz.",
    "READINESS": "Bugün borsa açılmadan önce bir sorun gördüm: {cause}. Düzelmezse bugün işlem yapmayacağım.",
    "ERROR": "Bot beklenmeyen bir sorunla karşılaştı. Claude'a 'trading botuna bak' yaz.",
    "NOT_SENT": "Bugün bir işlem yapmam gerekiyordu ama emri gönderemedim, çünkü {cause}. Elimdekilere dokunmadım. Sonraki işlem gününde yeniden karar veririm.",
    "TEST": "Deneme bildirimi: bu mesajı görüyorsan bildirimler çalışıyor.",
}


CAUSE_SENTENCES = {
    "PRICE_STALE": "fiyat bilgisine ulaşamadım",
    "PRICE_BAD": "fiyat bilgisine ulaşamadım",
    "PRICE_JUMP": "fiyat bilgisine ulaşamadım",
    "DATA_ERROR": "fiyat bilgisine ulaşamadım",
    "HISTORY_SHORT": "geçmiş fiyatlar eksik geldi",
    "HISTORY_BAD": "geçmiş fiyatlar eksik geldi",
    "HISTORY_GAP": "geçmiş fiyatlar eksik geldi",
    "HISTORY_STALE": "geçmiş fiyatlar eksik geldi",
    "DERIVED_BAD": "geçmiş fiyatlar eksik geldi",
    "BROKER_DOWN": "Alpaca'ya bağlanamadım",
    "CALENDAR_MISSING": "bugünün borsa saatlerini öğrenemedim",
    "MARKET_CLOSED": "bugünün borsa saatlerini öğrenemedim",
    "CLOCK_DOUBT": "saat bilgisi tutarsız göründü",
    "STATE_CORRUPT": "kayıt dosyam bozuk",
    "WEIGHT_BAD": "kayıt dosyam bozuk",
    "FRACTION_BAD": "kayıt dosyam bozuk",
    "EQUITY_DOUBT": "hesap değerini doğrulayamadım",
    "SNAPSHOT_INVALID": "hesap değerini doğrulayamadım",
    "RAIL_CONFIRMING": "hesap değerini doğrulayamadım",
    "NOTIFY_MISSING": "bildirim kanalı tanımlı değil",
    "PEAK_MISSING": "başlangıç değeri kayıtlı değil",
    "ACCOUNT_STATUS": "Alpaca hesabı işleme kapalı görünüyor",
    "BLOCKED": "Alpaca hesabı işleme kapalı görünüyor",
    "FOREIGN_ASSET": "hesapta benim almadığım bir varlık ya da emir var",
    "UNRESOLVED": "önceki emrimin akıbeti hala belli değil",
    "PAUSED": "bot duraklatılmış durumda",
    "FLOOR_HALT": "güvenlik sınırı aşıldığı için durmuş durumdayım",
    "BREAKER": "bugün sert düşüş olduğu için yeni alım yapmıyorum",
    "EQUITY_DOUBT": "hesap değerini doğrulayamadım",
    "TIME_OUT": "süre yetmedi",
    "NOT_FOUND": "emrim Alpaca'ya ulaşmadı",
}


FLOOR_WAIT_CAUSES = {
    "CLOCK_DOUBT": "saat bilgisi tutarsız göründü",
    "CALENDAR_MISSING": "bugünün borsa saatlerini öğrenemedim",
    "MARKET_CLOSED": "borsa şu an kapalı",
    "TOO_LATE": "kapanışa çok az kaldı",
    "UNRESOLVED": "önceki emrimin akıbeti hala belli değil",
    "FOREIGN_PRODUCT_ORDER": "hesapta benim vermediğim bir Nasdaq fonu emri var",
    "BROKER_DOWN": "Alpaca'ya bağlanamadım",
    "SNAPSHOT_INVALID": "hesap değerini doğrulayamadım",
    "STATE_CORRUPT": "kayıt dosyam bozuk",
    "DRY_RUN": "gerçek emir gönderme kapalı",
}


# What became of a decision that was not carried out. It replaces the decision's reasons in the daily summary:
# the summary never says what a decision would have done.
OUTCOME_SENTENCES = {
    "NOT_SENT": "Karar verdim ama emri gönderemedim.",
    "REFUSED": "Karar verdim ama Alpaca emrimi kabul etmedi.",
    "UNFILLED": "Karar verdim ama emrim gerçekleşmedi.",
    "PARTIAL": "Karar verdim ama emrimin yalnız bir kısmı gerçekleşti.",
    "NO_CASH": "Karar verdim ama alım için boş nakit yoktu.",
    "UNKNOWN": "Emrimin sonucunu henüz öğrenemedim.",
    "FLOOR": "Hesap güvenlik sınırının altına düştü.",
}


# Every message says which account it is about: nothing from the practice account or from a dry run may read
# like news about real money.
PAPER_LABEL = "DENEME HESABI. "
DRY_RUN_LABEL = "DENEME, gerçek emir gönderilmedi. "


def cause_for(code) -> str:
    return CAUSE_SENTENCES.get(code, "beklenmeyen bir sorun çıktı")


def _period(label, value) -> str:
    if value is None:
        return f"{label}: bilgi yok."           # not computable: never reported as a computed zero
    if fmt_pct(value) == "0,0":
        return f"{label}: değişim yok."
    direction = "artıda" if value > 0 else "ekside"
    return f"{label}: yüzde {fmt_pct(value)} {direction}."


def build_digest(ctx) -> str:
    required = (
        "date", "prefix", "equity", "day_usd", "day_pct", "product_weight",
        "action", "action_usd", "reason_keys", "month_pct", "total_pct", "status_text",
    )
    for key in required:
        ctx[key]
    prefixes = {"": "", "paper": PAPER_LABEL, "dry_run": DRY_RUN_LABEL}
    if ctx["prefix"] not in prefixes:
        raise ValueError("unknown digest prefix")
    session = ctx["date"]
    date_text = session.strftime("%d.%m.%Y")
    title = f"Gün sonu özeti ({date_text})"
    if ctx.get("correction"):
        title += ", düzeltme"
    lines = [prefixes[ctx["prefix"]] + title]

    account = (
        f"Hesap şu an {fmt_money(ctx['equity'])} dolar."
        if ctx.get("as_of_now")
        else f"Hesap: {fmt_money(ctx['equity'])} dolar."
    )
    day_usd = ctx["day_usd"]
    if ctx.get("as_of_now") or day_usd is None:
        pass                            # the day's change is not known: nothing is claimed about it
    elif fmt_money(day_usd) == "0,00":
        account += " Bugün değişmedi."
    elif day_usd > 0:
        account += f" Bugün yüzde {fmt_pct(ctx['day_pct'])} arttı ({fmt_money(day_usd)} dolar)."
    else:
        account += f" Bugün yüzde {fmt_pct(ctx['day_pct'])} azaldı ({fmt_money(day_usd)} dolar)."
    lines.append(account)

    product = int(fmt_weight(ctx["product_weight"]))
    owner = int(fmt_weight(ctx.get("owner_weight") or 0.0))
    other = int(fmt_weight(ctx.get("other_weight") or 0.0))
    # Each part is rounded by itself (82 + 17 + 2): together they must never read as more than the account.
    named = {"product": max(0, product), "owner": owner if owner >= 1 else 0, "other": other if other >= 1 else 0}
    excess = sum(named.values()) - 100
    while excess > 0:
        largest = max(named, key=named.get)
        cut = min(excess, named[largest])
        if cut <= 0:
            break
        named[largest] -= cut
        excess -= cut
    product, owner, other = named["product"], named["owner"], named["other"]
    cash = max(0, 100 - product - owner - other)
    held = "Elimde şu an" if ctx.get("holdings_now") or ctx.get("as_of_now") else "Elimde"
    if owner >= 1:
        # The owner's own shares sit beside the fund: say so, or the cash figure would be wrong.
        label = ctx["owner_label"]
        parts = [f"yüzde {product} Nasdaq fonu", f"yüzde {owner} senin aldığın hisse ({label})"]
        if other >= 1:
            parts.append(f"yüzde {other} benim almadığım varlık")
        parts.append(f"yüzde {cash} nakit")
        lines.append(f"{held}: " + ", ".join(parts) + ".")
    else:
        parts = [f"yüzde {product} Nasdaq fonu"]
        if other >= 1:
            parts.append(f"yüzde {other} benim almadığım varlık")
        parts.append(f"yüzde {cash} nakit")
        lines.append(f"{held}: " + ", ".join(parts) + ".")
    action = ctx["action"]
    if action not in {
        "none", "buy", "sell", "sell_all", "unknown",
        "would_buy", "would_sell", "would_sell_all",
    }:
        raise ValueError("unknown digest action")
    if action == "none":
        action_text = "İşlem yapmadım."
    elif action == "unknown":
        action_text = "Bir emir gönderdim ama sonucunu henüz öğrenemedim."
    elif action == "buy":
        action_text = f"{fmt_money(ctx['action_usd'])} dolarlık Nasdaq fonu aldım."
    elif action == "sell":
        action_text = f"{fmt_money(ctx['action_usd'])} dolarlık Nasdaq fonu sattım."
    elif action == "would_buy":
        action_text = f"Gerçek emir açık olsaydı {fmt_money(ctx['action_usd'])} dolarlık Nasdaq fonu alırdım."
    elif action == "would_sell":
        action_text = f"Gerçek emir açık olsaydı {fmt_money(ctx['action_usd'])} dolarlık Nasdaq fonu satardım."
    elif action == "would_sell_all":
        action_text = "Gerçek emir açık olsaydı elimdeki fonun tamamını satardım."
    else:
        action_text = "Elimdeki fonun tamamını sattım."
    lines.append("Bugün ne yaptım: " + action_text)

    outcome = ctx.get("outcome")
    if outcome is not None:
        reason_text = OUTCOME_SENTENCES[outcome]
    else:
        reasons = []
        if ctx["reason_keys"] is not None:
            reasons = [REASON_SENTENCES[key] for key in ctx["reason_keys"] if key in REASON_SENTENCES]
        reason_text = " ".join(reasons) if reasons else "Bugün karar veremedim."
    lines.append("Neden: " + reason_text)
    lines.append(_period("Bu ay", ctx["month_pct"]) + " " + _period("Başlangıçtan beri", ctx["total_pct"]))
    status = ctx["status_text"]
    lines.append("Durum: Her şey normal." if status is None else "Durum: " + status)
    return "\n".join(lines)


def build_alert(kind, **fields) -> str:
    template = TEMPLATES[kind]
    names = {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}
    formatted = {name: fields[name] for name in names}
    if "pct" in names:
        formatted["pct"] = fmt_pct(formatted["pct"])
    if "equity" in names:
        formatted["equity"] = fmt_money(formatted["equity"])
    if "product_pct" in names:
        formatted["product_pct"] = fmt_weight(formatted["product_pct"])
    if "date" in names:
        formatted["date"] = formatted["date"].strftime("%d.%m.%Y")     # like the first line of the summary
    return template.format(**formatted)


def _parse_time(value) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _seconds(now, then) -> float:
    try:
        return (now - then).total_seconds()
    except TypeError:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return (now - then).total_seconds()


class Outbox:
    def __init__(self, state_dir):
        self.path = Path(state_dir) / "outbox.jsonl"

    def _scan(self):
        messages = []
        by_id = {}
        attempts = {}
        delivered = {}
        corrupt = 0
        try:
            handle = self.path.open(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return messages, attempts, delivered, corrupt
        with handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                    if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                        raise ValueError
                    if "event" not in row:
                        for key in ("time", "kind", "state_key", "text", "priority"):
                            if key not in row:
                                raise ValueError
                        _parse_time(row["time"])
                        if row["id"] in by_id:
                            raise ValueError
                        messages.append(row)
                        by_id[row["id"]] = row
                        attempts[row["id"]] = []
                    else:
                        if row["event"] not in {"attempt", "delivered"} or row["id"] not in by_id:
                            raise ValueError
                        event_time = _parse_time(row["time"])
                        if row["event"] == "attempt":
                            attempts[row["id"]].append(event_time)
                        else:
                            delivered[row["id"]] = event_time
                except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
                    corrupt += 1
        return messages, attempts, delivered, corrupt

    @property
    def corrupt_lines(self) -> int:
        return self._scan()[3]

    def _append(self, row):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
        with self.path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def add(self, kind, text, now, state_key="", dedupe_key=None, priority=4) -> str:
        messages, _, _, _ = self._scan()
        if dedupe_key is not None:
            for message in messages:
                if message.get("dedupe_key") == dedupe_key:
                    return message["id"]
        identifier = uuid.uuid4().hex
        self._append(
            {
                "id": identifier,
                "time": now.isoformat(),
                "kind": kind,
                "state_key": state_key,
                "dedupe_key": dedupe_key,
                "text": text,
                "priority": priority,
            }
        )
        return identifier

    def pending(self, now) -> list:
        messages, attempts, delivered, _ = self._scan()
        result = []
        waits = (1, 2, 4, 8, 16, 32, 60)
        for message in messages:
            identifier = message["id"]
            age = _seconds(now, _parse_time(message["time"]))
            if identifier in delivered or age >= 48 * 60 * 60:
                continue
            history = attempts[identifier]
            if history:
                wait_minutes = waits[min(len(history) - 1, len(waits) - 1)]
                if _seconds(now, history[-1]) < wait_minutes * 60:
                    continue
            result.append(message)
        return result

    def _event(self, identifier, event, now):
        messages, _, _, _ = self._scan()
        if not any(message["id"] == identifier for message in messages):
            raise KeyError(identifier)
        self._append({"id": identifier, "event": event, "time": now.isoformat()})

    def mark_attempt(self, identifier, now):
        self._event(identifier, "attempt", now)

    def mark_delivered(self, identifier, now):
        self._event(identifier, "delivered", now)

    def oldest_undelivered_minutes(self, now) -> float | None:
        messages, _, delivered, _ = self._scan()
        ages = [
            _seconds(now, _parse_time(message["time"])) / 60.0
            for message in messages
            if message["id"] not in delivered
            and _seconds(now, _parse_time(message["time"])) < 48 * 60 * 60
        ]
        return max(ages) if ages else None

    def owner_unreachable(self, now) -> bool:
        messages, _, delivered, _ = self._scan()
        undelivered = [message for message in messages if message["id"] not in delivered]
        if any(
            120 * 60 < _seconds(now, _parse_time(message["time"])) < 48 * 60 * 60
            for message in undelivered
        ):
            return True
        for message in undelivered:
            written = _parse_time(message["time"])
            if _seconds(now, written) < 48 * 60 * 60:
                continue
            if not any(moment >= written for moment in delivered.values()):
                return True
        return False

    def last(self, kind) -> dict | None:
        messages = self._scan()[0]
        for message in reversed(messages):
            if message["kind"] == kind:
                return message
        return None

    def _messages(self):
        return self._scan()[0]


def default_post(url, data: bytes, headers: dict, timeout):
    import requests

    return requests.post(url, data=data, headers=headers, timeout=timeout).status_code


class Notifier:
    def __init__(self, settings, outbox, post_fn, now_fn):
        self.settings = settings
        self.outbox = outbox
        self.post_fn = post_fn
        self.now_fn = now_fn
        self._posted_successfully = set()

    @property
    def configured(self) -> bool:
        return bool(self.settings.ntfy_topic)

    def _label(self) -> str:
        mode = getattr(self.settings, "mode", None)
        if mode == "paper":
            return PAPER_LABEL
        if mode == "live" and getattr(self.settings, "orders_enabled", True) is False:
            return DRY_RUN_LABEL
        return ""

    @staticmethod
    def _priority(kind):
        return 5 if kind in {
            "FLOOR_DONE", "FLOOR_FLAT", "FLOOR_PENDING", "FLOOR_WAITING", "FLOOR_WAITING_BLIND",
            "RAIL_LIQUIDATING", "NOT_STARTED",
        } else 4

    def alert(self, kind, state_key="", real=False, **fields):
        text = ("" if real else self._label()) + build_alert(kind, **fields)
        now = self.now_fn()
        future_key = False
        for message in self.outbox._messages():
            if message["kind"] == kind and message.get("state_key", "") == state_key:
                age = _seconds(now, _parse_time(message["time"]))
                if state_key and age >= 0:
                    return None
                if state_key:
                    future_key = True
                if 0 <= age < 4 * 60 * 60:
                    return None
        identifier = self.outbox.add(
            kind,
            text,
            now,
            state_key=state_key,
            dedupe_key=f"ALERT:{kind}:{state_key}" if state_key and not future_key else None,
            priority=self._priority(kind),
        )
        self.flush()
        return identifier

    def digest(self, session_date, ctx) -> str:
        suffix = ":fix" if ctx.get("correction") else ""
        identifier = self.outbox.add(
            "DIGEST",
            build_digest(ctx),
            self.now_fn(),
            dedupe_key=f"DIGEST:{session_date.isoformat()}:{ctx['prefix']}{suffix}",
            priority=3,
        )
        self.flush()
        return identifier

    def reminder(self, kind, session_date, state_key="", **fields):
        identifier = self.outbox.add(
            kind,
            self._label() + build_alert(kind, **fields),
            self.now_fn(),
            state_key=state_key,
            dedupe_key=f"{kind}:{state_key}:{session_date.isoformat()}",
            priority=self._priority(kind),
        )
        self.flush()
        return identifier

    def flush(self) -> int:
        if not self.configured:
            return 0
        delivered = 0
        for message in self.outbox.pending(self.now_fn()):
            if message["id"] in self._posted_successfully:
                continue
            headers = {
                "Title": "Trading bot",
                "Priority": str(message["priority"]),
                "Tags": "bar_chart" if message["kind"] == "DIGEST" else "warning",
            }
            try:
                now = self.now_fn()
                body = message["text"]
                if message["kind"] != "DIGEST" and _seconds(now, _parse_time(message["time"])) > 60 * 60:
                    written = _parse_time(message["time"]).astimezone(ZoneInfo("America/New_York"))
                    delayed = f"Gecikmiş mesaj ({written:%d.%m.%Y}): "
                    for label in (PAPER_LABEL, DRY_RUN_LABEL):
                        if body.startswith(label):
                            body = label + delayed + body[len(label):]
                            break
                    else:
                        body = delayed + body
                status = self.post_fn(
                    self.settings.ntfy_base_url.rstrip("/") + "/" + self.settings.ntfy_topic,
                    body.encode("utf-8"),
                    headers,
                    10,
                )
                if 200 <= status <= 299:
                    self._posted_successfully.add(message["id"])
                    delivered += 1
                    try:
                        self.outbox.mark_delivered(message["id"], self.now_fn())
                    except Exception:
                        pass
                else:
                    try:
                        self.outbox.mark_attempt(message["id"], self.now_fn())
                    except Exception:
                        pass
            except Exception:
                try:
                    self.outbox.mark_attempt(message["id"], self.now_fn())
                except Exception:
                    pass
        return delivered
