"""conftest.py — Pytest Fixtures for MiniSQL Tests."""
import pytest
from minisql.catalog import Catalog, TableSchema, ColumnSchema
from minisql.storage import Storage

@pytest.fixture
def catalog():
    cat = Catalog()
    cat.add_table(TableSchema(
        name="students",
        columns=[
            ColumnSchema("id", "INT", 0, primary_key=True),
            ColumnSchema("name", "TEXT", 1),
            ColumnSchema("age", "INT", 2),
            ColumnSchema("gpa", "FLOAT", 3),
            ColumnSchema("active", "BOOL", 4),
        ]
    ))
    cat.add_table(TableSchema(
        name="courses",
        columns=[
            ColumnSchema("course_id", "TEXT", 0, primary_key=True),
            ColumnSchema("title", "TEXT", 1),
            ColumnSchema("credits", "INT", 2),
        ]
    ))
    cat.set_row_count("students", 5)
    cat.set_row_count("courses", 3)
    return cat

@pytest.fixture
def storage(catalog):
    st = Storage(catalog)
    st.create_table(catalog.get_table("students"))
    st.create_table(catalog.get_table("courses"))

    tbl_s = st.get_table("students")
    tbl_s.insert({"id": 1, "name": "Alice", "age": 20, "gpa": 3.8, "active": True})
    tbl_s.insert({"id": 2, "name": "Bob", "age": 22, "gpa": 3.2, "active": True})
    tbl_s.insert({"id": 3, "name": "Carol", "age": 21, "gpa": 3.9, "active": False})

    return st
