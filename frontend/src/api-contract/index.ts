/**
 * Typed re-exports of the API.md fixtures under ./fixtures. SAMPLE DATA — see
 * fixtures/README.md. Nothing under frontend/src/app imports this module;
 * wiring it up is a future step, not implied by its existence here.
 */
export * from "./types";

import type {
  TrackResponse,
  RulesResponse,
  BattlesResponse,
  TimelineResponse,
  ShadowPriceResponse,
  PlanResponse,
  SimulatePoliciesResponse,
} from "./types";

import trackBritishGrandPrix from "./fixtures/track.british_grand_prix.sample.json";
import rulesBritishGrandPrix from "./fixtures/rules.british_grand_prix.sample.json";
import battles from "./fixtures/battles.sample.json";
import timelineHamAntBattle03 from "./fixtures/timeline.2026_GBR_Race_HAM_ANT_Battle03.sample.json";
import shadowPriceBritishGrandPrix from "./fixtures/shadow_price.british_grand_prix.sample.json";
import plan from "./fixtures/plan.sample.json";
import simulatePolicies from "./fixtures/simulate.policies.sample.json";

export const sampleFixtures = {
  track: { british_grand_prix: trackBritishGrandPrix as TrackResponse },
  rules: { british_grand_prix: rulesBritishGrandPrix as RulesResponse },
  battles: battles as BattlesResponse,
  timeline: {
    "2026_GBR_Race_HAM_ANT_Battle03": timelineHamAntBattle03 as TimelineResponse,
  },
  shadowPrice: { british_grand_prix: shadowPriceBritishGrandPrix as ShadowPriceResponse },
  plan: plan as PlanResponse,
  simulatePolicies: simulatePolicies as SimulatePoliciesResponse,
};
