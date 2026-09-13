"""Registered content boundaries for ordinary merge guards."""
import re
EVENT = re.compile(r".*-\d\d$")
REPORTING = {"say-01": "say"}
SCOPES = {"want-01":"want", "believe-01":"believe", "hope-01":"hope", **REPORTING, "possible-01":"possible"}
