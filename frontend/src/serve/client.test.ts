import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchBattles, fetchPowerEnvelope, fetchTimeline, postPlan } from "./client";
import { makeSource, resolveUrl, ROUTES } from "./source";

const live = makeSource("live");
const replay = makeSource("replay");

function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response> | Response) {
  vi.stubGlobal("fetch", vi.fn((u: string, i?: RequestInit) => Promise.resolve(impl(u, i))));
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => vi.unstubAllGlobals());

describe("route resolution", () => {
  it("maps every logical route to a bundle file in replay mode", () => {
    expect(resolveUrl(replay, ROUTES.meta())).toBe("/api/replay/meta.json");
    expect(resolveUrl(replay, ROUTES.timeline("any-id"))).toBe("/api/replay/timeline.json");
    expect(resolveUrl(replay, ROUTES.plan())).toBe("/api/replay/plan.json");
  });

  it("reports the power envelope as absent from the bundle rather than inventing a URL", () => {
    expect(resolveUrl(replay, ROUTES.powerEnvelope("british_grand_prix"))).toBeNull();
  });

  it("URL-encodes a battle id on the live path", () => {
    const url = resolveUrl(live, ROUTES.timeline("a b/c"));
    expect(url).toContain("/battles/a%20b%2Fc/timeline");
  });
});

describe("failure taxonomy", () => {
  it("reports a dead backend as `network`, not as empty data", () => {
    mockFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    return fetchBattles(live).then((r) => {
      expect(r.ok).toBe(false);
      if (r.ok) throw new Error("unreachable");
      expect(r.kind).toBe("network");
    });
  });

  it("surfaces the backend's error code on a 404", async () => {
    mockFetch(() => json({ detail: { code: "UNKNOWN_BATTLE", message: "does-not-exist" } }, 404));
    const r = await fetchTimeline(live, "does-not-exist");
    expect(r.ok).toBe(false);
    if (r.ok) throw new Error("unreachable");
    expect(r.kind).toBe("http");
    expect(r.status).toBe(404);
    expect(r.code).toBe("UNKNOWN_BATTLE");
  });

  it("reports a 2xx body of the wrong shape as `malformed`, naming what it expected", async () => {
    // This is the API.md-vs-service disagreement: `steps` instead of `segments`.
    mockFetch(() => json({ battle_id: "b", steps: [] }));
    const r = await fetchTimeline(live, "b");
    expect(r.ok).toBe(false);
    if (r.ok) throw new Error("unreachable");
    expect(r.kind).toBe("malformed");
    expect(r.message).toContain("`segments`");
    expect(r.message).toContain("object{battle_id, steps}");
  });

  it("reports HTML served in place of JSON as malformed", async () => {
    mockFetch(() => new Response("<!doctype html><title>404</title>", { status: 200 }));
    const r = await fetchBattles(live);
    expect(r.ok).toBe(false);
    if (r.ok) throw new Error("unreachable");
    expect(r.kind).toBe("malformed");
  });

  it("reports a route the bundle cannot answer as `unsupported`, with the reason", async () => {
    const r = await fetchPowerEnvelope(replay, "british_grand_prix");
    expect(r.ok).toBe(false);
    if (r.ok) throw new Error("unreachable");
    expect(r.kind).toBe("unsupported");
    expect(r.message).toContain("does not include");
  });
});

describe("battle identity", () => {
  it("refuses a timeline whose battle_id is not the one asked for", async () => {
    // The bundle serves one timeline for any id; showing it under the wrong name would be
    // a silent wrong answer.
    mockFetch(() => json({ battle_id: "synthetic_2026_GBR_Race_HAM_ANT", segments: [] }));
    const r = await fetchTimeline(replay, "2026_GBR_Race_VER_NOR_Battle01");
    expect(r.ok).toBe(false);
    if (r.ok) throw new Error("unreachable");
    expect(r.message).toContain("asked for battle 2026_GBR_Race_VER_NOR_Battle01");
  });

  it("accepts a timeline whose battle_id matches", async () => {
    mockFetch(() => json({ battle_id: "b1", segments: [{ segment_id: 1 }] }));
    const r = await fetchTimeline(live, "b1");
    expect(r.ok).toBe(true);
  });
});

describe("the DRS boundary on outbound requests", () => {
  it("refuses to send a plan request carrying raw drs, and never calls fetch", async () => {
    const spy = vi.fn(() => json({}));
    mockFetch(spy);
    const r = await postPlan(live, { year: 2026, drs: 1 });
    expect(spy).not.toHaveBeenCalled();
    expect(r.ok).toBe(false);
    if (r.ok) throw new Error("unreachable");
    expect(r.code).toBe("FEATURE_SCHEMA_MISMATCH");
    expect(r.message).toContain("drs");
  });

  it("sends a clean plan request", async () => {
    mockFetch(() => json({ rule_violations: 0, status: "COMPLETE" }));
    const r = await postPlan(live, { include_baselines: true });
    expect(r.ok).toBe(true);
  });
});

describe("replay mode", () => {
  it("turns a POST route into a GET of its precomputed body", async () => {
    const seen: Array<{ url: string; method?: string }> = [];
    mockFetch((url, init) => {
      seen.push({ url, method: init?.method });
      return json({ rule_violations: 0 });
    });
    await postPlan(replay, { include_baselines: true });
    expect(seen[0]).toEqual({ url: "/api/replay/plan.json", method: "GET" });
  });
});
