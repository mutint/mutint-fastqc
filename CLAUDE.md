# CLAUDE.md — mutint-fastqc

Guidance for Claude Code working in this repository.

It is a **submodule of `mutint`**. Edit it **here**, in the suite-root checkout,
never in `mutint/mutint-fastqc`. That copy is on a detached HEAD, and a commit made there is
reachable only by SHA inside that one clone. See the suite `CLAUDE.md`.

---

## What this is

This plugin runs FastQC on the reads a sample is made from and keeps two things:

- **the summary numbers**, one `FastqcReport` row per read file;
- **FastQC's own HTML report**, linked from the box at the top of the sample's Mutations page.

It has no page, no nav entry and no import tab. It is a **read step**: an entry in core's
`mutint_common.read_step_registry`. Whichever producer honours that registry runs it; today
that is mutint-breseq, whose launcher draws it as a checkbox.

The step is `STAGE_INSPECT`. Every inspect step runs before every transform step, so FastQC
sees the reads as uploaded, before fastp trims them. That ordering is set by the stage, not by
where this app happens to load.

---

## The pieces

| file | what |
|---|---|
| `fastqc.py` | Pure: the argv, FastQC's output-naming rule, and parsing `fastqc_data.txt` out of its zip. Runs nothing. |
| `step.py` | The read step (`run`, `available`, `attach`, `discard`), the sample links, and the storage kind's measure and clear. |
| `models.py` | `FastqcReport`, and the receiver that owns its directory. |
| `views.py` | Serves one report, sandboxed. |
| `tests/fake_fastqc.py` | A real executable standing in for FastQC. It writes the zip and HTML FastQC writes, with `tests/data/fastqc_data.txt` inside. |

`tests/data/fastqc_data.txt` and `summary.txt` were **captured from a real run**: FastQC 0.12.1
from bioconda, on 2 000 generated reads of 100 to 151 bp. The parser is tested against what
FastQC actually writes, not against a hand-made imitation.

---

## Things that are load-bearing

### FastQC runs a bare `java`, and bioconda's JVM is not in `bin/`

bioconda's `fastqc` is a Perl wrapper that finds `java` on `PATH`. The `openjdk` it depends on
installs the JVM at `lib/jvm/bin/java` and sets `JAVA_HOME` from an activation script, which
`run_tool` never runs. With only `<tools>/bin` prepended to `PATH`, FastQC therefore runs on
the **host's** java. A developer Mac has one at `/usr/bin/java`; a clean machine has none.

The step runs FastQC under `mutint_common.tools.tool_environment()`, which puts the JVM's
`bin` on `PATH` and sets `JAVA_HOME`. That helper was lifted into core for this plugin, from
mutint-refsniff's copy. This was verified against the real package with `PATH=/bin`, a path
on which no host java is reachable. `test_fastqc_runs_with_the_tools_jvm_first_on_path` pins
the environment.

### Advisory: nothing here fails a breseq run

A QC report that could not be made is worth a sentence in the run's notes (`ctx.note`), not
the loss of hours of breseq. Each of these produces a note, and the run goes on:

- FastQC missing on the worker;
- FastQC exiting nonzero;
- FastQC not starting;
- a zip that cannot be read.

Only two things propagate to the producer: a cancellation, and running past the run's
deadline. The second means breseq would have no time left anyway.

The **launch** is stricter. Asking for the step explicitly where FastQC is not installed is a
400 before anything is uploaded, because it is refused beside the checkbox that asked for it.
Leaving `read_steps` out takes the page's defaults, which exclude a step that cannot run.

### The sample does not exist while this runs

Rows are created with `sample=None`, keyed by `producer` (`mutint_breseq:<pk>`). Then one of
two things happens:

- **`attach`** fills in the sample. It first deletes that sample's reports from any *other*
  producer: a sample made again from new reads is no longer described by the old ones.
- **`discard`** deletes the rows when the run fails or is cancelled.

`run` also deletes any rows already under its own producer before it starts, so a job handed
to a worker twice does not double the links.

**One gap:** a worker killed outright calls neither `attach` nor `discard`, so its rows stay
unattached. Nothing links to them and nothing reaps them. They are small, and the HTML is
counted by the storage kind.

### FastQC's output names follow its own rule

`fastqc.report_stem` reproduces FastQC's `HTMLReportArchive` naming. It strips `.gz`, `.bz2`,
`.txt`, `.fastq`, `.fq`, `.csfastq`, `.sam` and `.bam`, **in that order**, each once and
case-sensitively. Getting this wrong would mean looking for a report under a name FastQC never
wrote, and every run would note that its report could not be read.

Two inputs whose names reduce to the same stem would overwrite each other in one `--outdir`.
`fastqc.batches` therefore splits them into separate invocations.

### Served with `Content-Security-Policy: sandbox`, and no flags

FastQC builds its report from a user-supplied filename and user-supplied reads, so it gets
the posture core gives breseq's report. Measured on 0.12.1, the report contains:

- no `<script>`;
- only inline `data:` images;
- only `#fragment` links.

So it needs **none** of the flags breseq's report needs. It opens top-level in a tab of its
own, so the session cookie authorises it and there is no frame or signed token. It was checked
in headless Chrome under the header, and every section and chart renders.

A report somebody may not read is a 404, not a 403.

### Storage: clearing keeps the numbers

Each HTML report is about 650 KB, and the summary numbers in the row are tiny. The storage
kind counts and clears only the HTML. After a clear the rows stay, and `sample_links` stops
offering a link to a report that is gone.

### The migration depends on core by `__first__`, and `makemigrations` undoes that

`0001_initial` depends on `('mutint_experiment', '__first__')` and
`('mutint_sample', '__first__')`, not on core's filenames. Core may renumber its history, and
core's `test_migrations` fails an assembled run on any cross-component edge that names a file.
**`makemigrations` writes the filenames back every time it regenerates**, so fix them by hand
whenever it runs.

---

## What it deliberately does not do

- **No page listing reports.** A report is about one sample's reads, and the sample's page is
  where the link is.
- **No QC of trimmed reads.** The user chose raw reads only. A second, transform-stage step
  would be the way to add it.
- **Nothing gates on QC.** A failing FastQC module does not stop a run. The step reports; it
  does not judge.
- **No rebuilder, no export type, no example dataset.** See `apps.py`.

---

## Tests

```bash
cd mutint && ./mutint test mutint_fastqc
```

That tests the submodule clone. To test uncommitted edits in this checkout, put it first on
`PYTHONPATH`: `PYTHONPATH=/path/to/mutint-code/mutint-fastqc ./mutint test mutint_fastqc`.

**27 tests.** The end-to-end ones go through mutint-breseq's launcher, using fake breseq, fake
fastp and `fake_fastqc`, and are skipped where mutint-breseq is not installed. The cancellation
test uses the database task backend, so that launching queues the run instead of finishing it.
