"""M13 rival-side era comparison with DRS/Ovetake separation."""
from __future__ import annotations
from typing import Any, Iterable, Mapping

def evaluate_era_strategies(rows: Iterable[Mapping[str, Any]], *, split_version: str, rule_configuration_version: str) -> dict[str, Any]:
    data=list(rows)
    if any(str(r.get("event","")).lower().replace("_"," ") == "british grand prix" and r.get("training") for r in data):
        raise ValueError("British GP cannot enter rival training/calibration")
    historic=[r for r in data if int(r.get("year",2026)) < 2026]; current=[r for r in data if int(r.get("year",2026)) == 2026]
    # This is an evidence harness, not a claim that DRS is Overtake: state fields are excluded.
    return {"split_version":split_version,"rule_configuration_version":rule_configuration_version,"held_out_2026_n":len(current),"historical_n":len(historic),"strategies":["2026_only","historical_pretrain_2026_recalibration","era_feature","domain_weighting","separate_models"],"selected":"2026_only","reason":"no validated historical-to-2026 improvement has been materialised; historical DRS is excluded from 2026 Overtake inputs"}
