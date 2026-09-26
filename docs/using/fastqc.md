# Checking read quality

When you run breseq on reads from **Import data → Run breseq**, the launcher has a
**FastQC report** checkbox, which is on by default. When it is ticked,
[FastQC](https://www.bioinformatics.babraham.ac.uk/projects/fastqc/) runs on each read file
before anything else happens to it. That means the report describes the reads exactly as you
uploaded or downloaded them, before fastp trims them.

## Where the report is

Open the sample's **Mutations** page. The box at the top, which names the sample and the read
files it was made from, has one link per read file, such as **FastQC: s1_R1.fastq »**. Hover
over a link to see the summary:

- the number of reads;
- the read length;
- the GC content;
- any FastQC module that failed or warned.

Click the link to open FastQC's full report in a new tab.

The job's log also lists each file's summary, on `/jobs/` or from the run list.

## When there is no report

FastQC is **advisory**. If it cannot run, the breseq run still goes ahead and the run's notes
say why there is no report. This happens when FastQC is not installed on the machine that ran
the job, when it exits with an error, or when its output cannot be read.

If FastQC is not installed where you are working, the checkbox is shown disabled and gives
the reason. `./mutint install` provisions it from this component's `tools.txt`.

## What happens to reports

- **Running a sample again replaces its reports.** A sample made again from new reads is no
  longer described by the old ones.
- **A run that fails or is cancelled keeps no reports.**
- **Deleting a sample deletes its reports.**
- **Clearing "FastQC reports" in the Overview's Storage panel** removes the HTML reports, which
  are about 650 KB each, and keeps the summary numbers. The links go away with the HTML.
