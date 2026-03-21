#!/usr/bin/env python3
"""
Ad Audit CLI Tool
Audits Google Ads and Meta Ads CSV exports and generates a client-ready HTML report.

Usage:
    python audit.py --sample
    python audit.py --google google_ads.csv
    python audit.py --meta meta_ads.csv
    python audit.py --google google_ads.csv --meta meta_ads.csv
    python audit.py --google g.csv --google-prev g_prev.csv --compare
    python audit.py --google g.csv --output my_report.html
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from src.loader import load_google_ads, load_meta_ads
from src.calculator import calculate_google_metrics, calculate_meta_metrics, build_summary
from src.funnel_loader import load_funnel
from src.claude_client import analyze
from src.renderer import render_report

SAMPLE_DIR = Path(__file__).parent / "sample_data"


def print_status(msg: str, ok: bool = True) -> None:
    mark = "  OK" if ok else "  WARN"
    symbol = "+" if ok else "!"
    print(f"  [{symbol}] {msg}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit Google Ads and Meta Ads CSV exports and generate an HTML report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python audit.py --sample
  python audit.py --google google_ads.csv
  python audit.py --google google_ads.csv --meta meta_ads.csv
  python audit.py --google g.csv --google-prev g_prev.csv --compare
  python audit.py --google g.csv --output client_report.html
        """
    )

    parser.add_argument("--google", metavar="FILE", help="Google Ads CSV export")
    parser.add_argument("--meta", metavar="FILE", help="Meta Ads CSV export")
    parser.add_argument("--google-prev", metavar="FILE", dest="google_prev",
                        help="Previous period Google Ads CSV (required for --compare)")
    parser.add_argument("--meta-prev", metavar="FILE", dest="meta_prev",
                        help="Previous period Meta Ads CSV (required for --compare)")
    parser.add_argument("--compare", action="store_true",
                        help="Enable period comparison mode (requires --google-prev or --meta-prev)")
    parser.add_argument("--output", metavar="FILE",
                        help="Output HTML file path (default: audit_report_YYYYMMDD_HHMMSS.html)")
    parser.add_argument("--funnel", metavar="FILE",
                        help="Funnel CSV with MQL/SQL/Customer counts (any structure, auto-detected)")
    parser.add_argument("--sample", action="store_true",
                        help="Run with bundled sample data")

    args = parser.parse_args()

    print()
    print("  Ad Audit CLI")
    print("  " + "-" * 40)

    # Handle --sample flag
    if args.sample:
        args.google = str(SAMPLE_DIR / "google_ads_sample.csv")
        args.meta = str(SAMPLE_DIR / "meta_ads_sample.csv")
        args.google_prev = str(SAMPLE_DIR / "google_ads_prev.csv")
        args.meta_prev = str(SAMPLE_DIR / "meta_ads_prev.csv")
        args.funnel = str(SAMPLE_DIR / "funnel_sample.csv")
        args.compare = True
        print("  [*] Using bundled sample data")

    # Validate: at least one platform required
    if not args.google and not args.meta:
        print("  [!] Error: Provide at least one of --google or --meta (or use --sample)")
        parser.print_help()
        sys.exit(1)

    # Validate --compare requirements
    if args.compare:
        if args.google and not args.google_prev:
            print("  [!] --compare requires --google-prev when --google is provided")
            sys.exit(1)
        if args.meta and not args.meta_prev:
            print("  [!] --compare requires --meta-prev when --meta is provided")
            sys.exit(1)

    google_df = None
    meta_df = None
    prev_google_df = None
    prev_meta_df = None

    # Load Google Ads
    if args.google:
        print(f"  Loading Google Ads: {args.google}")
        try:
            google_df = load_google_ads(args.google)
            google_df = calculate_google_metrics(google_df)
            print_status(f"Google Ads loaded - {len(google_df)} campaigns")
        except (FileNotFoundError, ValueError) as e:
            print(f"  [!] {e}")
            sys.exit(1)

    if args.google_prev and args.compare:
        print(f"  Loading previous Google Ads: {args.google_prev}")
        try:
            prev_google_df = load_google_ads(args.google_prev)
            print_status("Previous Google Ads loaded")
        except (FileNotFoundError, ValueError) as e:
            print(f"  [!] {e}")
            sys.exit(1)

    # Load Meta Ads
    if args.meta:
        print(f"  Loading Meta Ads: {args.meta}")
        try:
            meta_df = load_meta_ads(args.meta)
            meta_df = calculate_meta_metrics(meta_df)
            print_status(f"Meta Ads loaded - {len(meta_df)} campaigns")
        except (FileNotFoundError, ValueError) as e:
            print(f"  [!] {e}")
            sys.exit(1)

    if args.meta_prev and args.compare:
        print(f"  Loading previous Meta Ads: {args.meta_prev}")
        try:
            prev_meta_df = load_meta_ads(args.meta_prev)
            print_status("Previous Meta Ads loaded")
        except (FileNotFoundError, ValueError) as e:
            print(f"  [!] {e}")
            sys.exit(1)

    # Load funnel data
    funnel_data = None
    if args.funnel:
        print(f"  Loading funnel data: {args.funnel}")
        try:
            funnel_data = load_funnel(args.funnel)
            stages = funnel_data["stages_available"]
            level = funnel_data["join_level"]
            print_status(f"Funnel loaded - stages: {', '.join(stages)} | join: {level}")
        except (FileNotFoundError, ValueError) as e:
            print(f"  [!] {e}")
            sys.exit(1)

    # Build analysis summary
    print("  Calculating metrics...")
    analysis = build_summary(
        google_df=google_df,
        meta_df=meta_df,
        compare_mode=args.compare,
        prev_google_df=prev_google_df,
        prev_meta_df=prev_meta_df,
        funnel=funnel_data,
    )

    # Count findings
    total_critical = 0
    total_warning = 0
    total_wasted = 0.0
    for platform in ["google", "meta"]:
        pdata = analysis.get(platform)
        if pdata:
            total_critical += pdata.get("critical_count", 0)
            total_warning += pdata.get("warning_count", 0)
            total_wasted += pdata.get("wasted_spend", 0.0)

    print_status(f"Found {total_critical} critical, {total_warning} warning campaigns")
    if total_wasted > 0:
        print(f"  [!] Wasted spend detected: Rs {total_wasted:,.0f}")

    # Claude analysis
    print("  Analyzing with Claude...")
    claude_output, used_claude = analyze(analysis)
    if used_claude:
        print_status("Claude analysis complete")
    else:
        print("  [!] Fallback analysis used (Claude CLI not available)")

    # Render HTML
    print("  Rendering HTML report...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or f"audit_report_{timestamp}.html"

    try:
        render_report(analysis, claude_output, output_path)
        print_status(f"Report rendered")
    except Exception as e:
        print(f"  [!] Render failed: {e}")
        sys.exit(1)

    print()
    print("  " + "=" * 40)
    print(f"  Report saved: {output_path}")
    if total_critical > 0:
        print(f"  Critical campaigns: {total_critical} (Rs {total_wasted:,.0f} wasted spend)")
    print("  " + "=" * 40)
    print()


if __name__ == "__main__":
    main()
