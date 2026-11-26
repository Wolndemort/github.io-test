def restore_cancelled_visit_balance(student, deleted_logs) -> bool:
    """Return one paid lesson when an active paid visit is cancelled."""
    deleted_paid_visit = any(
        (log.source or "gate") not in {"advance", "repeat"}
        for log in deleted_logs
    )
    if deleted_paid_visit and int(student.balance_lessons or 0) != 999:
        student.balance_lessons = int(student.balance_lessons or 0) + 1
    return deleted_paid_visit
