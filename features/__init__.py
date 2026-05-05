"""Feature lenses over the jobs DB.

Each subpackage is a self-contained lens: it loads jobs, analyses them,
renders to console, and exports to Excel. Add a new lens by creating
`features/<name>/` with the same shape and wiring it in `main.py`.
"""
