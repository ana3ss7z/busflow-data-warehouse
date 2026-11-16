CREATE SCHEMA IF NOT EXISTS oltp;

CREATE TABLE oltp.students (
    student_id          INT PRIMARY KEY,
    full_name           TEXT NOT NULL,
    grade_level         SMALLINT NOT NULL CHECK (grade_level BETWEEN 1 AND 12),
    home_neighborhood   TEXT NOT NULL,
    home_city           TEXT NOT NULL,
    home_effective_from DATE NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('ACTIVE', 'LEFT')),
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE oltp.routes (
    route_id            INT PRIMARY KEY,
    route_name          TEXT NOT NULL UNIQUE,
    planned_distance_km NUMERIC(6,2) NOT NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE oltp.stops (
    stop_id                 INT PRIMARY KEY,
    route_id                INT NOT NULL REFERENCES oltp.routes (route_id),
    stop_name               TEXT NOT NULL,
    stop_sequence           SMALLINT NOT NULL,
    latitude                NUMERIC(9,6) NOT NULL,
    longitude               NUMERIC(9,6) NOT NULL,
    location_effective_from DATE NOT NULL,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at              TIMESTAMPTZ NOT NULL,
    UNIQUE (route_id, stop_sequence)
);

CREATE TABLE oltp.buses (
    bus_id                 INT PRIMARY KEY,
    plate_number           TEXT NOT NULL UNIQUE,
    seat_capacity          SMALLINT NOT NULL CHECK (seat_capacity > 0),
    capacity_effective_from DATE NOT NULL,
    model_year             SMALLINT NOT NULL,
    status                 TEXT NOT NULL CHECK (status IN ('ACTIVE', 'MAINTENANCE', 'RETIRED')),
    updated_at             TIMESTAMPTZ NOT NULL
);

CREATE TABLE oltp.drivers (
    driver_id  INT PRIMARY KEY,
    full_name  TEXT NOT NULL,
    hire_date  DATE NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('ACTIVE', 'ON_LEAVE', 'LEFT')),
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE oltp.student_route_enrollments (
    enrollment_id  INT PRIMARY KEY,
    student_id     INT NOT NULL REFERENCES oltp.students (student_id),
    route_id       INT NOT NULL REFERENCES oltp.routes (route_id),
    pickup_stop_id INT NOT NULL REFERENCES oltp.stops (stop_id),
    valid_from     DATE NOT NULL,
    valid_to       DATE,
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
-- A student has at most one open enrollment
CREATE UNIQUE INDEX uq_one_open_enrollment
    ON oltp.student_route_enrollments (student_id) WHERE valid_to IS NULL;

CREATE TABLE oltp.trips (
    trip_id              BIGINT PRIMARY KEY,
    service_date         DATE NOT NULL,
    route_id             INT NOT NULL REFERENCES oltp.routes (route_id),
    bus_id               INT NOT NULL REFERENCES oltp.buses (bus_id),
    driver_id            INT REFERENCES oltp.drivers (driver_id),  -- nullable (scenario S07)
    direction            TEXT NOT NULL CHECK (direction IN ('TO_SCHOOL', 'FROM_SCHOOL')),
    scheduled_start_ts   TIMESTAMPTZ NOT NULL,
    scheduled_arrival_ts TIMESTAMPTZ NOT NULL,
    actual_start_ts      TIMESTAMPTZ,
    first_stop_ts        TIMESTAMPTZ,
    last_stop_ts         TIMESTAMPTZ,
    actual_arrival_ts    TIMESTAMPTZ,
    odometer_start_km    NUMERIC(8,1),
    odometer_end_km      NUMERIC(8,1),
    status               TEXT NOT NULL CHECK (status IN ('SCHEDULED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    cancel_reason        TEXT CHECK (cancel_reason IN ('BREAKDOWN', 'DRIVER_ABSENT', 'WEATHER', 'OTHER')),
    created_at           TIMESTAMPTZ NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL,
    UNIQUE (service_date, route_id, direction),
    CHECK ((status = 'CANCELLED') = (cancel_reason IS NOT NULL))
);

-- Raw append-only log: at-least-once delivery, so duplicates allowed; deliberately no FKs
CREATE TABLE oltp.stop_events (
    event_id     BIGSERIAL PRIMARY KEY,
    trip_id      BIGINT NOT NULL,
    student_id   INT NOT NULL,
    stop_id      INT NOT NULL,
    event_type   TEXT NOT NULL CHECK (event_type IN ('PICKUP', 'DROPOFF', 'NO_SHOW')),
    scheduled_ts TIMESTAMPTZ NOT NULL,
    actual_ts    TIMESTAMPTZ,
    received_at  TIMESTAMPTZ NOT NULL,
    CHECK ((event_type = 'NO_SHOW') = (actual_ts IS NULL)),
    CHECK (actual_ts IS NULL OR received_at >= actual_ts)
);

CREATE TABLE oltp.gps_pings (
    ping_id   BIGSERIAL,
    trip_id   BIGINT NOT NULL,
    ping_ts   TIMESTAMPTZ NOT NULL,
    latitude  NUMERIC(9,6) NOT NULL,
    longitude NUMERIC(9,6) NOT NULL,
    speed_kmh NUMERIC(5,1),
    PRIMARY KEY (ping_id, ping_ts)
) PARTITION BY RANGE (ping_ts);

-- Monthly partitions covering the default simulation window, plus a catch-all
DO $$
DECLARE
    m DATE := DATE '2025-09-01';
BEGIN
    WHILE m <= DATE '2026-01-01' LOOP
        EXECUTE format(
            'CREATE TABLE oltp.gps_pings_%s PARTITION OF oltp.gps_pings
             FOR VALUES FROM (%L) TO (%L)',
            to_char(m, 'YYYY_MM'), m, (m + INTERVAL '1 month')::date);
        m := (m + INTERVAL '1 month')::date;
    END LOOP;
END $$;

CREATE TABLE oltp.gps_pings_default PARTITION OF oltp.gps_pings DEFAULT;
