---
name: school-approval
description: Score addresses for education approval difficulty. Rates how hard it is to legally operate a private K-8 school by state. Used as a sub-skill of alpha-building-suitability.
version: 1.0.0
requires:
  mcp_servers:
    - google-maps
---

# Score Addresses for Education Approval

Skill: Education Approval / State Registration Rating (private K-8)

Given an address, output a deterministic rating for how hard it is to legally operate a recognized private K-8 school there.

## Output Contract

Always return this JSON structure (never crash, never return null):

```json
{
  "factor_id": "education_approval",
  "address": "123 Main St, Austin, TX 78701",
  "state": "TX",
  "locality": { "city": "Austin", "state": "TX" },
  "approval_authority": "state",
  "approval_type": "NONE",
  "gating_before_open": false,
  "ease_score_0_10": 9.5,
  "score_0_100": 95,
  "zone": "green",
  "timeline_days_preopen": { "min": 0, "likely": 7, "max": 30 },
  "requirements_summary": "Texas has minimal requirements...",
  "requirements_steps": [
    { "step": "File notification with state", "gating": false }
  ],
  "confidence_0_1": 0.9,
  "data_quality_flags": [],
  "rules_version": "1.0.0"
}
```

## Zone Thresholds

- **GREEN**: score_0_100 >= 80
- **YELLOW**: score_0_100 41-79
- **RED**: score_0_100 <= 40

## Approval Types

- `NONE` - No approval needed, just notify
- `REGISTRATION_SIMPLE` - Simple registration
- `LOCAL_APPROVAL_REQUIRED` - Local school committee approval (like MA)
- `LICENSE_REQUIRED` - State license required
- `CERTIFICATE_OR_APPROVAL_REQUIRED` - Formal approval process
- `COMPLEX_OR_OVERSIGHT` - Rigorous process, charter-only, etc.
- `UNKNOWN` - No data, use default

## Missing Data Rule

If state cannot be determined or isn't in the database:

- Return zone="yellow", score_0_100=70, confidence=0.3-0.4
- Add flag: "DATA_MISSING_DEFAULT_0_7" or "ADDRESS_STATE_UNRESOLVED"
- NEVER crash or return null

## State Scoring Data

### GREEN States (score 80+, easy)

| State | Score | Approval Type       | Gating | Timeline (days) |
| ----- | ----- | ------------------- | ------ | --------------- |
| TX    | 95    | NONE                | No     | 7               |
| ID    | 92    | NONE                | No     | 7               |
| AK    | 90    | NONE                | No     | 7               |
| OK    | 90    | REGISTRATION_SIMPLE | No     | 30              |
| WY    | 90    | NONE                | No     | 7               |
| MT    | 88    | NONE                | No     | 7               |
| MO    | 88    | NONE                | No     | 7               |
| IN    | 87    | NONE                | No     | 7               |
| IL    | 86    | NONE                | No     | 7               |
| KS    | 86    | NONE                | No     | 7               |
| NE    | 86    | NONE                | No     | 7               |
| AL    | 85    | NONE                | No     | 7               |
| AZ    | 82    | REGISTRATION_SIMPLE | No     | 30              |
| CO    | 80    | REGISTRATION_SIMPLE | No     | 30              |

### YELLOW States (score 41-79, moderate)

| State | Score | Approval Type                    | Gating | Timeline (days) |
| ----- | ----- | -------------------------------- | ------ | --------------- |
| FL    | 78    | REGISTRATION_SIMPLE              | No     | 30              |
| GA    | 78    | REGISTRATION_SIMPLE              | No     | 30              |
| NC    | 78    | REGISTRATION_SIMPLE              | No     | 30              |
| TN    | 78    | REGISTRATION_SIMPLE              | No     | 30              |
| UT    | 78    | REGISTRATION_SIMPLE              | No     | 30              |
| AR    | 76    | REGISTRATION_SIMPLE              | No     | 30              |
| LA    | 76    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| SC    | 76    | REGISTRATION_SIMPLE              | No     | 30              |
| VA    | 75    | REGISTRATION_SIMPLE              | No     | 30              |
| WI    | 75    | REGISTRATION_SIMPLE              | No     | 30              |
| MI    | 74    | REGISTRATION_SIMPLE              | No     | 30              |
| MN    | 74    | REGISTRATION_SIMPLE              | No     | 30              |
| OH    | 74    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| NM    | 72    | REGISTRATION_SIMPLE              | No     | 30              |
| NV    | 72    | LICENSE_REQUIRED                 | Yes    | 150             |
| WA    | 72    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| OR    | 70    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| DE    | 68    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| KY    | 68    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| WV    | 68    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| HI    | 65    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| IA    | 65    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| NH    | 65    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| CT    | 62    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| ME    | 62    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| VT    | 62    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| CA    | 60    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| NJ    | 60    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| PA    | 60    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| MA    | 58    | LOCAL_APPROVAL_REQUIRED          | Yes    | 120             |
| MD    | 55    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |
| RI    | 55    | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes    | 90              |

