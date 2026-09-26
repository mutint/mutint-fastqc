"""The pure half: FastQC's argv, its output names, and what is read out of its report.

Parsed against `data/fastqc_data.txt`, captured from FastQC 0.12.1 -- the bioconda build this
component's tools.txt installs -- run on 2 000 reads of 100 to 151 bp.
"""

import os
import tempfile
import zipfile

from django.test import SimpleTestCase

from mutint_fastqc import fastqc

DATA = os.path.join(os.path.dirname(__file__), "data", "fastqc_data.txt")


class NamesTestCase(SimpleTestCase):

    def test_fastqs_are_recognised_compressed_or_not(self):
        for name in ("a.fastq", "a.fq", "a.fastq.gz", "a.FQ.GZ", "a_R1.unpaired.fastq.bz2"):
            self.assertTrue(fastqc.is_fastq(name), name)
        for name in ("a.sam", "a.bam", "a.fasta", "metadata.csv", "a.fastq.zip"):
            self.assertFalse(fastqc.is_fastq(name), name)

    def test_report_names_follow_fastqcs_own_rule(self):
        """Suffixes stripped in FastQC's order, each once -- or the report is looked for under
        a name FastQC never wrote, and every run notes it could not be read."""
        self.assertEqual("S1_R1_fastqc", fastqc.report_stem("/x/S1_R1.fastq.gz"))
        self.assertEqual("S1_R2_fastqc", fastqc.report_stem("S1_R2.fq"))
        self.assertEqual("a.unpaired_fastqc", fastqc.report_stem("a.unpaired.fastq"))
        self.assertEqual("a.gz_fastqc", fastqc.report_stem("a.gz.fastq"))

    def test_files_that_would_share_a_report_go_in_separate_batches(self):
        self.assertEqual([["/a/x.fastq", "/a/y.fq"], ["/a/x.fq.gz"]],
                         fastqc.batches(["/a/x.fastq", "/a/y.fq", "/a/x.fq.gz"]))

    def test_the_argv_leaves_the_zip_whole_and_names_the_outdir(self):
        argv = fastqc.build_argv("/bin/fastqc", "/out", ["/r/a.fastq", "/r/b.fastq"], threads=2)
        self.assertEqual(["/bin/fastqc", "--noextract", "--threads", "2", "--outdir", "/out",
                          "/r/a.fastq", "/r/b.fastq"], argv)

    def test_threads_are_one_per_file_and_capped(self):
        self.assertEqual(1, fastqc.threads_for(1, processors=16))
        self.assertEqual(2, fastqc.threads_for(2, processors=16))
        self.assertEqual(fastqc.MAX_THREADS, fastqc.threads_for(40, processors=16))
        self.assertEqual(1, fastqc.threads_for(4, processors=1))


class ParseTestCase(SimpleTestCase):

    def setUp(self):
        with open(DATA) as handle:
            self.text = handle.read()

    def test_a_real_report_reads_back(self):
        stats = fastqc.parse_data(self.text)

        self.assertEqual("0.12.1", stats["version"])
        self.assertEqual("Sanger / Illumina 1.9", stats["encoding"])
        self.assertEqual(2000, stats["total_sequences"])
        self.assertEqual(0, stats["poor_quality"])
        self.assertEqual((100, 151), (stats["length_min"], stats["length_max"]))
        self.assertEqual(49, stats["gc_percent"])
        self.assertEqual("247.7 kbp", stats["total_bases"])
        self.assertEqual("pass", stats["modules"]["Basic Statistics"])
        self.assertEqual("warn", stats["modules"]["Sequence Length Distribution"])
        # Every module and verdict FastQC itself wrote into summary.txt beside it, in order.
        with open(os.path.join(os.path.dirname(DATA), "summary.txt")) as handle:
            summary = [line.split("\t")[:2] for line in handle.read().splitlines() if line]
        self.assertEqual([[verdict.lower(), name] for verdict, name in summary],
                         [[verdict, name] for name, verdict in stats["modules"].items()])

    def test_one_length_is_a_range_of_one(self):
        stats = fastqc.parse_data(self.text.replace("Sequence length\t100-151",
                                                    "Sequence length\t151"))
        self.assertEqual((151, 151), (stats["length_min"], stats["length_max"]))

    def test_no_basic_statistics_is_refused(self):
        with self.assertRaises(fastqc.FastqcOutputError):
            fastqc.parse_data("##FastQC\t0.12.1\n>>Per base sequence quality\tpass\n")

    def test_the_zip_is_read_where_fastqc_puts_the_data(self):
        path = os.path.join(tempfile.mkdtemp(), "S1_R1_fastqc.zip")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("S1_R1_fastqc/fastqc_data.txt", self.text)
        self.assertEqual(2000, fastqc.parse_zip(path)["total_sequences"])

    def test_an_unreadable_zip_is_an_output_error_not_a_crash(self):
        path = os.path.join(tempfile.mkdtemp(), "x_fastqc.zip")
        with open(path, "w") as handle:
            handle.write("not a zip")
        with self.assertRaises(fastqc.FastqcOutputError):
            fastqc.parse_zip(path)
