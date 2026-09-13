"""API.md-compatible synthetic development service.

The app deliberately uses the same model functions as the planner and replay
builder. It is useful for UI integration and checkpoint evidence, but it does
not promote synthetic values to observed telemetry or final release evidence.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from trackshift.data.registry import FeatureBoundaryError, assert_final_feature_boundary, validate_feature_admission
from trackshift.planner.api import RiskSpec, generate_baseline_plans, plan
from trackshift.rival.api import rival_state
from trackshift.rules import api as rules_api
from trackshift.rules.config import RuleConfigError, unsourced_keys
from trackshift.rules.eligibility import eligibility_margin, project_gap_at_line
from trackshift.serve import pass_fallback
from trackshift.serve.pass_service import (
    CheckpointViolation,
    NotModelEligible,
    load_predictor,
    normalise_features,
)
from trackshift.serve.track_data import track_response
from trackshift.sim.api import POLICIES, UnknownPolicyError, policy_registry, simulate
from trackshift.value.api import DPConfig, shadow_price
from trackshift.value.counterattack import evaluate_counterattack

#: CP-14 artifacts. Resolved from this file rather than a working directory so
#: a replay bundle built from any cwd finds the same models (section 5: the
#: bundle must be self-contained, with no machine-specific paths).
PASS_MODELS = Path(__file__).resolve().parents[3] / "artifacts" / "models" / "pass"

from . import config_data
from .fixture import (
    EVENT,
    BATTLE_ID,
    RULE_VERSION,
    SYNTHETIC_FIXTURE_VERSION,
    synthetic_era_report,
    synthetic_pass,
    synthetic_rival_model,
    synthetic_rival_rows,
    synthetic_rules,
    synthetic_segments,
    synthetic_state,
    synthetic_transition,
    synthetic_validation,
)

API_VERSION = "1.0.0"


def _versions() -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "git_commit": "synthetic-development",
        "models": {
            "rival": "m09_rival_benchmark_v2",
            "value": "m22_dp_development_v2",
            "planner": "m24_beam_development_v1",
            "simulator": "m26_simulator_development_v1",
            "policies": "m27_rival_policies_v1",
            "rules": RULE_VERSION,
        },
    }


def _clean_rules(rules: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in rules.items() if not str(key).startswith("_")}


def _year(payload: Mapping[str, Any]) -> int:
    nested = payload.get("state") if isinstance(payload.get("state"), Mapping) else {}
    ref = nested.get("ref") if isinstance(nested.get("ref"), Mapping) else {}
    value = payload.get("year", ref.get("year", nested.get("year", 2026)))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 2026


def _guard_payload(payload: Mapping[str, Any], context: str, *, final_mode: bool = False) -> None:
    try:
        validate_feature_admission(
            payload, year=_year(payload), consumer=context, mode="final" if final_mode else "development", provenance=payload,
        )
        if final_mode:
            assert_final_feature_boundary(payload, context, year=_year(payload))
    except FeatureBoundaryError as exc:
        raise HTTPException(status_code=422, detail={"code": "FEATURE_SCHEMA_MISMATCH", "message": str(exc)}) from exc


def _first_number(payload: Mapping[str, Any], *paths: tuple[str, ...]) -> float | None:
    """The first of several documented spellings that actually carries a number.

    The same quantity reaches this service under more than one shape: API.md 5.7
    nests the gap inside a ``StrategicState``, the UI posts it one level down
    under ``state``, and older callers put it at the top level. Reading only one
    spelling does not fail loudly -- it falls through to a default and returns a
    confident answer about a gap nobody asked about.

    A ``Quantity`` is unwrapped on the way past, so ``{"value": 0.8}`` and ``0.8``
    both resolve; that is the shape every number in this API is supposed to have.
    """
    for path in paths:
        node: Any = payload
        for key in path:
            if not isinstance(node, Mapping):
                node = None
                break
            node = node.get(key)
        if isinstance(node, Mapping):
            node = node.get("value", node.get("mean"))
        if node is None or isinstance(node, bool):
            continue
        try:
            value = float(node)
        except (TypeError, ValueError):
            continue
        if value == value:  # reject NaN, which would propagate silently
            return value
    return None


def _rules(event: str) -> dict[str, Any]:
    try:
        return rules_api.load_event_rules(event)
    except RuleConfigError as exc:
        raise HTTPException(status_code=404, detail={"code": "UNKNOWN_EVENT", "message": str(exc)}) from exc


def _normalise_state(state: dict[str, Any]) -> dict[str, Any]:
    """Accept the StrategicState shape API.md documents, not only the internal one.

    Two fields are spelled differently in the request body than in the state the
    DP reads, and both failed SILENTLY -- ``required_state_inputs`` found no
    energy and no eligibility, so the planner answered UNAVAILABLE with an empty
    action set. A caller posting exactly what API.md 5.12 documents got back
    "no legal actions at this state", which reads as a rule-engine verdict about
    their scenario rather than as a body the service could not read.

    Only spellings are translated here. Nothing is defaulted: a body that states
    neither energy nor eligibility still reaches the DP without them and still
    comes back UNAVAILABLE, which is the correct answer to a question that was
    not fully asked.
    """
    energy = state.get("energy")
    if isinstance(energy, Mapping) and "ers_soc_est_mj" not in energy:
        kj = energy.get("energy_kj")
        mj = energy.get("energy_mj")
        value = (float(kj) / 1000.0) if isinstance(kj, (int, float)) else (float(mj) if isinstance(mj, (int, float)) else None)
        if value is not None:
            state["energy"] = {**energy, "ers_soc_est_mj": {
                "value": value, "unit": "MJ", "provenance": "SIMULATED",
                "reason": "supplied in the request body as a scenario value, not measured",
            }}

    # API.md sends the Overtake state as a bare enum string; the reader wants the
    # block. A string is unambiguous, so widening it is a translation, not a guess.
    overtake = state.get("overtake_state")
    if isinstance(overtake, str):
        state["overtake_state"] = {
            "state": overtake, "armed": overtake.upper() in {"ARMED", "ACTIVE", "ELIGIBLE"},
            "provenance": "RULE",
        }
    return state


def _state(payload: Mapping[str, Any]) -> dict[str, Any]:
    supplied = payload.get("state")
    if not isinstance(supplied, Mapping):
        return synthetic_state()
    return _normalise_state(copy.deepcopy(dict(supplied)))


def _segments(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    supplied = payload.get("segments")
    if isinstance(supplied, list) and supplied and all(isinstance(item, Mapping) for item in supplied):
        return [dict(item) for item in supplied]
    return synthetic_segments()


CHECKPOINTS = ("DETECTION", "ACTIVATION", "BRAKING")

#: The season whose rate table is the headline measurement. 2022-25 are DRS-era
#: and exist only for the era comparison (API.md section 8: never present a
#: historical DRS value as an Overtake fact).
CURRENT_SEASON = "2026"

#: API.md 3.7. A stub answer is shaped exactly like a real one, so the only thing
#: standing between a placeholder and a screenshot that claims a measurement is
#: this header. The body carries `is_stub` too; the header is what a client can
#: check without understanding the route's schema.
STUB_HEADER = "X-TrackShift-Stub"
UNVERIFIED_HEADER = "X-TrackShift-Unverified"

#: Where the Next dev server runs. Named explicitly rather than allowing every
#: origin: the service answers from local telemetry on a developer's machine and
#: a wildcard would let any page that host visits read it.
DEV_ORIGINS = (
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:3001", "http://127.0.0.1:3001",
)


def _empirical_pass(app: FastAPI, gap_s: Any) -> dict[str, Any]:
    """The counted pass rate for this gap, beside whatever the model says.

    This is a measurement, not a prediction: ``n`` approaches inside this gap
    bucket were observed in the season's telemetry and ``passes`` of them
    completed, with a Wilson interval for the count. It rides ALONGSIDE the
    trained model rather than replacing it, because "the model says 9% where 508
    comparable approaches produced 13%" is the comparison that tells a viewer
    whether to believe the model -- and neither number alone carries it.

    Returns an empty mapping when there is no table or no gap to bucket. An
    absent measurement is absent; it is never filled with the pooled rate, which
    would answer a question about this gap using every other gap's data.
    """
    tables = getattr(app.state, "pass_rate_tables", None)
    if not tables:
        return {}
    try:
        gap = float(gap_s)
    except (TypeError, ValueError):
        return {}

    # The 2026 table is the only one describing the Overtake system; the earlier
    # seasons are DRS and belong in the comparison, never in the headline.
    current = tables.get(CURRENT_SEASON)
    if current is None:
        return {}
    counted = pass_fallback.predict_from_table(current, gap)
    if not counted:
        return {}

    block: dict[str, Any] = {
        "empirical": {
            "p_pass_by_outcome_horizon": {
                "value": counted["p_pass_by_outcome_horizon"],
                "unit": None,
                # DERIVED, not INFERRED: nothing was fitted. This is a frequency
                # counted off the telemetry for the bucket the gap falls in.
                "provenance": "DERIVED",
                "model": "empirical-rate-table",
            },
            "interval": {"low": counted["interval_low"], "high": counted["interval_high"],
                         "method": "wilson", "level": 0.95},
            "support": {"n": counted["n"], "passes": counted["passes"],
                        "gap_bucket_s": counted["gap_bucket_s"]},
        },
        # The interval and the support stay INSIDE `empirical` and are not also
        # flattened to the top level. They were, and a caller reading the
        # headline probability then found a band belonging to a different
        # quantity sitting beside it: the model's 16.3% was rendered as
        # "16.3% (6.0-9.6%)", a point estimate outside its own interval, with a
        # counted rate's DERIVED uncertainty attached to an INFERRED number.
        # The model's own spread is a separate thing (M12) and is absent until
        # it is wired, so the honest shape is no top-level interval at all.
    }
    comparison = pass_fallback.era_comparison(tables, gap)
    if comparison:
        # 2022-25 rows are DRS-era and 2026 is Overtake. API.md section 8 forbids
        # dressing one as the other, so the era travels with every season row.
        block["era_comparison"] = comparison
    return block


def _pass_predictor_factory(app: FastAPI):
    """Cache one predictor per checkpoint; a miss is remembered, not retried.

    Loading unpickles a model, so retrying on every request would make a missing
    artifact cost real latency on the hot path.
    """
    def get(checkpoint: str):
        cache = app.state.pass_predictors
        if checkpoint not in cache:
            try:
                cache[checkpoint] = load_predictor(PASS_MODELS, checkpoint=checkpoint)
            except Exception:
                cache[checkpoint] = None
        return cache[checkpoint]
    return get


def create_app(*, mode: str = "service", final_mode: bool = False) -> FastAPI:
    """Create the deterministic development service.

    ``final_mode=True`` is intentionally rejected at startup until official
    rules and accepted C4/C5 callbacks exist; this is a hard release boundary.
    """
    if final_mode:
        # This raises on unresolved Detection Gap/deployment/store inputs and
        # prevents a route from quietly falling back to the synthetic fixture.
        rules_api.load_event_rules(EVENT, final_mode=True)
    app = FastAPI(title="TrackShift", version=API_VERSION)
    # The UI is served from the Next dev server, a different origin from this
    # one. Without this every browser call fails at the preflight and the panels
    # report "backend unreachable" while curl against the same URL succeeds --
    # which sends the reader looking for a fault that is not there.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DEV_ORIGINS),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        # Nothing can read a custom response header cross-origin unless it is
        # named here, so omitting these would leave the stub badge unreachable
        # from the very client that has to render it.
        expose_headers=[STUB_HEADER, UNVERIFIED_HEADER],
    )
    app.state.mode = mode
    app.state.final_mode = final_mode
    app.state.synthetic = True
    app.state.fixture_version = SYNTHETIC_FIXTURE_VERSION
    app.state.rival_model = synthetic_rival_model()

    app.state.pass_predictors = {}
    # Counted pass rates per season, loaded once. Absent artifacts are not an
    # error: the route then answers from the model alone and says so, rather
    # than failing to start over a comparison that is nice to have.
    try:
        app.state.pass_rate_tables = pass_fallback.load_all_tables()
    except Exception:
        app.state.pass_rate_tables = {}
    # Every route that answers from a placeholder adds its name here, so the
    # replay bundle can report what it actually shipped instead of asserting an
    # empty list.
    app.state.stubs_used = set()
    _pass_predictor = _pass_predictor_factory(app)

    def _mark_stub(response: Response, name: str) -> None:
        """Record a placeholder answer in all three places it has to appear.

        ``stubs_used`` is what the replay manifest reports, ``is_stub`` in the
        body is what a client that knows the route's schema reads, and the
        header is what a generic client can check without knowing it. Only the
        pass route ever set any of them, so every other synthetic route shipped
        looking like a measurement -- and /meta's "stubs" list, which exists to
        catch exactly that, reported an empty set truthfully computed from
        nothing.
        """
        app.state.stubs_used.add(name)
        response.headers[STUB_HEADER] = "true"

    @app.get("/api/v1/meta")
    def meta() -> dict[str, Any]:
        # Measured, not asserted (INTEGRATION.md section 8). This was a hard-coded
        # empty list while `stubs_used` accumulated route names two lines away --
        # exactly the failure the field exists to detect.
        return {
            **_versions(), "mode": mode,
            "stubs": sorted(app.state.stubs_used),
            "pass_model": {
                checkpoint: (
                    None if app.state.pass_predictors.get(checkpoint) is None
                    else app.state.pass_predictors[checkpoint].artifact_version
                )
                for checkpoint in CHECKPOINTS if checkpoint in app.state.pass_predictors
            },
            "empirical_rate_seasons": sorted(getattr(app.state, "pass_rate_tables", {})),
            "synthetic_fixture": SYNTHETIC_FIXTURE_VERSION, "release_ready": False,
        }

    @app.get("/api/v1/validation")
    def validation(response: Response) -> dict[str, Any]:
        # Every block of this report is generated from the synthetic fixture --
        # the rival rows, the era comparison, the planner and simulator counts.
        # It is the page a reader goes to in order to decide whether to believe
        # the rest, so it is the last route that may look measured.
        _mark_stub(response, "validation")
        return {**_versions(), **synthetic_validation(), "era_evaluation": synthetic_era_report(),
                "is_stub": True}

    @app.get("/api/v1/track/{event}")
    def track(event: str, response: Response) -> dict[str, Any]:
        rules = _rules(event)
        lines: list[dict[str, Any]] = []
        for zone in (rules.get("overtake") or {}).get("zones", []):
            for field, kind in (("detection_line_m", "DETECTION"), ("activation_line_m", "ACTIVATION")):
                node = zone.get(field) if isinstance(zone, Mapping) else None
                if isinstance(node, Mapping) and node.get("value") is not None:
                    lines.append({"kind": kind, "zone": zone.get("zone"), "distance_m": node["value"], "provenance": node.get("value_source"), "source": node.get("source")})

        # The rule engine publishes this same lap length with its citation --
        # median max(distance) over sampled Race laps, cross-checked against the
        # homologated circuit length. This route used to re-publish the number
        # stripped of that citation and re-tagged SIMULATED, so one server made
        # two incompatible provenance claims about one measurement, and the
        # route the map actually draws from was the one that got it wrong.
        measured = rules.get("lap_length_m")
        measured = measured if isinstance(measured, Mapping) else {}
        lap_length_source = {
            "value_source": measured.get("value_source"),
            "source": measured.get("source"),
            "published_circuit_length_m": measured.get("published_circuit_length_m"),
            "delta_vs_published_m": measured.get("delta_vs_published_m"),
            "note": measured.get("note"),
        } if measured else None

        # The real M03 segmentation when config/geometry carries this circuit.
        # This is the ONLY source of `corner_type` a caller can send to
        # /pass/predict and have it land in a trained category -- the classifier
        # here and the one the model was fitted on are the same code. Reproducing
        # it in the browser from curvature would produce plausible labels the
        # model has never seen, which is worse than sending none.
        real = track_response(event)
        if real is not None:
            return {**real, "lines": lines,
                    "zones": (rules.get("overtake") or {}).get("zones", []),
                    "lap_length_source": lap_length_source,
                    "provenance": "DERIVED", "is_stub": False, "versions": _versions()}

        _mark_stub(response, f"track/{event}")
        # No geometry artifact for this event. The lap length is still a real
        # measurement and travels with its citation; everything that would have
        # to be invented comes back empty with a reason. What stood here before
        # answered every unknown event with Silverstone's name, Silverstone's
        # lap length tagged SIMULATED, and a three-point 0-360 m track -- onto
        # which the FIA-aligned Detection Line at 5789 m below cannot be placed.
        return {
            "event": event, "track": rules.get("circuit"),
            "lap_length_m": measured.get("value"),
            "lap_length_source": lap_length_source,
            "centreline": [],
            "centreline_note": "no config/geometry entry for this event; no centreline is "
                               "persisted for it and none is fabricated here",
            "segments": [], "segment_count": 0, "lines": lines,
            "zones": (rules.get("overtake") or {}).get("zones", []),
            "provenance": "RULE", "is_stub": True,
            "reason": f"no config/geometry entry for {event}; segment boundaries and centreline "
                      "are unavailable. Only the rule-configuration lap length and the "
                      "Detection/Activation lines answer here, and they carry their own sources.",
            "versions": _versions(),
        }

    @app.get("/api/v1/rules/{event}/power_envelope")
    def power_envelope(event: str, response: Response, mode: str = "both", step_kmh: float = 5.0) -> dict[str, Any]:
        rules = _rules(event)
        modes = ("normal", "override") if mode == "both" else (mode,)
        if any(item not in {"normal", "override"} for item in modes) or step_kmh <= 0:
            raise HTTPException(status_code=422, detail={"code": "RULE_KEY_MISSING", "message": "mode and step_kmh are invalid"})
        envelope = rules.get("power_envelope") or {}
        curves, breakpoints = {}, {}
        for selected in modes:
            grid, table = rules_api.envelope_table(rules, selected, max_speed_kmh=360, step_kmh=step_kmh)
            curves[selected] = [{"speed_kmh": float(speed), "max_power_kw": float(power), "provenance": "RULE"} for speed, power in zip(grid, table)]
            declared = envelope.get(selected) or {}
            # The curve is piecewise linear, so these are the exact corners the
            # sampled grid can only approach. A client given the samples alone
            # has to infer them, and at step_kmh=5 the inference lands on a grid
            # point rather than on the regulated speed.
            breakpoints[selected] = {
                "breakpoints_kmh": declared.get("breakpoints_kmh"),
                "max_power_kw": declared.get("max_power_kw"),
                "value_source": declared.get("value_source"),
                "source": declared.get("source"),
            }
        separation = envelope.get("separation_speed_kmh") or {}
        # Below this speed the two curves coincide, so `override` confers no
        # power advantage and the mode is not observable (section 20.2): the
        # discriminator must answer UNKNOWN there. Without this field a client
        # of this route alone cannot implement that rule at all.
        separation_block: dict[str, Any] = {
            "value": separation.get("value"), "unit": "km/h", "provenance": "RULE",
            "value_source": separation.get("value_source"), "source": separation.get("source"),
        }
        if separation.get("value") is None:
            separation_block["reason"] = (
                f"separation_speed_kmh is not resolved in {event}'s rule configuration, so the "
                "speed above which the two modes become discriminable is unknown")
        unverified = [key for key in unsourced_keys(rules) if key.startswith("power_envelope")]
        if unverified:
            # API.md 3.7 ENVELOPE_UNVERIFIED: a 200 whose envelope values are not
            # cited still has to be badgeable by a client that only reads headers.
            response.headers[UNVERIFIED_HEADER] = "power_envelope"
        return {"event": event, "separation_speed_kmh": separation_block,
                "breakpoints": breakpoints, "curves": curves, "provenance": "RULE",
                "verified": not unverified, "unverified_keys": unverified,
                "rule_configuration_version": RULE_VERSION, "versions": _versions()}

    @app.get("/api/v1/rules/{event}")
    def rules(event: str, response: Response) -> dict[str, Any]:
        loaded = _rules(event)
        # Computed from the RAW config, before _clean_rules strips the private
        # keys: every key whose value_source is not a claimable tier. Non-empty
        # means the page may not claim legality by construction (section 57) --
        # and until now this route published no way at all for it to tell, while
        # two of three energy keys and detection_gap_s are in fact unverified.
        unverified = list(unsourced_keys(loaded))
        if unverified:
            response.headers[UNVERIFIED_HEADER] = ",".join(sorted({key.split(".")[0] for key in unverified}))
        return {"event": event, "year": 2026, "config_version": RULE_VERSION,
                **_clean_rules(loaded),
                "unverified_keys": unverified, "all_values_verified": not unverified,
                "versions": _versions()}

    @app.get("/api/v1/battles")
    def battles(response: Response, year: int | None = None, event: str | None = None, session: str | None = None) -> dict[str, Any]:
        if event and event != EVENT:
            return {"battles": [], "versions": _versions()}
        # The one battle in this index is generated, not detected from a session.
        _mark_stub(response, "battles")
        return {"battles": [{"battle_id": BATTLE_ID, "year": 2026, "event": EVENT, "session": "Race", "attacker": "HAM", "defender": "ANT", "provenance": "SIMULATED"}], "is_stub": True, "versions": _versions()}

    @app.post("/api/v1/rival/state")
    def rival(payload: dict[str, Any], response: Response) -> dict[str, Any]:
        _guard_payload(payload, "C10 API")
        segments = payload.get("segments")
        # An ABSENT `segments` means "I have no battle window; show me the fixture",
        # and the response says so through `synthetic_fixture`. An EMPTY LIST is a
        # different statement -- the caller had a window and it contained nothing --
        # and C10 refuses it, because a belief over four tactical states inferred
        # from zero observations is not a weak answer, it is not an answer. That
        # refusal used to escape as an unhandled ValueError and reach the UI as a
        # bare 500 with no way to tell it from a dead backend.
        if segments is None:
            rows: list[Any] = list(synthetic_rival_rows()[:8])
            fixture: str | None = SYNTHETIC_FIXTURE_VERSION
        elif not isinstance(segments, list):
            raise HTTPException(status_code=422, detail={
                "code": "SEGMENTS_NOT_A_LIST",
                "message": f"segments must be a list of M08 battle segments, got {type(segments).__name__}",
            })
        elif not segments:
            raise HTTPException(status_code=422, detail={
                "code": "INSUFFICIENT_SEGMENTS",
                "message": "C10 requires at least one causal battle segment. Omit `segments` "
                           "entirely to read the labelled synthetic fixture instead.",
            })
        else:
            rows, fixture = list(segments), None
        try:
            answer = rival_state(rows, app.state.rival_model)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={
                "code": "RIVAL_SEGMENTS_REJECTED", "message": str(exc),
            }) from exc
        # Only the fixture branch is a placeholder. When the caller supplies its
        # own causal segments the belief is computed from what they sent, and
        # calling that a stub would understate it as badly as the reverse.
        if fixture:
            _mark_stub(response, "rival/state:synthetic_fixture")
        return {**answer, **({"synthetic_fixture": fixture, "is_stub": True} if fixture else {})}

    @app.get("/api/v1/battles/{battle_id}/timeline")
    def timeline(battle_id: str, response: Response) -> dict[str, Any]:
        if battle_id != BATTLE_ID:
            raise HTTPException(status_code=404, detail={"code": "UNKNOWN_BATTLE", "message": battle_id})
        rows = synthetic_rival_rows()[: len(synthetic_segments())]
        # Gap and energy here are an arithmetic ramp off the fixture, not a
        # measured battle: 0.72 - 0.04i and 2.4 - 0.08i.
        _mark_stub(response, f"battles/{battle_id}/timeline")
        return {"battle_id": battle_id, "is_stub": True, "segments": [{"segment_id": segment["segment_id"], "gap_s": {"value": 0.72 - i * 0.04, "unit": "s", "provenance": "SIMULATED"}, "energy_mj": {"value": 2.4 - i * 0.08, "unit": "MJ", "provenance": "SIMULATED"}, "rival_state": rival_state(rows[: i + 1], app.state.rival_model), "provenance": "SIMULATED"} for i, segment in enumerate(synthetic_segments())], "causal_cutoff": {"segment_index": len(rows) - 1, "provenance": "DERIVED"}, "versions": _versions()}

    @app.post("/api/v1/rules/legal_actions")
    def legal_actions(payload: dict[str, Any]) -> dict[str, Any]:
        _guard_payload(payload, "C3 API", final_mode=bool(app.state.final_mode))
        result = rules_api.legal_actions(_state(payload), _rules(str(payload.get("event", EVENT))), final_mode=bool(app.state.final_mode))
        return {**result, "versions": _versions()}

    @app.post("/api/v1/rules/eligibility")
    def eligibility(payload: dict[str, Any]) -> dict[str, Any]:
        _guard_payload(payload, "C6 API", final_mode=bool(app.state.final_mode))
        gap = _first_number(payload, ("gap_s", "time_gap_s"),
                            ("state", "gap", "time_gap_s"), ("gap", "time_gap_s"))
        if gap is None:
            # Refusing beats defaulting. The previous default answered every
            # request with the same probability whatever gap was asked about --
            # 0.30 s and 2.90 s returned byte-identical bodies -- and a constant
            # that looks like a projection is worse than no projection.
            raise HTTPException(status_code=422, detail={
                "code": "ILLEGAL_STATE",
                "message": "no time gap in the request. Send state.gap.time_gap_s "
                           "(API.md 5.7 StrategicState) or a top-level time_gap_s; "
                           "P(eligible) is not defined without one.",
            })
        rate = _first_number(payload, ("closing_rate_s_per_s",),
                             ("state", "gap", "gap_rate_s_per_s"),
                             ("gap", "gap_rate_s_per_s")) or 0.0
        trailing = payload.get("trailing_closing_rates")
        if not (isinstance(trailing, list) and len(trailing) >= 2):
            trailing = [rate, rate * 0.8, rate * 1.2]
        horizon = _first_number(payload, ("time_to_line_s",)) or 2.0
        projection = project_gap_at_line(gap, rate, horizon, [float(v) for v in trailing])
        return {
            "p_eligible": {"value": projection.p_eligible, "provenance": "SIMULATED"},
            # Both spellings: API.md 5.5's eligibility block calls it
            # `eligibility_margin_s` and this route has historically returned
            # `margin_s`. One of them was always being read as undefined.
            "eligibility_margin_s": {"value": projection.eligibility_margin_s, "unit": "s", "provenance": "SIMULATED"},
            "margin_s": {"value": projection.eligibility_margin_s, "unit": "s", "provenance": "SIMULATED"},
            "gap_s": {"value": gap, "unit": "s", "provenance": "DERIVED"},
            "terms_used": list(projection.terms_used),
            "versions": _versions(),
        }

    @app.post("/api/v1/pass/predict")
    def pass_predict(payload: dict[str, Any], response: Response) -> dict[str, Any]:
        _guard_payload(payload, "C4 API", final_mode=bool(app.state.final_mode))

        # API.md 5.8 names this field `decision_checkpoint`. `checkpoint` is the
        # older spelling and is still accepted -- but only as a fallback, because
        # reading the wrong one silently scored every ACTIVATION and BRAKING
        # request with the DETECTION model and then refused it for carrying its
        # own checkpoint's features.
        checkpoint = str(
            payload.get("decision_checkpoint") or payload.get("checkpoint") or "DETECTION"
        ).upper()
        if checkpoint not in CHECKPOINTS:
            raise HTTPException(status_code=422, detail={
                "code": "FEATURE_SCHEMA_MISMATCH",
                "message": f"decision_checkpoint must be one of {list(CHECKPOINTS)}, got {checkpoint!r}",
            })

        # Both documented request shapes, and the UI's own field names, reduced to
        # the model's locked schema. Without this the feature vector arrives empty
        # and the model answers with its base rate for every gap.
        features = normalise_features(payload)
        empirical = _empirical_pass(app, features.get("gap_at_checkpoint"))

        # The real CP-14 artifact when one is on disk. Its refusals --
        # CHECKPOINT_VIOLATION and NOT_MODEL_ELIGIBLE -- are answers, not
        # failures, so they propagate rather than falling through to the stub:
        # returning a synthetic probability for a request the real model just
        # refused would be the worst of both.
        predictor = _pass_predictor(checkpoint)
        if predictor is not None:
            try:
                answer = predictor.predict(features)
            except (CheckpointViolation, NotModelEligible) as exc:
                raise HTTPException(status_code=422, detail=exc.as_error()["error"]) from exc
            return {**answer, **empirical, "decision_checkpoint": checkpoint,
                    "outcome_horizon": "zone_exit_v1", "versions": _versions()}

        # No trained artifact. The counted rate is still a real answer for this
        # gap, so it becomes the headline -- tagged DERIVED, and explicitly not a
        # model output. Only when there is no measurement either does the
        # synthetic curve answer, and then it says so in the body and the header.
        if empirical:
            counted = empirical["empirical"]
            return {
                "p_pass_by_outcome_horizon": counted["p_pass_by_outcome_horizon"],
                "decision_checkpoint": checkpoint, "checkpoint": checkpoint,
                "outcome_horizon": "zone_exit_v1",
                "calibration": "none",
                "features_supplied": ["gap_at_checkpoint"],
                "features_missing": [],
                "evidence_grade": "MEASURED_RATE",
                "is_stub": False,
                "reason": "no CP-14 artifact for this checkpoint; answered from the "
                          "counted 2026 rate for this gap bucket",
                # Here -- and ONLY here -- the interval and support belong at the
                # top level, because here the headline IS the counted rate they
                # describe. Beside a model's probability they would be a band
                # from a different quantity.
                "interval": counted["interval"], "support": counted["support"],
                **empirical, "versions": _versions(),
            }

        gap = float(features.get("gap_at_checkpoint") or 0.72)
        deploy = float(payload.get("deploy_level", 0.5))
        probability = 1.0 / (1.0 + __import__("math").exp(4.0 * (gap - 0.35) - deploy))
        app.state.stubs_used.add(f"pass/predict:{checkpoint}")
        response.headers[STUB_HEADER] = "true"
        return {"p_pass_by_outcome_horizon": {"value": probability, "provenance": "SIMULATED"}, "decision_checkpoint": checkpoint, "checkpoint": checkpoint, "calibration": "synthetic-development", "is_stub": True, "versions": _versions()}

    @app.post("/api/v1/twin/segment_time")
    def segment_time(payload: dict[str, Any], response: Response) -> dict[str, Any]:
        _guard_payload(payload, "C5 API", final_mode=bool(app.state.final_mode))
        state = _state(payload)
        result = synthetic_transition(state, payload.get("action") or {}, payload.get("context") or synthetic_segments()[0])
        _mark_stub(response, "twin/segment_time")

        # What this twin has instead of an uncertainty model. The transition is a
        # closed form with no fitted residual, so a band around its output can
        # only be invented at the serializer -- which is what the hard-coded
        # +/-0.01 s here used to be. A renderer reading it drew an error bar that
        # measured nothing.
        no_band = ("the development transition is a deterministic closed form with no fitted "
                   "residual; an interval around it would be invented, not estimated")

        # kJ, not MJ. This is the energy moved across ONE segment, and API.md 5.3
        # reserves MJ for the store: emitting a per-segment delta under an MJ
        # name left a client that trusts the unit in the name reading low by
        # 1000. The transition computes in MJ, so the conversion happens here at
        # the serializer, with the internal maths untouched.
        gap_before = _first_number(state, ("gap", "time_gap_s"))
        gap_delta = None if gap_before is None else result["gap_s"] - gap_before
        return {
            "t_s": {"mean": result["time_delta_s"], "low": None, "high": None,
                    "unit": "s", "provenance": "SIMULATED", "reason": no_band},
            "delta_e_kj": {"mean": result["deployed_energy_mj"] * 1000.0, "low": None, "high": None,
                           "unit": "kJ", "provenance": "SIMULATED", "reason": no_band},
            "harvested_e_kj": {"mean": result["harvested_energy_mj"] * 1000.0, "low": None, "high": None,
                               "unit": "kJ", "provenance": "SIMULATED", "reason": no_band},
            # The gap DELTA the caller asked for, not the gap itself: the
            # transition already knows both ends of it.
            "projected_gap_delta_s": {
                "mean": gap_delta, "low": None, "high": None, "unit": "s",
                "provenance": "SIMULATED",
                "reason": no_band if gap_delta is not None else
                          "no time gap in the request state, so there is no before-value to "
                          "difference against",
            },
            # Not "analytical". The section 29 rungs grade a twin fitted against
            # telemetry; this transition was never fitted against any, so naming
            # a rung would claim a calibration that was not performed.
            "calibration_level": None,
            "calibration_level_reason": "synthetic development transition; no rung of the "
                                        "section 29 calibration hierarchy has been evaluated for it",
            "model_version": SYNTHETIC_FIXTURE_VERSION, "is_stub": True,
            "versions": _versions(),
        }

    @app.post("/api/v1/twin/energy_state")
    def energy_state(payload: dict[str, Any], response: Response) -> dict[str, Any]:
        _guard_payload(payload, "C5 API", final_mode=bool(app.state.final_mode))
        requested_battle = payload.get("battle_id")
        if requested_battle is not None and requested_battle != BATTLE_ID:
            # Answering a question about one battle with another's fixture is the
            # fabrication this service refuses everywhere else.
            raise HTTPException(status_code=404, detail={
                "code": "UNKNOWN_BATTLE",
                "message": f"{requested_battle!r} is not in the battle index; only {BATTLE_ID} exists",
            })
        state = _state(payload)
        segments = synthetic_segments()

        # The request names a causal cutoff and this route used to ignore it,
        # answering "segment 87" with 0.0 m off the fixture's own ref. The cutoff
        # is the one field that decides whether everything below it is causal, so
        # it is resolved or refused -- never defaulted. API.md 5.11's own example
        # asks for segment 87, and refusing it is the correct answer while the
        # window behind this route is two segments long.
        index = payload.get("up_to_segment_index")
        cutoff_provenance = "SIMULATED"
        if index is not None:
            try:
                position = int(index)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail={
                    "code": "ILLEGAL_STATE",
                    "message": f"up_to_segment_index must be an integer, got {index!r}",
                }) from None
            if not 0 <= position < len(segments):
                raise HTTPException(status_code=422, detail={
                    "code": "ILLEGAL_STATE",
                    "message": (f"up_to_segment_index {position} is outside the {len(segments)}-segment "
                                "synthetic development window, so the causal cutoff is undefined. "
                                "This route answers from the fixture; it has no segment index for a "
                                "real battle yet."),
                })
            cutoff, cutoff_reason = float(segments[position]["distance_m"]), None
            cutoff_provenance = "DERIVED"
        else:
            cutoff = _first_number(state, ("ref", "distance_m"))
            cutoff_reason = None if cutoff is not None else (
                "no up_to_segment_index and no state.ref.distance_m, so the causal window has "
                "no end and nothing below is known to be causal")

        energy = _first_number(state, ("energy", "ers_soc_est_mj"), ("energy_mj",))
        if energy is None:
            raise HTTPException(status_code=422, detail={
                "code": "ILLEGAL_STATE",
                "message": "no energy in the request state. Send state.energy.energy_kj "
                           "(API.md 5.6) or state.energy.ers_soc_est_mj; an ERS estimate is not "
                           "defined without one.",
            })
        _mark_stub(response, "twin/energy_state")
        return {
            # MJ, and here it is API.md 5.11 that is wrong rather than the code:
            # this is the STORE, and the unit rule reserves MJ for stored energy
            # and kJ for the per-segment deltas beside it. Publishing a megajoule
            # number under `energy_kj` is the 1000x the rule exists to prevent.
            "energy_mj": {"mean": energy, "low": None, "high": None, "unit": "MJ",
                          "provenance": "SIMULATED",
                          "reason": "the store level is a point value carried by the development "
                                    "fixture; nothing here estimates an interval around it, and "
                                    "the +/-0.2 MJ band that used to stand in its place was "
                                    "invented at the serializer"},
            # Fixture constants. They are labelled SIMULATED and they do not move
            # with the request, which is a different claim from "estimated for
            # this window" -- so the response says which one it is.
            "ers_deployment_kw": {"mean": 180.0, "low": None, "high": None, "unit": "kW",
                                  "provenance": "SIMULATED",
                                  "reason": "development fixture constant; not estimated from the "
                                            "causal window and does not vary with the request"},
            "ers_harvest_kw": {"mean": 12.0, "low": None, "high": None, "unit": "kW",
                               "provenance": "SIMULATED",
                               "reason": "development fixture constant; not estimated from the "
                                         "causal window and does not vary with the request"},
            # Null with a cause rather than omitted: a client that reads
            # `undefined` cannot tell an unbuilt estimator from a typo, and can
            # only render a blank where it should render "Unavailable -- why".
            "energy_deployed_kj": {"value": None, "unit": "kJ", "provenance": "SIMULATED",
                                   "reason": "M14 does not integrate deployment over the causal "
                                             "window here; the fixture carries a store level only"},
            "energy_harvested_kj": {"value": None, "unit": "kJ", "provenance": "SIMULATED",
                                    "reason": "M14 does not integrate harvest over the causal "
                                              "window here; the fixture carries a store level only"},
            "recharge_budget_remaining": {"value": None, "unit": "kJ", "provenance": "RULE",
                                          "reason": "per-lap recharge budget is not resolved in the "
                                                    "2026 rule configuration"},
            "power_envelope": {
                "regime": ((state.get("power_envelope") or {}).get("requested_mode")
                           if isinstance(state.get("power_envelope"), Mapping) else None),
                "power_limit_kw": {"value": None, "unit": "kW", "provenance": "RULE",
                                   "reason": "this route takes no event, so the speed-dependent cap "
                                             "cannot be resolved; read "
                                             "GET /rules/{event}/power_envelope"},
            },
            "fuel_kg": {"value": None, "unit": "kg", "provenance": "INFERRED",
                        "reason": "M34 fuel estimator is not wired; no fuel mass is estimated"},
            "causal_cutoff_distance_m": {
                "value": cutoff, "unit": "m", "provenance": cutoff_provenance,
                **({"reason": cutoff_reason} if cutoff_reason else {}),
            },
            "requested": {"battle_id": requested_battle, "driver": payload.get("driver"),
                          "up_to_segment_index": index},
            "model_version": {"twin": SYNTHETIC_FIXTURE_VERSION, "fuel": None},
            "is_stub": True, "versions": _versions(),
        }

    @app.get("/api/v1/value/{event}/shadow_price")
    def value_shadow(
        event: str, response: Response,
        energy_kj: float = 2400.0, time_gap_s: float = 0.72, eligibility: str = "NOT_ARMED",
        # Declared so FastAPI stops swallowing them. API.md 5.12 documents all
        # five, and an undeclared query parameter is accepted with a 200 and
        # discarded -- indistinguishable, to the caller, from one that was used.
        tyre_state: str | None = None, relative_speed_mps: float | None = None,
        gap_rate_s_per_s: float | None = None, rival_state: str | None = None,
        lap_index: int | None = None,
    ) -> dict[str, Any]:
        rules = _rules(event)
        state = synthetic_state()
        state["energy"]["ers_soc_est_mj"]["value"] = energy_kj / 1000.0
        state["gap"]["time_gap_s"]["value"] = time_gap_s
        state["overtake_state"]["armed"] = eligibility.upper() in {"ARMED", "ACTIVE", "ELIGIBLE"}
        result = shadow_price(synthetic_segments(), state, rules, transition_fn=synthetic_transition, config=DPConfig(rule_configuration_version=RULE_VERSION))
        _mark_stub(response, f"value/{event}/shadow_price")

        # M22 says itself that it cannot produce seconds: its development
        # terminal utility is abstract, so `time_based_shadow_price` comes back
        # null with that reason. The contract's field is emitted under its own
        # name and unit with that same null, so a client can bind to it today
        # and receive a number the day a calibrated C4/C5 time objective exists.
        # The abstract quantity M22 CAN produce stays under `shadow_price`,
        # labelled `abstract_utility_per_mj` by the model that made it.
        not_seconds = (result.get("time_based_shadow_price") or {}).get("reason") or (
            "M22's development terminal utility is abstract; seconds per kJ requires a "
            "calibrated C4/C5 time objective")
        unconditioned = ("the development DP conditions on energy, gap and eligibility only; "
                         "this dimension does not enter the value function and was not used")
        return {
            "event": event, "energy_kj": energy_kj, "gap_s": time_gap_s, "eligibility": eligibility,
            # Empty, not fabricated. shadow_price() returns ONE finite difference
            # at the current state -- a state-level scalar, not a function of lap
            # position -- so there is no per-segment quantity to publish. What
            # stood here repeated that single scalar once per segment, which made
            # the headline visual flat by construction while looking exactly like
            # a computed curve. Building the real per-position lambda is M22's
            # work (CP-10); until then this list is honestly empty.
            "profile": [],
            "profile_reason": (
                "no per-segment lambda_E profile is computed: M22 (CP-10) returns a "
                "state-level marginal value, not a function of lap position. Each entry will "
                "carry `lambda_s_per_kj` in s/kJ against its `segment_id` when it does."),
            "lambda_s_per_kj": {"value": None, "unit": "s/kJ", "provenance": "DERIVED",
                                "reason": not_seconds},
            # Which state dimensions actually reached the DP, and what became of
            # the rest. A 200 that silently drops half the query reads the same
            # as one that honoured it.
            "resolved_state": {
                "energy_kj": {"requested": energy_kj, "used": energy_kj, "reason": None},
                "time_gap_s": {"requested": time_gap_s, "used": time_gap_s, "reason": None},
                "eligibility": {"requested": eligibility, "used": eligibility, "reason": None},
                "tyre_state": {"requested": tyre_state, "used": None, "reason": unconditioned},
                "relative_speed_mps": {"requested": relative_speed_mps, "used": None, "reason": unconditioned},
                "gap_rate_s_per_s": {"requested": gap_rate_s_per_s, "used": None, "reason": unconditioned},
                "rival_state": {"requested": rival_state, "used": None, "reason": unconditioned},
                "lap_index": {"requested": lap_index, "used": None,
                              "reason": "the development horizon is the two-segment fixture, not a "
                                        "two-lap grid, so there is no lap to select"},
            },
            "shadow_price": result, "provenance": result.get("provenance", "SIMULATED"),
            "is_stub": True, "versions": _versions(),
        }

    @app.post("/api/v1/plan")
    def planner(payload: dict[str, Any], response: Response) -> dict[str, Any]:
        _guard_payload(payload, "M24 API", final_mode=bool(app.state.final_mode))

        # The request's `risk` block was read by nobody and the planner echoed a
        # default RiskSpec back in its place, so a caller who asked for CVaR got
        # an expected-value plan carrying a risk object that read as applied. The
        # development DP optimises expected value and computes no CVaR quantity,
        # so a risk treatment it cannot perform is refused here rather than
        # accepted and dropped. API.md 5.13's own example -- cvar_alpha at the
        # default, no criterion -- still passes, because it asks for nothing the
        # planner is not already doing.
        risk_request = payload.get("risk")
        if isinstance(risk_request, Mapping):
            criterion, alpha = risk_request.get("criterion"), risk_request.get("cvar_alpha")
            if criterion not in (None, "expected_value") or alpha not in (None, RiskSpec().cvar_alpha):
                raise HTTPException(status_code=422, detail={
                    "code": "RISK_CRITERION_UNSUPPORTED",
                    "message": (
                        f"risk criterion {criterion!r} with cvar_alpha {alpha!r} cannot be "
                        "honoured: the development planner optimises expected value only and "
                        "emits no cvar_p_ahead, so accepting it would return an "
                        "expected-value plan labelled as a risk-adjusted one. Omit `risk`, or "
                        f"send {{'criterion': 'expected_value', 'cvar_alpha': {RiskSpec().cvar_alpha}}}."),
                })

        state = _state(payload)
        rules = _rules(str(payload.get("event", EVENT)))
        result = plan(_segments(payload), state, rules, transition_fn=synthetic_transition, dp_config=DPConfig(rule_configuration_version=RULE_VERSION), risk=None, final_mode=bool(app.state.final_mode))
        if payload.get("include_baselines") and result.get("status") != "UNAVAILABLE":
            result["baselines"] = generate_baseline_plans(_segments(payload), state, rules)
        # The C5 transition behind every rollout is the synthetic fixture, so the
        # plan is a real search over a placeholder world.
        _mark_stub(response, "plan")
        return {**result, "is_stub": True, "versions": _versions()}

    @app.post("/api/v1/simulate")
    def simulation(payload: dict[str, Any], response: Response) -> dict[str, Any]:
        _guard_payload(payload, "M26 API", final_mode=bool(app.state.final_mode))
        rival_policy = str(payload.get("rival_policy", "DEFEND_CONSERVE"))
        # An unrecognised policy is the caller's mistake, and M26 signals it with
        # a bare ValueError that left FastAPI to answer text/plain "Internal
        # Server Error" -- the shape of a dead backend, for a request the server
        # understood perfectly well. Checked against the same registry
        # GET /simulate/policies serves, so the refusal names what does exist.
        if rival_policy not in POLICIES:
            raise HTTPException(status_code=422, detail={
                "code": "UNKNOWN_POLICY",
                "message": f"unsupported rival_policy {rival_policy!r}",
                "valid_policies": sorted(POLICIES),
            })
        try:
            result = simulate(_state(payload), _segments(payload), _rules(str(payload.get("event", EVENT))), our_policy=str(payload.get("our_policy", "beam_dp")), rival_policy=rival_policy, n_episodes=min(int(payload.get("n_episodes", 8)), 500), seed=int(payload.get("seed", 7)), transition_fn=synthetic_transition, pass_fn=synthetic_pass)
        except UnknownPolicyError as exc:
            # M27 raises this from inside the rollout. Caught by its own type and
            # not as a ValueError: `_ahead` and `_level` raise ValueError too, and
            # those are our state or C3 output being wrong, not the request --
            # calling them 422 would blame the caller for our fault.
            raise HTTPException(status_code=422, detail={
                "code": "UNKNOWN_POLICY", "message": str(exc),
                "valid_policies": sorted(POLICIES),
            }) from exc
        _mark_stub(response, "simulate")
        return {**result, "is_stub": True, "versions": _versions()}

    # /internal/* is a sibling of /api/v1, not part of the public contract: it backs the
    # config explorer with a live read of config/*.yaml. config_data.py has always said
    # app.py exposes it here, but the routes were never mounted -- so the explorer fell
    # through to its bundled snapshot on every load and reported the backend unreachable
    # while the backend was answering every other route beside it. A page whose whole job
    # is to show what the config currently says must not quietly show last week's copy.
    @app.get("/internal/config/variables")
    def internal_config_variables() -> dict[str, Any]:
        return {"categories": config_data.build_variables()}

    @app.get("/internal/config/events")
    def internal_config_events() -> dict[str, Any]:
        return {"events": config_data.build_events()}

    @app.get("/api/v1/simulate/policies")
    def policies() -> dict[str, Any]:
        return {**policy_registry(), "versions": _versions()}

    # The replay builder can call these exact route handlers in-process even
    # when the optional HTTP test client is not installed. The HTTP surface and
    # the replay surface therefore share one implementation, not two serializers.
    # The in-process caller has no HTTP response to mutate, so every handler
    # that sets a header gets a throwaway one. Nothing is lost: each of those
    # routes duplicates the header in its body as `is_stub`, and records itself
    # in `stubs_used`, which is what the bundle manifest reads.
    app.state.route_handlers = {
        ("GET", "/api/v1/meta"): meta,
        ("GET", "/api/v1/validation"): lambda: validation(Response()),
        ("GET", f"/api/v1/track/{EVENT}"): lambda: track(EVENT, Response()),
        ("GET", f"/api/v1/rules/{EVENT}"): lambda: rules(EVENT, Response()),
        ("GET", f"/api/v1/rules/{EVENT}/power_envelope"): lambda mode="both", step_kmh=5.0: power_envelope(EVENT, Response(), mode, float(step_kmh)),
        ("GET", "/api/v1/battles"): lambda **query: battles(Response(), **query),
        ("GET", f"/api/v1/battles/{BATTLE_ID}/timeline"): lambda: timeline(BATTLE_ID, Response()),
        ("GET", "/api/v1/simulate/policies"): policies,
        ("POST", "/api/v1/rules/legal_actions"): legal_actions,
        ("POST", "/api/v1/rules/eligibility"): eligibility,
        ("POST", "/api/v1/pass/predict"): lambda payload: pass_predict(payload, Response()),
        ("POST", "/api/v1/twin/segment_time"): lambda payload: segment_time(payload, Response()),
        ("POST", "/api/v1/twin/energy_state"): lambda payload: energy_state(payload, Response()),
        ("POST", "/api/v1/rival/state"): lambda payload: rival(payload, Response()),
        ("POST", "/api/v1/plan"): lambda payload: planner(payload, Response()),
        ("POST", "/api/v1/simulate"): lambda payload: simulation(payload, Response()),
    }
    return app


app = create_app()

__all__ = ["API_VERSION", "app", "create_app"]
