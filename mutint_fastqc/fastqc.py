"""FastQC's command line, the names it writes, and what its output says. Runs nothing.

Pure on purpose, in the shape `mutint_breseq.runner` is: the rules most likely to be broken by
accident -- which files FastQC is handed, where its report lands, which numbers are read out
of it -- are testable against a real `fastqc_data.txt` without FastQC, a worker or a request.
`tests/data/fastqc_data.txt` is one captured from FastQC 0.12.1 run on the bioconda build this
component's `tools.txt` installs.
"""

import os
import re
import zipfile

FASTQC = "fastqc"

# What FastQC is handed. Its own suffixes -- it would read a SAM or BAM too, but a producer
# hands those over for alignment, not as sequencing reads anybody wants a QC report on.
_FASTQ_RE = re.compile(r"\.(fastq|fq)(\.gz|\.bz2)?$", re.IGNORECASE)

# FastQC's own rule for naming its output, from `HTMLReportArchive`: these suffixes stripped
# **in this order**, each at most once and case-sensitively, then `_fastqc` appended. The order
# is why `x.fastq.gz` becomes `x_fastqc` and `x.gz.fastq` becomes `x.gz_fastqc`.
_FASTQC_STRIPPED = (".gz", ".bz2", ".txt", ".fastq", ".fq", ".csfastq", ".sam", ".bam")

# One thread per file is all FastQC will use, and it asks for 512 MB of heap per thread.
MAX_THREADS = 4


class FastqcOutputError(ValueError):
    """FastQC's output is missing, or does not say what this reads out of it."""


def is_fastq(path):
    return bool(_FASTQ_RE.search(os.path.basename(path)))


def report_stem(path):
    """`<stem>` in FastQC's `<stem>_fastqc.html` and `<stem>_fastqc.zip` for this input."""
    name = os.path.basename(path)
    for suffix in _FASTQC_STRIPPED:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name + "_fastqc"


def output_paths(out_dir, path):
    """`(html, zip)` FastQC writes into `out_dir` for the input `path`."""
    stem = report_stem(path)
    return (os.path.join(out_dir, stem + ".html"), os.path.join(out_dir, stem + ".zip"))


def batches(paths):
    """`paths` split so that no two in one batch share a report name.

    FastQC writes every report into one `--outdir`, so `a.fastq` and `a.fq` in one invocation
    would overwrite each other's report with no error. Almost every drop is one batch.
    """
    result = []
    for path in paths:
        stem = report_stem(path)
        for batch in result:
            if stem not in {report_stem(other) for other in batch}:
                batch.append(path)
                break
        else:
            result.append([path])
    return result


def threads_for(count, processors=None):
    """As many as there are files, capped, and never more than the machine can spare."""
    if processors is None:
        processors = max(1, (os.cpu_count() or 2) - 2)
    return max(1, min(count, processors, MAX_THREADS))


def build_argv(fastqc, out_dir, paths, threads=1):
    """FastQC's command line for `paths`, reports into `out_dir`.

    `--noextract` leaves the zip whole: only `fastqc_data.txt` is read out of it, and the HTML
    beside it is the report a person opens -- one self-contained file with its images inline.
    """
    return [fastqc, "--noextract", "--threads", str(threads), "--outdir", out_dir] + list(paths)


def parse_data(text):
    """What `fastqc_data.txt` says, as a dict.

    `version`, `encoding`, `total_sequences`, `poor_quality`, `length_min`, `length_max`,
    `gc_percent`, `total_bases` (FastQC's own rounded words, e.g. `247.7 kbp`, since 0.12) and
    `modules`, `{module name: 'pass' | 'warn' | 'fail'}` in FastQC's order.

    Raises FastqcOutputError when there is no Basic Statistics module or no sequence count:
    those are what make a report worth recording at all.
    """
    result = {"version": "", "encoding": "", "total_sequences": None, "poor_quality": None,
              "length_min": None, "length_max": None, "gc_percent": None, "total_bases": "",
              "modules": {}}
    module = None
    basic = {}
    for line in text.splitlines():
        if line.startswith("##FastQC"):
            result["version"] = line.split("\t", 1)[1].strip() if "\t" in line else ""
        elif line.startswith(">>END_MODULE"):
            module = None
        elif line.startswith(">>"):
            name, _tab, status = line[2:].partition("\t")
            module = name.strip()
            result["modules"][module] = status.strip().lower()
        elif module == "Basic Statistics" and line and not line.startswith("#"):
            key, _tab, value = line.partition("\t")
            basic[key.strip()] = value.strip()

    if "Basic Statistics" not in result["modules"] or "Total Sequences" not in basic:
        raise FastqcOutputError("FastQC's output has no Basic Statistics.")

    result["encoding"] = basic.get("Encoding", "")
    result["total_bases"] = basic.get("Total Bases", "")
    result["total_sequences"] = _integer(basic.get("Total Sequences"))
    result["poor_quality"] = _integer(basic.get("Sequences flagged as poor quality"))
    result["length_min"], result["length_max"] = _length_range(basic.get("Sequence length"))
    result["gc_percent"] = _number(basic.get("%GC"))
    if result["total_sequences"] is None:
        raise FastqcOutputError("FastQC's output has no sequence count.")
    return result


def parse_zip(zip_path):
    """`parse_data` of the `fastqc_data.txt` inside FastQC's zip."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [name for name in archive.namelist()
                       if name.endswith("/fastqc_data.txt") or name == "fastqc_data.txt"]
            if not members:
                raise FastqcOutputError("%s holds no fastqc_data.txt."
                                        % os.path.basename(zip_path))
            text = archive.read(members[0]).decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile) as exc:
        raise FastqcOutputError("%s could not be read: %s" % (os.path.basename(zip_path), exc))
    return parse_data(text)


def _integer(value):
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _length_range(value):
    """`151` -> (151, 151); `35-151` -> (35, 151); anything else -> (None, None)."""
    if not value:
        return None, None
    low, _dash, high = str(value).partition("-")
    low, high = _integer(low), _integer(high or low)
    if low is None or high is None:
        return None, None
    return low, high
