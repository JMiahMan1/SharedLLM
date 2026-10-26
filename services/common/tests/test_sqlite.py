"""The additive column migration every SQLModel service leans on."""

from sqlalchemy import inspect, text
from sqlmodel import Field, SQLModel, create_engine

from services.common.sqlite import add_missing_columns, apply_sqlite_pragmas


class Widget(SQLModel, table=True):
    __tablename__ = "widget"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(default="")
    # These two are the columns the hand-made table below will not have.
    colour: str = Field(default="red")
    size: int = Field(default=0)


def _engine():
    engine = create_engine("sqlite://")
    apply_sqlite_pragmas(engine)
    # The table as an older version of the model would have created it.
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE widget (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL DEFAULT '')"))
        conn.execute(text("INSERT INTO widget (id, name) VALUES (1, 'a')"))
        conn.commit()
    return engine


def test_a_column_the_model_added_is_created_on_startup():
    engine = _engine()
    added = add_missing_columns(engine, SQLModel.metadata)
    assert added == ["widget.colour", "widget.size"]
    columns = {column["name"] for column in inspect(engine).get_columns("widget")}
    assert {"id", "name", "colour", "size"} <= columns


def test_the_rows_already_there_survive_the_new_column():
    engine = _engine()
    add_missing_columns(engine, SQLModel.metadata)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT name, size FROM widget WHERE id = 1")).one()
    assert row[0] == "a"
    assert row[1] == 0


def test_running_it_again_adds_nothing():
    engine = _engine()
    add_missing_columns(engine, SQLModel.metadata)
    assert add_missing_columns(engine, SQLModel.metadata) == []


def test_another_dialect_is_left_alone():
    class Fake:
        class dialect:  # noqa: N801 - a stand-in for a real engine's dialect
            name = "postgresql"

    assert add_missing_columns(Fake(), SQLModel.metadata) == []


def test_a_table_that_does_not_exist_yet_is_skipped():
    # create_all owns table creation; this helper only alters what is there.
    engine = create_engine("sqlite://")
    assert add_missing_columns(engine, SQLModel.metadata) == []
