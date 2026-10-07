"""Independent dashboard calculations. Missing readings and prices stay unknown."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from .tariffs import tariff_at, usage_cost, standing_cost, interval_rate
from .timeutils import UK, UTC, HALF_HOUR, date_bounds, period_bounds, iso

def decimal_float(value):
    return float(value.quantize(Decimal('0.000001')))

def day_summary(day, readings, tariffs, now):
    start, end = date_bounds(day)
    elapsed_end = min(end, int(now) // HALF_HOUR * HALF_HOUR)
    usage, usage_pounds, off_peak_usage = Decimal(0), Decimal(0), Decimal(0)
    missing = unpriced = observed = priced = 0
    slots, warnings = [], set()
    for instant in range(start, end, HALF_HOUR):
        elapsed = instant < elapsed_end
        value = readings.get(instant) if elapsed else None
        tariff = tariff_at(tariffs, instant)
        rate, off_peak = interval_rate(tariff, instant) if tariff else (None, False)
        cost = None
        if elapsed:
            if value is None:
                missing += 1
            else:
                observed += 1
                energy = Decimal(value)
                usage += energy
                if tariff:
                    cost, rate, off_peak = usage_cost(tariff, instant, energy)
                    usage_pounds += cost
                    priced += 1
                    if off_peak:
                        off_peak_usage += energy
                    if tariff.get('warning'):
                        warnings.add(tariff['warning'])
                else:
                    unpriced += 1
        moment = datetime.fromtimestamp(instant, UK)
        slots.append({'start': iso(instant), 'time': moment.strftime('%H:%M'), 'zone': moment.tzname(),
            'state': 'future' if not elapsed else 'missing' if value is None else 'observed',
            'kwh': float(value) if value is not None else None,
            'cost': decimal_float(cost) if cost is not None else None,
            'rate': float(rate) if rate is not None else None, 'off_peak': off_peak,
            'tariff_label': tariff['label'] if tariff else None})
    standing_tariff = tariff_at(tariffs, start)
    standing = standing_cost(standing_tariff) if standing_tariff and start <= now else None
    if standing_tariff and standing_tariff.get('warning') and start <= now:
        warnings.add(standing_tariff['warning'])
    expected = max(0, (elapsed_end - start) // HALF_HOUR)
    known_total = usage_pounds + (standing or Decimal(0))
    fully_priced = expected > 0 and missing == 0 and unpriced == 0 and standing is not None
    return {'date': day.isoformat() if isinstance(day, date) else day, 'kwh': decimal_float(usage),
        'usage_cost': decimal_float(usage_pounds), 'standing': decimal_float(standing) if standing is not None else None,
        'known_cost': decimal_float(known_total), 'estimated_cost': decimal_float(known_total) if fully_priced else None,
        'expected': expected, 'full_day_expected': (end - start) // HALF_HOUR, 'received': observed,
        'missing': missing, 'unpriced': unpriced, 'priced': priced,
        'off_peak_kwh': decimal_float(off_peak_usage), 'in_progress': start <= now < end,
        'warnings': sorted(warnings), 'slots': slots}

def totals(days):
    relevant = [d for d in days if d['expected'] > 0]
    kwh = sum(d['kwh'] for d in relevant)
    priced_kwh = sum(s['kwh'] for d in relevant for s in d['slots'] if s['kwh'] is not None and s['cost'] is not None)
    usage_cost_total = sum(d['usage_cost'] for d in relevant)
    all_priced = bool(relevant) and all(d['estimated_cost'] is not None for d in relevant)
    return {'kwh': round(kwh, 6), 'known_cost': round(sum(d['known_cost'] for d in relevant), 6),
        'estimated_cost': round(sum(d['known_cost'] for d in relevant), 6) if all_priced else None,
        'standing': round(sum(d['standing'] or 0 for d in relevant), 6),
        'expected': sum(d['expected'] for d in relevant), 'received': sum(d['received'] for d in relevant),
        'missing': sum(d['missing'] for d in relevant), 'unpriced': sum(d['unpriced'] for d in relevant),
        'priced': sum(d['priced'] for d in relevant),
        'average_unit_rate': round(usage_cost_total * 100 / priced_kwh, 4) if priced_kwh else None,
        'off_peak_share': round(sum(d['off_peak_kwh'] for d in relevant) / kwh * 100, 2) if kwh else None,
        'warnings': sorted({w for d in relevant for w in d['warnings']})}

def dashboard(store, meter_id, view, anchor, now, until=None):
    first, last, previous_first = period_bounds(view, anchor, until)
    # Compare completed periods in full. Current week/month use matching elapsed
    # local days and wall-clock time, so partial periods don't imply false savings.
    today = datetime.fromtimestamp(now, UK).date()
    if view == 'custom' and last > today + timedelta(days=1):
        raise ValueError('Custom periods cannot end after today.')
    comparison_last = first
    previous_cutoff = now
    if first <= today < last:
        elapsed_days = (today - first).days
        previous_today = previous_first + timedelta(days=elapsed_days)
        if previous_today >= first:
            previous_today = first - timedelta(days=1)
        local_now = datetime.fromtimestamp(now, UK)
        previous_cutoff = int(datetime.combine(previous_today, local_now.timetz().replace(tzinfo=None), UK).timestamp())
        comparison_last = previous_today + timedelta(days=1)
    start, end = date_bounds(previous_first)[0], date_bounds(last)[0]
    rows = store.rows('SELECT start,kwh FROM readings WHERE meter_id=? AND start>=? AND start<?', (meter_id, start, end))
    readings = {r['start']: r['kwh'] for r in rows}
    tariffs = store.tariffs(meter_id)
    def make_days(a, b, cutoff):
        result = []
        while a < b:
            result.append(day_summary(a, readings, tariffs, cutoff))
            a += timedelta(days=1)
        return result
    days = make_days(first, last, now)
    previous_days = make_days(previous_first, comparison_last, previous_cutoff)
    current, previous = totals(days), totals(previous_days)
    # Percent changes are shown only when both windows have complete readings.
    comparable = current['expected'] > 0 and previous['expected'] > 0 and not current['missing'] and not previous['missing']
    change = ((current['kwh'] - previous['kwh']) / previous['kwh'] * 100) if comparable and previous['kwh'] else None
    cost_change = ((current['estimated_cost'] - previous['estimated_cost']) / previous['estimated_cost'] * 100) if comparable and current['estimated_cost'] is not None and previous['estimated_cost'] else None
    latest = store.rows('SELECT MAX(start) AS latest,MIN(start) AS earliest,COUNT(*) AS count FROM readings WHERE meter_id=?', (meter_id,))[0]
    latest_instant = latest['latest']
    stale = latest_instant is None or now - (latest_instant + HALF_HOUR) > 48 * 3600
    return {'view': view, 'from': first.isoformat(), 'to': last.isoformat(), 'anchor': anchor,
        'timezone': 'Europe/London', 'now': iso(now), 'summary': current,
        'comparison': {'from': previous_first.isoformat(), 'to': comparison_last.isoformat(),
            'matched_elapsed_period': first <= today < last, 'summary': previous,
            'usage_change_percent': round(change, 2) if change is not None else None,
            'cost_change_percent': round(cost_change, 2) if cost_change is not None else None},
        'days': days, 'latest_reading': iso(latest_instant) if latest_instant is not None else None,
        'earliest_reading': iso(latest['earliest']) if latest['earliest'] is not None else None,
        'stored_readings': latest['count'], 'stale': stale}
