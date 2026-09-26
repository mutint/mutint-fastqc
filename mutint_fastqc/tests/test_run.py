"""The whole path: reads launched through mutint-breseq, FastQC on them, and the report on the
sample's page.

Through mutint-breseq's own launcher because that is the producer this component is installed
beside, and the seam between them is the thing most worth testing: the order the steps run in,
the sample the reports are attached to, and what a failed or cancelled run leaves behind. With
real executables on disk for breseq, fastp and FastQC (`fake_fastqc`), so the argv and the
environment are what is asserted, and the suite's immediate task backend, so one POST runs the
whole thing.
"""

import json
import os
import shutil
import tempfile
from unittest import mock, skipUnless

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from mutint_common import store
from mutint_experiment.models import Project
from mutint_import import staging
from mutint_import.tests import breseq_fixture
from mutint_sample.models import Sample

from mutint_fastqc import step
from mutint_fastqc.models import FastqcReport
from mutint_fastqc.tests import fake_fastqc

BRESEQ = apps.is_installed("mutint_breseq")

if BRESEQ:
    from mutint_breseq import tasks as breseq_tasks
    from mutint_breseq.models import (STATUS_CANCELLED, STATUS_FAILED, STATUS_IMPORTED,
                                      BreseqRun)
    from mutint_breseq.tests import fake_breseq, fake_fastp, fastq_fixture
    from mutint_breseq.tests.test_launch import establish_reference


class _Setup:

    def setUp(self):
        self.owner = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.owner)

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        self.tools = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tools, True)
        self.scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.scratch, True)

        self.template = breseq_fixture.write_sample(self.scratch, "template")
        self.fastqc_record = os.path.join(self.scratch, "fastqc.jsonl")
        self.fastp_record = os.path.join(self.scratch, "fastp.jsonl")
        fake_breseq.install(self.tools)
        fake_fastp.install(self.tools)
        fake_fastqc.install(self.tools)
        patcher = override_settings(MUTINT_STORE_DIR=self.store, MUTINT_TOOLS_DIR=self.tools)
        patcher.enable()
        self.addCleanup(patcher.disable)
        for name, value in (("FAKE_BRESEQ_TEMPLATE", self.template),
                            ("FAKE_BRESEQ_ARGV", os.path.join(self.scratch, "argv.json")),
                            ("FAKE_FASTP_ARGV", self.fastp_record),
                            ("FAKE_FASTQC_ARGV", self.fastqc_record)):
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)

        self.project = Project.objects.create(name="p", user=self.owner)
        from mutint_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.owner)
        establish_reference(self.experiment)

    def _launch(self, read_steps=("fastqc", "trim"), sample="s1",
                names=("s1_R1.fastq", "s1_R2.fastq")):
        session = staging.open_session(
            self.owner, self.experiment, "mutint_breseq",
            [{"path": name, "size": 4} for name in names])
        root = store.ensure_dir(store.staging_dir(session.id))
        for name in names:
            fastq_fixture.write(os.path.join(root, name), count=4)
        body = {"upload_id": str(session.id), "sample": sample, "population": "",
                "time_point": "", "arguments": ""}
        if read_steps is not None:
            body["read_steps"] = list(read_steps)
        return self.client.post("/breseq/launch?experiment_id=%s" % self.experiment.id,
                                data=json.dumps(body), content_type="application/json")

    def _recorded(self, path):
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [json.loads(line) for line in handle if line.strip()]


