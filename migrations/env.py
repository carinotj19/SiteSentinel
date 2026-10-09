"""Alembic environment integrated with Flask-Migrate's application context."""

from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config
fileConfig(config.config_file_name)

db = current_app.extensions["migrate"].db
target_metadata = db.metadata


def get_url():
    # Escaping % is required because Alembic config values use interpolation.
    return db.engine.url.render_as_string(hide_password=False).replace("%", "%%")


def run_migrations_offline():
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    with db.engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
