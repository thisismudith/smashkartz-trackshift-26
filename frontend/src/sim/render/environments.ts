import * as THREE from "three";
import type { TrackModel, TrackSurface, TrackSurfaceTransform } from "../contract/types";

/**
 * The client-side mirror of `config/circuits.yaml` -- the Python registry of real
 * circuit models (scripts/simdata/glb_surface.py).
 *
 * WHAT THIS FILE IS NOT: it is not a second source of truth for the fit. The transform
 * the renderer actually places the model with is read from the track artifact's own
 * `surface.transform`, because that is the transform the baked heights were MEASURED
 * under; the `fit` recorded here is the registry's copy of the same numbers, kept so
 * the placement maths can be exercised against the real measured values in a test and
 * so a drift between the two is visible rather than silent.
 *
 * THE ADMISSION RULE. env_sim.md specifies the quality gate as a BUILD failure. For
 * this phase it is a REGISTRY ADMISSION TEST instead: a circuit that fails the gate, or
 * has no entry, or whose published asset cannot be fetched, keeps the procedural ribbon
 * and everything else carries on exactly as it does today. Three fallbacks, one
 * outcome. Nothing here may ever make a circuit worse than it is without a model.
 *
 * Both gate numbers below are the Python constants restated, not re-chosen:
 * GATE_MIN_COVERAGE = 0.99, GATE_MAX_RESIDUAL_STD_M = 0.15. Do not loosen either to
 * make a circuit pass -- a circuit that stays on the ribbon costs nothing, and a
 * circuit admitted on a bad alignment stands its cars in mid-air.
 */

/** coverage >= this fraction of ring stations, or the circuit is not admitted. */
export const GATE_MIN_COVERAGE = 0.99;
/** elevation residual std <= this, metres, or the circuit is not admitted. */
export const GATE_MAX_RESIDUAL_STD_M = 0.15;

/** What the Python bake measured, mirrored from `measured:` in config/circuits.yaml. */
export interface EnvironmentMeasurement {
  stations: number;
  stationsValid: number;
  /** fraction of ring stations the raycast landed on ANY drive surface */
  coverage: number;
  /** the stricter read: fraction landing on a NAME-ACCEPTED road surface. 0 is not a
   * failure -- Shanghai's export names nothing, so its coverage is carried entirely by
   * geometry, colour and texture. */
  roadCoverage: number;
  residualStdM: number;
  residualMaxM: number;
  camberCoverage: number;
}

/**
 * Material and light treatment for one model.
 *
 * Every number here is a DIAGNOSED BUG on a specific asset (env_sim.md Part B), not a
 * taste setting: a Sketchfab specular-workflow export converts to metalness values that
 * render grandstands solid black without an HDR environment, and ACES tone mapping made
 * the scene far darker than its own source render.
 */
export interface EnvironmentRenderProfile {
  /** metalness ceiling. 0.25: the converted specular metalness is too high without an
   * HDR env, which is what turned roofs and grandstands into black cut-outs. */
  maxMetalness: number;
  /** roughness floor, same cause. */
  minRoughness: number;
  /** texture anisotropy, clamped to the GPU's own maximum at apply time. */
  anisotropy: number;
  /** Hemisphere fill, the light that stops the unlit sides going black. */
  hemisphere: { skyHex: number; groundHex: number; intensity: number };
  ambientIntensity: number;
  directional: { intensity: number; position: [number, number, number] };
}

/** One circuit's row in the registry. */
export interface EnvironmentDef {
  slug: string;
  /** the event name Python keys the bake by */
  event: string;
  /** repo-relative path of the SOURCE asset the fit was measured on (gitignored) */
  sourceGlb: string;
  /** sha256 of that source asset -- NOT of the published bytes, which are prepared
   * (downscaled textures, meshopt) and therefore hash differently. The published
   * bytes' hash is in the artifact, as `surface.assetSha256`. */
  sourceSha256: string;
  /** which Python extractor profile produced the bake */
  profile: string;
  /** the measured fit, telemetry metres -> the model's Y-up world frame */
  fit: TrackSurfaceTransform;
  measured: EnvironmentMeasurement;
  /** the gate verdict Python recorded. Only "pass" is ever drawn. */
  gate: "pass" | "fail";
  render: EnvironmentRenderProfile;
}

