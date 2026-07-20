# Shared UI primitives

This project has no component framework. All reusable primitives are plain HTML,
CSS classes, and JavaScript helpers in `web/index.html`.

## Buttons

```html
<button class="primary">开始处理</button>
<button class="secondary-btn">查看详情</button>
<button class="danger">重试失败任务</button>
```

## Form field

```html
<label for="field">字段名称</label>
<input id="field" type="text">
<span class="help-text">面向用户的中文帮助说明。</span>
```

## Card and badge

```html
<section class="card">
  <h3>区块标题</h3>
  <p class="help-text">区块说明</p>
</section>
<span class="badge badge-ok">已完成</span>
```

## Tabs

```html
<button class="nav-item active" data-tab="pipeline">批量处理</button>
<section class="tab-content active" id="tab-pipeline"></section>
```

The full implementations and states are defined in `web/index.html`.
