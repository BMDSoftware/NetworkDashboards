import csv
import hashlib
import io
import logging
import os
import re
import numpy
import pandas

from django.conf import settings
from django.core.cache import caches
from django.db import connections
from materialized_queries_manager.models import MaterializedQuery
from redis_rw_lock import RWLock
from uploader.models import AchillesResults, DataSource, UploadHistory
from uploader.file_handler.postgres_errors import pg_diagnostics, pg_sqlstate, SQLSTATES
from django.core.exceptions import ValidationError

from .errors import (
    DuplicatedMetadataRow,
    EqualFileAlreadyUploaded,
    InvalidCSVFile,
    InvalidFieldValue,
    InvalidFileFormat,
    MissingFieldValue,
    TemporaryFailure,
    translate,
)

logger = logging.getLogger(__name__)


def sort_errors_by_line(errors):
    """Order the list of errors by number of line 
    ("N/A" goes to the end)."""
    def key(e):
        try:
            return (0, int(e.get("line")))
        except (TypeError, ValueError, AttributeError):
            return (1, 0)
    return sorted(errors, key=key)

def _generate_file_reader(uploaded_file):
    """
    Receives a python file pointer and returns a pandas csv file reader, along with the columns
     present in the file
    :param uploaded_file: python file pointer of the uploaded file
    """
    columns = [
        "analysis_id",
        "stratum_1",
        "stratum_2",
        "stratum_3",
        "stratum_4",
        "stratum_5",
        "count_value",
    ]

    wrapper = io.TextIOWrapper(uploaded_file)
    csv_reader = csv.reader(wrapper)

    try:
        first_row = next(csv_reader)
    except:  # noqa
        raise InvalidCSVFile(
            "There was an error parsing the provided file. "
            "Uploaded files must be comma-separated values (CSV) files. "
            "If you think this is an error, please contact the system administrator."
        )

    wrapper.detach()

    if len(first_row) == 16:
        columns.extend(
            (
                "min_value",
                "max_value",
                "avg_value",
                "stdev_value",
                "median_value",
                "p10_value",
                "p25_value",
                "p75_value",
                "p90_value",
            )
        )
    elif len(first_row) != 7:
        raise InvalidFileFormat(
            "The provided file has an invalid number of columns. "
            "Make sure you uploaded a valid comma-separated values (CSV) file."
        )

    uploaded_file.seek(0)

    try:
        file_reader = pandas.read_csv(
            uploaded_file,
            header=0,
            dtype=str,
            skip_blank_lines=False,
            index_col=False,
            chunksize=100,
        )
    except:  # noqa
        raise InvalidCSVFile(
            "There was an error parsing the provided file. "
            "Uploaded files must be comma-separated values (CSV) files. "
            "If you think this is an error, please contact the system administrator."
        )
    else:
        return file_reader, columns


def _check_correct(names, values, check, transform=None):
    """
    Transforms the values of given fields from the uploaded file
     and check if they end up in the desired format

    :param names: names of the fields to check
    :param values: values of the fields to transform and check if they are
     in the right format
    :param transform: callable to transform the values of the
     provided fields
    :param check: callable check if the transform processes generated
     a valid output
    :return: the transformed fields or an error string
    """
    assert len(names) == len(values)

    transformed_elements = [None] * len(names)
    bad_elements = []

    for i, name in enumerate(names):
        transformed = values[i] if not transform else transform(values[i])
        if not check(transformed):
            bad_elements.append(name)
        else:
            transformed_elements[i] = transformed

    if bad_elements:
        return (
            f" {bad_elements[0]} is"
            if len(bad_elements) == 1
            else f"s {', '.join(bad_elements[:-1])} and {bad_elements[-1]} are"
        )

    return transformed_elements

def _get_upload_attr(analysis, stratum):
    if analysis.empty:
        return None

    value = analysis.loc[0, stratum]

    if pandas.isna(value):
        return None
    return value

def _model_field(model, name):
    try:
        return model._meta.get_field(name)
    except Exception:  # noqa
        return None

VALIDATE_METADATA_DATES = True

