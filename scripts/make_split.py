#!/usr/bin/env python3
"""Deterministic DEV/HOLDOUT split by sha256(case_id). HOLDOUT ~20% (200/1000).
Orchestrator-only: agents read split.json but must not touch HOLDOUT cases."""
import csv, hashlib, json, sys

labels_csv, out_json = sys.argv[1], sys.argv[2]
dev, holdout = [], []
with open(labels_csv) as f:
    for row in csv.DictReader(f):
        cid = row["case_id"]
        h = int(hashlib.sha256(cid.encode()).hexdigest(), 16) % 5
        (holdout if h == 0 else dev).append(cid)
json.dump({"dev": sorted(dev), "holdout": sorted(holdout)}, open(out_json, "w"), indent=0)
print(f"dev={len(dev)} holdout={len(holdout)}")
