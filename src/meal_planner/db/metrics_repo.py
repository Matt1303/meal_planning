from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Connection, text

from meal_planner.metrics import MetricName


def record_metric(
    conn: Connection,
    name: MetricName | str,
    value: float | Decimal,
    *,
    plan_run_id: int | None = None,
    correlation_id: str | None = None,
) -> None:
    metric_name = name.value if isinstance(name, MetricName) else name
    conn.execute(
        text(
            """
            INSERT INTO meal_planning.pipeline_metric
                (metric_time, metric_name, metric_value, plan_run_id, correlation_id)
            VALUES (:t, :n, :v, :pr, :cid)
            """
        ),
        {
            "t": datetime.now(UTC),
            "n": metric_name,
            "v": Decimal(str(value)) if not isinstance(value, Decimal) else value,
            "pr": plan_run_id,
            "cid": correlation_id,
        },
    )
