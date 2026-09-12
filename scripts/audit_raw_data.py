#!/usr/bin/env python3
"""Read-only Phase 1 schema audit for TracingInsights raw mirrors."""
from __future__ import annotations
import argparse, csv, json, sys
from collections import defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from trackshift.progress import Progress

YEARS = ("2022", "2023", "2024", "2025", "2026")
TEL_FIELDS = ("time","distance","rel_distance","speed","rpm","gear","throttle","brake","drs","x","y","z","acc_x","acc_y","acc_z","DriverAhead","DistanceToDriverAhead")
CANONICAL = {
 "lap_elapsed_s":("time","float","s",True),"distance_m":("distance","float","m",True),"lap_fraction":("rel_distance","float","0..1",True),
 "speed_kmh":("speed","float","km/h",True),"engine_rpm":("rpm","float","rpm",False),"gear":("gear","int","gear",False),
 "throttle_pct":("throttle","float","%",False),"brake_on":("brake","bool","0/1",False),"drs_open":("drs","bool","0/1",False),
 "x_m":("x","float","m",False),"y_m":("y","float","m",False),"z_m":("z","float","m",False),
 "acc_longitudinal_mps2":("acc_x","float","m/s2",False),"acc_lateral_mps2":("acc_y","float","m/s2",False),"acc_vertical_mps2":("acc_z","float","m/s2",False),
 "driver_ahead_number":("DriverAhead","string|null",None,False),"gap_ahead_m":("DistanceToDriverAhead","float|null","m",False),
 "lap_number":("lap","int","lap",True),"lap_time_s":("time","float","s",False),"session_time_s":("sesT","float","s",False),
 "sector_1_s":("s1","float","s",False),"sector_2_s":("s2","float","s",False),"sector_3_s":("s3","float","s",False),
 "race_position":("pos","int|null",None,False),"tyre_compound":("compound","string|null",None,False),
 "tyre_life_laps":("life","float|null","laps",False),"stint":("stint","int|null",None,False),"driver":("drv","string",None,True),
 "team":("team","string",None,False),"track_status":("status","string|null",None,False)
}
def load(p):
    with p.open(encoding="utf-8") as f: return json.load(f)
def unwrap(x,key): return x.get(key,x) if isinstance(x,dict) else x
def numeric(v):
    """Coerce to float, treating the source's sentinels as missing.

    TracingInsights writes the literal string "None" for a missing sample, so a
    raw `b >= a` comparison raises TypeError on the first one. That crashed the
    audit on 11 files (2023 Qatar Sprint Shootout lap 2, whose first two distance
    samples are missing) and reported them as malformed, which is misleading:
    the JSON is fine and validation.py already rejects those laps correctly with
    NONFINITE_DISTANCE. Same sentinel class as the laptimes.json fix.
    """
    if v is None or isinstance(v, bool) or v == "None":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def value_type(v):
    if v is None or v == "None": return "null"
    if isinstance(v,bool): return "bool"
    if isinstance(v,int): return "int"
    if isinstance(v,float): return "float"
    return "string" if isinstance(v,str) else type(v).__name__
def audit_tel(p):
    tel=unwrap(load(p),"tel")
    if not isinstance(tel,dict) or not isinstance(tel.get("time"),list): raise ValueError(f"Malformed telemetry: {p}")
    n=len(tel["time"]); result={"rows":n,"fields":sorted(tel)}
    for name in TEL_FIELDS:
        values=tel.get(name)
        if not isinstance(values,list): result[name]={"present":False}; continue
        non=[v for v in values if v is not None and v!="None"]
        nums=[float(v) for v in non if isinstance(v,(int,float)) and not isinstance(v,bool)]
        result[name]={"present":True,"null_rate":(n-len(non))/n if n else None,"types":sorted({value_type(v) for v in values})}
        if nums: result[name].update(min=min(nums),max=max(nums))
    d=[numeric(v) for v in tel.get("distance",[])]; t=[numeric(v) for v in tel.get("time",[])]
    result["distance_nonnumeric_count"]=sum(1 for v in tel.get("distance",[]) if numeric(v) is None)
    pairs=[(a,b) for a,b in zip(d,d[1:]) if a is not None and b is not None]
    if pairs: result["distance_monotonic"]=all(b>=a for a,b in pairs)
    dt=[b-a for a,b in zip(t,t[1:]) if a is not None and b is not None and b>a]
    if dt: result["time_step_s"]={"min":min(dt),"median":median(dt),"max":max(dt)}
    return result
