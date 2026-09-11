import type { CSSProperties } from "react";
import { HAAS } from "@/lib/palette";
import { CAR_VIEWBOX, WHEEL_VB } from "./physics/constants";
import styles from "./car-top.module.css";

type WheelId = "rl" | "rr" | "fl" | "fr";

/**
 * Wheel boxes in percent of the car box, derived from WHEEL_VB (viewBox units, 100 per metre).
 * Plan view: the box is the tyre diameter along x and the tyre width along y.
 * "l" = screen-up side (y < 0 in the ground frame), "r" = screen-down side.
 */
const WHEELS: ReadonlyArray<{ id: WheelId; style: CSSProperties }> = (
  [
    { id: "rl", axle: WHEEL_VB.rear, side: -1 },
    { id: "rr", axle: WHEEL_VB.rear, side: 1 },
    { id: "fl", axle: WHEEL_VB.front, side: -1 },
    { id: "fr", axle: WHEEL_VB.front, side: 1 },
  ] as const
).map(({ id, axle, side }) => {
  const d = WHEEL_VB.diameter;
  const cy = CAR_VIEWBOX.h / 2 + side * axle.halfTrack;
  const pct = (v: number, total: number) => `${((v / total) * 100).toFixed(3)}%`;
  return {
    id,
    style: {
      left: pct(axle.cx - d / 2, CAR_VIEWBOX.w),
      top: pct(cy - axle.width / 2, CAR_VIEWBOX.h),
      width: pct(d, CAR_VIEWBOX.w),
      height: pct(axle.width, CAR_VIEWBOX.h),
    },
  };
});

const NUMBER_FONT: CSSProperties = {
  fontFamily: 'var(--font-display, "Chakra Petch"), Impact, "Arial Narrow", sans-serif',
};

/**
 * Plan-view 2026-style F1 car, nose pointing +x (screen right), drawn strictly in the Haas palette.
 * viewBox 0 0 560 200 = 5.6 m x 2.0 m at 100 units/m; rear of car at x=0, centreline y=100,
 * rear axle x=60, front axle x=400 (see WHEEL_VB). Tyres are NOT in the SVG: they are the four
 * absolutely-positioned wheel divs after it, so the runtime can scroll tread and steer the fronts
 * without re-rasterising the body. Server-compatible: no hooks, no client directive.
 */
