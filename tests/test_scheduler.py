"""El scheduler se arma con las tareas correctas según la configuración (no arranca ni toca la base)."""

from leadbot.core.config import Settings
from leadbot.jobs.scheduler import build_scheduler

BASE = {"tick", "discover", "enrich", "qualify", "health"}
EMAIL = {"send_email", "follow_ups", "gmail_account"} | {
    "fetch_replies",
    "fetch_replies_off_hours",
    "fetch_replies_weekend",
}


def job_ids(**env) -> set[str]:
    return {job.id for job in build_scheduler(Settings(_env_file=None, **env)).get_jobs()}


def test_without_gmail_nothing_that_sends_or_reads_mail_is_scheduled():
    assert job_ids() == BASE


def test_with_gmail_the_email_tasks_are_scheduled():
    assert job_ids(gmail_address="v@b.co", gmail_app_password="x") == BASE | EMAIL


def test_sheets_sync_needs_a_sheet_id():
    assert "sheets_sync" in job_ids(google_sheet_id="abc")
    assert "sheets_sync" not in job_ids()
