import * as THREE from "three";
import { HAAS } from "@/lib/palette";

/**
 * Floating name tags above each car.
 *
 * Drawn as sprites with a small canvas texture rather than HTML overlays: twenty
 * absolutely-positioned divs re-projected every frame would be exactly the kind of
 * per-frame DOM write this renderer avoids everywhere else. Sprites always face the
 * camera, cost one small texture each (built once), and never touch React.
 */

const PAD = 8;
const FONT_PX = 40;

function makeLabelTexture(text: string, accent: string): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d")!;
  ctx.font = `600 ${FONT_PX}px system-ui, sans-serif`;
  const w = Math.ceil(ctx.measureText(text).width) + PAD * 2 + 10;
  const h = FONT_PX + PAD * 2;
  canvas.width = w;
  canvas.height = h;

  const c = canvas.getContext("2d")!;
  c.font = `600 ${FONT_PX}px system-ui, sans-serif`;
  c.textBaseline = "middle";
  // plate
  c.fillStyle = "rgba(17,17,17,0.86)";
  c.fillRect(0, 0, w, h);
  // team colour flash down the left edge, the same identity cue the leaderboard uses
  c.fillStyle = accent;
  c.fillRect(0, 0, 6, h);
  c.strokeStyle = "rgba(174,174,174,0.55)";
  c.lineWidth = 2;
  c.strokeRect(1, 1, w - 2, h - 2);
  c.fillStyle = HAAS.white;
  c.fillText(text, PAD + 10, h / 2 + 1);

  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  return tex;
}

export interface DriverLabels {
  group: THREE.Group;
  sprites: THREE.Sprite[];
  dispose(): void;
}

/** One tag per driver, in the SAME index order as the pose buffer. */
export function buildDriverLabels(
  drivers: string[], teamColours: (string | null)[], heightM: number,
): DriverLabels {
  const group = new THREE.Group();
  group.name = "driver-labels";
  const sprites: THREE.Sprite[] = [];

  drivers.forEach((code, i) => {
    const tex = makeLabelTexture(code, teamColours[i] ?? HAAS.grey);
    const mat = new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true });
    const sprite = new THREE.Sprite(mat);
    // The initial size is a placeholder: the renderer rescales every sprite each frame
    // to hold a constant pixel height (see SimRenderer.applyPose). The aspect ratio is
    // stashed here so that rescale never has to touch the texture again.
    const aspect = tex.image.width / tex.image.height;
    sprite.userData.aspect = aspect;
    sprite.scale.set(heightM * aspect, heightM, 1);
    sprite.renderOrder = 10;
    sprite.frustumCulled = false;
    group.add(sprite);
    sprites.push(sprite);
  });

  return {
    group,
    sprites,
    dispose() {
      for (const s of sprites) {
        (s.material as THREE.SpriteMaterial).map?.dispose();
        (s.material as THREE.SpriteMaterial).dispose();
      }
      group.clear();
    },
  };
}
