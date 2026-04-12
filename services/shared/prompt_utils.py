"""Shared prompt utilities used across stock and ETF analyzers."""


def visual_output_instructions() -> str:
    """Instructions for emitting inline SVG/HTML visuals via fenced code blocks."""
    return """
            **Visual Output — draw charts when showing financial trends or comparisons:**

            When your answer involves revenue trends, earnings growth, margin changes,
            segment breakdowns, or multi-quarter/multi-year comparisons, DRAW A CHART
            using a fenced HTML block with Chart.js.

            **Palette (multi-series / segments):** `#818cf8`, `#34d399`, `#fb923c`,
            `#f472b6`, `#38bdf8`, `#a78bfa`.

            **Unified styling (every chart type):**
            - **Shell:** `<html>`, Inter from Google Fonts + `chart.js` CDN; `body` —
              `margin:0;padding:8px;background:transparent;font-family:Inter,system-ui,sans-serif`;
              `<canvas id="chart" style="width:100%;max-height:400px">`.
            - **Defaults:** `Chart.defaults.font.family = "Inter, system-ui, sans-serif"`;
              `Chart.defaults.font.size = 12`; `Chart.defaults.color = "#999999"`.
            - **Common `options`:** `responsive: true`, `maintainAspectRatio: true`,
              `layout: { padding: { top: 8, right: 8, bottom: 8, left: 8 } }`.
            - **`plugins.title`:** `display: true` when useful, `text` = short label,
              `color: '#aaaaaa'`, `font: { size: 15, weight: '600' }`,
              `padding: { bottom: 14, top: 4 }`.
            - **`plugins.legend.labels`:** `color: '#aaaaaa'`, `usePointStyle: true`,
              `pointStyle: 'circle'` (line/pie) or `'rectRounded'` (bar), `padding: 16`,
              `font: { size: 12 }`.
            - **`plugins.tooltip`:** `backgroundColor: 'rgba(17, 24, 39, 0.92)'`,
              `titleColor: '#f9fafb'`, `bodyColor: '#e5e7eb'`, `padding: 12`,
              `cornerRadius: 8`, `borderWidth: 0`.
            - **Cartesian only (bar, line):** `scales.x` — `grid: { display: false }`,
              `border: { display: false }`, `ticks: { color: '#999999' }`.
              `scales.y` — `grid: { color: 'rgba(136, 136, 136, 0.15)', drawTicks: false }`,
              `border: { display: false }`, `ticks: { color: '#999999' }`,
              `beginAtZero: false` unless zero baseline is meaningful.
            - **Pie / doughnut:** use the same `plugins` (title, legend, tooltip) and
              common `options`; **omit `scales`** (not used).

            **Reference implementation (line + gradient — copy shell and `options`;
            swap `type`/`data` using the recipes below):**

            ```html
            <html>
            <head>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            </head>
            <body style="margin:0;padding:8px;background:transparent;font-family:Inter,system-ui,sans-serif">
            <canvas id="chart" style="width:100%;max-height:400px"></canvas>
            <script>
            Chart.defaults.font.family = "Inter, system-ui, sans-serif";
            Chart.defaults.font.size = 12;
            Chart.defaults.color = "#999999";
            (function() {
              var canvas = document.getElementById('chart');
              var ctx = canvas.getContext('2d');
              var h = canvas.offsetHeight || 400;
              var g = ctx.createLinearGradient(0, 0, 0, h);
              g.addColorStop(0, 'rgba(129, 140, 248, 0.35)');
              g.addColorStop(1, 'rgba(129, 140, 248, 0)');
              new Chart(canvas, {
                type: 'line',
                data: {
                  labels: ['Q1 2024', 'Q2 2024', 'Q3 2024', 'Q4 2024'],
                  datasets: [{
                    label: 'Gross margin (%)',
                    data: [54, 52, 55, 56],
                    borderColor: '#818cf8',
                    backgroundColor: g,
                    fill: true,
                    tension: 0.35,
                    borderWidth: 2.5,
                    pointRadius: 4,
                    pointHoverRadius: 6,
                    pointBackgroundColor: '#818cf8',
                    pointBorderColor: '#ffffff',
                    pointBorderWidth: 2
                  }]
                },
                options: {
                  responsive: true,
                  maintainAspectRatio: true,
                  interaction: { intersect: false, mode: 'index' },
                  layout: { padding: { top: 8, right: 8, bottom: 8, left: 8 } },
                  plugins: {
                    title: {
                      display: true,
                      text: 'Margin trend',
                      color: '#aaaaaa',
                      font: { size: 15, weight: '600' },
                      padding: { bottom: 14, top: 4 }
                    },
                    legend: {
                      labels: {
                        color: '#aaaaaa',
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 16,
                        font: { size: 12 }
                      }
                    },
                    tooltip: {
                      backgroundColor: 'rgba(17, 24, 39, 0.92)',
                      titleColor: '#f9fafb',
                      bodyColor: '#e5e7eb',
                      padding: 12,
                      cornerRadius: 8,
                      borderWidth: 0
                    }
                  },
                  scales: {
                    x: {
                      grid: { display: false },
                      border: { display: false },
                      ticks: { color: '#999999' }
                    },
                    y: {
                      beginAtZero: false,
                      grid: { color: 'rgba(136, 136, 136, 0.15)', drawTicks: false },
                      border: { display: false },
                      ticks: { color: '#999999' }
                    }
                  }
                }
              });
            })();
            </script>
            </body>
            </html>
            ```

            **Type recipes (same shell, defaults, and `plugins`/`scales` as reference;
            only change `type`, `data`, and line-specific bits):**
            - **Bar:** `type: 'bar'`. Dataset: solid `backgroundColor` from palette (string
              or per-bar array), `borderRadius: 6`, `borderSkipped: false`. Omit
              `interaction`. Legend `pointStyle: 'rectRounded'`. No gradient.
            - **Line (multi-series):** one `createLinearGradient` per series using that
              series' RGBA at stops `0 → 0.35` and `1 → 0`, or `fill: false` and
              `borderWidth: 2.5` if fills overlap badly.
            - **Pie / doughnut:** `type: 'pie'` or `'doughnut'`. One dataset:
              `data: [...]`, `backgroundColor: ['#818cf8','#34d399', ...]` (one color per
              slice). Keep `plugins`; **remove the entire `scales` key** from `options`.

            Rules:
            - Place the chart INLINE right after the paragraph that discusses the data.
            - Use actual financial figures from your analysis — never placeholder data.
            - Follow the unified styling so charts read well on light and dark backgrounds.
            - For simple diagrams without data (flowcharts, structures), use ```svg blocks
              with currentColor for text and strokes.
            - Do NOT draw a chart for simple factual answers ("What was Apple's revenue?").
              Only when the visual genuinely shows a trend, comparison, or breakdown.
        """
