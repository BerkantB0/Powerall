import { test } from 'node:test';
import assert from 'node:assert/strict';
import { breakdown, readingsCsv, ukDate } from './energy.ts';
const slot = (changes = {}) => ({ start: '2026-10-25T00:00:00Z', time: '01:00', zone: 'BST', state: 'observed', kwh: 1, cost: .3, rate: 30, off_peak: false, tariff_label: 'Home', ...changes });
const day = (slots) => ({ date: '2026-10-25', slots, standing: .5, estimated_cost: null, warnings: [] });
test('stacked bars separate standard, off-peak and unknown use', () => {
  const d = day([slot(), slot({ kwh: 2, cost: .2, rate: 10, off_peak: true }), slot({ kwh: 3, cost: null, rate: null }), slot({ state: 'missing', kwh: null, cost: null })]);
  assert.deepEqual(breakdown(d, 'usage'), { standard: 1, offPeak: 2, other: 3 });
  assert.deepEqual(breakdown(d, 'cost'), { standard: .3, offPeak: .2, other: .5 });
});
test('CSV preserves repeated clock times, missing and zero readings, writes standing once, and skips future slots', () => {
  const csv = readingsCsv([day([slot({ kwh: 0, cost: 0 }), slot({ start: '2026-10-25T01:00:00Z', zone: 'GMT', state: 'missing', kwh: null, cost: null }), slot({ state: 'future', kwh: null, cost: null })])]);
  const rows = csv.trim().split('\r\n');
  assert.equal(rows.length, 3);
  assert.match(rows[1], /"BST","0","30"/);
  assert.match(rows[2], /2026-10-25T01:00:00Z.*"GMT",,/);
  assert.equal(csv.split('"0.5"').length - 1, 1);
  assert.match(rows[2], /missing_reading/);
});
test('CSV quotes labels and neutralizes spreadsheet formulas', () => {
  const csv = readingsCsv([day([slot({ tariff_label: ' =HYPERLINK("bad")' }), slot({ tariff_label: 'Name, with "quotes"' })])]);
  assert.ok(csv.includes('"\' =HYPERLINK(""bad"")"'));
  assert.ok(csv.includes('"Name, with ""quotes"""'));
});
test('edit dates use UK midnight rather than the previous UTC date in summer', () => {
  assert.equal(ukDate('2026-06-30T23:00:00Z'), '2026-07-01');
  assert.equal(ukDate('2026-01-01T00:00:00Z'), '2026-01-01');
});

test('free electricity stays priced at zero and exports as free', () => {
  const d = day([slot({ kwh: 2, rate: 0, cost: 0, off_peak: true })]);
  assert.deepEqual(breakdown(d, 'usage'), { standard: 0, offPeak: 2, other: 0 });
  assert.deepEqual(breakdown(d, 'cost'), { standard: 0, offPeak: 0, other: .5 });
  assert.match(readingsCsv([d]), /"2","0","free","0","0.5","received"/);
});
