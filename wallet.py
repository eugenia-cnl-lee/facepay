"""Command-line tool for the wallet.

Usage:
  python wallet.py balance <name>
  python wallet.py history <name>
  python wallet.py refund <payment_id>
"""

import argparse

import wallet_store as w


def cmd_balance(args):
    balance = w.get_balance(args.name)
    if balance is None:
        print(f"No account: {args.name}")
    else:
        print(f"{args.name}: {w.format_money(balance)}")


def cmd_history(args):
    rows = w.history(args.name)
    if not rows:
        print("No transactions.")
        return
    for payment_id, at, kind, payer, payee, amount in rows:
        print(f"#{payment_id}  {at}  {kind:<7}  {payer} -> {payee}  {w.format_money(amount)}")


def cmd_topup(args):
    w.topup(args.name, int(round(args.amount * 100)))
    print(f"{args.name}: {w.format_money(w.get_balance(args.name))}")


def cmd_refund(args):
    try:
        result = w.refund_by_id(args.payment_id)
    except ValueError as error:
        print(f"Refund failed: {error}")
        return
    outcome = "already refunded" if result["status"] == "duplicate" else "refunded"
    print(f"Payment #{args.payment_id} {outcome} ({w.format_money(result['amount_pence'])}).")


def main():
    parser = argparse.ArgumentParser(description="FacePay wallet CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    balance = sub.add_parser("balance", help="show an account balance")
    balance.add_argument("name")
    balance.set_defaults(func=cmd_balance)

    history = sub.add_parser("history", help="list transactions for an account")
    history.add_argument("name")
    history.set_defaults(func=cmd_history)

    topup = sub.add_parser("topup", help="load funds onto an account")
    topup.add_argument("name")
    topup.add_argument("amount", type=float)
    topup.set_defaults(func=cmd_topup)

    refund = sub.add_parser("refund", help="refund a payment by its id")
    refund.add_argument("payment_id", type=int)
    refund.set_defaults(func=cmd_refund)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
