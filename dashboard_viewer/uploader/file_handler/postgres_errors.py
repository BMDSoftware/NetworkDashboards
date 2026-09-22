"""Classification of PostgreSQL errors by who can fix them."""
import psycopg2

USER = "user"
TRANSIENT = "transient"

SQLSTATES = {
    # Class 22 — the file contains a value Postgres can't handle.
    "22003": (USER,   "a number is too large for the column that stores it"),
    "22001": (USER,   "a text value is longer than the column allows"),
    "22P02": (USER,   "a value could not be read as the type it needs to be"),
    "22012": (USER,   "a calculation divided by zero"),
    "22007": (USER,   "a date could not be interpreted"),
    # Class 23 — integrity.
    "23502": (USER,   "a required value is missing"),
    "23505": (USER,   "the same record appears more than once"),
    # Operational
    "57014": (TRANSIENT, "the operation took too long and was cancelled"),
    "53100": (TRANSIENT, "the database server ran out of disk space"),
    "53200": (TRANSIENT, "the database server ran out of memory"),
    "40P01": (TRANSIENT, "two operations deadlocked"),
}

def pg_sqlstate(exc):
    """Return the five-character SQLSTATE, or None if this isn't a DB error."""
    for candidate in (exc, getattr(exc, "__cause__", None), getattr(exc, "orig", None)):
        if candidate is None:
            continue
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if code:
            return str(code)
    return None

def pg_diagnostics(exc):
    """psycopg2's structured detail. For the log, never for the user."""
    for candidate in (exc, getattr(exc, "__cause__", None), getattr(exc, "orig", None)):
        diag = getattr(candidate, "diag", None)
        if diag is None:
            continue
        return {
            name: getattr(diag, name, None)
            for name in ("message_primary", "message_detail", "column_name", "table_name")
            if getattr(diag, name, None)
        }
    return {}

def classify(exc):
    """-> (blame, description). (None, None) if it isn't a recognised DB error."""
    if is_connection_lost(exc):
        return TRANSIENT, "the connection to the database was lost"
    code = pg_sqlstate(exc)
    if code in SQLSTATES:
        return SQLSTATES[code]
    if code and code.startswith("22"):
        return USER, "a value in the file couldn't be processed"
    if code and code.startswith(("08", "53", "57")):
        return TRANSIENT, "the database was unavailable"
    return None, None

def is_connection_lost(exc):
    if getattr(exc, "connection_invalidated", False):
        return True
    for err in (exc, getattr(exc, "orig", None), getattr(exc, "__cause__", None)):
        if isinstance(err, psycopg2.InterfaceError):
            return True
        if isinstance(err, psycopg2.OperationalError):
            code = getattr(err, "pgcode", None)
            if code is None or str(code).startswith("08"):
                return True
    return False