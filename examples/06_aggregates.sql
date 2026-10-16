-- 06_aggregates.sql — Aggregate function examples

-- 1. COUNT(*)
SELECT COUNT(*) AS total_students FROM students;

-- 2. COUNT with WHERE
SELECT COUNT(*) AS active_students FROM students WHERE active = TRUE;

-- 3. SUM
SELECT SUM(salary) AS total_payroll FROM employees;

-- 4. AVG
SELECT AVG(gpa) AS average_gpa FROM students WHERE active = TRUE;

-- 5. MIN / MAX
SELECT MIN(salary) AS min_sal, MAX(salary) AS max_sal FROM employees;

-- 6. COUNT with DISTINCT
SELECT COUNT(*) AS cs_courses FROM courses WHERE department = 'CS';
