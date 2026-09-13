"""Coverage for the measured pass-conversion export (sim/rivalries.<hash>.json).

Three things here are load-bearing and none of them is visible by reading the artifact:
the interval is a Wilson interval and not the normal one that goes negative at Monaco's
1-in-61; an unlabelled outcome is dropped rather than scored as a failed pass; and the
circuit keys come from the rule config, so they match params.<hash>.json instead of
merely looking like it.

The head-to-head cut adds a fourth: the defender is a CAR NUMBER in M07 and a CODE
everywhere else, so every pair key depends on a join to the 20 m lake. A join that half
works is the dangerous outcome -- the season total still reads 4,970 while some rivalry
quietly loses opportunities -- so the tests below check that it raises rather than drops,
that "ANT -> RUS" and "RUS -> ANT" stay separate rivalries, and that the pair path
excludes unlabelled outcomes exactly as the circuit path does.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "features" / "export_rivalries.py"


def _exporter():
    spec = importlib.util.spec_from_file_location("export_rivalries", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _exporter()


def frame(outcomes, event="british_grand_prix", session="Race", year="2026",
          attacker="ANT", defender="63"):
    """The three-checkpoint shape the real table has: one opportunity is three rows.

    The outcome label is written onto every checkpoint row upstream, so a summariser that
    forgets to anchor on one of them counts each opportunity three times -- which is what
    the first test below is really watching for.
    """
    rows = []
    for index, outcome in enumerate(outcomes):
        for checkpoint in ("DETECTION", "ACTIVATION", "BRAKING"):
            rows.append({
                "year": year, "event": event, "session": session,
                "decision_checkpoint": checkpoint,
                "passed_by_outcome_horizon": outcome,
                "opportunity_id": f"o-{index}",
                "attacker": attacker, "defender": defender,
            })
    return pd.DataFrame(rows).astype({"passed_by_outcome_horizon": object})


def pairs_frame(battles, event="british_grand_prix", session="Race", year="2026"):
    """Several rivalries in one table: (attacker, defender car number, outcomes) each."""
    return pd.concat(
        [frame(outcomes, event=event, session=session, year=year,
               attacker=attacker, defender=number)
         for attacker, number, outcomes in battles],
        ignore_index=True)


#: The 2026 numbers for the drivers used below, in the shape build_number_to_code
#: produces: keyed by (year, LAKE event name, session, car number).
NUMBERS = {
    ("2026", "British Grand Prix", "Race", "63"): "RUS",
    ("2026", "British Grand Prix", "Race", "12"): "ANT",
    ("2026", "British Grand Prix", "Race", "1"): "VER",
    ("2026", "British Grand Prix", "Sprint", "63"): "RUS",
}

ANT_RUS = f"ANT{MODULE.PAIR_ARROW}RUS"
RUS_ANT = f"RUS{MODULE.PAIR_ARROW}ANT"


# --------------------------------------------------------------- Wilson maths

@pytest.mark.parametrize("passes,n,low,high", [
    # The eight measured 2026 circuits, to three decimals as the build prints them.
    (129, 762, 0.144, 0.198),
    (33, 324, 0.073, 0.140),
    (43, 412, 0.078, 0.138),
    (160, 1228, 0.113, 0.150),
    (204, 754, 0.240, 0.303),
    (63, 362, 0.138, 0.216),
    (104, 870, 0.100, 0.143),
    (1, 61, 0.003, 0.087),
])
def test_wilson_matches_the_measured_intervals(passes, n, low, high):
    got_low, got_high = MODULE.wilson_interval(passes, n)
    assert (round(got_low, 3), round(got_high, 3)) == (low, high)


def test_wilson_agrees_with_an_independent_implementation():
    """Checked against statsmodels proportion_confint(method="wilson"), which uses the
    exact normal quantile 1.959964 rather than the stated 1.96. The two agree to five
    decimals on every real circuit -- which is the precision the artifact carries -- and
    diverge in the sixth, so these expectations are pinned at that precision."""
    for passes, n, low, high in [(43, 412, 0.07841, 0.13764),
                                 (1, 61, 0.00290, 0.08719),
                                 (204, 754, 0.24007, 0.30337)]:
        got_low, got_high = MODULE.wilson_interval(passes, n)
        assert (round(got_low, 5), round(got_high, 5)) == (low, high)


def test_wilson_never_leaves_the_unit_interval():
    """The whole reason Wilson is used: the normal interval is negative at 1 of 61."""
    assert MODULE.wilson_interval(1, 61)[0] > 0.0
    low, high = MODULE.wilson_interval(0, 5)
    assert (low, round(high, 5)) == (0.0, 0.43449)
    assert MODULE.wilson_interval(10, 10)[1] == 1.0


def test_a_rate_with_no_observations_is_refused_not_returned_as_zero():
    with pytest.raises(MODULE.RivalriesError):
        MODULE.wilson_interval(0, 0)
    with pytest.raises(MODULE.RivalriesError):
        MODULE.wilson_interval(5, 3)


# ------------------------------------------------------- unlabelled != failed

def test_unlabelled_outcomes_are_excluded_from_n_rather_than_counted_as_false():
    counts = MODULE.summarise_event(frame([True, False, None, None, True]))
    assert counts == {"opportunities": 5, "labelled": 3, "unlabelled": 2, "passes": 2}
    # The rate divides by the LABELLED count. 2/5 would be the bug this guards.
    assert MODULE.leaf(counts)["value"] == round(2 / 3, 5)
    assert MODULE.leaf(counts)["n"] == 3


def test_every_exclusion_is_stated_on_the_leaf_that_excluded_it():
    leaf = MODULE.leaf(MODULE.summarise_event(frame([True] * 3 + [None] * 2)))
    assert leaf["note"] == "3 of 3 labelled opportunities converted; 2 unlabelled excluded"
    assert leaf["provenance"] == "DERIVED"
    # Exactly the frontend's Leaf, so the insights page needs no new type.
    assert set(leaf) == {"value", "ci95", "n", "provenance", "note"}
    assert len(leaf["ci95"]) == 2


def test_one_opportunity_is_one_row_not_one_per_checkpoint():
    counts = MODULE.summarise_event(frame([True, False]))
    assert counts["opportunities"] == 2, "the three checkpoint rows were counted separately"


def test_an_outcome_that_is_neither_label_nor_null_is_refused():
    """A stray string is truthy and would silently inflate the pass count."""
    with pytest.raises(MODULE.RivalriesError):
        MODULE.summarise_event(frame([True, "PASSED"]))


def test_totals_report_the_unlabelled_rows_rather_than_hiding_them():
    counts = {"british_grand_prix": MODULE.summarise_event(frame([True, None, False]))}
    artifact = MODULE.build_artifact(counts, "2026", ["Race"])
    assert artifact["totals"]["opportunities"] == 3
    assert artifact["totals"]["labelled"] == 2
    assert artifact["totals"]["unlabelled"] == 1
    assert artifact["totals"]["passes"] == 1


def test_a_circuit_with_no_labelled_outcome_is_omitted_not_emitted_as_zero():
    counts = {"british_grand_prix": MODULE.summarise_event(frame([None, None]))}
    artifact = MODULE.build_artifact(counts, "2026", ["Race"])
    assert artifact["passConversionRate"] == {}
    assert artifact["totals"]["opportunities"] == 2, "it still counts as opportunity"


# ------------------------------------------------- display names, not guesses

def test_display_name_comes_from_the_rule_config():
    assert MODULE.event_display("british_grand_prix", "2026") == "British Grand Prix"


def test_keys_use_the_rule_config_display_name_not_a_title_cased_slug(monkeypatch):
    """Title-casing the slug agrees with the rule config for all eight 2026 circuits,
    so only a display name that DIFFERS can tell the two implementations apart."""
    monkeypatch.setattr(MODULE, "load_event_rules",
                        lambda slug, season: {"event_display": "Sao Paulo Grand Prix (Interlagos)"})
    artifact = MODULE.build_artifact(
        {"sao_paulo_grand_prix": MODULE.summarise_event(frame([True, False]))},
        "2026", ["Race"])
    assert list(artifact["passConversionRate"]) == ["Sao Paulo Grand Prix (Interlagos)"]


def test_a_missing_display_name_is_an_error_rather_than_a_guessed_slug(monkeypatch):
    """Silently falling back to the slug would key rows the insights page cannot join."""
    monkeypatch.setattr(MODULE, "load_event_rules", lambda slug, season: {})
    with pytest.raises(MODULE.RivalriesError):
        MODULE.event_display("british_grand_prix", "2026")

    def unconfigured(slug, season):
        raise KeyError(slug)

    monkeypatch.setattr(MODULE, "load_event_rules", unconfigured)
    with pytest.raises(MODULE.RivalriesError):
        MODULE.event_display("nowhere_grand_prix", "2026")


# --------------------------------------------------------------- artifact shape

def test_artifact_carries_its_own_provenance_and_a_stable_key_order():
    counts = {
        "monaco_grand_prix": MODULE.summarise_event(frame([True] + [False] * 9)),
        "british_grand_prix": MODULE.summarise_event(frame([True, False])),
    }
    artifact = MODULE.build_artifact(counts, "2026", ["Race", "Sprint"])
    assert artifact["schemaVersion"] == 1
    assert artifact["season"] == "2026"
    assert artifact["labelDefinition"] == "zone_exit_v1"
    assert artifact["provenance"] == "DERIVED"
    assert artifact["source"].startswith("data/processed/overtake_opportunities (")
    # Sorted, so the same measurement always hashes to the same filename.
    assert list(artifact["passConversionRate"]) == ["British Grand Prix", "Monaco Grand Prix"]


def test_a_mixed_season_is_refused_because_display_names_are_season_scoped(tmp_path):
    mixed = pd.concat([frame([True], year="2025"), frame([False], year="2026")])
    out = tmp_path / "event=british_grand_prix"
    out.mkdir()
    mixed.to_parquet(out / "opportunities.parquet")
    with pytest.raises(MODULE.RivalriesError, match="one season"):
        MODULE.measure(tmp_path)


def test_a_partition_whose_event_column_disagrees_with_its_directory_is_refused(tmp_path):
    """Attributing one circuit's passes to another is the worst available failure."""
    out = tmp_path / "event=monaco_grand_prix"
    out.mkdir()
    frame([True, False]).to_parquet(out / "opportunities.parquet")
    with pytest.raises(MODULE.RivalriesError, match="partitioned as"):
        MODULE.measure(tmp_path)


