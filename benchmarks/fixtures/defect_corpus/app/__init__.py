"""Deliberately defective benchmark fixture.

Every file here contains problems on purpose. See defects.json for the full
list, the evidence location for each one, and which analyzers are expected to
find it. Nothing in this package is imported by Sentinel's own code.
"""

__all__: list[str] = ["security", "views", "utils"]