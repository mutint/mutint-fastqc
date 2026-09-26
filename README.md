# mutint-fastqc

Run [FastQC](https://www.bioinformatics.babraham.ac.uk/projects/fastqc/) on the reads a
sample is made from, inside [MutInt](https://github.com/mutint/mutint-core), and link each
file's report from the top of the sample's page.

It is the first consumer of core's **read steps**. A read step is something a component does
to a sample's reads before a producer predicts mutations from them. This plugin's step is an
`inspect` step, so it sees the reads as they were uploaded, before fastp trims them. Whichever
producer honours the registry runs it, and today that is
[mutint-breseq](https://github.com/mutint/mutint-breseq). The launcher shows it as a checkbox.

Each read file's summary numbers are kept in a table:

- reads;
- length range;
- GC content;
- encoding;
- every FastQC module's pass, warn or fail.

FastQC's own HTML report is served sandboxed.

**FastQC is provisioned by `tools.txt`** from bioconda, like every other external tool.
Where it is missing, the checkbox is disabled and says why.

## Installing

```bash
git submodule add ../mutint-fastqc mutint-fastqc
```

It needs a producer to be of any use, so install it beside mutint-breseq.

MIT licensed. See [mutint-core](https://github.com/mutint/mutint-core) for the platform.
