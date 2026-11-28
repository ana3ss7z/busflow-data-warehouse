-- Staging: per-run working copy of the source. Truncated and refilled on every run,
-- no history. LIKE copies the column types only (no keys, defaults or indexes).
CREATE SCHEMA IF NOT EXISTS staging;

CREATE TABLE staging.students                  (LIKE oltp.students);
CREATE TABLE staging.routes                    (LIKE oltp.routes);
CREATE TABLE staging.stops                     (LIKE oltp.stops);
CREATE TABLE staging.buses                     (LIKE oltp.buses);
CREATE TABLE staging.drivers                   (LIKE oltp.drivers);
CREATE TABLE staging.student_route_enrollments (LIKE oltp.student_route_enrollments);
CREATE TABLE staging.trips                     (LIKE oltp.trips);
CREATE TABLE staging.stop_events               (LIKE oltp.stop_events);
-- gps_pings is deliberately not staged: it is not modeled in the warehouse.