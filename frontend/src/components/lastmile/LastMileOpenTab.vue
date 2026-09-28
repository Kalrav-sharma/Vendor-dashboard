<script setup>
// Last Mile Tracking > Open Shipments -- KPI strip still reflects the full
// "not complete, not RTO" open population (live, backlog, no_dispatch_date),
// whether or not it currently trips an alert. The per-AWB table that used to
// sit below it has been replaced with the Carrier Performance view.
import { computed } from "vue";
import { useLastMileData } from "../../composables/useLastMileData.js";
import SummaryKpis from "../SummaryKpis.vue";
import LastMileCarrierTab from "./LastMileCarrierTab.vue";

const { run, openShipments, loadError } = useLastMileData();

const kpiTiles = computed(() => {
  const all = openShipments.value;
  const alerted = all.filter(s => s.has_alert).length;
  const overdue = all.filter(s => s.days_overdue > 0).length;
  const unpolled = all.filter(s => s.status === "UNKNOWN").length;
  return [
    { label: "Total open shipments", value: all.length },
    { label: "Currently alerted", value: alerted, cls: alerted > 0 ? "critical" : "good" },
    { label: "Overdue", value: overdue, cls: overdue > 0 ? "critical" : "" },
    { label: "Not yet polled this run", value: unpolled },
  ];
});
</script>

<template>
  <SummaryKpis v-if="run" :tiles="kpiTiles" />

  <div v-if="loadError" class="form-error">{{ loadError }}</div>
  <div v-else-if="!run" class="empty-state" style="padding: 40px 0;">
    No sync has run yet, so there is nothing to show -- these are not measured zeros.
  </div>

  <template v-else>
    <LastMileCarrierTab />
  </template>
</template>
