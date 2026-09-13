import { describe, expect, it } from "vitest";
import {
  buildPassFeatures, closingRate, cornerTypeAt, weatherIndexAt,
} from "./passFeatures";
import type { DashboardRow, WeatherSeries } from "../contract/types";

function row(over: Partial<DashboardRow> = {}): DashboardRow {
  return {
    position: 1, driver: "VER", team: "Red Bull", gapToLeader: "LEADER",
    interval: "LEADER", compound: "MEDIUM", tyreLife: 12, status: "running",
    lastLapS: 90, energy: null, speedKph: 280, gear: 7, throttlePct: 100,
    brake: false, lapProgress: 0.5, ...over,
  } as DashboardRow;
}

const WEATHER: WeatherSeries = {
  tS: [0, 60, 120],
  airTempC: [22, 23, 24],
  trackTempC: [40, 41, 42],
  humidityPct: [50, 51, 52],
  rain: [false, false, false],
  windMps: [5, 5, 5],
  windFromDeg: [0, 0, 0],
} as WeatherSeries;

const SEGMENTS = [
  { start_distance_m: 0, end_distance_m: 160, corner_type: "STRAIGHT" },
  { start_distance_m: 160, end_distance_m: 400, corner_type: "FAST_RIGHT" },
];

function base(over = {}) {
  return {
    gapS: 0.8,
    attackerRow: row({ driver: "VER", compound: "MEDIUM", tyreLife: 12 }),
    defenderRow: row({ driver: "RUS", compound: "HARD", tyreLife: 16 }),
    weather: WEATHER, sessionTime: 70, headingRad: 0,
    pEligible: 0.64,
    previous: { gapS: 1.0, atSessionTime: 69 },
    segments: SEGMENTS, detectionM: 200,
    ...over,
  };
}

describe("closingRate", () => {
  it("is positive while the gap shrinks, matching rules/eligibility.py", () => {
    // The one thing that matters here. `closing_rate_s_per_s` is positive when
    // closing; API.md's gap_rate_ahead_s_per_s is the same magnitude negated.
    // A flipped sign feeds the model a car pulling away as one closing in.
    expect(closingRate({ gapS: 1.0, atSessionTime: 0 }, 0.6, 1)).toBeCloseTo(0.4, 6);
  });

  it("is negative while the gap opens", () => {
    expect(closingRate({ gapS: 0.6, atSessionTime: 0 }, 1.0, 1)).toBeCloseTo(-0.4, 6);
  });

  it("is null without a previous sample rather than zero", () => {
    // Zero is "holding station", which is a claim. Unknown is not.
    expect(closingRate(null, 0.6, 1)).toBeNull();
  });

  it("refuses a denominator too small to divide by", () => {
    expect(closingRate({ gapS: 1.0, atSessionTime: 0.99 }, 0.6, 1)).toBeNull();
  });

  it("refuses a backwards clock", () => {
    expect(closingRate({ gapS: 1.0, atSessionTime: 5 }, 0.6, 1)).toBeNull();
  });
});

describe("weatherIndexAt", () => {
  it("takes the last reading at or before the time, never interpolating", () => {
    expect(weatherIndexAt([0, 60, 120], 70)).toBe(1);
    expect(weatherIndexAt([0, 60, 120], 60)).toBe(1);
  });

  it("is -1 before the first reading", () => {
    expect(weatherIndexAt([10, 60], 5)).toBe(-1);
    expect(weatherIndexAt([], 5)).toBe(-1);
    expect(weatherIndexAt(undefined, 5)).toBe(-1);
  });
});

describe("cornerTypeAt", () => {
  it("finds the segment containing the station", () => {
    expect(cornerTypeAt(SEGMENTS, 200)).toBe("FAST_RIGHT");
    expect(cornerTypeAt(SEGMENTS, 10)).toBe("STRAIGHT");
  });

  it("is undefined off the end rather than clamping to the last segment", () => {
    expect(cornerTypeAt(SEGMENTS, 9999)).toBeUndefined();
  });

  it("is undefined with no segments or no station", () => {
    expect(cornerTypeAt(null, 200)).toBeUndefined();
    expect(cornerTypeAt(SEGMENTS, null)).toBeUndefined();
  });
});

describe("buildPassFeatures", () => {
  it("supplies fourteen of the fifteen the model was fitted on", () => {
    const { features } = buildPassFeatures(base());
    // `sector` is the fifteenth and is absent everywhere: null in
    // config/geometry for every circuit, and trained with a lone
    // `__missing__` category. Fourteen is the honest ceiling.
    expect(Object.keys(features).sort()).toEqual([
      "attacker_tyre_compound", "attacker_tyre_life_laps",
      "closing_rate_s_per_s", "corner_type", "defender_tyre_compound",
      "defender_tyre_life_laps", "gap_at_checkpoint", "p_eligible",
      "track_temperature", "tyre_compound_pair", "tyre_life_delta_laps",
      "wet_track_flag", "wind_cross_component_mps", "wind_head_component_mps",
    ]);
  });

  it("spells the compound pair attacker-first, as the artifact's categories are", () => {
    const { features } = buildPassFeatures(base());
    expect(features.tyre_compound_pair).toBe("MEDIUM|HARD");
  });

  it("takes the tyre delta as attacker minus defender", () => {
    // API.md 5.5: a 12-lap attacker behind a 16-lap defender is -4, so negative
    // means the attacker holds the fresher set.
    const { features } = buildPassFeatures(base());
    expect(features.tyre_life_delta_laps).toBe(-4);
  });

  it("omits an untrained compound rather than passing it through", () => {
    // An unseen category reaches the model as unknown anyway, but without the
    // misleading appearance of having been supplied.
    const { features, omitted } = buildPassFeatures(
      base({ attackerRow: row({ compound: "WET" }) }));
    expect(features.attacker_tyre_compound).toBeUndefined();
    expect(features.tyre_compound_pair).toBeUndefined();
    expect(omitted.attacker_tyre_compound).toBeTruthy();
  });

  it("names why each feature is missing when the session carries no weather", () => {
    const { features, omitted } = buildPassFeatures(base({ weather: null }));
    expect(features.track_temperature).toBeUndefined();
    expect(features.wind_head_component_mps).toBeUndefined();
    expect(omitted.track_temperature).toMatch(/no weather series/);
  });

  it("drops both wind components together when there is no heading to project onto", () => {
    // They are one projection: a bearing without the track's own heading is
    // neither a head nor a cross component.
    const { features, omitted } = buildPassFeatures(base({ headingRad: null }));
    expect(features.wind_head_component_mps).toBeUndefined();
    expect(features.wind_cross_component_mps).toBeUndefined();
    expect(omitted.wind_head_component_mps).toMatch(/no focused car/);
  });

  it("says corner_type is not reproducible here when the track route is silent", () => {
    const { features, omitted } = buildPassFeatures(base({ segments: null }));
    expect(features.corner_type).toBeUndefined();
    expect(omitted.corner_type).toMatch(/not reproducible here/);
  });

  it("still carries the gap when everything else is unavailable", () => {
    const { features } = buildPassFeatures(base({
      attackerRow: null, defenderRow: null, weather: null, headingRad: null,
      pEligible: null, previous: null, segments: null, detectionM: null,
    }));
    expect(features).toEqual({ gap_at_checkpoint: 0.8 });
  });
});
