# F1 AlloyDB: Data Dictionary

Technical and domain reference for the **f1db** database. Use this when constructing SQL queries or understanding table relationships.

---

## 1. Core Tables

### `f1_sessions`
One row per session (Race, Qualifying, Sprint, FP1/2/3) per event.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary key — join target for `f1_results`, `f1_telemetry_summary`, `f1_stints`, `f1_lap_summary`, `f1_race_control`. |
| `season` | `integer` | Championship year e.g. `2024`. |
| `round` | `integer` | Race number in the calendar (1 = season opener). |
| `date` | `date` | Date of the session. |
| `session_type` | `text` | `'Race'`, `'Qualifying'`, `'Sprint'`, `'Practice 1'`, `'Practice 2'`, `'Practice 3'`. |
| `race_name` | `text` | Official Grand Prix name e.g. `'Bahrain Grand Prix'`. Use `ILIKE '%Bahrain%'` for fuzzy matching. |
| `circuit_id` | `text` | Foreign key to `f1_circuits.circuit_id`. |

---

### `f1_drivers`
Static driver registry.

| Column | Type | Description |
| :--- | :--- | :--- |
| `driver_id` | `text` | Primary key — lowercase slug or 3-letter code e.g. `'VER'`, `'hamilton'`. |
| `code` | `text` | Official 3-letter broadcast code e.g. `'VER'`, `'HAM'`. Used in `f1_standings.entity_id`. |
| `full_name` | `text` | Full display name e.g. `'Max Verstappen'`. |
| `given_name` | `text` | First name. |
| `family_name` | `text` | Surname. |
| `nationality` | `text` | IOC country code e.g. `'NLD'`, `'GBR'`. |
| `number` | `integer` | Permanent racing number. |
| `updated_at` | `timestamp` | Last sync timestamp. |

---

### `f1_results`
Individual driver result in a specific session.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary key. |
| `session_id` | `uuid` | FK → `f1_sessions.id`. |
| `driver_id` | `text` | FK → `f1_drivers.driver_id`. |
| `team_id` | `text` | FK → `f1_teams.team_id` e.g. `'red_bull'`. |
| `grid` | `integer` | Starting grid position (1 = pole). |
| `position` | `integer` | Classified finishing position (1 = winner). NULL if DNF. |
| `points` | `double precision` | Championship points awarded. Supports half-points (sprint). |
| `classified` | `boolean` | `true` if driver completed ≥ 90% of race distance. |
| `fastest_lap` | `boolean` | `true` if driver set the fastest lap award for this GP. |
| `status` | `text` | `'Finished'`, `'Accident'`, `'Power Unit'`, `'+1 Lap'`, etc. |

---

### `f1_standings`
Championship points snapshot at a given round.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary key. |
| `season` | `integer` | Championship year. |
| `round` | `integer` | Round after which this snapshot was taken. |
| `standing_type` | `text` | `'driver'` (WDC) or `'constructor'` (WCC). |
| `entity_id` | `text` | Driver `code` (e.g. `'VER'`) or `team_id` (e.g. `'red_bull'`) depending on `standing_type`. |
| `position` | `integer` | Leaderboard rank. |
| `points` | `double precision` | Accumulated points. |
| `wins` | `integer` | Wins in season so far. |

> **Tip:** Always filter `WHERE round = (SELECT max(round) FROM f1_standings WHERE season = X)` to get the latest snapshot.

---

### `f1_teams`
Constructor registry.

| Column | Type | Description |
| :--- | :--- | :--- |
| `team_id` | `text` | Primary key — lowercase snake_case e.g. `'mercedes'`, `'red_bull'`, `'ferrari'`. |
| `name` | `text` | Official team name. |
| `nationality` | `text` | Licensing country. |
| `updated_at` | `timestamp` | Last sync timestamp. |

---

### `f1_circuits`
Track metadata and location.

| Column | Type | Description |
| :--- | :--- | :--- |
| `circuit_id` | `text` | Primary key — lowercase slug e.g. `'monaco'`, `'spa'`, `'bahrain'`. |
| `name` | `text` | Official circuit name. |
| `locality` | `text` | City or nearest town. |
| `country` | `text` | Country name. |
| `lat` | `double precision` | GPS latitude. |
| `lng` | `double precision` | GPS longitude. |
| `updated_at` | `timestamp` | Last sync timestamp. |

---

## 2. Telemetry Layer

Populated by `scripts/backfill_telemetry.py` from FastF1. Covers Race + Qualifying sessions 2020–2025.

