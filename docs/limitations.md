
# Simulator Limitations and Assumptions

This document describes the assumptions and known limitations of the
WP2 school transportation simulator.

The generated data is synthetic. It is designed to support
data engineering, warehouse development, SQL analytics and
partitioning experiments. It does not represent actual vehicle
telemetry.

## 1. One Bus per Route

The simulator uses one bus per route.

The assignment is deterministic:
- Routes are sorted by route_id.
- Buses are sorted by bus_id.
- The nth route receives the nth bus.
- Drivers are assigned using the same ordering.
- Additional drivers remain unused and can represent substitutes.

This is an explicit project assumption, not a general transportation
model.

The simulator validates that there are enough buses and drivers
to cover all routes.

It does not currently model:
- Multiple buses operating on the same route.
- Dynamic bus reassignment.
- Driver shift scheduling.
- Bus breakdowns or replacements.

## 2. Synthetic GPS Trajectories

GPS positions are generated through linear interpolation between
consecutive stop coordinates.

A small random geographic noise is added to make the positions
less perfectly uniform.

The bus waits at each stop for a configured dwell time.

GPS observations are generated every 20 seconds.

These trajectories are synthetic and do not follow actual road
networks.

Consequently:
- A bus may appear to travel through buildings or across roads.
- The route does not account for road geometry.
- Traffic congestion is not modeled geographically.
- Generated speeds are approximations rather than measured
  vehicle speeds.

The relatively low average moving speed is a consequence of
the simplified trajectory and the 50-minute trip window.

## 3. GPS Data and the Data Warehouse

The `gps_pings` table is generated to support OLTP data volume
and monthly partitioning experiments.

GPS observations are not currently modeled as a fact table
in the planned Data Warehouse.

Therefore, the synthetic GPS trajectories are not intended
for detailed road-level or geospatial performance analytics.

## 4. Deterministic Simulation

Each school day uses a random generator seeded with the
global seed and the simulation date.

This ensures that:
- A given day produces reproducible records.
- Simulating one day independently produces the same result
  as extracting it from a larger date range.
- Re-running a simulation does not change the generated
  baseline data.

## 5. Scope of the Baseline

The simulator generates a clean operational baseline:
- Completed trips.
- Student pickup and drop-off events.
- Student no-shows.
- GPS observations.
- Continuous odometer readings.

Deliberate data anomalies are introduced separately
through the scenario injection process (S02-S12).

This separation makes it possible to compare clean
operational data with data containing controlled anomalies.

## 6. Future Improvements

Possible extensions include:
- Road-network-based GPS interpolation using OSRM.
- Traffic-dependent travel times.
- Multiple buses per route.
- Dynamic driver and vehicle assignments.
- More realistic speed profiles.
- Bus breakdowns and cancelled trips.
