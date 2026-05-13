from __future__ import annotations

from datetime import date


def compute_date_recency(receipt_date: date | None) -> tuple[int | None, bool | None]:
    if receipt_date is None:
        return None, None

    today = date.today()
    days_from_today = (today - receipt_date).days

    try:
        from dateutil.relativedelta import relativedelta

        cutoff = today - relativedelta(months=3)
        is_within_3_months = receipt_date >= cutoff
    except Exception:
        is_within_3_months = days_from_today <= 90

    return days_from_today, is_within_3_months

