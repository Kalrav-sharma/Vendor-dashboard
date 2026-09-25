<script setup>
// Thin Chart.js wrapper for the SLA section. Series colours are CSS custom properties
// (--sla-s1 … in sla.css), read at draw time, so light and dark mode each get their own
// validated steps. The chart is rebuilt when the OS theme flips and whenever its inputs
// change, e.g. on the Weekly/Monthly toggle.
//
// An in-progress period (the current week or month) is drawn as a dashed final segment
// with a hollow point, so it never reads as a finished drop or spike.
import { ref, watch, onMounted, onBeforeUnmount } from "vue";
import {
  Chart, LineController, BarController, LineElement, BarElement, PointElement,
  LinearScale, CategoryScale, Tooltip, Legend, Filler,
} from "chart.js";

Chart.register(LineController, BarController, LineElement, BarElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend, Filler);

const props = defineProps({
  type: { type: String, default: "line" },            // "line" | "bar"
  labels: { type: Array, required: true },            // x labels (short)
  tooltipTitles: { type: Array, default: null },      // long x labels for the tooltip
  datasets: { type: Array, required: true },          // [{ label, data, color: "--sla-s1", width? }]
  format: { type: String, default: "pct" },           // "pct" (0-1 ratios) | "days"
  stacked: { type: Boolean, default: false },
  partialLast: { type: Boolean, default: false },
  height: { type: Number, default: 300 },
  yMin: { type: Number, default: null },
  yMax: { type: Number, default: null },
});

const canvas = ref(null);
let chart = null;
let mq = null;

const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  || getComputedStyle(canvas.value).getPropertyValue(name).trim();
const fmt = v => (v == null ? "–" : props.format === "pct" ? `${(v * 100).toFixed(1)}%` : `${v.toFixed(2)}d`);

function draw() {
  if (!canvas.value) return;
  chart?.destroy();
  const ink = cssVar("--ink"), muted = cssVar("--muted"), line = cssVar("--line"), surface = cssVar("--surface");
  const n = props.labels.length;
  const colorOf = ds => cssVar(ds.color) || ds.color;

  chart = new Chart(canvas.value, {
    type: props.type,
    data: {
      labels: props.labels,
      datasets: props.datasets.map(ds => {
        const c = colorOf(ds);
        if (props.type === "bar") {
          return {
            label: ds.label, data: ds.data, backgroundColor: c, borderColor: surface, borderWidth: { top: 2 },
            borderRadius: 4, borderSkipped: "bottom", maxBarThickness: 44, stack: props.stacked ? "s" : undefined,
          };
        }
        return {
          label: ds.label, data: ds.data, borderColor: c, backgroundColor: c,
          borderWidth: ds.width || 2, tension: 0.3, spanGaps: true,
          pointRadius: ctx => (ctx.dataIndex === n - 1 ? 4 : 2.5), pointHoverRadius: 6,
          pointBackgroundColor: ctx => (props.partialLast && ctx.dataIndex === n - 1 ? surface : c),
          pointBorderColor: c, pointBorderWidth: 2,
          segment: { borderDash: ctx => (props.partialLast && ctx.p1DataIndex === n - 1 ? [5, 4] : undefined) },
        };
      }),
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 350 },
      interaction: { mode: "index", intersect: false },
      layout: { padding: { top: 6, right: 8 } },
      plugins: {
        legend: {
          position: "bottom", align: "start",
          labels: { color: ink, usePointStyle: true, pointStyle: props.type === "bar" ? "rectRounded" : "line", boxWidth: 22, padding: 16, font: { family: "Open Sauce Sans", size: 12 } },
        },
        tooltip: {
          backgroundColor: surface, titleColor: ink, bodyColor: ink, borderColor: line, borderWidth: 1,
          padding: 10, boxPadding: 4, usePointStyle: true,
          titleFont: { family: "Open Sauce Sans", weight: "600" }, bodyFont: { family: "IBM Plex Mono", size: 12 },
          callbacks: {
            title: items => {
              const i = items[0].dataIndex;
              const t = props.tooltipTitles ? props.tooltipTitles[i] : props.labels[i];
              return props.partialLast && i === n - 1 ? `${t} (in progress)` : t;
            },
            label: item => ` ${item.dataset.label}: ${fmt(item.raw)}`,
          },
        },
      },
      scales: {
        x: {
          stacked: props.stacked, grid: { display: false }, border: { color: line },
          ticks: { color: muted, font: { family: "IBM Plex Mono", size: 11 }, maxRotation: 0, autoSkipPadding: 12 },
        },
        y: {
          stacked: props.stacked, min: props.yMin ?? undefined, max: props.yMax ?? undefined,
          grid: { color: line, drawTicks: false }, border: { display: false },
          ticks: { color: muted, padding: 8, font: { family: "IBM Plex Mono", size: 11 }, maxTicksLimit: 6, callback: v => (props.format === "pct" ? `${Math.round(v * 100)}%` : `${v}d`) },
        },
      },
    },
  });
}

watch(() => [props.labels, props.datasets, props.type, props.stacked, props.partialLast], draw, { deep: true });
onMounted(() => {
  draw();
  mq = window.matchMedia("(prefers-color-scheme: dark)");
  mq.addEventListener("change", draw);
});
onBeforeUnmount(() => { chart?.destroy(); mq?.removeEventListener("change", draw); });
</script>

<template>
  <div class="sla-chart" :style="{ height: height + 'px' }"><canvas ref="canvas" role="img" :aria-label="datasets.map(d => d.label).join(', ')"></canvas></div>
</template>
