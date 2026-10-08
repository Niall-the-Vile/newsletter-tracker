"""Data-health banner and the first-run Get started checklist. Everything is counted by query, never stored."""
from services import churches
from services.subscriptions import PENDING_NOTE

# Same test as the People & Churches "Needs attention" filter.
_PEOPLE_NEED_ATTENTION = ("COALESCE(active, 1) = 1 AND (fund_id IS NULL OR COALESCE(address1,'') = '' "
                          "OR COALESCE(city,'') = '' OR COALESCE(zip,'') = '')")


def _count(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def health_issues(conn):
    """What needs fixing, as [{'text', 'show'}]. show says which list to open: 'people' or 'churches'."""
    checks = [
        ("people", "{n} people have no fund or no full address",
         _count(conn, f"SELECT COUNT(*) FROM people WHERE {_PEOPLE_NEED_ATTENTION}")),
        ("churches", "{n} churches have several reps and nobody picked to receive church mail",
         len(churches.needs_default(conn))),
        ("churches", "{n} churches have no rep to send to",
         _count(conn, "SELECT COUNT(*) FROM churches c WHERE c.active = 1 "
                "AND NOT EXISTS (SELECT 1 FROM church_reps r WHERE r.church_id = c.id AND r.active = 1)")),
        ("churches", "{n} churches have no fund",
         _count(conn, "SELECT COUNT(*) FROM churches WHERE active = 1 AND fund_id IS NULL")),
        ("people", "{n} old subscriptions are waiting for review (Setup > Old subscriptions)",
         _count(conn, "SELECT COUNT(*) FROM subscriptions_v2 s JOIN people p ON p.id = s.person_id "
                "WHERE s.active = 0 AND s.notes = ? AND COALESCE(p.active, 1) = 1", (PENDING_NOTE,))),
        ("people", "{n} people or churches have mail marked as returned",
         _count(conn, "SELECT (SELECT COUNT(*) FROM people WHERE returned_mail = 1 AND COALESCE(active, 1) = 1) "
                "+ (SELECT COUNT(*) FROM churches WHERE returned_mail = 1 AND active = 1)")),
    ]
    return [{"text": text.format(n=n), "show": show} for show, text, n in checks if n]


def checklist(conn):
    """The Get started steps as [{'text', 'done', 'go'}]. go is where the Go button leads."""
    people = _count(conn, "SELECT COUNT(*) FROM people WHERE COALESCE(active, 1) = 1")
    no_fund = _count(conn, "SELECT COUNT(*) FROM people WHERE COALESCE(active, 1) = 1 AND fund_id IS NULL")
    church_count = _count(conn, "SELECT COUNT(*) FROM churches WHERE active = 1")
    unpicked = len(churches.needs_default(conn))
    gets = _count(conn, "SELECT COUNT(*) FROM subscriptions_v2 WHERE active = 1")
    mailings = _count(conn, "SELECT COUNT(*) FROM mailing_runs")
    return [
        {"text": "Bring in your mailing list", "done": people > 0, "go": "Import"},
        {"text": "Give every person a fund", "done": people > 0 and no_fund == 0, "go": "People"},
        {"text": "Group people into churches", "done": church_count > 0, "go": "Church grouping"},
        {"text": "Pick who receives each church's mail", "done": church_count > 0 and unpicked == 0, "go": "Churches"},
        {"text": "Set what each person and church gets", "done": gets > 0, "go": "People"},
        {"text": "Make your first mailing", "done": mailings > 0, "go": "Mailings"},
    ]
