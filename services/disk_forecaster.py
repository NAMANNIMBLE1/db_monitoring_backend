"""Simple linear-regression forecaster for disk usage.

Given a time series of ``(timestamp, percent)`` points, fit a line and
predict when the series will cross a given threshold.  No external
dependencies — plain Python math.
"""

from datetime import datetime, timedelta
from typing import Optional


def forecast_breach(
    series: list[tuple[datetime, float]],
    threshold: float,
    horizon_days: int,
) -> Optional[dict]:
    """Fit y = a + b*x over the series; predict when y hits ``threshold``.

    Args:
        series: list of (timestamp, percent) tuples, ordered by timestamp.
        threshold: percent value to project against (e.g. 80.0).
        horizon_days: only return a breach if it falls within this window.

    Returns:
        dict with ``expected_breach_date``, ``days_to_breach``, ``slope_pct_per_day``,
        ``current_pct``, ``samples`` — or ``None`` if no breach predicted
        within horizon (or insufficient / flat data).
    """
    if len(series) < 10:
        return None

    # Use seconds since first timestamp as the x-axis so numbers stay small.
    t0 = series[0][0]
    xs = [(ts - t0).total_seconds() / 86400.0 for ts, _ in series]  # days
    ys = [float(pct) for _, pct in series]

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return None

    slope = num / den  # percent per day
    intercept = mean_y - slope * mean_x

    current_pct = ys[-1]
    # Need an upward trend and we must not already be above threshold
    # (current-above-threshold is handled as a separate critical alert).
    if slope <= 0 or current_pct >= threshold:
        return None

    # Solve a + b*x = threshold  →  x = (threshold - a) / b
    x_breach = (threshold - intercept) / slope
    x_now = xs[-1]
    days_to_breach = x_breach - x_now
    if days_to_breach <= 0 or days_to_breach > horizon_days:
        return None

    breach_ts = series[-1][0] + timedelta(days=days_to_breach)
    return {
        "expected_breach_date": breach_ts.isoformat(),
        "days_to_breach": round(days_to_breach, 1),
        "slope_pct_per_day": round(slope, 4),
        "current_pct": round(current_pct, 2),
        "threshold_pct": threshold,
        "samples": n,
    }
