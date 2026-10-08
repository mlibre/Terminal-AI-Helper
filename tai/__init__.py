"""tai — embedded-first terminal autocomplete.

Deliberately empty. Every caller imports the module it needs — `tai.engine`,
`tai.store`, `tai.predictor` — and re-exporting them here meant that *any* import
of a submodule dragged three more in first: `from tai.store import db_path` pulled
in `predictor -> fresh -> engine` for names nothing read. A package root that
names nothing is also a root that cannot drift.
"""