/**
 * Shared by both Sketchfab exports. Kept as one object rather than copied per circuit
 * because the fix belongs to the EXPORT PIPELINE (specular -> metallic conversion),
 * not to any one circuit: a third Sketchfab model would need the same treatment.
 */
const SKETCHFAB_SPECULAR: EnvironmentRenderProfile = {
  maxMetalness: 0.25,
  minRoughness: 0.42,
  anisotropy: 16,
  hemisphere: { skyHex: 0xffffff, groundHex: 0x8b8b8b, intensity: 1.65 },
  ambientIntensity: 0.45,
  directional: { intensity: 1.1, position: [1000, 1800, 800] },
};

/**
 * Every row of config/circuits.yaml, INCLUDING the circuit that fails the gate.
 *
 * The failing row is kept deliberately. A UI that wants to say "Shanghai has a model but
 * it does not align well enough to stand cars on" needs the numbers to say it with, and
 * a test can pin every row against the gate constants -- which is what stops a stale
 * `measured` block keeping a circuit shipping after it has stopped aligning. Read a
 * circuit's environment through `environmentFor`, never out of this record directly:
 * that function is where the admission test lives.
 */
export const ENVIRONMENTS: Record<string, EnvironmentDef> = {
  "british-grand-prix": {
    slug: "british-grand-prix",
    event: "British Grand Prix",
    sourceGlb: "data/tracks/silverstone.glb",
    sourceSha256: "228c897e5c15674fd62d936160f0599602ca28c61e13aaf32555fd0084d58783",
    profile: "edelta-scorer",
    fit: {
      scale: 0.999404, yawDeg: 0.203281, mirror: -1,
      txM: -277.7467, tzM: 442.3924, tyM: -203.2841,
    },
    measured: {
      stations: 5832, stationsValid: 5832,
      coverage: 1.0, roadCoverage: 0.998457,
      residualStdM: 0.050814, residualMaxM: 0.165197,
      camberCoverage: 0.984739,
    },
    // PASSES: 100 % coverage at 0.0508 m residual std against a 0.15 m limit. 5513
    // stations land on `asphalt.001`, 310 on `Curb_new.001` at apex kerbs -- nothing
    // forces that, so it is evidence the alignment is right rather than merely
    // self-consistent.
    gate: "pass",
    render: SKETCHFAB_SPECULAR,
  },
  "chinese-grand-prix": {
    slug: "chinese-grand-prix",
    event: "Chinese Grand Prix",
    sourceGlb: "data/tracks/shanghai.glb",
    sourceSha256: "4bc4b1023fb79a76d5e9a9979d32c6f06f03c628d388ae77df52d521157fa464",
    profile: "edelta-scorer",
    fit: {
      scale: 1.008645, yawDeg: 359.323594, mirror: -1,
      txM: 63.7404, tzM: 187.4186, tyM: -17.6173,
    },
    measured: {
      stations: 5451, stationsValid: 5451,
      coverage: 1.0, roadCoverage: 0.0,
      residualStdM: 0.306146, residualMaxM: 1.193308,
      camberCoverage: 1.0,
    },
    // FAILS at 2.0x the residual limit, and stays failing: it KEEPS THE PROCEDURAL
    // RIBBON. The residual is regional rather than noise (eight contiguous runs above
    // 0.5 m, model relief 7 % flatter than the telemetry's), i.e. a 2018 layout against
    // 2026 telemetry. It needs a better asset, not a looser gate.
    gate: "fail",
    render: SKETCHFAB_SPECULAR,
  },
};

/**
 * The environment definition for a circuit, or null.
 *
 * A circuit with no entry and a circuit that FAILS the gate read exactly the same here
 * -- null -- because they mean the same thing downstream: draw the ribbon. The gate is
 * applied inside this function rather than by omitting the row, so the registry can
 * stay a faithful mirror of the Python one.
 */
