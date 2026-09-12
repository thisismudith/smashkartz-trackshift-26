#!/usr/bin/env python3
"""Materialise M08 from an already-built C8/M06 public artifact."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/"src"))
from trackshift.features.api import build_rival_state_features

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--input",type=Path,required=True); p.add_argument("--output",type=Path,required=True); p.add_argument("--split-reference",default="C9_NOT_MATERIALISED"); p.add_argument("--exclude-event",default="British Grand Prix")
    a=p.parse_args(); frame=pd.read_parquet(a.input)
    frame=frame[frame.get("event", pd.Series("", index=frame.index)).astype(str).str.lower() != a.exclude_event.lower()]
    result=build_rival_state_features(frame.to_dict("records"),split_reference={"reference":a.split_reference})
    result["availability_audit"] = {"input_rows_after_british_exclusion": int(len(frame)), "output_rows": len(result["rows"]), "c5_materialised": bool("fuel_load_delta_kg_est" in frame and frame["fuel_load_delta_kg_est"].notna().any()), "c6_materialised": bool("eligibility_probability" in frame and frame["eligibility_probability"].notna().any())}
    a.output.mkdir(parents=True,exist_ok=True); pd.DataFrame(result["rows"]).to_parquet(a.output/"rival_state_features.parquet",index=False)
    (a.output/"manifest.json").write_text(json.dumps({k:v for k,v in result.items() if k!="rows"},indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k!="rows"},indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())
