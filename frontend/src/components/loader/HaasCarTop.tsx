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

/** The logotype: heavy, tightly tracked and slanted, like the HAAS mark on the real car. */
const LOGO_FONT: CSSProperties = {
  fontFamily: 'var(--font-display, "Archivo"), Impact, "Arial Narrow", sans-serif',
  fontWeight: 900,
  fontStyle: "italic",
  fontStretch: "112%",
};

/**
 * Body outline, one symmetric path: tail -> engine cover -> sidepods -> chassis -> nose -> tip,
 * then mirrored back along the bottom. Half-widths follow the VF-21 plan view: a narrow tail, a
 * long tapering engine cover, sidepods at their widest just behind the cockpit (half-width 70),
 * a hard pinch in front of the sidepod inlets, then a slim nose spine out to the wing.
 */
const BODY =
  "M14 93 Q60 88 100 82 Q132 77 150 72 Q180 64 200 56 Q226 44 240 40 Q256 31 272 31 L310 30 " +
  "Q337 32 341 43 Q347 59 357 70 Q363 78 373 79 L548 92 Q554 100 548 108 L373 121 Q363 122 357 130 " +
  "Q347 141 341 157 Q337 168 310 170 L272 169 Q256 169 240 160 Q226 156 200 144 Q180 136 150 128 " +
  "Q132 123 100 118 Q60 112 14 107 Z";

/**
 * Plan-view Haas-liveried F1 car, nose pointing +x (screen right), drawn in the Haas palette.
 *
 * Livery mapping from the VF-21 plan view: white shell, black carbon (floor, wings, suspension,
 * halo, inlets), HAAS.blue for the rear-deck flash core and the front-wing endplate band, and red
 * for the flash edging plus the HAAS logotype itself. The car carries NO red outline — on the real
 * thing the red along the flanks is the oversized wordmark running off the edge of the bodywork,
 * reproduced here by clipping the logotype to the body outline.
 *
 * viewBox 0 0 560 200 = 5.6 m x 2.0 m at 100 units/m; rear of car at x=0, centreline y=100,
 * rear axle x=60, front axle x=400 (see WHEEL_VB). Tyres are NOT in the SVG: they are the four
 * absolutely-positioned wheel divs after it, so the runtime can scroll tread and steer the fronts
 * without re-rasterising the body. Server-compatible: no hooks, no client directive.
 */
