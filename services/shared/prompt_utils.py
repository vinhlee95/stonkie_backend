"""Shared prompt utilities used across stock and ETF analyzers."""


def visual_output_instructions() -> str:
    """Instructions for emitting inline SVG/HTML visuals via fenced code blocks."""
    return """
            **Visual Output — draw charts when showing financial trends or comparisons:**

            When your answer involves revenue trends, earnings growth, margin changes,
            segment breakdowns, or multi-quarter/multi-year comparisons, DRAW A CHART
            using a fenced HTML block with Chart.js:

            ```html
            <html>
            <head><script src="https://cdn.jsdelivr.net/npm/chart.js"></script></head>
            <body style="margin:0;background:transparent">
            <canvas id="chart" style="width:100%;max-height:400px"></canvas>
            <script>
            new Chart(document.getElementById('chart'), {
              type: 'bar',
              data: {
                labels: ['Q1 2024', 'Q2 2024', 'Q3 2024', 'Q4 2024'],
                datasets: [{
                  label: 'Revenue ($B)',
                  data: [90.8, 85.8, 94.9, 124.3],
                  backgroundColor: '#6366f1'
                }]
              },
              options: {
                responsive: true,
                plugins: { legend: { labels: { color: '#888' } } },
                scales: {
                  x: { ticks: { color: '#888' } },
                  y: { ticks: { color: '#888' }, beginAtZero: false }
                }
              }
            });
            </script>
            </body>
            </html>
            ```

            Rules:
            - Place the chart INLINE right after the paragraph that discusses the data.
            - Use actual financial figures from your analysis — never placeholder data.
            - Pick the right chart type: line for trends over time, bar for comparisons,
              pie/doughnut for segment breakdowns.
            - Use gray (#888) for labels/ticks so it works on both light and dark backgrounds.
            - For simple diagrams without data (flowcharts, structures), use ```svg blocks
              with currentColor for text and strokes.
            - Do NOT draw a chart for simple factual answers ("What was Apple's revenue?").
              Only when the visual genuinely shows a trend, comparison, or breakdown.
        """
