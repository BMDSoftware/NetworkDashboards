"""User-facing errors for the upload pipeline."""

import uuid

from .postgres_errors import (
    TRANSIENT, USER, classify, pg_diagnostics, pg_sqlstate,
)


class UploadError(Exception):
    title = "The upload could not be completed"
    what_happened = "Something went wrong while processing your file."
    next_steps = ("Try uploading the file again.",)
    data_saved = False

    def __init__(self, what_happened=None, *, details=(), incident_id=None):
        self.what_happened = what_happened or self.what_happened
        self.details = tuple(details)
        self.incident_id = incident_id or uuid.uuid4().hex[:8].upper()
        super().__init__(self.render_text(), self.as_dict())

    def as_dict(self):
        return {
            "title": self.title,
            "what_happened": self.what_happened,
            "data_saved": self.data_saved,
            "next_steps": list(self.next_steps),
            "details": list(self.details),
            "incident_id": self.incident_id,
        }

    def render_text(self):
        lines = [self.title, "", self.what_happened]
        if self.details:
            lines += [""] + [f"- {d}" for d in self.details]
        lines += ["", "What to do next:"]
        lines += [f"{i}. {s}" for i, s in enumerate(self.next_steps, 1)]
        lines += ["", f"Reference: {self.incident_id}"]
        return "\n".join(lines)


class InvalidCSVFile(UploadError):
    title = "This file couldn't be read as a CSV"
    next_steps = (
        "Check the first line is a comma-separated header row.",
        "Re-run CatalogueExport and upload its output unmodified.",
    )


class FileDataCorrupted(UploadError):
    title = "Some values in this file can't be stored"
    next_steps = (
        "Review the analyses in your source file.",
        "Confirm the export completed and wasn't truncated or edited afterwards.",
    )


class TemporaryFailure(UploadError):
    title = "The upload was interrupted"
    next_steps = (
        "Please, wait a few minutes and upload the same file again.",
        "Nothing was saved, so retrying is safe.",
    )

class InvalidFileFormat(UploadError):
    title = "The file layout wasn't recognised"
    what_happened = (
        "Achilles result files have either 7 columns, or 16 when they include "
        "distribution values."
    )
    next_steps = (
        "Check that no columns were added, removed or reordered.",
        "Re-run CatalogueExport and upload its output unmodified.",
    )


class InvalidFieldValue(UploadError):
    title = "Some values in the file aren't in the expected format"
    what_happened = (
        "One or more cells contain a value that doesn't match the type expected "
        "for that column. Only the stratum_* columns accept free text; every "
        "other column must contain a number."
    )
    next_steps = (
        "Check the file wasn't opened and re-saved by a spreadsheet application, "
        "which can reformat numbers.",
        "Re-run CatalogueExport and upload its output unmodified.",
    )


class DuplicatedMetadataRow(UploadError):
    title = "The file contains conflicting metadata rows"
    what_happened = (
        "A metadata analysis appears on more than one row, so we can't tell which "
        "values describe this export."
    )
    next_steps = (
        "Check whether two exports were concatenated into a single file.",
        "Re-run CatalogueExport and upload a single, unmerged file.",
    )


class MissingFieldValue(UploadError):
    title = "The file is missing required metadata"
    what_happened = (
        "The row describing the export itself is missing, so we can't record "
        "which CDM and vocabulary versions this data came from."
    )
    next_steps = (
        "Re-run CatalogueExport on your database and upload the resulting file.",
        "If the row is still absent from a fresh export, contact your database "
        "administrator.",
    )


class EqualFileAlreadyUploaded(UploadError):
    data_saved = True
    title = "This file has already been uploaded"
    what_happened = (
        "This file is identical to the most recent upload for this data source, "
        "so there's nothing new to process."
    )
    next_steps = (
        "No action is needed, the existing data is already up to date.",
        "If you meant to upload newer results, re-run CatalogueExport and upload "
        "the new file.",
    )

BLAME_TO_ERROR = {
    USER: FileDataCorrupted,
    TRANSIENT: TemporaryFailure,
}

def translate(exc, stage, log, **context):
    """
    Map a raw exception to a user-facing one, logging the full detail first.

    This is the single point where the two audiences diverge: everything
    technical goes to the log, only the curated object goes onward.
    """
    blame, reason = classify(exc)
    error = (
        BLAME_TO_ERROR[blame](f"While {stage}, {reason}.")
        if blame
        else InvalidCSVFile()  # no SQLSTATE and not a dead connection -> parsing error
    )
    log.exception(
        "%s failed [sqlstate=%s blame=%s incident=%s] %s %s",
        stage, pg_sqlstate(exc), blame, error.incident_id,
        pg_diagnostics(exc), context,
        exc_info=exc,
    )
    return error