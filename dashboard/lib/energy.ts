// Shared presentation helpers: unknown readings/prices never become zero readings.
export type EnergySlot = {
  start: string; time: string; zone: string; state: string;
  kwh: number | null; cost: number | null; rate: number | null;
  off_peak: boolean; tariff_label: string | null;
};
export type EnergyDay = {
  date: string; slots: EnergySlot[]; standing: number | null;
  estimated_cost: number | null; warnings: string[];
};

export function breakdown(day: EnergyDay, metric: "usage" | "cost") {
  const parts = { standard: 0, offPeak: 0, other: 0 };
  for (const slot of day.slots) {
    const value = metric === "usage" ? slot.kwh : slot.cost;
    if (value === null) continue;
    if (slot.rate === null) parts.other += value;
    else if (slot.off_peak) parts.offPeak += value;
    else parts.standard += value;
  }
  if (metric === "cost") parts.other += day.standing ?? 0;
  return parts;
}

export function ukDate(instant: string) {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(instant));
  const get = (type: string) => parts.find(p => p.type === type)?.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

// Protect text cells from spreadsheet formula execution, and quote RFC 4180 cells.
function cell(value: string | number | null) {
  if (value === null) return "";
  let text = String(value);
  if (typeof value === "string" && /^[\s]*[=+@-]/.test(text)) text = "'" + text;
  return `"${text.replaceAll('"', '""')}"`;
}

export function readingsCsv(days: EnergyDay[]) {
  const rows: (string | number | null)[][] = [[
    "interval_start_utc", "uk_date", "uk_time", "uk_timezone", "usage_kwh",
    "unit_rate_p_per_kwh", "rate_period", "estimated_usage_cost_gbp",
    "standing_charge_gbp_once_per_day", "reading_status", "tariff_name", "estimate_notes",
  ]];
  for (const day of days) {
    let standingWritten = false;
    for (const slot of day.slots) {
      if (slot.state === "future") continue;
      rows.push([slot.start, day.date, slot.time, slot.zone, slot.kwh, slot.rate,
        slot.rate === null ? "unknown" : slot.rate === 0 ? "free" : slot.off_peak ? "off_peak" : "standard",
        slot.cost, standingWritten ? null : day.standing,
        slot.state === "missing" ? "missing_reading" : slot.cost === null ? "missing_tariff" : "received",
        slot.tariff_label, day.warnings.join("; ")]);
      standingWritten = true;
    }
  }
  return rows.map(row => row.map(cell).join(",")).join("\r\n") + "\r\n";
}
