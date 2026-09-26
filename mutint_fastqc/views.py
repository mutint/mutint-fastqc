"""Serving a FastQC report, which is HTML this codebase did not write.

FastQC builds its report from the read file's name and the reads themselves, both of which a
user supplied, so it is served the way mutint-core serves breseq's report: with
`Content-Security-Policy: sandbox`, which gives the document an opaque origin and so no access
to the reader's session or its CSRF token -- and `nosniff`.

**No flags on the sandbox, unlike breseq's.** Measured on FastQC 0.12.1: the report has no
`<script>`, every image is an inline `data:` URI, and every link is a `#fragment` inside the
page. It needs nothing a bare `sandbox` withholds, so it is granted nothing.

**No frame and no signed token either.** breseq's report is framed inside a MutInt page, and a
sandboxed frame's requests carry no cookie, so its files are authorised by a token in the URL.
This report is one file opened in a tab of its own: a top-level navigation carries the session,
the permission check below is the ordinary one, and the header is the whole of the containment.
"""

from django.http import Http404

from mutint_common.fileserve import serve_file
from mutint_experiment.permissions import can_view_project

from mutint_fastqc import fastqc
from mutint_fastqc.models import FastqcReport

REPORT_HEADERS = {"Content-Security-Policy": "sandbox",
                  "X-Content-Type-Options": "nosniff"}


def report(request, pk):
    """FastQC's HTML report for one read file.

    404 rather than 403 for a report somebody may not read, so the route cannot be used to
    discover which report ids exist -- the posture core's breseq report takes.
    """
    found = (FastqcReport.objects.filter(pk=pk)
             .select_related("experiment__project").first())
    if found is None or not can_view_project(request.user, found.experiment.project):
        raise Http404("No FastQC report.")
    return serve_file(request, found.report_path(),
                      fastqc.report_stem(found.file_name) + ".html",
                      headers=REPORT_HEADERS)
