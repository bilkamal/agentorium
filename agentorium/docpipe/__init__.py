"""Document pipeline: ingest, versions and (later) review/revision of DOCX/PDF documents."""
import logging

import structlog

# adeu logs every edit at debug level through structlog; keep the server log readable.
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
