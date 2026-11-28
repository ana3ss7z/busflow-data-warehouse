-- Static dimensions and special members (spec 9.3 and 9.4).
-- Written to be re-runnable: the ETL can execute the same statements on every run.

-- ---- special members: -1 Unknown in every dimension, -2 Not applicable in date and time
INSERT INTO dwh.dim_date (date_key, day_name) VALUES (-1, 'Unknown'), (-2, 'Not applicable')
ON CONFLICT (date_key) DO NOTHING;

INSERT INTO dwh.dim_time_of_day (time_key, time_label, day_part)
VALUES (-1, 'Unknown', 'UNKNOWN'), (-2, 'Not applicable', 'NOT_APPLICABLE')
ON CONFLICT (time_key) DO NOTHING;

INSERT INTO dwh.dim_event_type (event_type_key, event_type_code, direction, event_type_label)
VALUES (-1, 'UNKNOWN', 'UNKNOWN', 'Unknown')
ON CONFLICT (event_type_key) DO NOTHING;

INSERT INTO dwh.dim_trip_profile (trip_profile_key, direction, trip_status, cancel_reason, profile_label)
VALUES (-1, 'UNKNOWN', 'UNKNOWN', 'NONE', 'Unknown')
ON CONFLICT (trip_profile_key) DO NOTHING;

INSERT INTO dwh.dim_route (route_key, route_id, route_name, created_load_id, updated_load_id)
VALUES (-1, -1, 'Unknown', 0, 0)
ON CONFLICT (route_key) DO NOTHING;

INSERT INTO dwh.dim_driver (driver_key, driver_id, driver_name, driver_status, created_load_id, updated_load_id)
VALUES (-1, -1, 'Unknown', 'UNKNOWN', 0, 0)
ON CONFLICT (driver_key) DO NOTHING;

INSERT INTO dwh.dim_student (student_key, student_id, student_name, student_status,
    home_neighborhood, home_city, effective_date, expiration_date, is_current, scd2_hash,
    created_load_id, updated_load_id)
VALUES (-1, -1, 'Unknown', 'UNKNOWN', 'Unknown', 'Unknown',
        DATE '1900-01-01', DATE '9999-12-31', TRUE, md5('unknown'), 0, 0)
ON CONFLICT (student_key) DO NOTHING;

INSERT INTO dwh.dim_stop (stop_key, stop_id, stop_name, effective_date, expiration_date,
    is_current, scd2_hash, created_load_id, updated_load_id)
VALUES (-1, -1, 'Unknown', DATE '1900-01-01', DATE '9999-12-31', TRUE, md5('unknown'), 0, 0)
ON CONFLICT (stop_key) DO NOTHING;

INSERT INTO dwh.dim_bus (bus_key, bus_id, plate_number, bus_status, effective_date,
    expiration_date, is_current, scd2_hash, created_load_id, updated_load_id)
VALUES (-1, -1, 'Unknown', 'UNKNOWN', DATE '1900-01-01', DATE '9999-12-31', TRUE,
        md5('unknown'), 0, 0)
ON CONFLICT (bus_key) DO NOTHING;

-- ---- dim_time_of_day: one row per minute
INSERT INTO dwh.dim_time_of_day (time_key, hour, minute, time_label, day_part)
SELECT h * 100 + m, h, m, to_char(h, 'FM00') || ':' || to_char(m, 'FM00'),
       CASE WHEN h < 5 THEN 'NIGHT'
            WHEN h < 8 THEN 'EARLY_MORNING'
            WHEN h < 12 THEN 'MORNING'
            WHEN h < 14 THEN 'MIDDAY'
            WHEN h < 18 THEN 'AFTERNOON'
            ELSE 'EVENING' END
FROM generate_series(0, 23) AS h, generate_series(0, 59) AS m
ON CONFLICT (time_key) DO NOTHING;

-- ---- dim_event_type
INSERT INTO dwh.dim_event_type (event_type_key, event_type_code, direction, event_type_label)
VALUES (1, 'PICKUP',  'TO_SCHOOL',   'Picked up (to school)'),
       (2, 'DROPOFF', 'FROM_SCHOOL', 'Dropped off (from school)'),
       (3, 'NO_SHOW', 'TO_SCHOOL',   'No-show (to school)'),
       (4, 'NO_SHOW', 'FROM_SCHOOL', 'No-show (from school)')
ON CONFLICT (event_type_key) DO NOTHING;

-- ---- dim_trip_profile: 7 profiles per direction
INSERT INTO dwh.dim_trip_profile (trip_profile_key, direction, trip_status, cancel_reason, profile_label)
SELECT base + p.ord, d.direction, p.status, p.reason,
       d.direction || ' / ' || p.status || CASE WHEN p.reason = 'NONE' THEN '' ELSE ' / ' || p.reason END
FROM (VALUES ('TO_SCHOOL', 0), ('FROM_SCHOOL', 7)) AS d(direction, base),
     (VALUES (1, 'COMPLETED',   'NONE'),
             (2, 'IN_PROGRESS', 'NONE'),
             (3, 'SCHEDULED',   'NONE'),
             (4, 'CANCELLED',   'BREAKDOWN'),
             (5, 'CANCELLED',   'DRIVER_ABSENT'),
             (6, 'CANCELLED',   'WEATHER'),
             (7, 'CANCELLED',   'OTHER')) AS p(ord, status, reason)
ON CONFLICT (trip_profile_key) DO NOTHING;