### `f1_telemetry_summary`
Pre-aggregated fastest-lap telemetry per driver per session. Replaces live FastF1 calls for historical data.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary key. |
| `session_id` | `uuid` | FK → `f1_sessions.id`. |
| `driver_id` | `text` | FK → `f1_drivers.driver_id`. |
| `avg_speed` | `float` | Average speed on fastest lap (km/h). |
| `top_speed` | `float` | Maximum speed on fastest lap (km/h). |
| `avg_throttle` | `float` | Mean throttle application (0–100%). |
| `avg_brake` | `float` | Mean brake application (0–100%). |
| `avg_rpm` | `float` | Mean engine RPM on fastest lap. |
| `peak_rpm` | `float` | Maximum engine RPM on fastest lap. |
| `fastest_lap_time` | `interval` | Fastest lap duration e.g. `00:01:31.447`. |
| `created_at` | `timestamp` | Insert timestamp. |

> **Unique constraint:** `(session_id, driver_id)` — one row per driver per session.

---

### `f1_stints`
Pit stop stint data per driver per race. One row per stint (tyre compound + lap range).

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary key. |
| `session_id` | `uuid` | FK → `f1_sessions.id`. |
| `driver_id` | `text` | FK → `f1_drivers.driver_id` (stores 3-letter code e.g. `'VER'`). |
| `stint_number` | `integer` | Stint index (1 = first stint of the race). |
| `compound` | `text` | Tyre compound: `'SOFT'`, `'MEDIUM'`, `'HARD'`, `'INTER'`, `'WET'`. |
| `start_lap` | `integer` | Lap number the stint began. |
| `end_lap` | `integer` | Lap number the stint ended. |
| `lap_count` | `integer` | Total laps on this stint (`end_lap - start_lap + 1`). |

> **Unique constraint:** `(session_id, driver_id, stint_number)`.

---

### `f1_lap_summary`
Lap-by-lap validity and sector times. Used by `f1_steward_agent` for incident validation.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary key. |
| `session_id` | `uuid` | FK → `f1_sessions.id`. |
| `driver_id` | `text` | FK → `f1_drivers.driver_id`. |
| `lap_number` | `integer` | Lap number within the session. |
| `lap_time` | `interval` | Total lap time. |
| `sector1_time` | `interval` | Sector 1 time. |
| `sector2_time` | `interval` | Sector 2 time. |
| `sector3_time` | `interval` | Sector 3 time. |
| `is_valid` | `boolean` | `true` if lap was not deleted (e.g. track limits). |
| `deleted_reason` | `text` | Why lap was invalidated e.g. `'TrackLimits'`, `'Speeding'`. |
| `pit_in_lap` | `boolean` | `true` if driver pitted at the end of this lap. |
| `pit_out_lap` | `boolean` | `true` if driver exited the pits on this lap. |
| `compound` | `text` | Tyre compound on this lap. |
| `tyre_age` | `integer` | Laps on the current set of tyres. |

> **Unique constraint:** `(session_id, driver_id, lap_number)`.

---

### `f1_race_control`
Race control messages — flags, safety cars, VSC, penalty notifications.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary key. |
| `session_id` | `uuid` | FK → `f1_sessions.id`. |
| `lap_number` | `integer` | Lap on which the message was issued. |
| `timestamp` | `timestamp` | Exact time of the message. |
| `flag_type` | `text` | `'YELLOW'`, `'RED'`, `'SC'` (safety car), `'VSC'`, `'GREEN'`, `'CHEQUERED'`. |
| `sector` | `integer` | Track sector affected (1, 2, 3). NULL = full track. |
| `message` | `text` | Full race control message text e.g. `'CAR 1 (VER) FIVE SECOND TIME PENALTY - UNSAFE RELEASE'`. |
| `driver_id` | `text` | Driver involved, if applicable. |

---

## 3. RAG Layer

Populated by `scripts/ingest_regulations.py` and `scripts/build_steward_decisions.py`. Both tables have **768-dimensional pgvector embeddings** (Vertex AI `text-embedding-005`) and **AlloyDB ScaNN indexes** for sub-millisecond cosine similarity search.

### `f1_regulations`
FIA Formula 1 regulations chunked by article. Covers 2021–2026 across Sporting, Technical, Financial, General, and Operational regulation types.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary key. |
| `year` | `integer` | Regulation year e.g. `2025`. |
| `reg_type` | `text` | `'Sporting'`, `'Technical'`, `'Financial'`, `'General'`, `'Operational'`. |
| `article_number` | `text` | FIA article identifier e.g. `'39'`, `'B1.1'`, `'A2'`. |
| `article_title` | `text` | Article title. |
| `content` | `text` | Article text (capped at 4000 chars per chunk). |
| `embedding` | `vector(768)` | Semantic embedding for similarity search. |
| `source_url` | `text` | GCS URI of the source PDF. |
| `created_at` | `timestamp` | Insert timestamp. |

