import { describe, expect, it } from "vitest";
import { describeCross, describeHead, windComponents } from "./wind";

/** Heading in the ring frame: 0 rad is travelling toward +x, i.e. due EAST. */
const EAST = 0;
const NORTH = Math.PI / 2;

describe("windComponents", () => {
  it("a wind from dead ahead is a pure headwind", () => {
    // Travelling east; wind FROM the east (90 deg) blows westward, into the car.
    const w = windComponents(10, 90, EAST)!;
    expect(w.headMps).toBeCloseTo(10, 6);
    expect(w.crossMps).toBeCloseTo(0, 6);
  });

  it("a wind from directly behind is a pure tailwind, signed negative", () => {
    // Travelling east; wind FROM the west (270 deg) pushes the car along.
    const w = windComponents(10, 270, EAST)!;
    expect(w.headMps).toBeCloseTo(-10, 6);
    expect(w.crossMps).toBeCloseTo(0, 6);
  });

  it("a wind across the car is pure cross, positive from the left", () => {
    // Travelling east, wind FROM the north (0 deg) blows southward. North is to
    // the car's left, so this is a crosswind from the left.
    const w = windComponents(10, 0, EAST)!;
    expect(w.headMps).toBeCloseTo(0, 6);
    expect(w.crossMps).toBeCloseTo(10, 6);
  });

  it("the same compass bearing is head on one heading and tail on the opposite", () => {
    // This is the whole reason a bearing alone is forbidden on screen.
    const east = windComponents(8, 90, EAST)!;
    const west = windComponents(8, 90, Math.PI)!;
    expect(east.headMps).toBeCloseTo(8, 6);
    expect(west.headMps).toBeCloseTo(-8, 6);
  });

  it("rotating the heading rotates the split, conserving magnitude", () => {
    for (const heading of [0, NORTH, Math.PI, -NORTH, 1.1, -2.4]) {
      const w = windComponents(7, 215, heading)!;
      expect(Math.hypot(w.headMps, w.crossMps)).toBeCloseTo(7, 6);
    }
  });

  it("is absent, never zero, when any input is missing", () => {
    // A calm wind and an unknown wind are different facts; 0 would read as calm.
    expect(windComponents(undefined, 90, 0)).toBeNull();
    expect(windComponents(10, undefined, 0)).toBeNull();
    expect(windComponents(10, 90, undefined)).toBeNull();
    expect(windComponents(null, null, null)).toBeNull();
    expect(windComponents(NaN, 90, 0)).toBeNull();
  });
});

describe("describeHead / describeCross", () => {
  it("names the direction rather than leaving a sign to be read", () => {
    expect(describeHead(2.1)).toBe("2.1 m/s headwind");
    expect(describeHead(-0.4)).toBe("0.4 m/s tailwind");
    expect(describeCross(1.2)).toBe("1.2 m/s from the left");
    expect(describeCross(-0.3)).toBe("0.3 m/s from the right");
  });

  it("says calm rather than printing a meaningless 0.0", () => {
    expect(describeHead(0.01)).toBe("calm");
    expect(describeCross(-0.01)).toBe("none");
  });
});
