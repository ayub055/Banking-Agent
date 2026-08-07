"""BANK_ANALYSER CLI entry.

Report generation:
    python main.py --customer 698167220
    python main.py --customer 698167220 --theme bank_v2
    python main.py "Generate customer report for customer 698167220"

Run over an xns statement file (converted to xn_d1 schema first):
    python main.py --from-xns path/to/statement.xlsx --cust-id 8111272729
"""

import argparse
import logging
import re
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _extract_customer_id(text: str) -> int:
    m = re.search(r"\b(\d{5,})\b", text)
    if not m:
        raise SystemExit(f"Could not parse customer id from: {text!r}")
    return int(m.group(1))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a banking-only customer report")
    parser.add_argument("--customer", type=int, help="Customer id (e.g. 698167220)")
    parser.add_argument("--theme", default="bank_v2", choices=["bank_v2"],
                        help="Report theme (only the bank_v2 Chart.js layout is supported)")
    parser.add_argument("--serve", action="store_true",
                        help="After generating the report, serve it on http://127.0.0.1 with the admin /save endpoint enabled")
    parser.add_argument("--port", type=int, default=8765, help="Port for --serve (default 8765)")
    parser.add_argument("--from-xns", dest="from_xns", default=None,
                        help="Path to an xns statement file (.xlsx/.csv) to convert and run over")
    parser.add_argument("--cust-id", dest="cust_id", default=None,
                        help="Customer id to use with --from-xns")
    parser.add_argument("query", nargs="*", help="Free-text query (e.g. 'Generate customer report for customer 124')")
    args = parser.parse_args()

    statement_source = None
    if args.from_xns:
        from tools.xns_ingest import ingest_xns
        from data.loader import load_transactions
        converted = ingest_xns(args.from_xns, cust_id=args.cust_id)
        statement_source = str(converted)
        # Seed the loader cache so the whole pipeline runs over this customer's data.
        load_transactions(path=statement_source, force_reload=True)
        cid = int(args.cust_id) if args.cust_id and str(args.cust_id).isdigit() else _extract_customer_id(converted.stem)
        print(f"Ingested xns -> {converted}")
    elif args.customer is not None:
        cid = args.customer
    elif args.query:
        cid = _extract_customer_id(" ".join(args.query))
    else:
        text = input("Enter query (e.g. 'Generate customer report for customer 698167220'): ").strip()
        cid = _extract_customer_id(text)

    from tools.bank_report import generate_bank_report
    report, path = generate_bank_report(cid, theme=args.theme, statement_source=statement_source)
    if report is None:
        print(f"No banking data for customer {cid}")
        sys.exit(1)
    print(f"Report generated: {path}")

    if args.serve:
        from pipeline.renderers.serve_report import serve
        serve(cid, path, port=args.port)


if __name__ == "__main__":
    main()
