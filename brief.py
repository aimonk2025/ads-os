#!/usr/bin/env python3
"""
AdLens Morning Brief
--------------------
Fetches open anomalies for all clients from a running AdLens instance,
runs them through Claude (same subprocess pattern as the rest of the app),
and prints a formatted brief to the terminal.

Usage:
    python brief.py                        # all clients, localhost:5000
    python brief.py --client-id 1          # specific client
    python brief.py --url http://localhost:5000
    python brief.py --watch --interval 300 # keep running, brief every 5 min

Requires AdLens to be running: python web/app.py
"""

import sys
import json
import time
import argparse
import subprocess
from datetime import datetime

try:
    import urllib.request as urlreq
    import urllib.error as urlerr
except ImportError:
    pass


BRIEF_PROMPT = """You are an ad performance analyst delivering a morning brief.

You receive JSON with anomaly data across one or more clients.

Write a concise morning brief covering:
1. A one-line headline with the most critical issue (or "All Clear" if none)
2. Per-client breakdown - only mention clients with issues
3. Top 3 recommended actions for today, ranked by urgency

Rules:
- Use plain text, no markdown headers
- Be direct and specific (name the campaign, the metric, the number)
- If no anomalies, say so clearly and suggest running a fresh audit
- Max 300 words

Data:
"""


def fetch(url: str) -> dict:
    req = urlreq.Request(url, headers={"Accept": "application/json"})
    with urlreq.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def get_clients(base_url: str) -> list:
    return fetch(f"{base_url}/api/clients")


def get_anomalies(base_url: str, client_id: int) -> dict:
    return fetch(f"{base_url}/api/anomalies/{client_id}?status=open&limit=20")


def build_payload(base_url: str, client_ids: list) -> dict:
    payload = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "clients": []}
    for cid in client_ids:
        try:
            data = get_anomalies(base_url, cid)
            summary = data.get("summary", {})
            anomalies = data.get("anomalies", [])
            # Find client name
            name = f"Client {cid}"
            payload["clients"].append({
                "client_id": cid,
                "client_name": name,
                "summary": summary,
                "anomalies": [
                    {
                        "campaign": a["campaign_name"],
                        "platform": a["platform"],
                        "metric": a["metric"],
                        "description": a["description"],
                        "severity": a["severity"],
                        "pct_change": a.get("pct_change"),
                    }
                    for a in anomalies[:10]
                ],
            })
        except Exception as e:
            payload["clients"].append({"client_id": cid, "error": str(e)})
    return payload


def run_claude(payload: dict) -> tuple[str, bool]:
    prompt = BRIEF_PROMPT + json.dumps(payload, indent=2)
    try:
        result = subprocess.run(
            ["claude", "--print", prompt],
            capture_output=True, text=True, timeout=90
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), True
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return fallback_brief(payload), False


def fallback_brief(payload: dict) -> str:
    lines = [f"AdLens Morning Brief - {payload['generated_at']}", ""]
    total_critical = 0
    total_warning = 0
    for c in payload.get("clients", []):
        if "error" in c:
            lines.append(f"{c['client_name']}: Could not fetch data - {c['error']}")
            continue
        s = c.get("summary", {})
        crit = s.get("critical", 0)
        warn = s.get("warning", 0)
        total_critical += crit
        total_warning += warn
        if crit == 0 and warn == 0:
            lines.append(f"{c['client_name']}: All clear")
        else:
            lines.append(f"{c['client_name']}: {crit} critical, {warn} warnings")
            for a in c.get("anomalies", [])[:3]:
                lines.append(f"  [{a['severity'].upper()}] {a['campaign']} ({a['platform']}) - {a['description']}")
    lines.append("")
    lines.append(f"Total: {total_critical} critical, {total_warning} warnings")
    return "\n".join(lines)


def print_brief(brief: str, used_claude: bool) -> None:
    ts = datetime.now().strftime("%H:%M")
    bar = "=" * 60
    source = "Claude" if used_claude else "fallback"
    print(f"\n{bar}")
    print(f"  AdLens Brief  [{ts}]  ({source})")
    print(bar)
    print(brief)
    print(bar + "\n")


def main():
    parser = argparse.ArgumentParser(description="AdLens Morning Brief")
    parser.add_argument("--url", default="http://localhost:5000", help="AdLens base URL")
    parser.add_argument("--client-id", type=int, default=None, help="Specific client ID (default: all)")
    parser.add_argument("--watch", action="store_true", help="Keep running and re-brief on interval")
    parser.add_argument("--interval", type=int, default=300, help="Watch interval in seconds (default: 300)")
    args = parser.parse_args()

    def run_once():
        try:
            if args.client_id:
                client_ids = [args.client_id]
                # Try to get name
                try:
                    clients = get_clients(args.url)
                    for c in clients:
                        if c["id"] == args.client_id:
                            break
                except Exception:
                    pass
            else:
                clients = get_clients(args.url)
                client_ids = [c["id"] for c in clients]
                # Patch in names
                name_map = {c["id"]: c["name"] for c in clients}

            payload = build_payload(args.url, client_ids)

            # Patch in real client names if we have them
            if not args.client_id:
                for c in payload["clients"]:
                    c["client_name"] = name_map.get(c["client_id"], f"Client {c['client_id']}")

            brief, used_claude = run_claude(payload)
            print_brief(brief, used_claude)

        except urlerr.URLError:
            print(f"\nCould not connect to AdLens at {args.url}")
            print("Make sure AdLens is running: python web/app.py\n")
            sys.exit(1)
        except Exception as e:
            print(f"\nError: {e}\n")
            sys.exit(1)

    run_once()

    if args.watch:
        print(f"Watching... next brief in {args.interval}s. Press Ctrl+C to stop.\n")
        try:
            while True:
                time.sleep(args.interval)
                run_once()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
