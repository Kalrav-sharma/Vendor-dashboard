"""One-off, READ-ONLY probe of the official Delhivery and Shadowfax tracking APIs
for RTO shipments, to see what the return leg looks like on a live response.

Why it exists: both adapters' official-API parsers (lsp/delhivery.py
parse_official, lsp/shadowfax.py parse_official) were written from the vendors'
documentation, never checked against a live capture. Before building the Returns
section on them, print the real fields for a few known RTO AWBs: status, the
latest scans, and any key that looks return-related (a new return AWB, a
reverse-leg flag, a parent/child link) -- the way Blue Dart's `NewWaybillNo` was
found.

Prints only whitelisted fields plus key NAMES for everything else. Values are
printed only for return-related scalar keys, and never for address/contact
fields. Tokens are never printed (GitHub also masks them in logs).

Environment:
  DELHIVERY_API_TOKEN, SHADOWFAX_API_TOKEN  -- optional; a missing one is
                                               reported and skipped
  DLV_AWBS, SFX_AWBS                        -- comma/space-separated AWB lists

Run with: python3 scripts/probe_rto_return_legs.py
"""
import os
import re
import sys

import requests

TIMEOUT = 45
DELHIVERY_URL = "https://track.delhivery.com/api/v1/packages/json/"
SHADOWFAX_URL = "https://dale.shadowfax.in/api/v4/clients/bulk_track/"

RETURNISH = re.compile(r"return|rto|rts|reverse|new_?awb|new_?waybill|parent|child|linked|original", re.I)
PII = re.compile(r"address|phone|mobile|name|email|pin|city|state|contact|attention", re.I)


def awbs(env_name):
    return [a for a in re.split(r"[,\s]+", os.environ.get(env_name, "")) if a]


def returnish_values(obj):
    """{key: value} for scalar return-related keys, skipping contact fields."""
    return {k: str(v)[:80] for k, v in obj.items()
            if RETURNISH.search(k) and not PII.search(k) and not isinstance(v, (dict, list))}


def probe_delhivery(token, batch):
    print("\n=== Delhivery (official) ===")
    r = requests.get(DELHIVERY_URL, params={"waybill": ",".join(batch), "ref_ids": ""},
                     headers={"Authorization": "Token " + token, "Accept": "application/json"},
                     timeout=TIMEOUT)
    print("http", r.status_code)
    try:
        payload = r.json()
    except ValueError:
        print("non-JSON body:", r.text[:200])
        return
    shipments = payload.get("ShipmentData")
    if not isinstance(shipments, list):
        print("no ShipmentData list; top-level keys:", sorted(payload)[:10], "|", str(payload)[:200])
        return
    seen = set()
    for entry in shipments:
        shp = (entry or {}).get("Shipment") or {}
        awb = str(shp.get("AWB") or shp.get("Waybill") or "?")
        seen.add(awb)
        status = shp.get("Status") or {}
        print(f"\n-- {awb}")
        print("  Status:", {k: status.get(k) for k in ("Status", "StatusType", "StatusDateTime", "Instructions", "RecievedBy")})
        for row in (shp.get("Scans") or [])[:8]:
            d = row.get("ScanDetail", row) if isinstance(row, dict) else {}
            print("  scan:", d.get("ScanDateTime"), "|", d.get("Scan"), "|", d.get("ScanType"), "|", d.get("ScannedLocation"))
        print("  keys:", sorted(shp.keys()))
        print("  return-related values:", returnish_values(shp))
    for a in batch:
        if a not in seen:
            print(f"\n-- {a}: not present in response")


def probe_shadowfax(token, batch):
    print("\n=== Shadowfax (official Unified API) ===")
    r = requests.post(SHADOWFAX_URL, json={"awb_numbers": batch},
                      headers={"Authorization": "Token " + token, "Accept": "application/json"},
                      timeout=TIMEOUT)
    print("http", r.status_code)
    try:
        payload = r.json()
    except ValueError:
        print("non-JSON body:", r.text[:200])
        return
    print("message:", payload.get("message"))
    data = payload.get("data")
    if not isinstance(data, list):
        print("no data list; top-level keys:", sorted(payload)[:10], "|", str(payload)[:200])
        return
    seen = set()
    for rec in data:
        awb = str(rec.get("awb_number") or "?")
        seen.add(awb)
        print(f"\n-- {awb}")
        print("  status:", rec.get("status"), "|", rec.get("status_display"), "| promised:", rec.get("promised_delivery_date"))
        steps = sorted((s for s in (rec.get("tracking_details") or []) if isinstance(s, dict)),
                       key=lambda s: str(s.get("created") or ""), reverse=True)
        for s in steps[:8]:
            print("  scan:", s.get("created"), "|", s.get("status"), "|", s.get("location"), "|", str(s.get("remarks"))[:80])
        print("  keys:", sorted(rec.keys()))
        print("  return-related values:", returnish_values(rec))
    for a in batch:
        if a not in seen:
            print(f"\n-- {a}: not present in response")


def main():
    for name, env_awbs, env_token, fn in (
            ("Delhivery", "DLV_AWBS", "DELHIVERY_API_TOKEN", probe_delhivery),
            ("Shadowfax", "SFX_AWBS", "SHADOWFAX_API_TOKEN", probe_shadowfax)):
        token = (os.environ.get(env_token) or "").strip()
        batch = awbs(env_awbs)
        print(f"{name}: token {'SET' if token else 'NOT SET'}, {len(batch)} AWB(s)")
        if not token:
            print(f"  skipped: {env_token} is not set in this repo's Actions secrets")
            continue
        if not batch:
            print("  skipped: no AWBs given")
            continue
        try:
            fn(token, batch[:50])
        except requests.exceptions.RequestException as e:
            print(f"  request failed: {type(e).__name__}")


if __name__ == "__main__":
    sys.exit(main())
