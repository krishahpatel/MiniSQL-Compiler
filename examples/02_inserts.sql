-- 02_inserts.sql — Sample data inserts
-- Populate the school database with sample data.

INSERT INTO students (id, name, age, gpa, active) VALUES
    (1, 'Alice Johnson',  20, 3.8, TRUE),
    (2, 'Bob Smith',      22, 3.2, TRUE),
    (3, 'Carol White',    21, 3.9, TRUE),
    (4, 'David Brown',    23, 2.7, FALSE),
    (5, 'Emma Davis',     20, 3.5, TRUE),
    (6, 'Frank Miller',   24, 3.1, TRUE),
    (7, 'Grace Wilson',   22, 3.7, TRUE),
    (8, 'Henry Moore',    21, 2.9, FALSE);

INSERT INTO courses (course_id, title, credits, department) VALUES
    (101, 'Compiler Design',       3, 'CS'),
    (102, 'Database Systems',      4, 'CS'),
    (103, 'Operating Systems',     3, 'CS'),
    (104, 'Linear Algebra',        3, 'MATH'),
    (105, 'Machine Learning',      4, 'CS'),
    (106, 'Discrete Mathematics',  3, 'MATH');

INSERT INTO enrollments (student_id, course_id, grade, semester) VALUES
    (1, 101, 9.2, 'Fall2024'),
    (1, 102, 8.8, 'Fall2024'),
    (2, 101, 7.5, 'Fall2024'),
    (2, 103, 8.1, 'Fall2024'),
    (3, 102, 9.8, 'Fall2024'),
    (3, 105, 9.5, 'Fall2024'),
    (4, 104, 6.2, 'Fall2024'),
    (5, 101, 8.9, 'Fall2024'),
    (5, 105, 9.1, 'Fall2024'),
    (6, 102, 7.8, 'Fall2024'),
    (7, 103, 8.6, 'Fall2024'),
    (8, 104, 5.9, 'Fall2024');

INSERT INTO employees (emp_id, name, department, salary, manager_id) VALUES
    (1,  'Sarah Connor',   'Engineering', 95000.0, NULL),
    (2,  'John Doe',       'Engineering', 75000.0, 1),
    (3,  'Jane Smith',     'Marketing',   65000.0, NULL),
    (4,  'Mike Johnson',   'Engineering', 80000.0, 1),
    (5,  'Lisa Anderson',  'Marketing',   55000.0, 3),
    (6,  'Tom Wilson',     'HR',          60000.0, NULL),
    (7,  'Emma Brown',     'Engineering', 85000.0, 1),
    (8,  'Chris Taylor',   'HR',          58000.0, 6),
    (9,  'Amy Martinez',   'Engineering', 72000.0, 4),
    (10, 'Robert Garcia',  'Marketing',   70000.0, 3);