export default function HaasCarTop({ className }: { className?: string }) {
  const { white, grey, red, blue, black } = HAAS;
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
          {/* every livery graphic is clipped to the shell, so the wordmark runs off the edge */}
          <clipPath id="hct-body">
            <path d={BODY} />
          </clipPath>
        </defs>

        {/* ground shadow: the real silhouette (shell + floor + wings), pre-blurred */}
        <g fill={black} opacity="0.45" transform="translate(5 7)" filter="url(#hct-shadow)">
          <path d={BODY} />
          <path d="M106 60L140 26H350Q358 26 358 34V166Q358 174 350 174H140L106 140Z" />
          <rect x="0" y="44" width="48" height="112" />
          <rect x="504" y="2" width="56" height="196" />
        </g>

        {/* ---- floor: black carbon, visible outboard of the bodywork between the wheels ---- */}
        <path d="M106 60L140 26H350Q358 26 358 34V166Q358 174 350 174H140L106 140Z" fill={black} />
        <path
          d="M140 27H350Q357 27 357 34M140 173H350Q357 173 357 166"
          fill="none"
          stroke={grey}
          strokeWidth="1.2"
          opacity="0.6"
        />
        {/* floor fences */}
        <g stroke={grey} strokeWidth="1" opacity="0.3">
          <path d="M160 32H210M168 37H210M300 30V42M312 30V40M324 30V38" />
          <path d="M160 168H210M168 163H210M300 170V158M312 170V160M324 170V162" />
        </g>

        {/* ---- rear suspension: wishbone legs + driveshaft to each wheel centre (60, 100±72.5) ---- */}
        <g stroke={black} strokeWidth="5" strokeLinecap="round">
          <path d="M54 88L58 30M96 86L64 30M54 112L58 170M96 114L64 170" />
        </g>
        <g stroke={grey} strokeWidth="1.4" strokeLinecap="round" opacity="0.7">
          <path d="M54 88L58 30M96 86L64 30M54 112L58 170M96 114L64 170" />
        </g>
        <path d="M68 92L64 32M68 108L64 168" stroke={black} strokeWidth="3.5" />

        {/* ---- front suspension: upper/lower wishbones + pushrod to each wheel centre (400, 100±77.5) ---- */}
        <g stroke={black} strokeWidth="5" strokeLinecap="round">
          <path d="M356 74L396 26M394 80L404 26M376 78L399 32M356 126L396 174M394 120L404 174M376 122L399 168" />
        </g>
        <g stroke={grey} strokeWidth="1.4" strokeLinecap="round" opacity="0.7">
          <path d="M356 74L396 26M394 80L404 26M356 126L396 174M394 120L404 174" />
        </g>

        {/* ---- rear crash structure + rain light ---- */}
        <path d="M2 95H62V105H2Z" fill={white} />
        <ellipse cx="6" cy="100" rx="8" ry="6" fill={red} opacity="0.55" filter="url(#hct-glow)" />
        <rect x="1" y="96" width="7" height="8" rx="1" fill={red} />

        {/* ---- bodywork shell (no outline: the red on the flanks is the wordmark, not a border) ---- */}
        <path d={BODY} fill={white} />

        {/* ---- livery, all clipped to the shell ---- */}
        <g clipPath="url(#hct-body)">
          {/* engine-cover cooling louvres */}
          <g fill={black} opacity="0.85">
            <rect x="150" y="84" width="24" height="2.6" rx="1.3" />
            <rect x="156" y="89" width="24" height="2.6" rx="1.3" />
            <rect x="150" y="113.4" width="24" height="2.6" rx="1.3" />
            <rect x="156" y="108.4" width="24" height="2.6" rx="1.3" />
          </g>

          {/* sidepod inlets: the black intake mouths at the leading edge */}
          <path d="M336 44 Q346 56 352 70 L344 74 Q338 58 330 48 Z" fill={black} />
          <path d="M336 156 Q346 144 352 130 L344 126 Q338 142 330 152 Z" fill={black} />
        </g>

        {/* ---- airbox / roll hoop intake, behind the driver ---- */}
        <path d="M232 89 Q246 86 252 93 L252 107 Q246 114 232 111 Z" fill={black} />
        <path d="M236 93 Q246 91 249 95 L249 105 Q246 109 236 107 Z" fill={grey} opacity="0.28" />

        {/*
          ---- cockpit: survival-cell opening with its padded surround, then the halo.
          In plan view the halo is a slim loop running from two rear mounts, out around the
          opening, converging on a single pillar ahead of the driver — not a fat ring.
        */}
        {/* coaming around the survival-cell opening */}
        <path
          d="M262 84 Q268 79 284 78 L308 79 Q322 82 324 92 L324 108 Q322 118 308 121 L284 122 Q268 121 262 116 Z"
          fill={black}
        />
        <path
          d="M267 87 Q272 83 285 82 L307 83 Q319 86 320 93 L320 107 Q319 114 307 117 L285 118 Q272 117 267 113 Z"
          fill="none"
          stroke={grey}
          strokeWidth="1.1"
          opacity="0.55"
        />
        {/* headrest padding wrapping the back of the driver's head */}
        <path
          d="M276 88 Q270 100 276 112"
          fill="none"
          stroke={grey}
          strokeWidth="7"
          strokeLinecap="round"
          opacity="0.35"
        />
        {/* helmet from above: dark shell, lit crown, visor edge facing forward (+x) */}
        <ellipse cx="296" cy="100" rx="9.5" ry="7.5" fill={black} />
        <ellipse cx="294.5" cy="99" rx="6.6" ry="4.8" fill={grey} opacity="0.45" />
        <path d="M303 95.5 Q305.5 100 303 104.5" fill="none" stroke={red} strokeWidth="2" strokeLinecap="round" />
        {/* halo: slim loop + central forward pillar */}
        <path
          d="M256 80 Q286 70 312 74 Q338 80 350 100 Q338 120 312 126 Q286 130 256 120"
          fill="none"
          stroke={black}
          strokeWidth="5.5"
          strokeLinecap="round"
        />
        <path
          d="M256 79 Q286 69 312 73 Q338 79 350 99"
          fill="none"
          stroke={grey}
          strokeWidth="1.2"
          strokeLinecap="round"
          opacity="0.65"
        />
        <path d="M350 100H360" stroke={black} strokeWidth="5" strokeLinecap="round" />

        {/* ---- mirrors on stalks ---- */}
        <path d="M344 62L338 74M344 138L338 126" stroke={black} strokeWidth="2" />
        <rect x="338" y="54" width="13" height="8" rx="2.5" fill={black} />
        <rect x="338" y="138" width="13" height="8" rx="2.5" fill={black} />

        {/*
          ---- nose markings. Rotated -90 so they run ACROSS the nose, which is how they sit on the
          real car: painted to be read from the side, so a plan view catches them side-on. Matches
          the rear-wing logotype, and both now turn with the car instead of facing the viewer.
        */}
        <text
          x="450"
          y="100"
          textAnchor="middle"
          dominantBaseline="central"
          fontSize="22"
          fill={red}
          style={LOGO_FONT}
          transform="rotate(-90 450 100)"
        >
          7
        </text>
        {/*
          Fixed textLength instead of letterSpacing: the nose narrows quickly here (body half-width
          ~17 at x=421), so a font-metric-dependent width could crowd the edges. Pinning it to 19
          guarantees clear padding on both sides regardless of how the font renders.
        */}
        <text
          x="421"
          y="100"
          textAnchor="middle"
          dominantBaseline="central"
          fontSize="9"
          textLength="19"
          lengthAdjust="spacingAndGlyphs"
          fill={black}
          style={LOGO_FONT}
          transform="rotate(-90 421 100)"
        >
          HAAS
        </text>
        <g fill={grey} opacity="0.45">
          <rect x="480" y="97" width="14" height="1.8" rx="0.9" />
          <rect x="480" y="101" width="9" height="1.8" rx="0.9" />
        </g>

        {/*
          ---- rear wing, drawn after the shell so the body and flash cannot cover it:
          black endplates + DRS flap, white main plane carrying the red HAAS logotype across the span.
        */}
        <rect x="46" y="66" width="9" height="68" rx="2" fill={black} />
        <path d="M50 66V134" stroke={grey} strokeWidth="1" opacity="0.55" />
        <rect x="0" y="44" width="48" height="9" rx="1.5" fill={black} />
        <rect x="0" y="147" width="48" height="9" rx="1.5" fill={black} />
        <path d="M2 46H46M2 154H46" stroke={grey} strokeWidth="1" opacity="0.45" />
        <rect x="2" y="53" width="11" height="94" rx="1" fill={black} />
        <path d="M8 53V147" stroke={grey} strokeWidth="1" opacity="0.5" />
        <rect x="13" y="53" width="31" height="94" rx="1.5" fill={white} />
        <text
          x="29"
          y="100"
          textAnchor="middle"
          dominantBaseline="central"
          fontSize="18"
          fill={red}
          style={LOGO_FONT}
          transform="rotate(-90 29 100)"
        >
          HAAS
        </text>
        <path d="M20 88V78M20 112V122" stroke={black} strokeWidth="3" />

        {/*
          ---- front wing: swept multi-element planform. Carbon elements inboard, white endplates
          outboard carrying the angled red-over-blue flash, and the white centre the nose plugs into.
        */}
        <path
          d="M504 12 Q504 2 516 2 L546 2 Q558 2 558 14 L558 186 Q558 198 546 198 L516 198 Q504 198 504 188 Z"
          fill={black}
        />
        {/* chordwise element separators across the span */}
        <g stroke={grey} strokeWidth="1" opacity="0.35">
          <path d="M518 8V192M532 8V192M545 8V192" />
        </g>
        {/* endplates, swept so the outer ends lead the centre */}
        <path d="M504 12 Q504 2 516 2 L546 2 Q558 2 558 14 L558 34 L504 41 Z" fill={white} />
        <path d="M504 188 Q504 198 516 198 L546 198 Q558 198 558 186 L558 166 L504 159 Z" fill={white} />
        <path d="M504 41 L558 34 L558 25 L504 31 Z" fill={red} />
        <path d="M504 31 L558 25 L558 16 L504 21 Z" fill={blue} />
        <path d="M504 159 L558 166 L558 175 L504 169 Z" fill={red} />
        <path d="M504 169 L558 175 L558 184 L504 179 Z" fill={blue} />
        {/* white centre section the nose plugs into, then the red tip */}
        <path d="M504 90H558V110H504Z" fill={white} />
        <path d="M504 90H558M504 110H558" stroke={grey} strokeWidth="1" opacity="0.4" />
        <path d="M534 95L550 97Q553 100 550 103L534 105Z" fill={red} />
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
