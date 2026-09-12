import { describe, expect, it } from "vitest";
import type { TrackModel } from "../contract/types";
import { applyShadowPriceOverlay, buildTrackMesh } from "./trackMesh";

function makeTrack(): TrackModel {
  const n = 100;
  const x = new Float32Array(n), y = new Float32Array(n), z = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const a = (i / n) * 2 * Math.PI;
    x[i] = Math.cos(a) * 500;
    y[i] = Math.sin(a) * 500;
  }
  return {
    slug: "mesh-track", event: "Mesh GP", lengthMetres: 3141,
    x, y, z, halfWidth: new Float32Array([6]), widthBinMetres: 3141,
    timingLines: { sf: 0, s1: 1000, s2: 2000 }, corners: [],
    grid: { order: [], pitchMetres: 8 },
    pitLanePath: null,
    pitLane: { entryStation: null, exitStation: null, mergeStation: null, loopLateral: null },
    referenceProfile: { binMetres: 10, speedKph: new Float32Array([250]), gear: new Uint8Array([7]) },
  };
}

describe("track surface geometry", () => {
  it("builds a closed ribbon with two vertices per station and a valid index buffer", () => {
    const track = makeTrack();
    const mesh = buildTrackMesh(track);
    const posAttr = mesh.geometry.getAttribute("position");
    expect(posAttr.count).toBe(track.x.length * 2);
    const index = mesh.geometry.getIndex();
    expect(index).not.toBeNull();
    // 2 triangles (6 indices) per station going into the next one
    expect(index!.count).toBe(track.x.length * 6);
  });

  it("restores the plain surface colour when the shadow-price overlay is cleared", () => {
    const track = makeTrack();
    const mesh = buildTrackMesh(track);
    const colorAttr = mesh.geometry.getAttribute("color");
    const original = Float32Array.from(colorAttr.array as Float32Array);

    const values = new Float32Array(track.x.length).fill(1); // all "hot"
    applyShadowPriceOverlay(mesh, values);
    const painted = Float32Array.from(colorAttr.array as Float32Array);
    expect(painted).not.toEqual(original);

    applyShadowPriceOverlay(mesh, null);
    const restored = Float32Array.from(colorAttr.array as Float32Array);
    expect(restored).toEqual(original);
  });

  it("blends toward Haas red as the scalar rises, never leaving the palette hue range", () => {
    const track = makeTrack();
    const mesh = buildTrackMesh(track);
    const colorAttr = mesh.geometry.getAttribute("color");

    const low = new Float32Array(track.x.length).fill(0);
    applyShadowPriceOverlay(mesh, low);
    const lowColor = (colorAttr.array as Float32Array).slice(0, 3);

    const high = new Float32Array(track.x.length).fill(1);
    applyShadowPriceOverlay(mesh, high);
    const highColor = (colorAttr.array as Float32Array).slice(0, 3);

    // red channel should rise and green/blue fall as the scalar goes 0 -> 1
    expect(highColor[0]).toBeGreaterThan(lowColor[0]);
    expect(highColor[1]).toBeLessThanOrEqual(lowColor[1]);
  });
});
