import json

data = json.load(open("logs/stock_backtest_20260410_081638.json"))
trades = data.get("trades", [])

print("=== TUM TRADE DETAYLARI ===")
for i, t in enumerate(trades):
    action = t["action"]
    sym = t["symbol"]
    price = t["price"]
    time_str = str(t.get("time", ""))[:10]

    if action == "BUY":
        invest = t.get("invest", 0)
        conf = t.get("confidence", 0)
        reasons = t.get("reasons", "")
        print(f"  {i+1:2d}. {time_str} | BUY  {sym:5s} @ ${price:8.2f} | Invest: ${invest:6.2f} | Conf: {conf:3.0f} | {reasons}")

    elif action == "SELL":
        pnl = t.get("pnl_usd", 0)
        pnl_pct = t.get("pnl_pct", 0)
        reason = t.get("reason", "")
        mark = "+" if pnl >= 0 else ""
        print(f"  {i+1:2d}. {time_str} | SELL {sym:5s} @ ${price:8.2f} | P&L: {mark}${pnl:6.2f} ({pnl_pct:+5.1f}%) | {reason}")

    elif action == "PARTIAL_SELL":
        pnl_pct = t.get("pnl_pct", 0)
        print(f"  {i+1:2d}. {time_str} | PART {sym:5s} @ ${price:8.2f} | {pnl_pct:+5.1f}%")

print()
print("=== OZET ===")
print(f"  Toplam Getiri: ${data['total_return_usd']:.2f} ({data['total_return_pct']:.1f}%)")
print(f"  Win/Loss: {data['wins']}W / {data['losses']}L (WR: {data['win_rate']:.1f}%)")
print(f"  Profit Factor: {data['profit_factor']:.2f}")
print(f"  Max DD: {data['max_drawdown_pct']:.2f}%")
print(f"  Sharpe: {data['sharpe_ratio']:.2f}")
print(f"  Satis Sebepleri: {data['sell_reasons']}")
