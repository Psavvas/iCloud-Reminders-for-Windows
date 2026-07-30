/** The app icon, drawn at the proportions scripts/make_icons.py uses. */
export default function AppMark({ size = 52 }) {
  const rows = [
    [13.2, "#ff3b30"],
    [19.1, "#ff9500"],
    [24.9, "#007aff"],
    [30.8, "#34c759"],
  ];
  return (
    <svg viewBox="0 0 44 44" width={size} height={size} aria-hidden="true">
      <defs>
        <linearGradient id="am-face" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="1" stopColor="#eeeef2" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="40" height="40" rx="9.9"
            fill="url(#am-face)" stroke="#d6d6dc" strokeWidth="1" />
      {rows.map(([cy, fill]) => (
        <g key={cy}>
          <circle cx="12.8" cy={cy} r="2.7" fill={fill} />
          <rect x="18.5" y={cy - 1.3} width="15.8" height="2.6" rx="1.3" fill="#c7c7cc" />
        </g>
      ))}
    </svg>
  );
}
