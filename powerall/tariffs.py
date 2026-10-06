"""Provider-independent tariff validation, selection and decimal costing."""
import re
import uuid
from decimal import Decimal, InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo
from .timeutils import UTC, HALF_HOUR, local_midnight

class TariffError(ValueError):
    pass

def number(value, label, maximum=10000):
    try:
        value = Decimal(str(value))
        if not value.is_finite() or value < 0 or value > maximum:
            raise ValueError()
        return str(value)
    except (InvalidOperation, ValueError, TypeError):
        raise TariffError(f'{label} must be a finite number between 0 and {maximum}.')

def validate_windows(windows):
    if not isinstance(windows, list) or len(windows) > 12:
        raise TariffError('Enter at most twelve time-of-use windows.')
    result, occupied = [], set()
    for window in windows:
        if not isinstance(window, dict):
            raise TariffError('Invalid time-of-use window.')
        for key in ('start', 'end'):
            if not re.fullmatch(r'(?:[01]\d|2[0-3]):(?:00|30)', str(window.get(key, ''))):
                raise TariffError('Tariff windows must use half-hour boundaries in HH:MM format.')
        start, end = minutes(window['start']), minutes(window['end'])
        if start == end:
            raise TariffError('A tariff window must have different start and end times.')
        slots = {m for m in range(0, 1440, 30) if contains(m, start, end)}
        if occupied & slots:
            raise TariffError('Tariff windows cannot overlap.')
        occupied |= slots
        result.append({'start': window['start'], 'end': window['end'], 'rate': number(window.get('rate'), 'Window rate')})
    return result

def minutes(value):
    hours, mins = map(int, value.split(':'))
    return hours * 60 + mins

def contains(value, start, end):
    return start <= value < end if start < end else value >= start or value < end

def manual_tariff(payload, meter):
    if not isinstance(payload, dict):
        raise TariffError('Invalid tariff.')
    label = str(payload.get('label', '')).strip()
    if not label or len(label) > 120:
        raise TariffError('Enter a tariff name of up to 120 characters.')
    try:
        start = local_midnight(payload['from'])
        end = local_midnight(payload['to']) if payload.get('to') else None
    except (KeyError, ValueError, TypeError):
        raise TariffError('Enter valid effective dates.')
    if end is not None and end <= start:
        raise TariffError('End date must be after start date; the end date is excluded.')
    clock, vat = payload.get('clock', 'Europe/London'), payload.get('vat', 'included')
    if clock not in ('Europe/London', 'UTC') or vat not in ('included', 'excluded'):
        raise TariffError('Choose a supported tariff clock and VAT treatment.')
    return {'id': str(uuid.uuid4()), 'meter_id': meter, 'source': 'manual', 'label': label,
        'effective_from': start, 'effective_to': end,
        'rate': number(payload.get('rate'), 'Standard rate'),
        'standing': number(payload.get('standing'), 'Standing charge'),
        'windows': validate_windows(payload.get('windows', [])), 'clock': clock, 'vat': vat, 'warning': None}

def tariff_at(tariffs, instant):
    matches = [t for t in tariffs if t['effective_from'] <= instant and (t['effective_to'] is None or instant < t['effective_to'])]
    return max(matches, key=lambda t: (t['source'] == 'manual', t['effective_from'])) if matches else None

def interval_rate(tariff, instant):
    moment = datetime.fromtimestamp(instant, ZoneInfo(tariff['clock']))
    value = moment.hour * 60 + moment.minute
    selected = tariff['rate']
    off_peak = False
    for window in tariff['windows']:
        if contains(value, minutes(window['start']), minutes(window['end'])):
            selected, off_peak = window['rate'], True
            break
    rate = Decimal(selected) * (Decimal('1.05') if tariff['vat'] == 'excluded' else Decimal(1))
    return rate, off_peak

def usage_cost(tariff, instant, kwh):
    rate, off_peak = interval_rate(tariff, instant)
    return Decimal(str(kwh)) * rate / Decimal(100), rate, off_peak

def standing_cost(tariff):
    return Decimal(tariff['standing']) / Decimal(100) * (Decimal('1.05') if tariff['vat'] == 'excluded' else Decimal(1))