def test_missing_source_data_is_an_error_at_this_level(tmp_path):
    """build_sim_data.py decides whether to SKIP; the exporter itself never invents rows."""
    with pytest.raises(MODULE.RivalriesError):
        MODULE.measure(tmp_path)


# ------------------------------------------- the defender is a number, not a code

def _written(tmp_path, table):
    """One event partition on disk, the way measure_pairs expects to find it."""
    out = tmp_path / "event=british_grand_prix"
    out.mkdir()
    table.to_parquet(out / "opportunities.parquet")
    return tmp_path


def test_the_defender_number_is_resolved_through_the_lake_map_not_left_as_a_number():
    """M07 knows the attacker as "ANT" and the defender only as "63". A key spelled
    "ANT -> 63" would split one rivalry across every season that number was reused in."""
    codes = MODULE.resolve_defender_codes(
        frame([True, False], attacker="ANT", defender="63"), "British Grand Prix", NUMBERS)
    assert set(codes) == {"RUS"}


def test_the_number_to_code_map_is_the_one_battle_join_builds():
    """A second implementation would have to re-derive the per-session scoping and the
    "44" / "44.0" / NaN canonicalisation, and would drift from the builder that made the
    opportunities in the first place."""
    from trackshift.features.battle_join import build_number_to_code
    assert MODULE.build_number_to_code is build_number_to_code


