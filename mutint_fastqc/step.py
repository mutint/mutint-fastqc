"""The read step: FastQC on each read file as it arrived, before anything rewrites it.

Registered through `mutint_common.read_step_registry` as an **inspect** step, so every producer
that honours the registry -- mutint-breseq today -- runs it before any transform step (fastp
trimming) and it describes the reads somebody uploaded rather than what trimming left.

**Advisory, and that is the whole failure posture.** A QC report that could not be made is
worth a sentence in the run's notes and not worth a breseq run: FastQC missing, exiting
nonzero, or writing something this cannot read each end in `ctx.note` and a return. Only a
cancellation, and a run that has overrun its whole budget, go on up to the producer -- the
first because somebody asked, the second because breseq would have no time left anyway.
"""

import logging
import os
import shutil

from mutint_common import storage_registry, store, tools

from mutint_fastqc import fastqc
from mutint_fastqc.models import FastqcReport

logger = logging.getLogger("mutint_fastqc.step")

NAME = "fastqc"


def available():
    """`(True, '')` when FastQC can be run here, else `(False, <why>)`."""
    if tools.tool_path(fastqc.FASTQC):
        return True, ""
    try:
        tools.require(fastqc.FASTQC)
    except tools.ToolMissing as missing:
        return False, str(missing)
    return False, "FastQC is not installed."


def run(ctx):
    """FastQC over every FASTQ in `ctx.reads`; one `FastqcReport` per file, sample unset."""
    # A producer re-running the same row -- a job handed to a worker twice -- starts clean
    # rather than doubling the links on the sample it eventually becomes.
    FastqcReport.objects.filter(producer=ctx.producer).delete()

    reads = [path for path in ctx.reads if fastqc.is_fastq(path)]
    for path in ctx.reads:
        if path not in reads:
            ctx.write("FastQC: skipped %s (not a FASTQ file)" % ctx.display_name(path))
    if not reads:
        return None

    executable = tools.tool_path(fastqc.FASTQC)
    if not executable:
        ctx.note("FastQC is not installed on the machine that ran this, so no QC report was "
                 "made. The run went on without one.")
        return None

    env = tools.tool_environment()
    scratch = ctx.scratch_dir("fastqc")
    made = 0
    for index, batch in enumerate(fastqc.batches(reads)):
        out_dir = store.ensure_dir(os.path.join(scratch, str(index)))
        argv = fastqc.build_argv(executable, out_dir, batch,
                                 threads=fastqc.threads_for(len(batch)))
        names = ", ".join(ctx.display_name(path) for path in batch)
        try:
            returncode = ctx.run_tool(argv, what="FastQC", env=env)
        except OSError as exc:
            ctx.note("FastQC could not be started (%s), so no QC report was made for %s."
                     % (exc, names))
            continue
        if returncode != 0:
            ctx.note("FastQC exited %d on %s, so there is no QC report for them. Its output "
                     "is in this job's log." % (returncode, names))
            continue
        for path in batch:
            made += _record(ctx, path, out_dir)

    if made:
        storage_registry.request_remeasure(ctx.experiment.pk, reason="FastQC reports written")
    return None


def _record(ctx, path, out_dir):
    """Read one file's report into a row and keep its HTML. 1 if it did, 0 if it could not."""
    name = ctx.display_name(path)
    html_path, zip_path = fastqc.output_paths(out_dir, path)
    try:
        stats = fastqc.parse_zip(zip_path)
    except fastqc.FastqcOutputError as unreadable:
        ctx.note("FastQC's report on %s could not be read (%s), so it was not kept."
                 % (name, unreadable))
        return 0

    report = FastqcReport.objects.create(
        experiment=ctx.experiment, producer=ctx.producer, file_name=name,
        fastqc_version=stats["version"], encoding=stats["encoding"],
        total_sequences=stats["total_sequences"], poor_quality=stats["poor_quality"],
        length_min=stats["length_min"], length_max=stats["length_max"],
        gc_percent=stats["gc_percent"], total_bases=stats["total_bases"],
        modules=stats["modules"])
    if os.path.isfile(html_path):
        store.ensure_dir(report.directory())
        shutil.move(html_path, report.report_path())
    ctx.write("FastQC: %s -- %s" % (name, report.summary()))
    return 1


def attach(producer, sample):
    """The reads became `sample`: hang this run's reports on it, and drop older ones.

    Older ones because a sample made again from new reads -- a re-run superseding it -- is no
    longer described by a report on the reads it was made from before.
    """
    reports = FastqcReport.objects.filter(producer=producer, sample__isnull=True)
    if not reports.exists():
        return
    FastqcReport.objects.filter(sample=sample).exclude(producer=producer).delete()
    reports.update(sample=sample)
    storage_registry.request_remeasure(sample.experiment.pk, reason="FastQC reports attached")


def discard(producer):
    """The run came to nothing: its reports describe reads no sample was made from."""
    reports = FastqcReport.objects.filter(producer=producer)
    experiment_ids = set(reports.values_list("experiment_id", flat=True))
    reports.delete()
    for experiment_id in experiment_ids:
        storage_registry.request_remeasure(experiment_id, reason="FastQC reports discarded")


def sample_links(sample, request):
    """One link per kept report, at the top of the sample's page."""
    from django.urls import reverse

    links = []
    for report in FastqcReport.objects.filter(sample=sample):
        if not report.has_report():
            continue
        links.append(("FastQC: %s" % report.file_name,
                      reverse("fastqc_report", args=(report.pk,)), report.summary()))
    return links


def measure(experiment):
    return sum(storage_registry.directory_bytes(report.directory())
               for report in FastqcReport.objects.filter(experiment=experiment))


def clear(experiment):
    """Remove the HTML and keep the numbers: the rows are small, and still say what FastQC
    found; only the link to the full report goes."""
    for report in FastqcReport.objects.filter(experiment=experiment):
        shutil.rmtree(report.directory(), ignore_errors=True)
