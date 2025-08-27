# Tesla PV-Surplus Charging Controller — Functional Specification

## Overview
This service controls Tesla vehicle charging current (amps) to consume only solar surplus power.  
It listens to **HEMS / Smart Meter** data via **ECHONET Lite** and adjusts Tesla charging via the **Fleet API**.  
Key rules:
1. **User schedules must be respected** (automation runs only inside Tesla’s configured charging window).
2. **User overrides of amps must be detected and honored** (automation backs off if driver changes current manually).

---

## Components
- **ECHONET Lite Reader**
  - Reads instantaneous power values from HEMS/smart meter (`0x0287` Electric Energy Meter class).
  - Provides `net_export_watts` (positive = export to grid, negative = import).
- **Surplus Calculator**
  - Computes desired charging current = `floor(net_export_watts / (voltage * phases))`.
  - Applies min/max bounds: `min_amps`, `max_amps`, `circuit_limit`.
  - Applies hysteresis and deadband to avoid thrashing.
- **Tesla Fleet API Client**
  - Handles OAuth2 Authorization Code flow.
  - Manages refresh token and access token renewal.
  - Provides calls:
    - `wake_up`
    - `get_vehicle_state` (charging_state, charger_actual_current, charge_current_request, scheduled_charging fields, SOC)
    - `start_charging`
    - `stop_charging`
    - `set_charging_amps`

---

## Control Logic

### 0. Preconditions
- Store and refresh Tesla OAuth tokens.
- Cache last commanded amps (`last_cmd_amps`).
- Know schedule window (`scheduled_charging_start_time`, `scheduled_charging_enabled`).

### 1. Respect Schedule
- **If now < schedule_start_time OR schedule disabled:**  
  → Do nothing. Let car sleep.  
- **If within schedule window:**  
  → Continue to step 2.

### 2. Surplus Calculation
- Compute `desired_amps` from surplus.
- Clamp to [0, circuit_limit].
- If unchanged since last command and last change < debounce_window (e.g. 15s): skip.

### 3. Vehicle State
- Query `get_vehicle_state`.
- If asleep:
  - Only wake if `desired_amps > 0`.
  - Call `wake_up`, poll until `online` or timeout (60s).
- Read current amps (`charge_current_request`), charging_state, SOC.

### 4. User Override Detection
- Compare `charge_current_request` vs `last_cmd_amps`.
- If different and no recent command was issued by this service:
  - Flag **user_override_active = true**.
  - Enter **cooldown mode** (e.g. 30 minutes).
- If `user_override_active` and still in cooldown:
  - Do nothing.
- If cooldown expired:
  - Clear override flag.

### 5. Apply Control
- **Case A: desired_amps == 0**
  - If charging_state == Charging → `stop_charging`.
  - Update `last_cmd_amps = 0`.
- **Case B: desired_amps > 0**
  - If charging_state != Charging → `start_charging`.
  - If `abs(charge_current_request - desired_amps) >= 1`:
    - `set_charging_amps(desired_amps)`
    - Update `last_cmd_amps = desired_amps`.

---

## Timing & Rate Limits
- Main loop: every 10–15 seconds.
- Debounce: don’t send repeated `set_charging_amps` if value unchanged.
- Command limit:
  - ≤30 commands/minute (Fleet API limit).
  - Target ≤50 commands/day (empirical cap).
- Wake limit: ≤3 wake attempts per minute.

---

## Edge Cases
- **Unplugged:** Do nothing.
- **Charge Complete:** Do nothing unless user raises charge limit SOC.
- **Low temperature:** Allow car to adjust current briefly; re-check after 30s.
- **Breaker / wall connector:** Never exceed `circuit_limit`.

---

## Logging
- Each loop logs:
  - Timestamp
  - Surplus W
  - Desired amps
  - Current amps
  - Charging state
  - Action taken
  - Reason (surplus, schedule, override, etc.)

---

## Future Extensions
- Support multiple vehicles (per-VIN state machines).
- Optionally integrate battery SOC and grid import caps.
- Optional MQTT publishing of status/commands.