def test_an_unresolved_car_number_raises_rather_than_dropping_the_opportunity():
    """Every one of the 4,970 opportunities in 2026 resolves, so a miss is a wrong join
    key. Dropping the row would leave the season total intact while some rivalry quietly
    lost opportunities to nowhere, and nothing in the artifact would say so."""
    with pytest.raises(MODULE.RivalriesError, match="no driver code"):
        MODULE.resolve_defender_codes(frame([True], defender="99"),
                                      "British Grand Prix", NUMBERS)


def test_the_slug_is_normalised_to_the_lake_event_name_before_the_lookup(tmp_path):
    """The lake says "British Grand Prix"; the parquet says "british_grand_prix". Feeding
    the slug straight into the map resolves nothing at all."""
    with pytest.raises(MODULE.RivalriesError, match="no driver code"):
        MODULE.resolve_defender_codes(frame([True]), "british_grand_prix", NUMBERS)

    root = _written(tmp_path, frame([True, False]))
    measured = MODULE.measure_pairs(root, "2026", number_to_code=NUMBERS)
    assert list(measured["pairs"]) == [ANT_RUS], "the event name was not normalised"


def test_the_number_map_needs_racing_sessions_and_says_so_when_there_are_none(tmp_path):
    with pytest.raises(MODULE.RivalriesError, match="Race or Sprint"):
        MODULE.load_number_to_code(tmp_path)