export function environmentFor(slug: string | null | undefined): EnvironmentDef | null {
  if (!slug) return null;
  const def = ENVIRONMENTS[slug];
  if (!def) return null;
  return def.gate === "pass" ? def : null;
}

/** True when a bake's own measurements clear both gate limits. Used to check a
 * registry row against the numbers it carries, rather than trusting its verdict. */
export function measurementPasses(m: EnvironmentMeasurement): boolean {
  return m.coverage >= GATE_MIN_COVERAGE && m.residualStdM <= GATE_MAX_RESIDUAL_STD_M;
}

/**
 * Where the loaded GLB's root goes in the render frame.
 *
 * `scale` is uniform, `rotationY` is a rotation about the render-frame up axis, and
 * `position` is the translation applied after both -- i.e. exactly what an Object3D's
 * three transform channels express, with no reflection anywhere.
 */
export interface EnvironmentPlacement {
  position: [number, number, number];
  rotationY: number;
  scale: number;
}

/**
 * The placement DERIVED from the measured fit. Nothing here is hand-tuned.
 *
 * The fit maps telemetry metres onto the model's Y-up world frame:
 *
 *     u  = (s*x, s*mirror*y)
 *     wx = cos(yaw)*u.x - sin(yaw)*u.y + tx
 *     wz = sin(yaw)*u.x + cos(yaw)*u.y + tz
 *     wy = s*z + ty
 *
 * and the renderer maps the same telemetry point by toRenderFrame, (x, y, z) ->
 * (x, z, -y). So the transform to hang on the GLB is the fit INVERTED and then sent
 * through toRenderFrame. Writing a = wx - tx, b = wz - tz, that inverse is
 *
 *     RX = ( cos(yaw)*a + sin(yaw)*b) / s
 *     RY = (wy - ty) / s
 *     RZ = mirror * ( sin(yaw)*a - cos(yaw)*b) / s
 *
 * whose determinant is -mirror. With the MEASURED mirror of -1 that is +1: a proper
 * rotation, which collapses to `rotation.y = yaw`, a uniform `scale = 1/s`, and a
 * position of -R_y(yaw) * t / s. (Sanity check on the British fit: this yields
 * (276.34, 203.41, -443.64) at rotation.y = 0.00355, against the (276.23, 203.68,
 * -442.88) env_sim.md verified independently across all 5832 stations under its own
 * earlier fit -- agreement to under a metre between two separately measured fits.)
 *
 * A fit with mirror = +1 has determinant -1 and CANNOT be expressed this way. It would
 * need a negative scale component, which mirrors the model's advertising boards,
 * sponsor logos and signage -- so such a circuit is refused (null) and keeps its
 * ribbon, rather than being shipped back-to-front. Both measured fits are -1.
 */
export function environmentPlacement(fit: TrackSurfaceTransform): EnvironmentPlacement | null {
  const { scale, yawDeg, mirror, txM, tyM, tzM } = fit;
  if (![scale, yawDeg, txM, tyM, tzM].every((v) => Number.isFinite(v))) return null;
  if (!(scale > 0)) return null;
  if (mirror !== -1) return null;

  const yaw = (yawDeg * Math.PI) / 180;
  const c = Math.cos(yaw), s = Math.sin(yaw);
  const inv = 1 / scale;
  const tx = txM * inv, ty = tyM * inv, tz = tzM * inv;
  return {
    position: [-(c * tx + s * tz), -ty, -(-s * tx + c * tz)],
    rotationY: yaw,
    scale: inv,
  };
}

/** A circuit's environment, resolved against the artifact that names its asset. */
export interface ResolvedEnvironment {
  def: EnvironmentDef;
  /** served URL of the published GLB, straight from the artifact */
  assetUrl: string;
  /** sha256 of the PUBLISHED bytes, or null when the artifact carries none */
  assetSha256: string | null;
  placement: EnvironmentPlacement;
}