export default function HaasCarTop({ className }: { className?: string }) {
  const { white, grey, red, black } = HAAS;
  const box = className ? `${styles.carBox} ${className}` : styles.carBox;
  return (
    <div className={box} data-car>
      <svg
        className={styles.svg}
        viewBox={`0 0 ${CAR_VIEWBOX.w} ${CAR_VIEWBOX.h}`}
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
        focusable="false"
      >
        <defs>
          <filter id="hct-shadow" x="-4%" y="-12%" width="110%" height="126%">
            <feGaussianBlur stdDeviation="6" />
          </filter>
          <filter id="hct-glow" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="3" />
          </filter>
        </defs>

        {/* ground shadow: coarse silhouette (wings, floor, nose, tyre footprints), pre-blurred */}
        <path
          d="M0 46H48V60H100L138 8H372V82H518V2H560V198H518V118H372V192H138L100 140H48V154H0ZM24 9H96V47H24ZM24 153H96V191H24ZM364 9H436V37H364ZM364 163H436V191H364Z"
          fill={black}
          fillRule="evenodd"
          opacity="0.55"
          transform="translate(6 8)"
          filter="url(#hct-shadow)"
        />

        {/* ---- floor: visible outboard of the sidepods, edge outlined ---- */}
        <path
          d="M104 54L142 10H360Q372 10 372 22V178Q372 190 360 190H142L104 146Z"
          fill={black}
          stroke={grey}
          strokeWidth="1.5"
        />
        {/* floor-edge wings / fences */}
        <path d="M150 14H200M150 186H200" stroke={grey} strokeWidth="2" />
        <path d="M348 16H366M348 184H366" stroke={grey} strokeWidth="1" />
        {/* sidepod undercut shading on the floor */}
        <path
          d="M156 74Q180 36 250 31L300 30Q332 30 338 46M156 126Q180 164 250 169L300 170Q332 170 338 154"
          fill="none"
          stroke={grey}
          strokeWidth="7"
          opacity="0.7"
        />

        {/* ---- rear suspension: wishbone legs + driveshaft to each wheel centre (60, 100±72.5) ---- */}
        <g stroke={black} strokeWidth="4" strokeLinecap="round">
          <path d="M56 90L56 30M98 88L64 30M56 110L56 170M98 112L64 170" />
        </g>
        <g stroke={grey} strokeWidth="1.5" strokeLinecap="round">
          <path d="M56 90L56 30M98 88L64 30M56 110L56 170M98 112L64 170" />
        </g>
        <path d="M66 90L62 32M66 110L62 168" stroke={black} strokeWidth="3" />

        {/* ---- front suspension: upper/lower wishbones + pushrod to each wheel centre (400, 100±77.5) ---- */}
        <g stroke={black} strokeWidth="4" strokeLinecap="round">
          <path d="M352 76L396 26M392 80L404 26M376 80L399 32M352 124L396 174M392 120L404 174M376 120L399 168" />
        </g>
        <g stroke={grey} strokeWidth="1.5" strokeLinecap="round">
          <path d="M352 76L396 26M392 80L404 26M352 124L396 174M392 120L404 174" />
        </g>

        {/* ---- rear crash structure + rain light (tail pokes out behind the wing) ---- */}
        <path d="M2 95H62V105H2Z" fill={white} stroke={grey} strokeWidth="1" />
        <ellipse cx="6" cy="100" rx="8" ry="6" fill={red} opacity="0.55" filter="url(#hct-glow)" />
        <rect x="1" y="96" width="7" height="8" rx="1" fill={red} />
        {/* beam wing */}
        <rect x="46" y="64" width="10" height="72" rx="2" fill={grey} />

        {/* ---- rear wing: endplates, DRS flap (rearmost), red main-plane top surface ---- */}
        <rect x="0" y="46" width="48" height="6" rx="1.5" fill={white} stroke={grey} strokeWidth="1" />
        <rect x="0" y="148" width="48" height="6" rx="1.5" fill={white} stroke={grey} strokeWidth="1" />
        <rect x="2" y="54" width="12" height="92" rx="1" fill={white} stroke={grey} strokeWidth="1" />
        <rect x="12" y="52" width="32" height="96" rx="1.5" fill={red} />
        <path d="M43 52V148" stroke={white} strokeWidth="1.5" opacity="0.7" />

        {/* ---- body: engine cover, sidepods, chassis, nose (one symmetric outline) ---- */}
        <path
          d="M14 94L60 90L110 86L156 74Q180 36 250 31L300 30Q332 30 338 46V62L350 74L372 78L556 92Q561 100 556 108L372 122L350 126L338 138V154Q332 170 300 170L250 169Q180 164 156 126L110 114L60 110L14 106Z"
          fill={white}
        />
        {/* sidepod ramps (light grey wash) outboard of the engine-cover spine */}
        <path
          d="M156 74Q180 36 250 31L300 30Q332 30 338 46V74Q300 72 250 76Q200 72 160 78ZM156 126Q180 164 250 169L300 170Q332 170 338 154V126Q300 128 250 124Q200 128 160 122Z"
          fill={grey}
          opacity="0.3"
        />
        <path
          d="M48 92Q120 84 160 78Q200 72 250 76M48 108Q120 116 160 122Q200 128 250 124"
          fill="none"
          stroke={grey}
          strokeWidth="1"
        />
        {/* red spine stripe along the engine cover */}
        <path d="M52 100L244 95V105Z" fill={red} />

        {/* ---- sidepod inlets (dark vertical slots) + red swooshes sweeping rearward-outward ---- */}
        <rect x="333" y="40" width="7" height="24" rx="2" fill={black} />
        <rect x="333" y="136" width="7" height="24" rx="2" fill={black} />
        <path d="M322 70Q276 54 208 42L204 49Q268 63 316 76Z" fill={red} />
        <path d="M322 130Q276 146 208 158L204 151Q268 137 316 124Z" fill={red} />

        {/* ---- mirrors on stalks ---- */}
        <path d="M350 58L352 74M350 142L352 126" stroke={black} strokeWidth="1.5" />
        <rect x="344" y="50" width="12" height="8" rx="2" fill={grey} />
        <rect x="344" y="142" width="12" height="8" rx="2" fill={grey} />

        {/* ---- airbox mouth, roll hoop, cockpit opening ---- */}
        <rect x="220" y="96" width="8" height="8" rx="1" fill={black} />
        <ellipse cx="238" cy="100" rx="9" ry="14" fill={black} />
        <ellipse cx="238" cy="100" rx="9" ry="14" fill="none" stroke={grey} strokeWidth="1" />
        <ellipse
          cx="292"
          cy="100"
          rx="34"
          ry="20"
          fill={black}
          stroke={grey}
          strokeWidth="2"
          strokeOpacity="0.8"
        />
        {/* helmet with red visor crescent at the front */}
        <circle cx="286" cy="100" r="11" fill={white} stroke={black} strokeWidth="1.5" />
        <path d="M289 90A11 11 0 0 1 289 110A7 7 0 0 0 289 90Z" fill={red} />

        {/* ---- halo ring, grey top highlight, central pillar ---- */}
        <ellipse cx="290" cy="100" rx="44" ry="30" fill="none" stroke={black} strokeWidth="6" />
        <ellipse cx="290" cy="99" rx="44" ry="30" fill="none" stroke={grey} strokeWidth="1.5" />
        <path d="M334 100H354" stroke={black} strokeWidth="5" strokeLinecap="round" />
        <path d="M334 99H354" stroke={grey} strokeWidth="1" strokeLinecap="round" />

        {/* ---- nose: race number (red tip is drawn after the wing so it sits on top) ---- */}
        <text
          x="472"
          y="107"
          textAnchor="middle"
          fontSize="20"
          fontWeight="700"
          fontStyle="italic"
          fill={black}
          style={NUMBER_FONT}
        >
          31
        </text>

        {/* ---- front wing: endplates, two flaps (red outer tips), mainplane at the very front ---- */}
        <rect x="512" y="2" width="48" height="6" rx="1.5" fill={white} stroke={grey} strokeWidth="1" />
        <rect x="512" y="192" width="48" height="6" rx="1.5" fill={white} stroke={grey} strokeWidth="1" />
        <rect x="518" y="8" width="22" height="184" fill={white} stroke={grey} strokeWidth="1" />
        <path d="M529 8V192" stroke={grey} strokeWidth="1" />
        <rect x="518" y="8" width="22" height="34" fill={red} />
        <rect x="518" y="158" width="22" height="34" fill={red} />
        <rect x="540" y="8" width="20" height="184" rx="2" fill={white} stroke={grey} strokeWidth="1" />
        {/* red nose tip over the wing centre section */}
        <path d="M528 90L556 92Q561 100 556 108L528 110Z" fill={red} />
        <path d="M528 90V110" stroke={white} strokeWidth="1" />
      </svg>

      {WHEELS.map((w) => (
        <div key={w.id} className={styles.wheel} style={w.style} data-wheel={w.id}>
          <div className={styles.tread} data-tread />
          <div className={styles.blur} data-blur />
        </div>
      ))}
    </div>
  );
}
