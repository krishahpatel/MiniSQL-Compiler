-- 05_joins.sql — JOIN examples

-- 1. INNER JOIN: students with their enrollments
SELECT s.name, e.course_id, e.grade
FROM students s
JOIN enrollments e ON s.id = e.student_id;

-- 2. Join with WHERE
SELECT s.name, c.title, e.grade
FROM students s
JOIN enrollments e ON s.id = e.student_id
JOIN courses c ON e.course_id = c.course_id
WHERE e.grade >= 9.0;

-- 3. Self-join: employees with their managers
SELECT e.name, m.name AS manager_name
FROM employees e
JOIN employees m ON e.manager_id = m.emp_id;

-- 4. JOIN with ORDER BY
SELECT s.name, c.title, e.grade
FROM students s
JOIN enrollments e ON s.id = e.student_id
JOIN courses c ON e.course_id = c.course_id
ORDER BY e.grade DESC
LIMIT 5;