@skipUnless(BRESEQ, "mutint-fastqc's producer, mutint-breseq, is not installed")
class FastqcRunTestCase(_Setup, TestCase):

    def test_each_read_file_gets_a_report_attached_to_the_sample(self):
        response = self._launch()
        run = BreseqRun.objects.get(pk=response.json()["run_ids"][0])
        self.assertEqual(run.status, STATUS_IMPORTED, run.error)

        reports = list(FastqcReport.objects.order_by("file_name"))
        self.assertEqual(["s1_R1.fastq", "s1_R2.fastq"], [r.file_name for r in reports])
        for report in reports:
            self.assertEqual(run.sample_id, report.sample_id)
            self.assertEqual(2000, report.total_sequences)
            self.assertEqual((100, 151), (report.length_min, report.length_max))
            self.assertEqual("warn", report.modules["Sequence Length Distribution"])
            self.assertTrue(report.has_report(), "the HTML was not kept")
        self.assertIn("FastQC: s1_R1.fastq -- 2,000 reads", run.log)

    def test_fastqc_reads_the_reads_as_uploaded_before_fastp_touches_them(self):
        response = self._launch()
        run = BreseqRun.objects.get(pk=response.json()["run_ids"][0])

        calls = self._recorded(self.fastqc_record)
        self.assertEqual(1, len(calls), "one FastQC invocation for the pair")
        inputs = calls[0]["argv"][calls[0]["argv"].index("--outdir") + 2:]
        self.assertEqual([os.path.join(run.reads_dir(), "s1_R1.fastq"),
                          os.path.join(run.reads_dir(), "s1_R2.fastq")], inputs)
        self.assertIn("--noextract", calls[0]["argv"])
        # Ordered in the job log as they ran: FastQC finished before fastp started.
        self.assertLess(run.log.index("Analysis complete"), run.log.index("total reads"))

    def test_fastqc_runs_with_the_tools_jvm_first_on_path(self):
        """bioconda's FastQC is a Perl wrapper that runs a bare `java`, and bioconda's openjdk
        puts that in lib/jvm/bin rather than bin/. Without this the run turns on whether the
        host has a java -- which a developer Mac does, and a clean machine does not."""
        jvm = os.path.join(self.tools, "lib", "jvm")
        os.makedirs(os.path.join(jvm, "bin"))

        self._launch()

        call = self._recorded(self.fastqc_record)[0]
        entries = call["path"].split(os.pathsep)
        self.assertEqual([os.path.join(self.tools, "bin"), os.path.join(jvm, "bin")],
                         entries[:2])
        self.assertEqual(jvm, call["java_home"])

    def test_the_sample_page_links_each_report(self):
        self._launch()
        sample = Sample.objects.get()
        report = FastqcReport.objects.get(file_name="s1_R1.fastq")

        page = self.client.get("/mutations/breseq", {"experiment_id": self.experiment.pk,
                                                     "sample_id": sample.pk})

        self.assertContains(page, 'href="%s"' % reverse("fastqc_report", args=(report.pk,)))
        self.assertContains(page, "FastQC: s1_R1.fastq &raquo;")
        self.assertContains(page, "FastQC: s1_R2.fastq &raquo;")

    def test_the_report_is_served_sandboxed(self):
        self._launch()
        report = FastqcReport.objects.get(file_name="s1_R1.fastq")

        response = self.client.get(reverse("fastqc_report", args=(report.pk,)))

        self.assertEqual(200, response.status_code)
        self.assertEqual("sandbox", response["Content-Security-Policy"])
        self.assertEqual("nosniff", response["X-Content-Type-Options"])
        self.assertTrue(response["Content-Type"].startswith("text/html"))
        self.assertIn(b"FastQC report for s1_R1.fastq", b"".join(response.streaming_content))

    def test_a_reader_who_cannot_see_the_project_gets_a_404(self):
        self._launch()
        url = reverse("fastqc_report", args=(FastqcReport.objects.first().pk,))

        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        self.client.force_login(stranger)
        self.assertEqual(404, self.client.get(url).status_code)
        self.client.logout()
        self.assertEqual(404, self.client.get(url).status_code)
        self.assertEqual(404, self.client.get(reverse("fastqc_report", args=(999999,)))
                         .status_code)

    def test_fastqc_failing_is_a_note_and_the_sample_still_imports(self):
        os.environ["FAKE_FASTQC_FAIL"] = "3"
        self.addCleanup(os.environ.pop, "FAKE_FASTQC_FAIL", None)

        response = self._launch()

        run = BreseqRun.objects.get(pk=response.json()["run_ids"][0])
        self.assertEqual(run.status, STATUS_IMPORTED, run.error)
        self.assertTrue(any("FastQC exited 3" in note for note in run.notes), run.notes)
        self.assertFalse(FastqcReport.objects.exists())

    def test_output_that_cannot_be_read_is_a_note_too(self):
        os.environ["FAKE_FASTQC_GARBAGE"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_FASTQC_GARBAGE", None)

        response = self._launch()

        run = BreseqRun.objects.get(pk=response.json()["run_ids"][0])
        self.assertEqual(run.status, STATUS_IMPORTED, run.error)
        self.assertTrue(any("could not be read" in note for note in run.notes), run.notes)
        self.assertFalse(FastqcReport.objects.exists())

    def test_a_failed_run_leaves_no_reports_behind(self):
        os.environ["FAKE_FASTP_FAIL"] = "2"
        self.addCleanup(os.environ.pop, "FAKE_FASTP_FAIL", None)

        self._launch()

        run = BreseqRun.objects.get()
        self.assertEqual(run.status, STATUS_FAILED)
        self.assertTrue(self._recorded(self.fastqc_record), "FastQC never ran")
        self.assertFalse(FastqcReport.objects.exists())
        component_root = os.path.join(self.store, "components", "mutint_fastqc")
        self.assertEqual([], os.listdir(component_root) if os.path.isdir(component_root)
                         else [])

    def test_running_a_sample_again_replaces_its_reports(self):
        self._launch()
        first = set(FastqcReport.objects.values_list("pk", flat=True))
        old_dirs = [report.directory() for report in FastqcReport.objects.all()]

        self._launch()

        sample = Sample.objects.get()
        now = FastqcReport.objects.filter(sample=sample)
        self.assertEqual(2, now.count())
        self.assertFalse(first & set(now.values_list("pk", flat=True)))
        self.assertFalse(any(os.path.exists(path) for path in old_dirs))

    def test_deleting_the_sample_takes_the_reports_and_their_html(self):
        self._launch()
        directories = [report.directory() for report in FastqcReport.objects.all()]

        Sample.objects.get().delete()

        self.assertFalse(FastqcReport.objects.exists())
        self.assertFalse(any(os.path.exists(path) for path in directories))

    def test_left_unticked_it_does_not_run(self):
        self._launch(read_steps=["trim"])
        self.assertEqual([], self._recorded(self.fastqc_record))
        self.assertFalse(FastqcReport.objects.exists())

    def test_storage_counts_the_html_and_clearing_keeps_the_numbers(self):
        self._launch()
        self.assertGreater(step.measure(self.experiment), 0)

        step.clear(self.experiment)

        self.assertEqual(0, step.measure(self.experiment))
        self.assertEqual(2, FastqcReport.objects.count())
        self.assertEqual([], step.sample_links(Sample.objects.get(), None))


@skipUnless(BRESEQ, "mutint-fastqc's producer, mutint-breseq, is not installed")
class MissingFastqcTestCase(_Setup, TestCase):

    def setUp(self):
        super().setUp()
        os.remove(os.path.join(self.tools, "bin", "fastqc"))
        patcher = mock.patch.dict(os.environ, {"PATH": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_launcher_offers_it_disabled_and_says_why(self):
        page = self.client.get("/breseq/?experiment_id=%s" % self.experiment.id).content.decode()
        checkbox = page[page.index('value="fastqc"'):][:200]
        self.assertIn("disabled", checkbox)
        self.assertIn("fastqc is not installed", page)

    def test_asking_for_it_is_refused_at_launch(self):
        response = self._launch(read_steps=["fastqc"])
        self.assertEqual(400, response.status_code)
        self.assertEqual("read_steps", response.json()["field"])

    def test_gone_by_the_time_the_worker_runs_is_a_note(self):
        with mock.patch("mutint_common.read_step_registry.ReadStep.available",
                        return_value=(True, "")):
            response = self._launch(read_steps=["fastqc"])
        run = BreseqRun.objects.get(pk=response.json()["run_ids"][0])
        self.assertEqual(run.status, STATUS_IMPORTED, run.error)
        self.assertTrue(any("FastQC is not installed" in note for note in run.notes))


DATABASE_BACKEND = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}


@skipUnless(BRESEQ, "mutint-fastqc's producer, mutint-breseq, is not installed")
@override_settings(TASKS=DATABASE_BACKEND)
class CancelledRunTestCase(_Setup, TestCase):
    """Against the database backend, so launching queues the run rather than finishing it."""

    def test_a_run_cancelled_after_fastqc_discards_its_reports(self):
        self._launch()
        run = BreseqRun.objects.get()

        # Cancelled from the moment FastQC has written a report: the next poll is the one
        # `run_read_steps` makes before fastp.
        with mock.patch.object(breseq_tasks.jobs, "is_cancelled",
                               side_effect=lambda *args: FastqcReport.objects.exists()):
            self.assertIsNone(breseq_tasks.run_breseq.call(None, run.pk))

        run.refresh_from_db()
        self.assertEqual(run.status, STATUS_CANCELLED)
        self.assertTrue(self._recorded(self.fastqc_record), "FastQC never ran")
        self.assertEqual([], self._recorded(self.fastp_record))
        self.assertFalse(FastqcReport.objects.exists())
