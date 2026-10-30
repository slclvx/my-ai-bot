"""
Alpaca PAPER-trading script for the SPY 10-month moving average rule
(beginner-friendly)

The rule (same as the backtest):
  - Look at SPY's closing price on the last trading day of each COMPLETED month.
  - If the latest month-end close is ABOVE the average of the last 10 month-end
    closes -> the target is to hold SPY.
  - Otherwise -> the target is to hold cash.

You can run this script on any day. It always uses the last COMPLETED month,
so the answer is the same all month long. It compares the target with what
the paper account holds and only trades if they differ.

SAFETY:
  - This script is hard-wired to Alpaca's PAPER (fake money) environment.
  - DRY_RUN = True means it only PRINTS what it would do and places no orders.
    Set it to False yourself once the printout looks right.

One-time setup:
  1. Create a free Alpaca account and open the PAPER trading dashboard.
  2. Generate Paper API keys there.
  3. Store them as environment variables (never paste keys into this file):
       Windows (Command Prompt):
         setx ALPACA_API_KEY "your-paper-key"
         setx ALPACA_SECRET_KEY "your-paper-secret"
       Then CLOSE and REOPEN the Command Prompt so they take effect.
  4. Install the libraries:
         python -m pip install alpaca-py yfinance pandas

Run it:
    python alpaca_paper_10month.py
"""

import csv
import os
import sys
from datetime import datetime

import pandas as pd
import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

# ---------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------
SYMBOL = "SPY"
SMA_MONTHS = 10          # Must match the backtest
DRY_RUN = False           # True = print only, no orders. Change to False to place paper orders.
INVEST_FRACTION = 0.98   # Use 98% of cash when buying, leaving a small cushion
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trading_log.csv")


# ---------------------------------------------------------------
# STEP 1: Work out today's signal (same logic as the backtest)
# ---------------------------------------------------------------
def get_signal():
    """Return the rule's current decision and the numbers behind it."""
    data = yf.download(SYMBOL, period="5y", interval="1d",
                       auto_adjust=True, progress=False)
    if data.empty:
        sys.exit("Could not download price data. Check your internet connection.")

    close = data["Close"]
    if isinstance(close, pd.DataFrame):      # newer yfinance returns a nested table
        close = close.squeeze("columns")
    close = close.dropna()

    # Closing price on the last trading day of each month
    month_end_dates = close.index.to_series().groupby(close.index.to_period("M")).last()
    month_end_close = close.loc[month_end_dates.values]

    # Keep only COMPLETED months (drop the current, unfinished month)
    this_month = pd.Timestamp.today().to_period("M")
    completed = month_end_close[(month_end_dates.index < this_month)]

    if len(completed) < SMA_MONTHS:
        sys.exit("Not enough monthly history to compute the average.")

    last_close = float(completed.iloc[-1])
    sma = float(completed.iloc[-SMA_MONTHS:].mean())
    return {
        "as_of": completed.index[-1].date(),
        "last_close": last_close,
        "sma": sma,
        "hold_spy": last_close > sma,
    }


# ---------------------------------------------------------------
# STEP 2: Connect to the Alpaca PAPER account
# ---------------------------------------------------------------
def connect():
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        sys.exit("Missing keys. Set ALPACA_API_KEY and ALPACA_SECRET_KEY as "
                 "environment variables (see the instructions at the top of this file), "
                 "then reopen your Command Prompt.")
    # paper=True points the client at Alpaca's fake-money environment
    return TradingClient(api_key, secret_key, paper=True)


def current_spy_position(client):
    """Return the open SPY position, or None if we hold none."""
    for position in client.get_all_positions():
        if position.symbol == SYMBOL:
            return position
    return None


# ---------------------------------------------------------------
# STEP 3: Compare target with reality and trade if needed
# ---------------------------------------------------------------
def write_log(signal, holding, action):
    """Append one line per run so you can review decisions later."""
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["run_time", "month_end_used", "last_close", "sma",
                             "target", "currently_holding", "action", "dry_run"])
        writer.writerow([datetime.now().isoformat(timespec="seconds"),
                         signal["as_of"], f"{signal['last_close']:.2f}",
                         f"{signal['sma']:.2f}",
                         "SPY" if signal["hold_spy"] else "CASH",
                         "SPY" if holding else "CASH", action, DRY_RUN])


def main():
    signal = get_signal()
    client = connect()

    account = client.get_account()
    position = current_spy_position(client)
    holding = position is not None and float(position.qty) > 0

    print("=" * 60)
    print(f"{SYMBOL} {SMA_MONTHS}-MONTH RULE   (PAPER account, DRY_RUN={DRY_RUN})")
    print("=" * 60)
    print(f"Last completed month-end used: {signal['as_of']}")
    print(f"  Month-end close:             ${signal['last_close']:,.2f}")
    print(f"  {SMA_MONTHS}-month average:           ${signal['sma']:,.2f}")
    print(f"  Close above average?         {'YES' if signal['hold_spy'] else 'NO'}")
    print(f"Target:                        {'HOLD ' + SYMBOL if signal['hold_spy'] else 'HOLD CASH'}")
    print(f"Currently holding:             {'SPY' if holding else 'cash only'}")
    print(f"Account cash:                  ${float(account.cash):,.2f}")
    print("-" * 60)

    clock = client.get_clock()
    if not clock.is_open:
        print("Note: the market is closed. Any order placed now waits for the next open.")

    action = "none"
    if signal["hold_spy"] and not holding:
        amount = round(float(account.cash) * INVEST_FRACTION, 2)
        action = f"BUY ${amount:,.2f} of {SYMBOL}"
        print(f"ACTION: {action}")
        if not DRY_RUN:
            order = client.submit_order(MarketOrderRequest(
                symbol=SYMBOL, notional=amount,
                side=OrderSide.BUY, time_in_force=TimeInForce.DAY))
            print(f"Order submitted. ID: {order.id}")
    elif not signal["hold_spy"] and holding:
        action = f"SELL entire {SYMBOL} position"
        print(f"ACTION: {action}")
        if not DRY_RUN:
            client.close_position(SYMBOL)
            print("Sell order submitted.")
    else:
        print("ACTION: none (the account already matches the target)")

    if DRY_RUN and action != "none":
        print("\nDRY_RUN is True, so no order was placed.")

    write_log(signal, holding, action)
    print(f"\nRun recorded in {LOG_FILE}")


if __name__ == "__main__":
    main()
