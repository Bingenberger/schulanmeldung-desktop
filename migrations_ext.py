"""Database schema migration extension."""

from flask_migrate import Migrate


migrate = Migrate(compare_type=True, render_as_batch=True)
