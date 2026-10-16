-- 03_simple_select.sql — Basic SELECT queries

-- 1. Select all students
SELECT * FROM students;

-- 2. Select specific columns
SELECT name, age, gpa FROM students;

-- 3. Alias column
SELECT name, gpa AS grade_point_average FROM students;

-- 4. LIMIT result
SELECT name, age FROM students LIMIT 5;

-- 5. Select with constant expression
SELECT name, age, age + 1 AS next_year_age FROM students;
