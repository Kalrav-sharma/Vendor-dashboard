// Lets Transport delivery TAT calculator -- source: "Native - Commercials"
// Google Sheet, "Lets transport - PTL commercials" tab, "Delivery TAT"
// table (rows 51-65) + its "Zone Classifications" table, both added by
// Kalrav 2026-09-29.
//
// The TAT matrix gives a [min, max] day RANGE per origin-zone x
// dest-zone pair. Estimated delivery date uses the MAX of that range
// (Kalrav's explicit call, 2026-09-29) -- conservative, so a shipment
// isn't shown "on track" past a date it was never actually guaranteed by.
//
// CITY_TO_ZONE deliberately only lists cities the classification table
// names explicitly (plus obvious spelling variants -- Bangalore/Bengaluru,
// Gurgaon/Gurugram, Cochin/Kochi). Every zone also has "rest of <state>"
// buckets that this does NOT attempt to cover by inferring state
// membership -- same no-guessing discipline as the FM/MM/Packaging rate
// cards elsewhere in this project (flag as unmapped, never a proxy).
// Extend this table as new real origin/destination cities are observed.
export const CITY_TO_ZONE = {
  // East 1
  HOWRAH: "East 1", BHUBANESWAR: "East 1", RANCHI: "East 1", PATNA: "East 1",
  JAMSHEDPUR: "East 1", AURANGABAD: "East 1", DHANBAD: "East 1",
  // East 2 -- "rest of West Bengal/Bihar/Odisha/Jharkhand". Kolkata itself
  // isn't named in the classification table; Kalrav's explicit call
  // (2026-09-29) is East 2, not East 1 (Howrah's zone).
  KOLKATA: "East 2",
  // North East 1
  GUWAHATI: "North East 1",
  // North 1 -- NCR
  DELHI: "North 1", GURUGRAM: "North 1", GURGAON: "North 1",
  FARIDABAD: "North 1", GHAZIABAD: "North 1",
  // North 3
  GORAKHPUR: "North 3", AYODHYA: "North 3", BASTI: "North 3",
  MIRZAPUR: "North 3", BHADOHI: "North 3", MAU: "North 3",
  // South 1
  BANGALORE: "South 1", BENGALURU: "South 1", HYDERABAD: "South 1",
  // South 2
  CHENNAI: "South 2",
  // South 3
  COCHIN: "South 3", KOCHI: "South 3",
  // West 1
  MUMBAI: "West 1",
  // West 2 -- Ahmedabad is the one Gujarat city seen in real shipment
  // data so far; "rest of MP/Chattisgarh/Maharashtra/Goa/Gujarat" isn't
  // otherwise enumerated, per the no-guessing note above.
  AHMEDABAD: "West 2",
};

