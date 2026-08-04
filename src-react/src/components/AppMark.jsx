/**
 * The app icon, drawn to the same geometry scripts/make_icons.py uses -- the
 * numbers below are that script's formulas evaluated at a 44-unit box, so the
 * mark on the sign-in screen and the one in the taskbar are one drawing.
 */
const ROWS = [
  [10.95, "#ff3b30"],
  [18.32, "#ff9500"],
  [25.68, "#007aff"],
  [33.05, "#34c759"],
];

const BAR_X = 12.19;
const BAR_W = 38.28 - 12.19;
const BAR_H = 2.55;

export default function AppMark({ size = 52 }) {
  return (
    <svg viewBox="0 0 44 44" width={size} height={size} aria-hidden="true">
      <defs>
        <linearGradient id="am-face" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="1" stopColor="#f0f0f4" />
        </linearGradient>
        {/* The card is white and is usually shown on white. Without a shadow it
            dissolves -- the same reason the generated icon carries one. */}
        <filter id="am-shade" x="-25%" y="-25%" width="150%" height="150%">
          <feDropShadow
            dx="0" dy="0.9" stdDeviation="1.1"
            floodColor="#000" floodOpacity="0.22"
          />
        </filter>
      </defs>
      <rect
        x="1.98" y="1.98" width="40.04" height="40.04" rx="9.01"
        fill="url(#am-face)" stroke="#c9c9d1" strokeWidth="0.8"
        filter="url(#am-shade)"
      />
      {ROWS.map(([cy, fill]) => (
        <g key={cy}>
          <circle cx="7.04" cy={cy} r="2.73" fill={fill} />
          <rect
            x={BAR_X} y={cy - BAR_H / 2} width={BAR_W} height={BAR_H}
            rx={BAR_H / 2} fill="#c4c4ca"
          />
        </g>
      ))}
    </svg>
  );
}