def _validate_metadata(value, field_name, model, model_field_name, is_date=False):
    """
    Validates a metadata field before saving it to the DataSource/UploadHistory.

    - Length: if the field has a max_length, reject values that are too long.
    - Date: optional quality rule. If enabled, reject dates that cannot be
      interpreted.
    """
    if value is None or pandas.isna(value):
        return None

    text = str(value).strip()
    if not text:
        return None

    field = _model_field(model, model_field_name)

    limit = getattr(field, "max_length", None)
    if isinstance(limit, int) and len(text) > limit:
        raise InvalidFieldValue(
            f"The metadata field '{field_name}' is too long: it has {len(text)} "
            f"characters and the maximum is {limit}."
        )

    if is_date and VALIDATE_METADATA_DATES:
        try:
            if field is not None and field.get_internal_type() in ("DateField", "DateTimeField"):
                field.to_python(text)
            else:
                pandas.to_datetime(text)
        except (ValueError, OverflowError, TypeError, ValidationError):
            raise InvalidFieldValue(
                f"The metadata field '{field_name}' contains a date that could not be "
                f"interpreted: {value!r}."
            ) from None

    return text

def _calculate_sha256(file_path):
    """
    Calculates the SHA-256 checksum of a file in streaming 64 KB chunks.
    Reads the target file in fixed-size blocks to safely compute hashes
    for large files without causing Out-Of-Memory (OOM) errors.
    Args:
        file_path (str | PathLike): The absolute or relative path to the file on disk.
    Returns:
        str | None: The hexadecimal SHA-256 digest string if successful,
                    or None if an IOError occurs while reading the file.
    """
    hasher = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            # Iteratively read 64 KB chunks until EOF (empty byte string)
            # as we might get large files, so it should prevent OOM
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except IOError:
        return None

def check_for_duplicated_files(uploaded_file, data_source_id):
    """
    Verifies if the uploaded file is an exact copy of any previously 
    submitted file for this data source.
    """
    if not uploaded_file or not hasattr(uploaded_file, 'path') or not os.path.exists(uploaded_file.path):
        return

    incoming_hash = _calculate_sha256(uploaded_file.path)
    if not incoming_hash:
        return

    history_records = UploadHistory.objects.filter(
        data_source_id=data_source_id
    ).only("errors_summary", "uploaded_file", "upload_date")

    for upload in history_records:
        stored_hash = None
        if isinstance(upload.errors_summary, dict):
            stored_hash = upload.errors_summary.get("file_hash")

        if not stored_hash and upload.uploaded_file and os.path.exists(upload.uploaded_file.path):
            stored_hash = _calculate_sha256(upload.uploaded_file.path)

        if stored_hash and stored_hash == incoming_hash:
            raise EqualFileAlreadyUploaded(
                f"This exact file has already been uploaded for this datasource on {upload.upload_date.strftime('%Y-%m-%d %H:%M')}."
            )

def _get_mat_view_queries():
    """Extracts view definitions, redirects to staging table"""
    all_mat_views = MaterializedQuery.objects.exclude(matviewname__contains="tmp")
    mat_views = {}

    for mat_view in all_mat_views:
        tmp_mat_view_name = mat_view.to_dict()["matviewname"] + "_tmp"
        # To run the mat views (with data) against the "temporary table"
        # To run for all mat views, as the data source can become with draft equal to true
        tmp_definition = mat_view.to_dict()["definition"].replace(
            "achilles_results", "achilles_results_tmp"
        )
        
        tmp_definition = re.sub(r'NULLIF\((.+?),\s*0\)', r'\1', tmp_definition, flags=re.IGNORECASE)

        mat_views[tmp_mat_view_name] = [
            tmp_definition,
        ]
        # since draft can change with time, we must run the queries for all types of draft, namely with draft = true and draft = false
        # We normally don't use draft set to true in the queries, otherwise we would need to test it here also
        if "draft = false" in tmp_definition:
            mat_views[tmp_mat_view_name].append(
                tmp_definition.replace("draft = false", "draft = true")
            )
    return mat_views

def _row_conversion_reasons(row, types):
    """identifies which columns fail to convert and why,
      in language for the user."""
    reasons = []
    for col, dtype in types.items():
        if dtype == "string" or pandas.isna(row.get(col)):
            continue
        shown = repr(str(row[col]))
        if len(shown) > 60:
            shown = shown[:57] + "..."
        try:
            pandas.Series([row[col]]).astype(dtype)
        except OverflowError:
            reasons.append(f"A number is too large for the column that stores it ('{col}' = {shown})")
        except (ValueError, TypeError):
            reasons.append(f"A value could not be read as the type it needs to be ('{col}' = {shown})")
    return reasons 


