"""test_e2e.py — End-to-End Pipeline Integration Tests"""
import pytest
from minisql.catalog import Catalog
from minisql.storage import Storage
from minisql.cli import _run_pipeline

def run_sql(sql: str, catalog=None, storage=None):
    if catalog is None:
        catalog = Catalog()
    if storage is None:
        storage = Storage(catalog)
    results = _run_pipeline(sql, catalog, storage, show=set())
    return results, catalog, storage

def test_e2e_create_insert_select():
    cat = Catalog()
    st  = Storage(cat)
    
    # 1. Create table
    sql1 = "CREATE TABLE items (id INT PRIMARY KEY, title TEXT, price FLOAT);"
    res1, cat, st = run_sql(sql1, cat, st)
    assert len(res1) == 1
    assert cat.has_table("items")

    # 2. Insert values
    sql2 = "INSERT INTO items VALUES (1, 'Book', 15.5), (2, 'Pen', 2.0);"
    res2, cat, st = run_sql(sql2, cat, st)
    assert len(res2) == 1
    assert res2[0][1].rows_affected == 2

    # 3. Select with WHERE
    sql3 = "SELECT title, price FROM items WHERE price > 5.0;"
    res3, cat, st = run_sql(sql3, cat, st)
    assert len(res3) == 1
    rows = res3[0][1].rows
    assert len(rows) == 1
    assert rows[0]["title"] == "Book"
    assert rows[0]["price"] == 15.5

def test_e2e_aggregate_and_group_by():
    cat = Catalog()
    st  = Storage(cat)
    run_sql("CREATE TABLE emp (id INT, dept TEXT, sal FLOAT);", cat, st)
    run_sql("INSERT INTO emp VALUES (1, 'CS', 5000), (2, 'CS', 7000), (3, 'EE', 4000);", cat, st)

    sql = "SELECT dept, SUM(sal) AS total, AVG(sal) AS avg_sal FROM emp GROUP BY dept ORDER BY total DESC;"
    res, cat, st = run_sql(sql, cat, st)
    rows = res[0][1].rows
    assert len(rows) == 2
    assert rows[0]["dept"] == "CS"
    assert rows[0]["total"] == 12000.0
    assert rows[0]["avg_sal"] == 6000.0

def test_e2e_three_table_join_returns_projected_rows():
    cat = Catalog()
    st = Storage(cat)
    run_sql(
        "CREATE TABLE students (id INT PRIMARY KEY, name TEXT, gpa FLOAT);",
        cat, st,
    )
    run_sql(
        "CREATE TABLE enrollments (student_id INT, course_id TEXT, grade TEXT);",
        cat, st,
    )
    run_sql(
        "CREATE TABLE courses (course_id TEXT PRIMARY KEY, title TEXT);",
        cat, st,
    )
    run_sql(
        "INSERT INTO students VALUES (1, 'Alice Johnson', 3.8), (2, 'Bob Smith', 3.2);",
        cat, st,
    )
    run_sql(
        "INSERT INTO enrollments VALUES (1, 'CS101', 'A'), (2, 'CS101', 'B');",
        cat, st,
    )
    run_sql("INSERT INTO courses VALUES ('CS101', 'Compiler Design');", cat, st)

    results, _, _ = run_sql(
        "SELECT s.name, c.title, e.grade "
        "FROM students s "
        "JOIN enrollments e ON s.id = e.student_id "
        "JOIN courses c ON e.course_id = c.course_id "
        "WHERE s.gpa >= 3.5;",
        cat, st,
    )

    assert results[0][1].rows == [{
        "s.name": "Alice Johnson",
        "c.title": "Compiler Design",
        "e.grade": "A",
    }]

def test_new_empty_table_row_count_starts_at_zero():
    cat = Catalog()
    st = Storage(cat)
    run_sql("CREATE TABLE empty_items (id INT);", cat, st)

    assert cat.get_row_count("empty_items") == 0

    run_sql("INSERT INTO empty_items VALUES (1);", cat, st)
    assert cat.get_row_count("empty_items") == 1
