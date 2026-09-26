from django.apps import AppConfig


class FastqcConfig(AppConfig):
    name = 'mutint_fastqc'
    verbose_name = 'FastQC'

    def ready(self):
        from django.urls import include, re_path
        from mutint_common.about_registry import register_about_section
        from mutint_common.plugin_registry import register_plugin_urlpatterns
        from mutint_common.read_step_registry import STAGE_INSPECT, register_read_step
        from mutint_common.sample_link_registry import register_sample_link
        from mutint_common.storage_registry import register_storage_kind
        from mutint_fastqc import step
        from mutint_fastqc.version import __version__

        # The whole of what this does: a step on a sample's reads, run by whichever producer
        # honours core's read-step registry -- mutint-breseq's launcher draws it as a checkbox.
        # An `inspect` step, so it sees the reads as uploaded, before fastp trims them.
        register_read_step(
            self, step.NAME, 'FastQC report', step.run,
            stage=STAGE_INSPECT, default=True, available=step.available,
            attach=step.attach, discard=step.discard,
            description=('Runs FastQC on each read file as it was uploaded, before any '
                         'trimming, and links the report from the sample\'s page.'))
        # Where the reports are seen: a link per read file in the box at the top of the
        # sample's Mutations page, to FastQC's own HTML, served sandboxed from here.
        register_sample_link(self, 'fastqc', step.sample_links)
        register_plugin_urlpatterns([
            re_path(r'^fastqc/', include('mutint_fastqc.urls')),
        ])
        # The HTML is most of a report's size -- about 650 KB a file -- so it is counted and
        # can be cleared. Clearing keeps the numbers and loses the link.
        register_storage_kind(
            self, key='fastqc', label='FastQC reports', measure=step.measure,
            clear=step.clear,
            description=('FastQC\'s HTML report on each read file. Clearing keeps the '
                         'summary numbers and removes the link to the full report.'))
        register_about_section(self, name='mutint-fastqc', version=__version__,
                               template='about/sections/mutint_fastqc.html')

        # Nothing else is registered, and each absence is a decision:
        #
        # **No nav entry, no page and no import tab.** A report is about one sample's reads,
        # so the sample's own page is where it is linked from; a page listing every report
        # would be a second place to look for the same thing.
        #
        # **No rebuilder.** Nothing here is derived from the mutations. A report describes
        # reads, and is made once, when they are.
        #
        # **No export type, no example dataset.** It adds no mutation type, and an example
        # would have to ship reads and run FastQC to demonstrate anything.