def validate_data_in_existing_mat_views_df(data_source_id, valid_df, pending_upload):
    cache = caches["workers_locks"]
    ctx = {"ds": data_source_id, "upload": pending_upload.id}
    mat_view_errors = []

    with RWLock(
        cache.client.get_client(), "celery_worker_updating", RWLock.WRITE, expire=None
    ):
        try:
            # The validations here are made through the materialized views:
            # if a line fails a view, that line is located and
            # removed from the set to insert, so the upload can proceed
            # with warnings instead of failing completely.

            mat_views = _get_mat_view_queries()
            if mat_views and not valid_df.empty:
                _load_staging_table_from_df(valid_df, data_source_id, logger, ctx)
                probe_errors, lines_to_drop = _probe_charts(mat_views, valid_df, logger, ctx)
                for (line_num, description), (analysis_id_val, detail) in probe_errors.items():
                    mat_view_errors.append({
                        "line": line_num,
                        "analysis_id": str(analysis_id_val),
                        "reason": f"{description}: {detail}" if detail else description,
                    })
                # the lines that break the views are skipped: if they were inserted,
                # the refresh of the views would fail after the upload.
                drop = [ln - 2 for ln in lines_to_drop if (ln - 2) in valid_df.index]
                if drop:
                    valid_df.drop(index=drop, inplace=True)
                    logger.info("Removed %d row(s) that break materialized views %s", len(drop), ctx)

            mat_view_errors = sort_errors_by_line(mat_view_errors)

        finally:
            _drop_staging_table(logger)

    return mat_view_errors

def _load_staging_table_from_df(valid_df, data_source_id, log, ctx):

    try:
        with connections["achilles"].cursor() as cursor, \
                settings.ACHILLES_DB_SQLALCHEMY_ENGINE.connect() as pandas_connection, \
                pandas_connection.begin():
        
            cursor.execute("DROP TABLE IF EXISTS achilles_results_tmp CASCADE")
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS achilles_results_tmp AS SELECT * FROM "
                + AchillesResults._meta.db_table + " WHERE FALSE"
            )
            cursor.execute("CREATE SEQUENCE IF NOT EXISTS achilles_results_tmp_seq_id")
            cursor.execute(
                "ALTER TABLE achilles_results_tmp ALTER COLUMN id "
                "SET DEFAULT nextval('achilles_results_tmp_seq_id')"
            )
            cursor.execute("ALTER TABLE achilles_results_tmp ALTER COLUMN id SET NOT NULL")
            cursor.execute("ALTER TABLE achilles_results_tmp ADD COLUMN source_line integer")

            chunk = valid_df.assign(
                data_source_id=data_source_id,
                source_line=lambda d: d.index.to_numpy() + 2,
            )
            chunk.to_sql("achilles_results_tmp", pandas_connection, if_exists="append", index=False)
            log.info("Staged %d rows %s", len(chunk), ctx)

    except Exception as exc:
        raise translate(exc, "loading your data", log, **ctx) from exc

# Erros that reproduce with the same line (invalid value for a cast/conversion).
ROW_LEVEL_SQLSTATES = {"22003", "22001", "22P02", "22007", "22008", "22005", "22000", "22018"}
# Erros that depend on the context: the divisor is a count from ANOTHER line/analysis
# (e.g., count of people = 0). They don't reproduce when isolating the line.
CONTEXT_SQLSTATES = {"22012"}


def _normalize(definition):
    """One CTE can't be referenced with the schema ahead (public.nome), for this reason it is removed."""
    return re.sub(r"\bpublic\.achilles_results_tmp", "achilles_results_tmp", definition)


def _run_probe(cursor, definition, where):
    """Run a probe on the view only on the rows from the staging table that meet `where`.
    The CTE has the name of the table, so it shadows it (also in the qualified names)."""
    base = _normalize(definition).rstrip().rstrip(";")
    cursor.execute(
        f"WITH achilles_results_tmp AS "
        f"(SELECT * FROM achilles_results_tmp WHERE {where}) {base}"
    )
    cursor.fetchall()


def _describe(sqlstate, exc):
    if sqlstate in SQLSTATES:
        return SQLSTATES[sqlstate][1].capitalize()
    return "A value in the file couldn't be processed"


def _detail(exc):
    msg = pg_diagnostics(exc).get("message_primary")
    if not msg:
        first = (str(exc).strip().splitlines() or [""])[0]
        msg = re.sub(r"^ERROR:\s*", "", first)
    return msg[:200]


