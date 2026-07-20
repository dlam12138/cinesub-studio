# Theme

Source: `web/index.html`.

```css
:root {
  color-scheme: dark;
  --bg: #090d14;
  --panel: #111823;
  --panel-strong: #151f2d;
  --panel-soft: #1b2736;
  --text: #edf2f7;
  --muted: #9aa8ba;
  --line: rgba(170, 188, 212, .18);
  --primary: #63b3ff;
  --primary-dark: #3187d8;
  --danger: #ff6b6b;
  --ok: #49d18d;
  --warn: #f7b955;
  --ink: #f8fbff;
  --soft: #0e1520;
  --radius: 8px;
  --shadow: 0 20px 48px rgba(0, 0, 0, .28);
}
```

- Display/body font: local system Chinese sans-serif, led by Microsoft YaHei.
- Utility/data font: Segoe UI and system monospace where paths/logs require it.
- No remote fonts, CDN, npm bundle, or front-end framework.
- Visual identity: graphite workstation surfaces, blue interaction emphasis, mint
  success/progress state, compact technical labels used only where meaningful.
- Accessibility: visible focus, 44px primary targets, responsive stacking, and
  reduced-motion support.
