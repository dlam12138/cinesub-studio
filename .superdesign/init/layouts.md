# Shared layouts

## Application shell

- Source: `web/index.html`
- Structure: fixed desktop sidebar, top status header, and scrollable main workspace.
- Desktop: sidebar + content; the batch workspace uses task content plus a configuration rail.
- Narrow viewport: sidebar becomes a horizontal/navigation region and multi-column content stacks.

```html
<div class="app-shell">
  <aside class="sidebar" aria-label="主导航">...</aside>
  <div class="app-main">
    <header class="topbar">...</header>
    <main class="workspace">
      <section class="tab-content active" id="tab-pipeline">...</section>
      <section class="tab-content" id="tab-transcribe">...</section>
    </main>
  </div>
</div>
```

All shell markup, responsive branches, and styles are colocated in
`web/index.html`.