> **Index:** `regulations_embedding_idx` — ScaNN cosine, `num_leaves=50`.
> **Coverage:** 5,525 chunks across 17 PDFs (2021–2026).
> **Note:** 2026 uses section-letter article numbers (A1, B2.1, C34) instead of plain numbers (39, 39.1).

---

### `f1_decisions`
Historical FIA steward decisions and race control penalty records. Sourced from OpenF1 race control messages.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary key. |
| `race` | `text` | Grand Prix name e.g. `'Sakhir Grand Prix'`. |
| `year` | `integer` | Season year. |
| `driver_id` | `text` | 3-letter driver code e.g. `'VER'`. Empty for team penalties. |
| `team_id` | `text` | Team name. |
| `incident` | `text` | Description of what happened e.g. `'CAUSING A COLLISION WITH ANOTHER DRIVER'`. |
| `ruling` | `text` | Steward verdict: `'Penalty imposed'`, `'No further action'`, `'Referred to stewards'`, `'Reprimand'`, `'Disqualification'`. |
| `penalty` | `text` | Penalty applied e.g. `'5 second time penalty'`, `'Drive-through penalty'`, `'10-place grid penalty'`. |
| `article_ref` | `text` | FIA article cited e.g. `'B34.13'`, `'B2 Appendix L Ch.IV Art.2'`. |
| `content` | `text` | Full structured text used for embedding (race + driver + incident + ruling + penalty + article + raw message). |
| `embedding` | `vector(768)` | Semantic embedding for similarity search. |
| `created_at` | `timestamp` | Insert timestamp. |

> **Index:** `decisions_embedding_idx` — ScaNN cosine, `num_leaves=30`.
> **Coverage:** 664 decisions across 74 race weekends (2023–2026).

---

## 4. Joining Conventions

### Standard result lookup
```sql
SELECT d.full_name, t.name AS team, r.position, r.points, r.status
FROM f1_results r
JOIN f1_sessions s  ON s.id = r.session_id
JOIN f1_drivers d   ON d.driver_id = r.driver_id
JOIN f1_teams   t   ON t.team_id   = r.team_id
WHERE s.season      = 2024
  AND s.race_name   ILIKE '%Bahrain%'
  AND s.session_type = 'Race'
ORDER BY r.position ASC;
```

### Latest standings
```sql
SELECT fs.position, d.full_name, fs.points, fs.wins
FROM f1_standings fs
JOIN f1_drivers d ON d.code = fs.entity_id
WHERE fs.season       = 2025
  AND fs.standing_type = 'driver'
  AND fs.round = (SELECT max(round) FROM f1_standings WHERE season = 2025)
ORDER BY fs.position ASC;
```

### Stint strategy for a race
```sql
SELECT d.code, st.stint_number, st.compound, st.start_lap, st.end_lap, st.lap_count
FROM f1_stints st
JOIN f1_sessions s ON s.id = st.session_id
JOIN f1_drivers  d ON d.driver_id = st.driver_id
WHERE s.season      = 2024
  AND s.race_name   ILIKE '%Monaco%'
  AND s.session_type = 'Race'
ORDER BY d.code, st.stint_number;
```

### Semantic search over regulations
```sql
SELECT article_number, article_title, content, year, reg_type,
       embedding <=> '[...768 floats...]'::vector AS distance
FROM f1_regulations
WHERE year = 2025
ORDER BY distance ASC
LIMIT 5;
```

### Penalty precedent lookup
```sql
SELECT race, year, driver_id, incident, ruling, penalty, article_ref,
       embedding <=> '[...768 floats...]'::vector AS distance
FROM f1_decisions
ORDER BY distance ASC
LIMIT 5;
```

---

## 5. Key Constants

| Value | Context |
| :--- | :--- |
| `session_type = 'Race'` | Main Sunday race |
| `session_type = 'Qualifying'` | Saturday qualifying |
| `session_type = 'Sprint'` | Sprint race (selected weekends) |
| `standing_type = 'driver'` | World Drivers' Championship |
| `standing_type = 'constructor'` | World Constructors' Championship |
| `compound IN ('SOFT','MEDIUM','HARD','INTER','WET')` | Valid tyre compounds |
| `classified = true` | Driver finished ≥ 90% of race distance |
| `fastest_lap = true` | Driver holds the lap record for that GP |
| `is_valid = false` | Lap time deleted (track limits, speeding in pit lane, etc.) |
