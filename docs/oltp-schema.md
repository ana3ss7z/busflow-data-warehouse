# OLTP schema (`oltp`)

Solid lines are foreign keys. Dotted lines are logical links without a database FK (raw ingestion tables).

```mermaid
erDiagram
    students {
        INT student_id PK
        TEXT full_name
        SMALLINT grade_level
        TEXT home_neighborhood
        TEXT home_city
        DATE home_effective_from
        TEXT status
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }
    routes {
        INT route_id PK
        TEXT route_name UK
        NUMERIC planned_distance_km
        BOOLEAN is_active
        TIMESTAMPTZ updated_at
    }
    stops {
        INT stop_id PK
        INT route_id FK
        TEXT stop_name
        SMALLINT stop_sequence
        NUMERIC latitude
        NUMERIC longitude
        DATE location_effective_from
        BOOLEAN is_active
        TIMESTAMPTZ updated_at
    }
    buses {
        INT bus_id PK
        TEXT plate_number UK
        SMALLINT seat_capacity
        DATE capacity_effective_from
        SMALLINT model_year
        TEXT status
        TIMESTAMPTZ updated_at
    }
    drivers {
        INT driver_id PK
        TEXT full_name
        DATE hire_date
        TEXT status
        TIMESTAMPTZ updated_at
    }
    student_route_enrollments {
        INT enrollment_id PK
        INT student_id FK
        INT route_id FK
        INT pickup_stop_id FK
        DATE valid_from
        DATE valid_to
    }
    trips {
        BIGINT trip_id PK
        DATE service_date
        INT route_id FK
        INT bus_id FK
        INT driver_id FK
        TEXT direction
        TIMESTAMPTZ scheduled_start_ts
        TIMESTAMPTZ scheduled_arrival_ts
        TIMESTAMPTZ actual_start_ts
        TIMESTAMPTZ first_stop_ts
        TIMESTAMPTZ last_stop_ts
        TIMESTAMPTZ actual_arrival_ts
        NUMERIC odometer_start_km
        NUMERIC odometer_end_km
        TEXT status
        TEXT cancel_reason
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }
    stop_events {
        BIGSERIAL event_id PK
        BIGINT trip_id
        INT student_id
        INT stop_id
        TEXT event_type
        TIMESTAMPTZ scheduled_ts
        TIMESTAMPTZ actual_ts
        TIMESTAMPTZ received_at
    }
    gps_pings {
        BIGSERIAL ping_id PK
        TIMESTAMPTZ ping_ts PK
        BIGINT trip_id
        NUMERIC latitude
        NUMERIC longitude
        NUMERIC speed_kmh
    }

    routes ||--o{ stops : has
    routes ||--o{ trips : runs
    buses ||--o{ trips : serves
    drivers |o--o{ trips : drives
    students ||--o{ student_route_enrollments : enrolls
    routes ||--o{ student_route_enrollments : for
    stops ||--o{ student_route_enrollments : pickup
    trips ||..o{ stop_events : "logical"
    students ||..o{ stop_events : "logical"
    stops ||..o{ stop_events : "logical"
    trips ||..o{ gps_pings : "logical"
```

## Notes
- `students`, `stops` and `buses` overwrite values in place and keep no history, which is why the warehouse needs SCD2.
- `stop_events` is append-only with at-least-once delivery, so duplicates are possible.
- `gps_pings` is partitioned by month on `ping_ts`.
- Timestamps are stored in UTC. Business time is Africa/Casablanca.