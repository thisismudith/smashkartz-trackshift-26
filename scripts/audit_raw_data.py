#!/usr/bin/env python3
"""Read-only Phase 1 schema audit for TracingInsights raw mirrors."""
from __future__ import annotations
import argparse, csv, json
from collections import defaultdict
from pathlib import Path
from statistics import median

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
    d=tel.get("distance",[]); t=tel.get("time",[])
    if len(d)>1: result["distance_monotonic"]=all(b>=a for a,b in zip(d,d[1:]) if a is not None and b is not None)
    dt=[b-a for a,b in zip(t,t[1:]) if isinstance(a,(int,float)) and isinstance(b,(int,float)) and b>a]
    if dt: result["time_step_s"]={"min":min(dt),"median":median(dt),"max":max(dt)}
    return result
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--raw-root",type=Path,default=Path("data/raw"))
    ap.add_argument("--output",type=Path,default=Path("artifacts/schema_audit"))
    a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    fields=defaultdict(set); events=[]; quality=[]; summary={}; errors=[]
    for year in YEARS:
        root=a.raw_root/year
        if not root.exists(): summary[year]={"status":"missing"}; continue
        summary[year]={"status":"present","top_level_files":sorted(p.name for p in root.iterdir() if p.is_file()),
                       "event_directories":sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))}
        for event in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for session in sorted(p for p in event.iterdir() if p.is_dir()):
                fs=list(session.iterdir()); drivers=[p for p in fs if p.is_dir()]
                events.append({"year":year,"event":event.name,"session":session.name,"driver_directories":len(drivers),
                               "session_files":";".join(sorted(p.name for p in fs if p.is_file()))})
                for driver in drivers:
                    lp=driver/"laptimes.json"
                    if lp.exists():
                        laps=unwrap(load(lp),"laptimes")
                        if isinstance(laps,list) and laps and isinstance(laps[0],dict):
                            for k in laps[0]: fields[("laptimes.json",k)].add(year)
                    for tel in driver.glob("*_tel.json"):
                        try: q=audit_tel(tel)
                        except Exception as exc: errors.append({"path":str(tel),"error":str(exc)}); continue
                        for k in q["fields"]: fields[("tel.json",k)].add(year)
                        quality.append({"year":year,"event":event.name,"session":session.name,"driver":driver.name,"lap_file":tel.name,**q})
                for fn,key in (("weather.json","weather"),("rcm.json","rcm"),("drivers.json","drivers"),("corners.json","corners"),("session_laptimes.json","session_laptimes")):
                    p=session/fn
                    if p.exists():
                        obj=unwrap(load(p),key)
                        if isinstance(obj,dict):
                            for k in obj: fields[(fn,k)].add(year)
    (a.output/"repository_summary.json").write_text(json.dumps({"raw_root":str(a.raw_root),"years":summary,"malformed":errors},indent=2),encoding="utf-8")
    with (a.output/"events_sessions.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["year","event","session","driver_directories","session_files"]); w.writeheader(); w.writerows(events)
    with (a.output/"field_availability.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["file_kind","raw_field","years"]); w.writeheader()
        for (kind,name),ys in sorted(fields.items()): w.writerow({"file_kind":kind,"raw_field":name,"years":";".join(sorted(ys))})
    rows=[{"file_kind":k,"field":n,"years":sorted(ys),"missing_years":[y for y in YEARS if y not in ys]} for (k,n),ys in sorted(fields.items())]
    (a.output/"schema_differences.json").write_text(json.dumps({"by_file_and_field":rows},indent=2),encoding="utf-8")
    canonical={"phase":"2 proposal only","fields":[{"canonical_name":n,"raw_source_fields":[raw],"datatype":dtype,"unit":unit,"required":required,"supported_years":list(YEARS),"notes":"Retain raw values and expose absence/year-specific semantics."} for n,(raw,dtype,unit,required) in CANONICAL.items()]}
    (a.output/"canonical_schema.json").write_text(json.dumps(canonical,indent=2),encoding="utf-8")
    cols=["year","event","session","driver","lap_file","rows","distance_monotonic","time_step_s","speed","throttle","brake","drs","DriverAhead","DistanceToDriverAhead","x","y","z"]
    with (a.output/"data_quality_summary.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
        for r in quality: w.writerow({k:json.dumps(r[k]) if isinstance(r.get(k),(dict,list)) else r.get(k) for k in cols})
    british=[r for r in quality if r["year"]=="2026" and r["event"]=="British Grand Prix" and r["session"]=="Sprint" and r["driver"] in {"HAM","ANT"}]
    (a.output/"british_sprint_2026.json").write_text(json.dumps({"scope":"2026 British Grand Prix Sprint, HAM and ANT","laps":british},indent=2),encoding="utf-8")
if __name__=="__main__": main()
