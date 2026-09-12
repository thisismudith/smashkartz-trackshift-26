import { describe, expect, it } from "vitest";
import type { CarState } from "../contract/types";
import { packPose, unpackPoseAt } from "./pose";
import { POSE_STATUS } from "./protocol";

function makeState(overrides: Partial<CarState> = {}): CarState {
  return {
    driver: "AAA", team: "Team A", stationM: 123.4, lateralM: -1.2, elevationM: 5.6,
    headingRad: 0.7, speedKph: 280, gear: 7, throttlePct: 95, brake: false,
    tyreCompound: "MEDIUM", tyreLife: 10, lapsDone: 3, lapProgress: 0.42, position: 1,
    gapToLeaderS: null, lapsDownFromLeader: 0, intervalS: null, inPit: false,
    status: "track", provenance: "OBSERVED", ...overrides,
  };
}

describe("pose packing", () => {
  it("round-trips every channel for a car present in the state map", () => {
    const states = new Map([["AAA", makeState()]]);
    const buf = packPose(states, ["AAA"]);
    const p = unpackPoseAt(buf, 0);
    expect(p.stationM).toBeCloseTo(123.4, 4);
    expect(p.lateralM).toBeCloseTo(-1.2, 4);
    expect(p.elevationM).toBeCloseTo(5.6, 4);
    expect(p.headingRad).toBeCloseTo(0.7, 4);
    expect(p.speedKph).toBe(280);
    expect(p.gear).toBe(7);
    expect(p.throttlePct).toBe(95);
    expect(p.brake).toBe(false);
    expect(p.lapsDone).toBe(3);
    expect(p.lapProgress).toBeCloseTo(0.42, 4);
    expect(p.position).toBe(1);
  });

  it("zero-fills a driver missing from the state map (e.g. not yet on track)", () => {
    const states = new Map([["AAA", makeState()]]);
    const buf = packPose(states, ["AAA", "BBB"]);
    const missing = unpackPoseAt(buf, 1);
    expect(missing.stationM).toBe(0);
    expect(missing.speedKph).toBe(0);
  });

  it("reuses a supplied buffer without reallocating when the size matches", () => {
    const states = new Map([["AAA", makeState()]]);
    const scratch = new Float32Array(13);
    const buf = packPose(states, ["AAA"], scratch);
    expect(buf).toBe(scratch);
  });

  it("packs the car status code so the renderer can tell grid/pit/parked apart", () => {
    const states = new Map([
      ["AAA", makeState({ status: "track" })],
      ["BBB", makeState({ driver: "BBB", status: "pit" })],
      ["CCC", makeState({ driver: "CCC", status: "grid" })],
    ]);
    const buf = packPose(states, ["AAA", "BBB", "CCC"]);
    expect(unpackPoseAt(buf, 0).status).toBe(POSE_STATUS.track);
    expect(unpackPoseAt(buf, 1).status).toBe(POSE_STATUS.pit);
    expect(unpackPoseAt(buf, 2).status).toBe(POSE_STATUS.grid);
  });

  it("preserves brake=true", () => {
    const states = new Map([["AAA", makeState({ brake: true })]]);
    const buf = packPose(states, ["AAA"]);
    expect(unpackPoseAt(buf, 0).brake).toBe(true);
  });
});
