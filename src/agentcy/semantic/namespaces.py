from __future__ import annotations

import os
from rdflib import Namespace


def _base_uri() -> str:
    base = os.getenv("AGENTCY_BASE_URI", "http://agentcy.ai/")
    if not base.endswith("/"):
        base += "/"
    return base


BASE_URI = _base_uri()
ONTOLOGY = Namespace(f"{BASE_URI}ontology#")
RESOURCE = Namespace(f"{BASE_URI}resource/")
PROV = Namespace("http://www.w3.org/ns/prov#")
SH = Namespace("http://www.w3.org/ns/shacl#")
