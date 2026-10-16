-- 04_where_filter.sql — WHERE clause examples

-- 1. Simple comparison
SELECT name, age FROM students WHERE age > 21;

-- 2. AND condition
SELECT name, gpa FROM students WHERE age >= 21 AND gpa >= 3.5;

-- 3. OR condition
SELECT name, active FROM students WHERE age < 21 OR active = FALSE;

-- 4. NOT condition
SELECT name, gpa FROM students WHERE NOT active = FALSE;

-- 5. BETWEEN
SELECT name, age FROM students WHERE age BETWEEN 20 AND 22;

-- 6. IN list
SELECT name FROM students WHERE age IN (20, 21, 22);

-- 7. LIKE pattern
SELECT name FROM students WHERE name LIKE 'A%';

-- 8. IS NULL
SELECT name, manager_id FROM employees WHERE manager_id IS NULL;

-- 9. IS NOT NULL
SELECT name, manager_id FROM employees WHERE manager_id IS NOT NULL;

-- 10. Complex expression
SELECT name, salary FROM employees
WHERE salary > 70000 AND department = 'Engineering';