def _probe_error(cursor, definition, where, view_sqlstate):
    """None if the query passes; otherwise (sqlstate, description, detail) of the error that this query produced."""
    try:
        _run_probe(cursor, definition, where)
        return None
    except Exception as exc:
        st = pg_sqlstate(exc)
        if st and (st == view_sqlstate or st.startswith("22")):
            return st, _describe(st, exc), _detail(exc)
        return None


def _locate(cursor, definition, analysis_id, lines, err, view_sqlstate):
    """Binary search (errors from a single line). Returns [(line, error)], each line with its own error."""
    if len(lines) == 1:
        return [(lines[0], err)]
    mid = len(lines) // 2
    found = []
    for half in (lines[:mid], lines[mid:]):
        where = (f"analysis_id = {analysis_id} AND "
                 f"source_line IN ({','.join(str(n) for n in half)})")
        half_err = _probe_error(cursor, definition, where, view_sqlstate)
        if half_err:
            found += _locate(cursor, definition, analysis_id, half, half_err, view_sqlstate)
    return found


def _fails_without(cursor, definition, removed, view_sqlstate):
    """True if the view continues to fail after removing the lines `removed` from the staging table."""
    where = f"source_line NOT IN ({','.join(str(n) for n in removed)})" if removed else "TRUE"
    return _probe_error(cursor, definition, where, view_sqlstate) is not None


def _necessary(cursor, definition, lines, already_removed, view_sqlstate):
    """Lines without which the error disappears (binary search by removal)."""
    if len(lines) == 1:
        return list(lines)
    mid = len(lines) // 2
    found = []
    for half in (lines[:mid], lines[mid:]):
        if not _fails_without(cursor, definition, already_removed + half, view_sqlstate):
            found += _necessary(cursor, definition, half, already_removed, view_sqlstate)
    return found


def _locate_context(cursor, definition, lines_by_analysis, counts, gone, view_sqlstate, max_rounds=20):
    """
    Contextual errors (division by zero): looks for the lines without which the error disappears.
    A divisor is a count of zero, so among the necessary lines, the ones that have
    count_value = 0 (the others are just the numerator).
    After finding a divisor, it corrects it in the staging table (count_value = 1) and repeats,
    because one error can hide another (e.g., two counts of zero in different analyses).
    Removing the line is not sufficient: it would empty the joins and hide the second error.
    The staging is temporary; what is inserted comes from valid_df, which is not modified here.
    """
    found = []
    for _ in range(max_rounds):
        if not _fails_without(cursor, definition, [], view_sqlstate):
            break
        involved = []
        for lines in lines_by_analysis.values():
            remaining = [ln for ln in lines if ln not in gone]
            if remaining and not _fails_without(cursor, definition, remaining, view_sqlstate):
                involved += _necessary(cursor, definition, remaining, [], view_sqlstate)
        zeros = [ln for ln in involved if counts.get(ln) == 0]
        meta_lines = set(lines_by_analysis.get(0, [])) | set(lines_by_analysis.get(5000, []))
        others = [ln for ln in involved if ln not in meta_lines]
        culprits = zeros or others or involved

        if not culprits:
            break
        ids = ",".join(str(n) for n in culprits)
        if zeros:
            cursor.execute(f"UPDATE achilles_results_tmp SET count_value = 1 WHERE source_line IN ({ids})")
            for ln in zeros:
                counts[ln] = 1
        else:
            cursor.execute(f"DELETE FROM achilles_results_tmp WHERE source_line IN ({ids})")
            gone.update(culprits)
        found += culprits
    return found


