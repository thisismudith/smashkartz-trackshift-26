import * as THREE from "three";
import { CAR } from "@/components/loader/physics/constants";
import { CAR_RENDER_LENGTH_M, CAR_RENDER_WIDTH_M, CAR_VISUAL_SCALE } from "./presentation";

/**
 * A recognisable F1 car silhouette, built procedurally and merged into ONE geometry
 * so all twenty cars still render as a single InstancedMesh (one draw call).
 *
 * Built rather than downloaded on purpose: no third-party asset means no licensing or
 * provenance question, nothing to ship over the wire, and it matches the rest of this
 * project (the intro car is hand-built SVG for the same reason).
 *
 * Colour is carried in a vertex-colour channel: bodywork is left white so the
 * per-instance team colour shows through unchanged, while tyres and wing planes are
 * darkened. The instance colour multiplies these, so one geometry + one material
 * still gives per-team cars with black tyres.
 *
 * Local frame matches the box it replaces: +X is forward (nose), +Y up, +Z to the
 * side, and the model is centred on the origin so the existing placement maths and
 * the focus outline need no changes.
 *
 * SIZE: the parts below are laid out at the car's TRUE size (5.6 m x 2.0 m), then the
 * whole thing is multiplied by CAR_VISUAL_SCALE on the way out. That multiply is the
 * only place the drawn size and the true size diverge; CAR_RENDER_LENGTH_M and
 * CAR_RENDER_WIDTH_M keep meaning real metres for everything that reasons about space
 * (see the note on CAR_VISUAL_SCALE in presentation.ts). Because the scale lives in the
 * vertices, every consumer that MEASURES the geometry -- the ride-height lift in
 * carInstanceY, the focus ghost, the instanced frustum sphere -- follows it for free.
 */

const L = CAR_RENDER_LENGTH_M;          // 5.6 m
const W = CAR_RENDER_WIDTH_M;           // 2.0 m
const WHEEL_R = CAR.wheelRadiusM;       // 0.355 m
const BODY = new THREE.Color(1, 1, 1);  // takes the team colour untouched
const DARK = new THREE.Color(0.16, 0.16, 0.16); // tyres, wing planes, cockpit

interface Part { geom: THREE.BufferGeometry; colour: THREE.Color }

function box(w: number, h: number, d: number, x: number, y: number, z: number, colour: THREE.Color): Part {
  const geom = new THREE.BoxGeometry(w, h, d);
  geom.translate(x, y, z);
  return { geom, colour };
}

function wheel(x: number, z: number): Part {
  // a cylinder laid on its side: three's cylinder runs along Y, so tip it about X
  const geom = new THREE.CylinderGeometry(WHEEL_R, WHEEL_R, 0.36, 14);
  geom.rotateX(Math.PI / 2);
  geom.translate(x, WHEEL_R - 0.30, z);
  return { geom, colour: DARK };
}

function buildParts(): Part[] {
  const halfTrack = W / 2 - 0.18;
  const frontAxle = L * 0.32;
  const rearAxle = -L * 0.30;

  return [
    // ---- floor / tub: the long central body, tapering visually via two stacked boxes
    box(L * 0.62, 0.16, W * 0.42, -L * 0.02, -0.10, 0, BODY),
    box(L * 0.40, 0.22, W * 0.30, -L * 0.10, 0.06, 0, BODY),
    // ---- nose: a slim tapered snout reaching the front wing
    box(L * 0.34, 0.14, W * 0.13, L * 0.30, -0.02, 0, BODY),
    // ---- sidepods either side of the cockpit
    box(L * 0.26, 0.20, W * 0.20, -L * 0.02, -0.02, halfTrack * 0.62, BODY),
    box(L * 0.26, 0.20, W * 0.20, -L * 0.02, -0.02, -halfTrack * 0.62, BODY),
    // ---- engine cover + airbox behind the driver
    box(L * 0.30, 0.26, W * 0.18, -L * 0.20, 0.16, 0, BODY),
    box(L * 0.10, 0.18, W * 0.13, -L * 0.04, 0.24, 0, BODY),
    // ---- cockpit opening + halo bar, dark so the shape reads from above
    box(L * 0.11, 0.10, W * 0.15, L * 0.02, 0.22, 0, DARK),
    box(L * 0.13, 0.05, W * 0.24, L * 0.03, 0.30, 0, DARK),
    // ---- front wing: wide, low, dark plane
    box(L * 0.09, 0.05, W * 0.92, L * 0.455, -0.16, 0, DARK),
    // ---- rear wing: raised plane on two endplates
    box(L * 0.10, 0.05, W * 0.80, -L * 0.45, 0.34, 0, DARK),
    box(L * 0.10, 0.30, 0.05, -L * 0.45, 0.20, W * 0.38, DARK),
    box(L * 0.10, 0.30, 0.05, -L * 0.45, 0.20, -W * 0.38, DARK),
    // ---- four wheels
    wheel(frontAxle, halfTrack),
    wheel(frontAxle, -halfTrack),
    wheel(rearAxle, halfTrack),
    wheel(rearAxle, -halfTrack),
  ];
}

/**
 * Concatenates the parts by hand (rather than pulling in a merge addon), writing a
 * per-vertex colour for each so tyres stay dark while bodywork takes the team hue.
 *
 * `visualScale` multiplies the finished vertex positions about the model origin, and
 * defaults to CAR_VISUAL_SCALE. Pass 1 for the true-size car (the tests measure both, so
 * the scaling can be checked rather than trusted). Normals are left alone: a UNIFORM
 * scale does not rotate them and they stay unit-length, so lighting is unaffected.
 * Colours are likewise untouched.
 */
export function buildF1CarGeometry(visualScale: number = CAR_VISUAL_SCALE): THREE.BufferGeometry {
  const parts = buildParts().map((p) => ({ ...p, geom: p.geom.toNonIndexed() }));

  let total = 0;
  for (const p of parts) total += p.geom.getAttribute("position").count;

  const position = new Float32Array(total * 3);
  const normal = new Float32Array(total * 3);
  const colour = new Float32Array(total * 3);

  let o = 0;
  for (const p of parts) {
    const pos = p.geom.getAttribute("position") as THREE.BufferAttribute;
    const nor = p.geom.getAttribute("normal") as THREE.BufferAttribute;
    const n = pos.count;
    position.set(pos.array as Float32Array, o * 3);
    normal.set(nor.array as Float32Array, o * 3);
    for (let i = 0; i < n; i++) {
      colour[(o + i) * 3] = p.colour.r;
      colour[(o + i) * 3 + 1] = p.colour.g;
      colour[(o + i) * 3 + 2] = p.colour.b;
    }
    o += n;
    p.geom.dispose();
  }

  // The one place drawn size leaves true size. Scaling the merged positions (rather than
  // each part, or the mesh) keeps the model origin fixed, so the car still rotates about
  // its own centre and minY -- the ride-height lift -- scales with the body: at 1.3 the
  // wheels sit at -0.390 m instead of -0.300 m and carInstanceY lifts by exactly that
  // much more.
  if (visualScale !== 1) for (let i = 0; i < position.length; i++) position[i] *= visualScale;

  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(position, 3));
  geom.setAttribute("normal", new THREE.BufferAttribute(normal, 3));
  geom.setAttribute("color", new THREE.BufferAttribute(colour, 3));
  geom.computeBoundingSphere();
  return geom;
}
