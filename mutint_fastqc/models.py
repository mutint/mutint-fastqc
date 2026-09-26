"""One FastQC report on one read file, and the directory that holds its HTML.

A row per read file rather than per sample, because FastQC reports per file and a sample is
often a pair of them, or a lane split of four. The numbers are columns, so they can be listed
and compared without opening anything; the whole report is FastQC's own HTML, kept under
`components/mutint_fastqc/<pk>/` and served sandboxed by `views.report`.

**`sample` is null while the run that made it is still going.** A read step runs before the
producer has imported anything, so the row is keyed by `producer` -- the producer's own run,
e.g. `mutint_breseq:42` -- until `step.attach` is told which sample the reads became, or
`step.discard` is told they became nothing. `CASCADE` on both foreign keys, and a `post_delete`
receiver for the directory: core reaps nothing under `components/`, so this row is the whole
lifecycle, and deleting a sample or purging an experiment reaches the HTML through it.
"""

import logging
import os
import shutil

from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from mutint_common import store

logger = logging.getLogger("mutint_fastqc.models")

COMPONENT = "mutint_fastqc"
REPORT_FILENAME = "report.html"


class FastqcReport(models.Model):
    experiment = models.ForeignKey("mutint_experiment.Experiment", on_delete=models.CASCADE,
                                   related_name="fastqc_reports")
    sample = models.ForeignKey("mutint_sample.Sample", null=True, blank=True,
                               on_delete=models.CASCADE, related_name="fastqc_reports")
    # The producer's run, as `ReadStepContext.producer` names it. What `attach` and `discard`
    # find the rows by before there is a sample.
    producer = models.CharField(max_length=100, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # The name the person gave the file, not the one it had on disk mid-run.
    file_name = models.CharField(max_length=255)

    fastqc_version = models.CharField(max_length=32, blank=True)
    encoding = models.CharField(max_length=64, blank=True)
    total_sequences = models.BigIntegerField(null=True, blank=True)
    poor_quality = models.BigIntegerField(null=True, blank=True)
    length_min = models.IntegerField(null=True, blank=True)
    length_max = models.IntegerField(null=True, blank=True)
    gc_percent = models.FloatField(null=True, blank=True)
    # FastQC's own words (`247.7 kbp`): it rounds, and a number parsed back out of that would
    # claim a precision the report does not have.
    total_bases = models.CharField(max_length=32, blank=True)
    # `{module name: 'pass' | 'warn' | 'fail'}`, in FastQC's order.
    modules = models.JSONField(default=dict)

    class Meta:
        ordering = ["file_name", "pk"]

    def __str__(self):
        return "FastqcReport %s (%s)" % (self.pk, self.file_name)

    def directory(self):
        return store.component_dir(COMPONENT, self.pk)

    def report_path(self):
        return os.path.join(self.directory(), REPORT_FILENAME)

    def has_report(self):
        return os.path.isfile(self.report_path())

    def flagged(self, status):
        return [name for name, verdict in (self.modules or {}).items() if verdict == status]

    def length_text(self):
        if self.length_min is None:
            return ""
        if self.length_min == self.length_max:
            return "%d bp" % self.length_min
        return "%d-%d bp" % (self.length_min, self.length_max)

    def summary(self):
        """One line: reads, length, GC, and what FastQC failed or warned about."""
        parts = []
        if self.total_sequences is not None:
            parts.append("{:,} reads".format(self.total_sequences))
        if self.length_text():
            parts.append(self.length_text())
        if self.gc_percent is not None:
            parts.append("%g%% GC" % self.gc_percent)
        failed, warned = self.flagged("fail"), self.flagged("warn")
        if failed:
            parts.append("failed: %s" % ", ".join(failed))
        if warned:
            parts.append("warnings: %s" % ", ".join(warned))
        return "; ".join(parts)


@receiver(post_delete, sender=FastqcReport)
def _remove_report_directory(sender, instance, **kwargs):
    try:
        shutil.rmtree(instance.directory(), ignore_errors=True)
    except (ValueError, TypeError):
        logger.debug("no directory to remove for an unsaved FastqcReport")
