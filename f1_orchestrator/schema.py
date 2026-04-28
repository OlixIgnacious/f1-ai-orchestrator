F1_TABLE_METADATA = """
DATABASE: f1db (AlloyDB / PostgreSQL)
This schema is the AUTHORITATIVE source of truth. Do not invent table or column names.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DATA AVAILABILITY RULE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PAST races (date ≤ today) → query AlloyDB tables below.
FUTURE races (date > today) → NOT in the database.
  Use get_f1_schedule(year) to find upcoming race name/date/location.
  Use get_f1_standings(year) + query_f1_db on PAST races for prediction context.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VALID TABLES — only these exist. Any other name will fail.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

f1_drivers        — driver_id (TEXT, PK = 3-letter code e.g. 'VER'), code (TEXT, same as driver_id),
                    full_name, given_name, family_name, nationality, number, updated_at

f1_teams          — team_id (TEXT, PK e.g. 'mercedes', 'red_bull', 'ferrari'),
                    name, nationality, updated_at

f1_circuits       — circuit_id (TEXT, PK), name, locality, country, lat, lng, updated_at

f1_sessions       — id (UUID, PK), season (INT), round (INT), session_type (TEXT), race_name (TEXT),
                    circuit_id (TEXT → f1_circuits), date (DATE)
                    session_type values: 'Race' | 'Qualifying'  ← only these two

f1_results        — id (UUID, PK), session_id (UUID → f1_sessions), driver_id (TEXT → f1_drivers),
                    team_id (TEXT → f1_teams), grid (INT), position (INT), classified (BOOL),
                    status (TEXT), points (FLOAT), fastest_lap (BOOL)

f1_standings      — id (UUID), season (INT), round (INT), standing_type (TEXT), entity_id (TEXT),
                    position (INT), points (FLOAT), wins (INT)
                    standing_type values: 'driver' | 'constructor'  ← lowercase, only these two
                    entity_id for 'driver'      = driver code e.g. 'VER', 'HAM'
                    entity_id for 'constructor' = team_id  e.g. 'mercedes', 'red_bull'

f1_telemetry_summary — id (UUID), session_id (UUID → f1_sessions), driver_id (TEXT → f1_drivers),
                       avg_speed, top_speed, avg_throttle, avg_brake, avg_rpm, peak_rpm (all FLOAT),
                       fastest_lap_time (INTERVAL), created_at

f1_stints         — id (UUID), session_id (UUID → f1_sessions), driver_id (TEXT → f1_drivers),
                    stint_number (INT), compound (TEXT), start_lap (INT), end_lap (INT), lap_count (INT)

f1_lap_summary    — id (UUID), session_id (UUID → f1_sessions), driver_id (TEXT → f1_drivers),
                    lap_number (INT), lap_time (INTERVAL), sector1_time, sector2_time, sector3_time (INTERVAL),
                    is_valid (BOOL), pit_in_lap (BOOL), pit_out_lap (BOOL), compound (TEXT), tyre_age (INT)

f1_race_control   — id (UUID), session_id (UUID → f1_sessions), lap_number (INT),
                    timestamp (TIMESTAMP), flag_type (TEXT), sector (INT), message (TEXT),
                    driver_id (TEXT)

f1_regulations    — id (UUID), year (INT), reg_type (TEXT), article_number (TEXT), article_title (TEXT),
                    content (TEXT), embedding (VECTOR), source_url, created_at

f1_decisions      — id (UUID), race (TEXT), year (INT), driver_id (TEXT), team_id (TEXT),
                    incident (TEXT), ruling (TEXT), penalty (TEXT), article_ref (TEXT),
                    content (TEXT), embedding (VECTOR), created_at

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL JOIN PATTERNS (copy these exactly)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Race results with driver and team names:
SELECT d.full_name, t.name AS team, r.position, r.points
FROM f1_results r
JOIN f1_drivers  d ON d.driver_id = r.driver_id
JOIN f1_teams    t ON t.team_id   = r.team_id
JOIN f1_sessions s ON s.id        = r.session_id
WHERE s.race_name ILIKE '%Bahrain%'
  AND s.season = 2024
  AND s.session_type = 'Race'
ORDER BY r.position ASC;

-- Driver championship standings:
SELECT d.full_name, fs.position, fs.points, fs.wins
FROM f1_standings fs
JOIN f1_drivers d ON d.driver_id = fs.entity_id
WHERE fs.season = 2025
  AND fs.standing_type = 'driver'
  AND fs.round = (SELECT MAX(round) FROM f1_standings WHERE season = 2025)
ORDER BY fs.position ASC;

-- Constructor championship standings:
SELECT t.name AS constructor, fs.position, fs.points, fs.wins
FROM f1_standings fs
JOIN f1_teams t ON t.team_id = fs.entity_id
WHERE fs.season = 2025
  AND fs.standing_type = 'constructor'
  AND fs.round = (SELECT MAX(round) FROM f1_standings WHERE season = 2025)
ORDER BY fs.position ASC;

-- Most common penalty types (frequency, not raw rows):
SELECT penalty, COUNT(*) AS times_given
FROM f1_decisions
WHERE year = 2025
GROUP BY penalty
ORDER BY times_given DESC
LIMIT 10;

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TABLES THAT DO NOT EXIST — querying these will fail
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
races, race_schedule, grand_prix, race_results,
driver_standings, team_standings, constructors,
drivers, results, seasons, circuits (Ergast names)
→ For upcoming races/schedule: use get_f1_schedule tool.
→ For current standings: use get_f1_standings tool.
"""