# --------------------------------------------------- a rivalry has a direction

def test_both_directions_of_a_pair_are_separate_rivalries_with_their_own_rates(tmp_path):
    """ANT attacking RUS and RUS attacking ANT are different questions about different
    cars. Keying on an unordered pair would average the two into a number describing
    neither, and would hide exactly the asymmetry the panel exists to show."""
    root = _written(tmp_path, pairs_frame([("ANT", "63", [True, False, False, False]),
                                           ("RUS", "12", [True, True, False])]))

    measured = MODULE.measure_pairs(root, "2026", number_to_code=NUMBERS)
    assert sorted(measured["pairs"]) == sorted([ANT_RUS, RUS_ANT])
    assert measured["pairs"][ANT_RUS] == {"opportunities": 4, "labelled": 4,
                                          "unlabelled": 0, "passes": 1}
    assert measured["pairs"][RUS_ANT] == {"opportunities": 3, "labelled": 3,
                                          "unlabelled": 0, "passes": 2}

    artifact = MODULE.build_artifact({}, "2026", ["Race"], measured)
    assert artifact["headToHead"]["pairs"][ANT_RUS]["value"] == 0.25
    assert artifact["headToHead"]["pairs"][RUS_ANT]["value"] == round(2 / 3, 5)


def test_the_pair_key_is_the_arrow_the_frontend_splits_on():
    """U+2192 with a space either side. The artifact key IS the contract."""
    assert MODULE.pair_key("ANT", "RUS") == "ANT → RUS"
    assert ANT_RUS.split(MODULE.PAIR_ARROW) == ["ANT", "RUS"]


def test_one_rivalry_is_one_row_per_opportunity_not_one_per_checkpoint(tmp_path):
    root = _written(tmp_path, frame([True, False]))
    measured = MODULE.measure_pairs(root, "2026", number_to_code=NUMBERS)
    assert measured["pairs"][ANT_RUS]["opportunities"] == 2


# ------------------------------------- unlabelled != failed, in the pair path too

def test_unlabelled_outcomes_are_excluded_from_a_pair_n_as_well_as_a_circuit_one(tmp_path):
    """The same discipline reached by a different grouping. 1/3 here would be the bug."""
    root = _written(tmp_path, frame([True, None, None]))

    measured = MODULE.measure_pairs(root, "2026", number_to_code=NUMBERS)
    head = MODULE.build_artifact({}, "2026", ["Race"], measured)["headToHead"]
    assert head["pairs"][ANT_RUS]["value"] == 1.0
    assert head["pairs"][ANT_RUS]["n"] == 1
    assert head["pairs"][ANT_RUS]["note"].startswith("1 of 1 labelled opportunities")
    # The exclusion travels with the count rather than being silently absorbed.
    assert head["counts"][ANT_RUS] == {"opportunities": 1, "passes": 1, "unlabelled": 2}


