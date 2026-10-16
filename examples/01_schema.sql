-- 01_schema.sql — CREATE TABLE definitions for the demo schema
-- This file sets up the school database schema.
-- Run first: python -m minisql run examples/01_schema.sql

CREATE TABLE students (
    id      INT PRIMARY KEY,
    name    TEXT,
    age     INT,
    gpa     FLOAT,
    active  BOOL
);

CREATE TABLE courses (
    course_id   INT PRIMARY KEY,
    title       TEXT,
    credits     INT,
    department  TEXT
);

CREATE TABLE enrollments (
    student_id  INT,
    course_id   INT,
    grade       FLOAT,
    semester    TEXT
);

CREATE TABLE employees (
    emp_id      INT PRIMARY KEY,
    name        TEXT,
    department  TEXT,
    salary      FLOAT,
    manager_id  INT
);