### RED States (score <=40, difficult)

| State | Score | Approval Type        | Gating | Timeline (days) |
| ----- | ----- | -------------------- | ------ | --------------- |
| NY    | 45    | COMPLEX_OR_OVERSIGHT | Yes    | 365             |
| ND    | 42    | COMPLEX_OR_OVERSIGHT | Yes    | 365             |
| DC    | 40    | COMPLEX_OR_OVERSIGHT | Yes    | 365             |

## How to Score

### Single Address

1. Extract state from address
2. Look up state in table above
3. Return full JSON with all fields

### CSV/Batch

1. Read CSV, find address column ("address", "Address", "location", "site")
2. For each row, score the address
3. Add columns: `edu_score`, `edu_zone`, `edu_approval_type`, `edu_gating`, `edu_timeline_days`, `edu_summary`
4. Write output CSV

### If State Not Found

Return:

```json
{
  "score_0_100": 70,
  "zone": "yellow",
  "confidence_0_1": 0.3,
  "data_quality_flags": [
    "ADDRESS_STATE_UNRESOLVED",
    "DATA_MISSING_DEFAULT_0_7"
  ],
  "requirements_summary": "Could not determine state. Using default estimate."
}
```

## Example Responses

**Austin, TX:**

> Score: 95 (GREEN)
> Approval: NONE - just file notification
> Gating: No
> Timeline: ~7 days
> Texas has minimal requirements. No state approval needed, no teacher certification, flexible curriculum.

**Boston, MA:**

> Score: 58 (YELLOW)
> Approval: LOCAL_APPROVAL_REQUIRED
> Gating: Yes
> Timeline: ~120 days
> Massachusetts delegates to local school committees. Boston School Committee approval required before opening.

**Las Vegas, NV:**

> Score: 72 (YELLOW)
> Approval: LICENSE_REQUIRED
> Gating: Yes
> Timeline: ~150 days
> Nevada requires state licensure for private schools.

**New York, NY:**

> Score: 45 (RED)
> Approval: COMPLEX_OR_OVERSIGHT
> Gating: Yes
> Timeline: ~365 days
> New York has rigorous substantial equivalency requirements and oversight.

## Test Cases

Use these to verify correct behavior:

1. `Austin, TX` → GREEN, ~95, gating=false
2. `Boston, MA` → YELLOW, ~58, LOCAL_APPROVAL_REQUIRED, gating=true
3. `Las Vegas, NV` → YELLOW, ~72, LICENSE_REQUIRED, gating=true
4. `Fargo, ND` → RED, ~42, gating=true
5. `Some Place, XX` → YELLOW, 70, data_quality_flags includes "ADDRESS_STATE_UNRESOLVED"

---

## Scoring for Building Suitability

This skill is used as a sub-skill of `alpha-building-suitability`. Convert the score to 0-1 scale:

### Conversion Formula

```
approval_score = score_0_100 / 100
```

### Examples

| State | score_0_100 | approval_score (0-1) |
|-------|-------------|----------------------|
| TX | 95 | 0.95 |
| CA | 60 | 0.60 |
| MA | 58 | 0.58 |
| NY | 45 | 0.45 |
| Unknown | 70 | 0.70 |

### Output for Parent Skill

Return to `alpha-building-suitability`:
- `approval_score`: 0-1 score (score_0_100 / 100)
- `zone`: GREEN / YELLOW / RED
- `gating`: boolean (must approve before opening?)
- `timeline_days`: likely timeline in days