/**
 * The environment to draw for this track, or null -- which is the normal answer for 12
 * of 13 circuits and for every artifact built before schemaVersion 2.
 *
 * Four things must all hold, and any one missing means "ribbon":
 *   1. the slug is in the registry AND passed the gate,
 *   2. the artifact carries a baked `surface` (without it there is no surface to stand
 *      a car on, and no measured transform to place the model with),
 *   3. that block names a published asset,
 *   4. the transform is one a rotation can express (see environmentPlacement).
 */
export function environmentForTrack(
  track: Pick<TrackModel, "slug" | "surface">,
): ResolvedEnvironment | null {
  const def = environmentFor(track.slug);
  if (!def) return null;
  const surface: TrackSurface | null = track.surface ?? null;
  if (!surface || !surface.assetUrl) return null;
  const placement = environmentPlacement(surface.transform);
  if (!placement) return null;
  return { def, assetUrl: surface.assetUrl, assetSha256: surface.assetSha256, placement };
}

/** Texture slots that get the anisotropy bump. Colour slots additionally get sRGB. */
const ANISO_SLOTS = ["map", "emissiveMap", "normalMap", "roughnessMap", "metalnessMap"] as const;
const SRGB_SLOTS = ["map", "emissiveMap"] as const;

function textureAt(mat: THREE.Material, slot: string): THREE.Texture | null {
  const tex = (mat as unknown as Record<string, unknown>)[slot];
  return tex instanceof THREE.Texture ? tex : null;
}

/**
 * The material fixes from env_sim.md Part B, applied to one material.
 *
 * Ported verbatim in EFFECT from the prototype, and every line of it is a bug that was
 * actually observed on these assets rather than a preference:
 *   - sRGB on `map` and `emissiveMap`: the export's colour textures are sRGB-encoded
 *     and three assumes linear unless told, which washes the whole circuit out.
 *   - metalness ceiling / roughness floor: see EnvironmentRenderProfile.
 *   - anisotropy: the road is viewed at a grazing angle from a chase camera, which is
 *     the exact case isotropic mip filtering smears into mush.
 */
export function configureEnvironmentMaterial(
  mat: THREE.Material, def: EnvironmentDef, anisotropy: number,
): void {
  for (const slot of ANISO_SLOTS) {
    const tex = textureAt(mat, slot);
    if (!tex) continue;
    tex.anisotropy = anisotropy;
    tex.needsUpdate = true;
  }
  for (const slot of SRGB_SLOTS) {
    const tex = textureAt(mat, slot);
    if (!tex) continue;
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.minFilter = THREE.LinearMipmapLinearFilter;
    tex.magFilter = THREE.LinearFilter;
    tex.needsUpdate = true;
  }
  const std = mat as THREE.MeshStandardMaterial;
  if (std.isMeshStandardMaterial) {
    std.metalness = Math.min(std.metalness ?? 0, def.render.maxMetalness);
    std.roughness = Math.max(std.roughness ?? 0.5, def.render.minRoughness);
  }
  mat.needsUpdate = true;
}

/**
 * Applies the profile across a loaded model. Returns how many materials it touched, so
 * a caller (or a test) can tell "applied to nothing" from "applied".
 *
 * Shadows are explicitly turned OFF on every mesh: the prototype measured that real-time
 * shadow maps over a 1.2 M-triangle circuit cost more than they add, and the sim's own
 * renderer has shadowMap disabled, so a mesh asking to cast one is pure overhead.
 */
export function applyEnvironmentMaterials(
  root: THREE.Object3D, def: EnvironmentDef, maxAnisotropy: number,
): number {
  const anisotropy = Math.max(1, Math.min(def.render.anisotropy, maxAnisotropy || 1));
  let touched = 0;
  root.traverse((obj) => {
    if (!(obj instanceof THREE.Mesh)) return;
    obj.castShadow = false;
    obj.receiveShadow = false;
    const mat = obj.material;
    for (const m of Array.isArray(mat) ? mat : [mat]) {
      if (!m) continue;
      configureEnvironmentMaterial(m, def, anisotropy);
      touched++;
    }
  });
  return touched;
}
