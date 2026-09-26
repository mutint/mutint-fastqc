"""A stand-in for FastQC, on the same terms as mutint-breseq's `fake_breseq`: a real executable
on disk rather than a patched `subprocess`, because the things most likely to be wrong here are
the argv and the environment it runs in -- `java` on PATH above all -- and a patch would assert
against the call rather than against a process that actually has to start.

It records its argv, PATH and JAVA_HOME, one JSON line per call, to `$FAKE_FASTQC_ARGV`, and
writes for each input the two files FastQC writes: `<stem>_fastqc.zip`, holding the
`fastqc_data.txt` captured from a real FastQC 0.12.1 run (`data/fastqc_data.txt`) with its
Filename line changed, and `<stem>_fastqc.html`.
"""

import os
import stat
import sys
import textwrap

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fastqc_data.txt")

SCRIPT = textwrap.dedent('''\
    #!{python}
    """Stand-in FastQC. Writes a report per input, records its argv."""
    import json
    import os
    import sys
    import zipfile

    argv = sys.argv[1:]
    record = os.environ.get("FAKE_FASTQC_ARGV")
    if record:
        with open(record, "a") as handle:
            handle.write(json.dumps({{"argv": argv, "path": os.environ.get("PATH", ""),
                                     "java_home": os.environ.get("JAVA_HOME")}}) + "\\n")

    if os.environ.get("FAKE_FASTQC_FAIL"):
        sys.stderr.write("Failed to process file: not a FASTQ\\n")
        sys.exit(int(os.environ["FAKE_FASTQC_FAIL"]))

    out_dir = argv[argv.index("--outdir") + 1]
    inputs = [arg for arg in argv[argv.index("--outdir") + 2:]]
    with open({data!r}) as handle:
        template = handle.read()

    for path in inputs:
        name = os.path.basename(path)
        for suffix in (".gz", ".bz2", ".txt", ".fastq", ".fq", ".csfastq", ".sam", ".bam"):
            if name.endswith(suffix):
                name = name[:-len(suffix)]
        stem = name + "_fastqc"
        if os.environ.get("FAKE_FASTQC_GARBAGE"):
            with open(os.path.join(out_dir, stem + ".zip"), "w") as handle:
                handle.write("not a zip")
        else:
            text = template.replace("Filename\\tS1_R1.fastq.gz",
                                    "Filename\\t" + os.path.basename(path))
            with zipfile.ZipFile(os.path.join(out_dir, stem + ".zip"), "w") as archive:
                archive.writestr(stem + "/fastqc_data.txt", text)
        with open(os.path.join(out_dir, stem + ".html"), "w") as handle:
            handle.write("<html><body>FastQC report for %s</body></html>" % os.path.basename(path))
        sys.stdout.write("Analysis complete for %s\\n" % os.path.basename(path))
''')


def install(tools_dir):
    """Write the fake into ``<tools_dir>/bin/fastqc`` and return its path."""
    bin_dir = os.path.join(tools_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    path = os.path.join(bin_dir, "fastqc")
    with open(path, "w") as handle:
        handle.write(SCRIPT.format(python=sys.executable, data=DATA))
    os.chmod(path, stat.S_IRWXU)
    return path