def load_prior(output, selected, fields, events, quality, summary, errors):
    """Read an existing audit and carry forward years this run is not redoing.

    The audit rewrites its whole output directory, so auditing the remaining
    seasons would otherwise destroy a finished one. Mutates the collections in
    place and returns the list of years carried over.

    Rows belonging to a year in `selected` are deliberately dropped: a re-audit
    of that year should replace its rows, not append a second copy.
    """
    kept=set()
    quality_csv=output/"data_quality_summary.csv"
    if quality_csv.exists():
        with quality_csv.open(encoding="utf-8",newline="") as handle:
            for row in csv.DictReader(handle):
                year=row.get("year")
                if year in selected or not year: continue
                # Numeric/dict columns were JSON-encoded on write; restore them
                # so the merged file is byte-comparable with a single-pass run.
                restored={}
                for key,value in row.items():
                    if value in ("","None"): restored[key]=None; continue
                    try: restored[key]=json.loads(value)
                    except (TypeError,ValueError): restored[key]=value
                quality.append(restored); kept.add(year)
    events_csv=output/"events_sessions.csv"
    if events_csv.exists():
        with events_csv.open(encoding="utf-8",newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("year") in selected or not row.get("year"): continue
                row["driver_directories"]=int(row["driver_directories"]) if str(row.get("driver_directories","")).isdigit() else row.get("driver_directories")
                events.append(row); kept.add(row["year"])
    fields_csv=output/"field_availability.csv"
    if fields_csv.exists():
        with fields_csv.open(encoding="utf-8",newline="") as handle:
            for row in csv.DictReader(handle):
                for year in (row.get("years") or "").split(";"):
                    if year and year not in selected:
                        fields[(row["file_kind"],row["raw_field"])].add(year)
    summary_json=output/"repository_summary.json"
    if summary_json.exists():
        try: prior=json.loads(summary_json.read_text(encoding="utf-8"))
        except (OSError,ValueError): prior={}
        for year,value in (prior.get("years") or {}).items():
            if year not in selected: summary[year]=value; kept.add(year)
        for item in prior.get("malformed") or []:
            path=str(item.get("path",""))
            if not any(("/%s/"%y) in path.replace("\\","/") for y in selected):
                errors.append(item)
    return sorted(kept)


def count_tel_files(raw_root, years):
    """Pre-count lap files so progress has a denominator.

    Stats directory entries only, no JSON parsing, so this costs a few seconds
    even across the full 177k-file mirror."""
    total = 0
    for year in years:
        root = raw_root / year
        if not root.is_dir(): continue
        for event in root.iterdir():
            if not event.is_dir() or event.name.startswith("."): continue
            for session in event.iterdir():
                if not session.is_dir(): continue
                for driver in session.iterdir():
                    if driver.is_dir():
                        total += sum(1 for f in driver.iterdir()
                                     if f.is_file() and f.name.endswith("_tel.json"))
    return total


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--raw-root",type=Path,default=Path("data/raw"))
    ap.add_argument("--output",type=Path,default=Path("artifacts/schema_audit"))
    ap.add_argument("--merge",action="store_true",
                    help="Combine with an existing audit in --output instead of overwriting it. "
                         "Rows for years in --years are replaced; every other year is preserved. "
                         "Use this to audit remaining seasons without redoing finished ones.")
    ap.add_argument("--no-progress",action="store_true",
                    help="Suppress the progress line. Progress is on by default and goes to stderr.")
    ap.add_argument("--years",default=None,
                    help="CSV subset of years to audit, e.g. 2026 or 2025,2026. "
                         "Default: all of %s. The full mirror is ~177k files and "
                         "single-threaded, so scope this when you do not need every season."%",".join(YEARS))
    a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    selected=YEARS
    if a.years:
        selected=tuple(y.strip() for y in a.years.split(",") if y.strip())
        unknown=[y for y in selected if y not in YEARS]
        if unknown: ap.error("unsupported year(s): %s; supported: %s"%(",".join(unknown),",".join(YEARS)))
    fields=defaultdict(set); events=[]; quality=[]; summary={}; errors=[]
    prior_years=[]
    if a.merge:
        # Carry forward every year that this run is not re-auditing. Rows for the
        # selected years are dropped so a re-run corrects rather than duplicates.
        prior_years=load_prior(a.output,selected,fields,events,quality,summary,errors)
        if prior_years:
            print("merging with existing audit for %s"%",".join(prior_years),file=sys.stderr)
        else:
            print("--merge: no prior audit found in %s, writing fresh"%a.output,file=sys.stderr)
    print("counting lap files...",file=sys.stderr)
    prog=Progress(count_tel_files(a.raw_root,selected),enabled=not a.no_progress)
    print("auditing %d lap files across %s"%(prog.total,",".join(selected)),file=sys.stderr)
    for year in selected:
        root=a.raw_root/year
        if not root.exists(): summary[year]={"status":"missing"}; continue
        summary[year]={"status":"present","top_level_files":sorted(p.name for p in root.iterdir() if p.is_file()),
                       "event_directories":sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))}
        for event in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for session in sorted(p for p in event.iterdir() if p.is_dir()):
                fs=list(session.iterdir()); drivers=[p for p in fs if p.is_dir()]
                events.append({"year":year,"event":event.name,"session":session.name,"driver_directories":len(drivers),
                               "session_files":";".join(sorted(p.name for p in fs if p.is_file()))})
                prog.set_label("%s %s / %s"%(year,event.name,session.name))
                for driver in drivers:
                    lp=driver/"laptimes.json"
                    if lp.exists():
                        laps=unwrap(load(lp),"laptimes")
                        if isinstance(laps,list) and laps and isinstance(laps[0],dict):
                            for k in laps[0]: fields[("laptimes.json",k)].add(year)
                    for tel in driver.glob("*_tel.json"):
                        try: q=audit_tel(tel)
                        except Exception as exc: errors.append({"path":str(tel),"error":str(exc)}); prog.tick(); continue
                        for k in q["fields"]: fields[("tel.json",k)].add(year)
                        quality.append({"year":year,"event":event.name,"session":session.name,"driver":driver.name,"lap_file":tel.name,**q})
                        prog.tick()
                for fn,key in (("weather.json","weather"),("rcm.json","rcm"),("drivers.json","drivers"),("corners.json","corners"),("session_laptimes.json","session_laptimes")):
                    p=session/fn
                    if p.exists():
                        obj=unwrap(load(p),key)
                        if isinstance(obj,dict):
                            for k in obj: fields[(fn,k)].add(year)
    prog.close()
    (a.output/"repository_summary.json").write_text(json.dumps({
        "raw_root":str(a.raw_root),
        "years_audited":sorted(set(selected)|set(prior_years)),
        "years_audited_this_run":list(selected),
        "years_carried_forward":prior_years,
        "merged":bool(a.merge and prior_years),
        "years_available":list(YEARS),
        "partial_audit":sorted(set(selected)|set(prior_years))!=sorted(YEARS),
        "lap_files_audited":len(quality),
        "years":summary,
        "malformed":errors},indent=2),encoding="utf-8")
    with (a.output/"events_sessions.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["year","event","session","driver_directories","session_files"]); w.writeheader(); w.writerows(events)
    with (a.output/"field_availability.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["file_kind","raw_field","years"]); w.writeheader()
        for (kind,name),ys in sorted(fields.items()): w.writerow({"file_kind":kind,"raw_field":name,"years":";".join(sorted(ys))})
    covered=sorted(set(selected)|set(prior_years))
    rows=[{"file_kind":k,"field":n,"years":sorted(ys),"missing_years":[y for y in covered if y not in ys]} for (k,n),ys in sorted(fields.items())]
    (a.output/"schema_differences.json").write_text(json.dumps({"years_audited":covered,"partial_audit":covered!=sorted(YEARS),"by_file_and_field":rows},indent=2),encoding="utf-8")
    canonical={"phase":"2 proposal only","fields":[{"canonical_name":n,"raw_source_fields":[raw],"datatype":dtype,"unit":unit,"required":required,"supported_years":list(YEARS),"notes":"Retain raw values and expose absence/year-specific semantics."} for n,(raw,dtype,unit,required) in CANONICAL.items()]}
    (a.output/"canonical_schema.json").write_text(json.dumps(canonical,indent=2),encoding="utf-8")
    cols=["year","event","session","driver","lap_file","rows","distance_monotonic","distance_nonnumeric_count","time_step_s","speed","throttle","brake","drs","DriverAhead","DistanceToDriverAhead","x","y","z"]
    with (a.output/"data_quality_summary.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
        for r in quality: w.writerow({k:json.dumps(r[k]) if isinstance(r.get(k),(dict,list)) else r.get(k) for k in cols})
    british=[r for r in quality if r["year"]=="2026" and r["event"]=="British Grand Prix" and r["session"]=="Sprint" and r["driver"] in {"HAM","ANT"}]
    (a.output/"british_sprint_2026.json").write_text(json.dumps({"scope":"2026 British Grand Prix Sprint, HAM and ANT","laps":british},indent=2),encoding="utf-8")
if __name__=="__main__": main()