def _probe_charts(mat_views, valid_df, log, ctx):
    """
    Called only to locate where the problem is: the view has already failed on the staging table.
      - Single-line errors (casts, to_date): tests by analysis_id and locates by binary search.
      - Contextual errors (division by zero): looks for the lines without which the error disappears.
      - If the view fails and no line can be located, the error is reported
        (line "N/A"), to never be lost in silence.
    The view name does not enter the key: the same error on the same line in several views appears once.
    Returns ({(line, description): (analysis_id, detail)}, {lines to skip}).
    """
    errors = {}
    lines_to_drop = set()
    gone = set() 

    lines_by_analysis, analysis_of, counts = {}, {}, {}
    for idx, row in valid_df.iterrows():
        ln = int(idx) + 2
        analysis_id = int(row["analysis_id"])
        lines_by_analysis.setdefault(analysis_id, []).append(ln)
        analysis_of[ln] = analysis_id
        counts[ln] = int(row["count_value"])
        
    def add(ln, err, view):
        _, description, detail = err
        text = f"{detail} (view {view})" if detail else f"(view {view})"
        errors.setdefault((ln, description), (analysis_of.get(ln, "N/A"), text))
        if isinstance(ln, int):
            lines_to_drop.add(ln)

    with connections["achilles"].cursor() as cursor:
        for name, definitions in mat_views.items():
            view = name[:-4] if name.endswith("_tmp") else name
            for definition in definitions:
                try:
                    cursor.execute(f"CREATE MATERIALIZED VIEW {name} AS {definition}")
                    cursor.execute(f"DROP MATERIALIZED VIEW {name}")
                    continue
                except Exception as exc:
                    if not isinstance(exc, TemporaryFailure):
                        try:
                            cursor.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name}")
                        except Exception:
                            log.warning("Could not drop %s after failure", name)

                    view_sqlstate = pg_sqlstate(exc)

                    if not view_sqlstate or view_sqlstate[:2] not in ("22", "23"):
                        log.warning("View %s failed for a reason unrelated to the data "
                                    "[sqlstate=%s] %s", name, view_sqlstate, ctx)
                        continue
                    whole_err = (view_sqlstate, _describe(view_sqlstate, exc), _detail(exc))

                found = False
                if view_sqlstate not in CONTEXT_SQLSTATES:
                    for analysis_id, lines in lines_by_analysis.items():
                        err = _probe_error(cursor, definition, f"analysis_id = {analysis_id}", view_sqlstate)
                        if err is None:
                            continue
                        found = True
                        if (err[0] in ROW_LEVEL_SQLSTATES) or (err[0] and err[0].startswith("22")):
                            located = _locate(cursor, definition, analysis_id, lines, err, view_sqlstate)
                            if located:
                                for ln, line_err in located:
                                    add(ln, line_err, view)
                            else:
                                for ln in lines:
                                    lines_to_drop.add(ln)
                                add(min(lines), err, view)
                        else:
                            for ln in lines:
                                lines_to_drop.add(ln)
                            add(min(lines), err, view)

                if not found:
                    culprits = _locate_context(cursor, definition, lines_by_analysis, counts, gone, view_sqlstate)
                    for ln in culprits:
                        add(ln, whole_err, view)
                    found = bool(culprits)

                if not found:
                    log.warning("Could not locate the row behind the error of view %s %s", name, ctx)
                    add("N/A", whole_err, view)

    return errors, lines_to_drop

def _drop_staging_table(log):
    try:
        with connections["achilles"].cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS achilles_results_tmp CASCADE")
            cursor.execute("DROP SEQUENCE IF EXISTS achilles_results_tmp_seq_id")
    except Exception:
        log.exception("Failed to drop the staging table")