def test_a_rivalry_with_nothing_labelled_is_omitted_from_pairs_and_from_counts(tmp_path):
    """Emitting it as zero would read as "never passes" rather than "never measured"."""
    root = _written(tmp_path, pairs_frame([("ANT", "63", [True, False]),
                                           ("RUS", "12", [None, None])]))

    measured = MODULE.measure_pairs(root, "2026", number_to_code=NUMBERS)
    head = MODULE.build_artifact({}, "2026", ["Race"], measured)["headToHead"]
    assert list(head["pairs"]) == [ANT_RUS]
    assert list(head["counts"]) == [ANT_RUS], "counts and pairs must key the same rivalries"


# --------------------------------------------------------- totals, and the shape

def test_driver_totals_aggregate_rows_rather_than_averaging_the_pair_rates(tmp_path):
    """A mean of pair rates lets a one-opportunity rivalry weigh as much as a ten-. ANT
    converts 1 of 10 against RUS and 1 of 1 against VER: 2/11, not (0.1 + 1.0) / 2."""
    root = _written(tmp_path, pairs_frame([("ANT", "63", [True] + [False] * 9),
                                           ("ANT", "1", [True])]))

    artifact = MODULE.build_artifact({}, "2026", ["Race"],
                                     MODULE.measure_pairs(root, "2026",
                                                          number_to_code=NUMBERS))
    assert artifact["attackerTotals"]["ANT"]["value"] == round(2 / 11, 5)
    assert artifact["attackerTotals"]["ANT"]["n"] == 11
    # Defenders are counted by the code the number resolved to, never by the number.
    assert sorted(artifact["defenderTotals"]) == ["RUS", "VER"]
    assert artifact["defenderTotals"]["RUS"]["n"] == 10


def test_every_head_to_head_value_is_exactly_the_frontend_leaf(tmp_path):
    """The whole point of the shape: it drops into fromLeaves() with no component change,
    the same way passConversionRate already does."""
    root = _written(tmp_path, pairs_frame([("ANT", "63", [True, False, None]),
                                           ("RUS", "12", [False])]))

    artifact = MODULE.build_artifact({}, "2026", ["Race"],
                                     MODULE.measure_pairs(root, "2026",
                                                          number_to_code=NUMBERS))
    leaves = (list(artifact["headToHead"]["pairs"].values())
              + list(artifact["attackerTotals"].values())
              + list(artifact["defenderTotals"].values()))
    assert leaves
    for one in leaves:
        assert set(one) == {"value", "ci95", "n", "provenance", "note"}
        assert one["provenance"] == "DERIVED"
        assert len(one["ci95"]) == 2
        assert 0.0 <= one["ci95"][0] <= one["value"] <= one["ci95"][1] <= 1.0


def test_the_new_sections_are_additive_and_change_nothing_that_was_there(tmp_path):
    """schemaVersion keeps its meaning, passConversionRate and totals are untouched, and a
    reader that has never heard of headToHead sees the artifact it saw before."""
    root = _written(tmp_path, frame([True, False, None]))
    counts = {"british_grand_prix": MODULE.summarise_event(frame([True, False, None]))}
    measured = MODULE.measure_pairs(root, "2026", number_to_code=NUMBERS)

    before = MODULE.build_artifact(counts, "2026", ["Race"])
    after = MODULE.build_artifact(counts, "2026", ["Race"], measured)
    assert set(after) - set(before) == {"headToHead", "attackerTotals", "defenderTotals"}
    assert all(after[key] == before[key] for key in before)
    assert after["schemaVersion"] == 1
    assert after["headToHead"]["note"]
    # Sorted, so the same measurement always hashes to the same filename.
    assert list(after["headToHead"]["pairs"]) == sorted(after["headToHead"]["pairs"])
