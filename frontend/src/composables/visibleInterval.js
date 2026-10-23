// setInterval that skips its tick while the browser tab is hidden (background tab, minimised
// window), so a portal left open overnight doesn't keep re-fetching every table. Returns the
// interval id, so existing clearInterval(id) cleanup keeps working. A tick missed while hidden is
// made up by the next tick after the tab is shown again.
export function visibleInterval(fn, ms) {
  return setInterval(() => { if (!document.hidden) fn(); }, ms);
}