// [minDays, maxDays] by [origin zone][destination zone]. Transcribed
// directly from the sheet's Delivery TAT table -- symmetric by
// construction (checked against the source), so a lookup miss in one
// direction would be a transcription bug, not expected behavior.
const TAT_MATRIX = {
  "North 1": { "North 1": [1, 2], "North 2": [1, 3], "North 3": [3, 4], "West 1": [3, 4], "West 2": [4, 6], "South 1": [4, 5], "South 2": [5, 7], "South 3": [6, 7], "South 4": [8, 9], "East 1": [3, 4], "East 2": [4, 7], "North East 1": [7, 8], "North East 2": [8, 10] },
  "North 2": { "North 1": [1, 3], "North 2": [1, 3], "North 3": [3, 4], "West 1": [5, 7], "West 2": [7, 8], "South 1": [7, 8], "South 2": [8, 9], "South 3": [9, 10], "South 4": [10, 11], "East 1": [5, 6], "East 2": [7, 8], "North East 1": [8, 9], "North East 2": [9, 10] },
  "North 3": { "North 1": [3, 4], "North 2": [3, 4], "North 3": [3, 4], "West 1": [7, 8], "West 2": [8, 9], "South 1": [8, 9], "South 2": [9, 10], "South 3": [10, 11], "South 4": [11, 12], "East 1": [6, 7], "East 2": [7, 8], "North East 1": [9, 10], "North East 2": [11, 12] },
  "West 1": { "North 1": [3, 4], "North 2": [5, 7], "North 3": [7, 8], "West 1": [1, 2], "West 2": [1, 4], "South 1": [3, 4], "South 2": [5, 6], "South 3": [6, 7], "South 4": [7, 8], "East 1": [5, 7], "East 2": [7, 8], "North East 1": [8, 9], "North East 2": [9, 10] },
  "West 2": { "North 1": [4, 6], "North 2": [7, 8], "North 3": [8, 9], "West 1": [1, 4], "West 2": [2, 5], "South 1": [4, 5], "South 2": [5, 6], "South 3": [6, 7], "South 4": [7, 8], "East 1": [7, 8], "East 2": [8, 9], "North East 1": [9, 10], "North East 2": [10, 11] },
  "South 1": { "North 1": [4, 5], "North 2": [7, 8], "North 3": [8, 9], "West 1": [3, 4], "West 2": [4, 5], "South 1": [1, 2], "South 2": [2, 4], "South 3": [4, 5], "South 4": [5, 6], "East 1": [5, 6], "East 2": [6, 7], "North East 1": [8, 9], "North East 2": [9, 10] },
  "South 2": { "North 1": [5, 7], "North 2": [8, 9], "North 3": [9, 10], "West 1": [5, 6], "West 2": [5, 6], "South 1": [2, 4], "South 2": [3, 5], "South 3": [5, 6], "South 4": [6, 7], "East 1": [7, 8], "East 2": [8, 9], "North East 1": [9, 10], "North East 2": [10, 11] },
  "South 3": { "North 1": [6, 7], "North 2": [9, 10], "North 3": [10, 11], "West 1": [6, 7], "West 2": [6, 7], "South 1": [4, 5], "South 2": [5, 6], "South 3": [2, 4], "South 4": [3, 5], "East 1": [8, 9], "East 2": [9, 10], "North East 1": [10, 11], "North East 2": [11, 12] },
  "South 4": { "North 1": [8, 9], "North 2": [10, 11], "North 3": [11, 12], "West 1": [7, 8], "West 2": [7, 8], "South 1": [5, 6], "South 2": [6, 7], "South 3": [3, 5], "South 4": [3, 6], "East 1": [9, 10], "East 2": [10, 11], "North East 1": [11, 12], "North East 2": [12, 13] },
  "East 1": { "North 1": [3, 4], "North 2": [5, 6], "North 3": [6, 7], "West 1": [5, 7], "West 2": [7, 8], "South 1": [5, 6], "South 2": [7, 8], "South 3": [8, 9], "South 4": [9, 10], "East 1": [2, 4], "East 2": [3, 5], "North East 1": [5, 6], "North East 2": [7, 9] },
  "East 2": { "North 1": [4, 7], "North 2": [7, 8], "North 3": [7, 8], "West 1": [7, 8], "West 2": [8, 9], "South 1": [6, 7], "South 2": [8, 9], "South 3": [9, 10], "South 4": [10, 11], "East 1": [3, 5], "East 2": [4, 6], "North East 1": [6, 7], "North East 2": [8, 10] },
  "North East 1": { "North 1": [7, 8], "North 2": [8, 9], "North 3": [9, 10], "West 1": [8, 9], "West 2": [9, 10], "South 1": [8, 9], "South 2": [9, 10], "South 3": [10, 11], "South 4": [11, 12], "East 1": [5, 6], "East 2": [6, 7], "North East 1": [1, 3], "North East 2": [2, 5] },
  "North East 2": { "North 1": [8, 10], "North 2": [9, 10], "North 3": [11, 12], "West 1": [9, 10], "West 2": [10, 11], "South 1": [9, 10], "South 2": [10, 11], "South 3": [11, 12], "South 4": [12, 13], "East 1": [7, 9], "East 2": [8, 10], "North East 1": [2, 5], "North East 2": [2, 4] },
};

function zoneOf(city) {
  if (!city) return null;
  return CITY_TO_ZONE[String(city).trim().toUpperCase()] || null;
}

// Returns { minDate, maxDate } (ISO date strings) or null if either city
// isn't in CITY_TO_ZONE -- never guesses a zone for an unmapped city.
export function letsTransportTatRange(originCity, destCity) {
  const originZone = zoneOf(originCity);
  const destZone = zoneOf(destCity);
  if (!originZone || !destZone) return null;
  const range = TAT_MATRIX[originZone]?.[destZone];
  if (!range) return null;
  return { minDays: range[0], maxDays: range[1] };
}

function addDays(dateStr, days) {
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return null;
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

// dispatchedDate: a date-only string (po_item_shipments.dispatched_date).
// Returns an ISO date string (the MAX-of-range estimate) or null if the
// route can't be resolved to a known zone pair.
export function letsTransportEstimatedDeliveryDate(dispatchedDate, originCity, destCity) {
  if (!dispatchedDate) return null;
  const range = letsTransportTatRange(originCity, destCity);
  if (!range) return null;
  return addDays(dispatchedDate, range.maxDays);
}