def extract_data_from_uploaded_file_tolerant(uploaded_file):
    """
    Reads the CSV file in chunks and isolates rows with type errors or required nulls,
    accumulating them in a list of warnings/errors.
    Returns: (file_metadata, metadata_dict, valid_df, errors_list)
    """
    file_reader, columns = _generate_file_reader(uploaded_file)

    types = {
        "analysis_id": numpy.int64,
        "stratum_1": "string",
        "stratum_2": "string",
        "stratum_3": "string",
        "stratum_4": "string",
        "stratum_5": "string",
        "count_value": numpy.int64,
    }
    if len(columns) == 16:
        types.update(
            {
                "min_value": float,
                "max_value": float,
                "avg_value": float,
                "stdev_value": float,
                "median_value": float,
                "p10_value": float,
                "p25_value": float,
                "p75_value": float,
                "p90_value": float,
            },
        )

    metadata = None
    errors = []
    valid_chunks = []

    while True:
        try:
            chunk = next(file_reader)
        except StopIteration:
            break
        except ValueError:
            raise InvalidFileFormat(
                "The provided file has an invalid csv format. Make sure is a text file separated"
                " by commas and you either have 7 or 16 columns."
            )
        except:  # noqa
            raise InvalidCSVFile(
                "There was an error parsing the provided file. "
                "Uploaded files must be comma-separated values (CSV) files."
            )

        if not chunk.empty:
            line_map = {idx: int(idx) + 2 for idx in chunk.index}

            metadata_rows = chunk[chunk.analysis_id.astype(str).isin(("0", "5000"))]
            if metadata is None:
                metadata = metadata_rows.copy()
            else:
                metadata = pandas.concat((metadata, metadata_rows), ignore_index=True, copy=False)

            null_mask = chunk[["analysis_id", "count_value"]].isna().any(axis=1)
            if null_mask.any():
                invalid_rows = chunk[null_mask]
                for idx, row in invalid_rows.iterrows():
                    line_num = line_map[idx]
                    analysis_id_str = str(row.get("analysis_id")) if pandas.notna(row.get("analysis_id")) else "N/A"
                    for col in ("analysis_id", "count_value"):
                        if pandas.isna(row.get(col)):
                            errors.append({
                                "line": line_num,
                                "analysis_id": analysis_id_str,
                                "reason": f"A required value is missing ('{col}')",
                            })
                    for reason in _row_conversion_reasons(row, types):
                        errors.append({
                            "line": line_num,
                            "analysis_id": analysis_id_str,
                            "reason": reason,
                        })
                chunk = chunk[~null_mask]

            if not chunk.empty:
                try:
                    chunk_converted = chunk.astype(types)
                    valid_chunks.append(chunk_converted)
                except (OverflowError, ValueError):
                    for idx, row in chunk.iterrows():
                        line_num = line_map[idx]
                        analysis_id_str = str(row.get("analysis_id")) if pandas.notna(row.get("analysis_id")) else "N/A"
                        try:
                            single_row_df = pandas.DataFrame([row], index=[idx]).astype(types)
                            valid_chunks.append(single_row_df)
                        except (OverflowError, ValueError):
                            reasons = _row_conversion_reasons(row, types) or [
                                "A value could not be read as the type it needs to be"
                            ]
                            for reason in reasons:
                                errors.append({
                                    "line": line_num,
                                    "analysis_id": analysis_id_str,
                                    "reason": reason,
                                })

    if valid_chunks:
        valid_df = pandas.concat(valid_chunks) 
    else:
        valid_df = pandas.DataFrame(columns=columns)

    if metadata is not None and not metadata.empty:
        output = _check_correct(
            ["0", "5000"],
            (
                metadata[metadata.analysis_id.astype(str) == "0"],
                metadata[metadata.analysis_id.astype(str) == "5000"],
            ),
            lambda e: len(e) <= 1,
        )
        if isinstance(output, str):
            raise DuplicatedMetadataRow(
                f"Analysis id{output} duplicated on multiple rows."
            )

    analysis_0 = metadata[metadata.analysis_id.astype(str) == "0"] if metadata is not None else pandas.DataFrame()
    if analysis_0.empty:
        raise MissingFieldValue(
            "Analysis id 0 is missing. Try (re)running the plugin CatalogueExport on your database."
        )

    analysis_0 = analysis_0.reset_index(drop=True)
    analysis_5000 = metadata[metadata.analysis_id.astype(str) == "5000"].reset_index(drop=True) if metadata is not None else pandas.DataFrame()

    metadata_dict = {
        "generation_date": _validate_metadata(
            _get_upload_attr(analysis_0, "stratum_3"),
            "generation_date",
            UploadHistory,
            "generation_date",
            is_date=True,
        ),
        "source_release_date": _validate_metadata(
            _get_upload_attr(analysis_5000, "stratum_2"),
            "source_release_date",
            DataSource,
            "release_date",
            is_date=True,
        ),
        "cdm_release_date": _validate_metadata(
            _get_upload_attr(analysis_5000, "stratum_3"),
            "cdm_release_date",
            UploadHistory,
            "cdm_release_date",
            is_date=True,
        ),
        "cdm_version": _validate_metadata(
            _get_upload_attr(analysis_5000, "stratum_4"),
            "cdm_version",
            UploadHistory,
            "cdm_version",
        ),
        "r_package_version": _validate_metadata(
            _get_upload_attr(analysis_0, "stratum_2"),
            "r_package_version",
            UploadHistory,
            "r_package_version",
        ),
        "vocabulary_version": _validate_metadata(
            _get_upload_attr(analysis_5000, "stratum_5"),
            "vocabulary_version",
            UploadHistory,
            "vocabulary_version",
        ),
    }
    return {"columns": columns, "types": types}, metadata_dict, valid_df, errors