# F1 AlloyDB: Data Dictionary

This document provides a technical and domain reference for the **f1db** database. Use this as a guide for constructing SQL queries and interpreting results.

---

## 1. Core Tables

### `f1_sessions`
Represents a specific F1 weekend session (Race, Qualifying, etc.).
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary Key for joining with `f1_results`. |
| `season` | `integer` | The F1 year (e.g. 2024). |
| `round` | `integer` | Race number in the calendar. |
| `date` | `date` | Date of the session. |
| `session_type` | `text` | 'Race', 'Qualifying', 'Sprint', etc. |
| `race_name` | `text` | Official Grand Prix name. |
| `circuit_id` | `text` | Link to `f1_circuits` (e.g. 'sakhir'). |

### `f1_drivers`
Static metadata for drivers.
| Column | Type | Description |
| :--- | :--- | :--- |
| `driver_id` | `text` | Machine ID or 3-letter code (e.g. 'VER', 'SAR'). |
| `code` | `text` | Official 3-letter broadcast code. |
| `full_name` | `text` | Full display name (e.g. 'Max Verstappen'). |
| `nationality` | `text` | Driver's home country (e.g. 'NLD', 'USA'). |
| `number` | `integer` | Permanent racing number. |

### `f1_results`
Individual driver performance in a specific session.
| Column | Type | Description |
| :--- | :--- | :--- |
| `session_id` | `uuid` | Links to `f1_sessions`. |
| `driver_id` | `text` | Links to `f1_drivers`. |
| `team_id` | `text` | Links to `f1_teams` (e.g. 'red_bull'). |
| `grid` | `integer` | Starting position (1 for Pole). |
| `position` | `integer` | Finishing position (1 for Winner). |
| `points` | `double` | Points awarded (supports half points). |
| `classified` | `boolean` | `True` if the driver finished >= 90% distance. |
| `status` | `text` | 'Finished', 'Accident', 'Power Unit', etc. |
| `fastest_lap` | `boolean` | `True` if the driver set the lap record for that GP. |

### `f1_standings`
Championship leaderboard snapshot at a specific round.
| Column | Type | Description |
| :--- | :--- | :--- |
| `season` | `integer` | Year of the championship. |
| `round` | `integer` | Round number up to which points are calculated. |
| `standing_type` | `text` | 'driver' (for WDC) or 'constructor' (for WCC). |
| `entity_id` | `text` | Driver code or Team ID. |
| `position` | `integer` | Leaderboard rank. |
| `points` | `double` | Total accumulated points. |
| `wins` | `integer` | Total wins in that season so far. |

### `f1_teams`
Static metadata for constructor teams.
| Column | Type | Description |
| :--- | :--- | :--- |
| `team_id` | `text` | Lowercase snake_case ID (e.g. 'mercedes', 'ferrari'). |
| `name` | `text` | Official team name. |
| `nationality` | `text` | Team's licensing country. |

### `f1_circuits`
Track metadata and coordinates.
| Column | Type | Description |
| :--- | :--- | :--- |
| `circuit_id` | `text` | Unique lowercase track ID (e.g. 'monaco', 'spa'). |
| `name` | `text` | Official circuit name. |
| `locality` | `text` | City or nearest town. |
| `lat` / `lng` | `double` | GPS coordinates for mapping tools. |

---

## 2. Querying Constants & Conventions
*   **Case Sensitivity**: Table and column names in AlloyDB are case-sensitive if quoted, but generally lowercase. IDs (`driver_id`, `team_id`) are stored as **lowercase text**.
*   **Joining Path**: 
    1. Start at `f1_sessions` to find a specific race.
    2. Join `f1_results` on `session_id`.
    3. Join `f1_drivers` on `driver_id` for names.
    4. Join `f1_teams` on `team_id` for constructor analysis.
