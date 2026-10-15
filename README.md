# NGP VAN Metadata Grabber

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![API: NGP VAN v4](https://img.shields.io/badge/API-NGP%20VAN%20v4-blueviolet)](https://docs.ngpvan.com/reference/overview)

A lightweight, zero-fuss tool designed for **campaign and party data volunteers** to quickly pull clean, structured metadata out of **NGP VAN (VoteBuilder)**.

Stop clicking through the VAN web interface to hunt down individual IDs. Run this script once to export clean CSVs and JSONs so you can jump straight into mapping your canvass, phone bank, or vendor call sheets for bulk upload.

**WARNING** NEVER EVER EVER COMMIT YOUR API KEY. Double Check the .gitignore file before doing anything. I swear to god if anyone clones this and then posts their API key to git...

You can reach out to me if you need help.

---

## The Problem It Solves

When bulk uploading voter contact results or databack spreadsheets into NGP VAN, the system requires exact internal numeric IDs:

- **Survey Question IDs** & **Response IDs** (e.g., Support levels, Yard Signs, Candidate IDs)
- **Input Type IDs** (e.g., `Bulk`, `API`, `VPB`, `Manual`, `Mobile`)
- **Contact Type IDs** (e.g., `Phone`, `Walk`, `Paid Phone`, `Text`)
- **Result Code IDs** (e.g., `Canvassed`, `Busy`, `Disconnected`, `Left Message`, `Refused`)
- **Activist Code IDs** (e.g., `Volunteer`, `Yard Sign`, `Donor`)

This tool queries the NGP VAN API (v4), handles pagination automatically, flattens nested structures, and outputs clean reference tables ready for **Excel `XLOOKUP`**, **Google Sheets**, **Python pandas**, **R**, or **SQL**.

---

## Quickstart (Under 2 Minutes)

### 1. Clone the repository

```bash
git clone https://github.com/kevinverhoff/ngp_van_metadata_grabber.git
cd ngp_van_metadata_grabber
```

### 2. Set up a virtual environment

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure your credentials

Copy the example environment file:
```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in your details:
```ini
app_name=your_application_name
van_api_key=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
db_mode=0
```

> **Where do I get an API Key and Application Name?**
> Ask your state party, coordinated campaign, or county party VAN Administrator for an **API key**. They will provide an **Application Name** (e.g., `IN.013.mobilize` or `acmeProduct`) and an **API Key** (a 36-character GUID).
>
> **Which `db_mode` should I use?**
> - `0` = **My Voters** (Default — used for voter files, voter contact, precinct canvassing)
> - `1` = **My Campaign** (Used for committee volunteers, donors, event attendees)

### 5. Test your connection

Run a quick health check to verify your credentials against the VAN `/apiKeyProfiles` endpoint:

```bash
python pull_metadata.py --test
```

If successful, you will see your committee name, database, and permissions:
```text
Checking connection to /apiKeyProfiles...
[OK] Connected successfully! Key Profile:
  - Committee:   IN133 !Putnam County Democratic Party
  - Database:    VoteBuilder Indiana
  - Username:    IN.013.mobilize
  - My Voters:   True
  - My Campaign: True
```

### 6. Pull your metadata

Fetch all metadata and export to CSV and JSON:

```bash
python pull_metadata.py
```

---

## Output Files & Bulk Upload Mapping Guide

Files are saved into the `metadata/` folder (customizable via `--output-dir`):

| File | What's In It | How to Use in Bulk Upload / Databack Mapping |
| :--- | :--- | :--- |
| **`survey_questions.csv`** | All survey questions, IDs, cycles, types, script text | Look up question IDs (`surveyQuestionId`) by question name or candidate |
| **`survey_responses.csv`** | Flattened question + response choices | Map survey answers (e.g. "Strong Support", "Undecided") to `surveyResponseId` |
| **`input_types.csv`** | Methods of data entry (`API`, `Bulk`, `VPB`, etc.) | Set `inputTypeId` (usually `4` for Bulk Upload or `11` for API) |
| **`contact_types.csv`** | Contact channels (`Phone`, `Walk`, `Paid Phone`, etc.) | Set `contactTypeId` (e.g., `1` for Phone, `2` for Walk) |
| **`result_codes.csv`** | Contact dispositions (`Canvassed`, `Busy`, `Disconnected`, etc.) | Map vendor call dispositions to `resultCodeId` (e.g., `14` for Canvassed) |
| **`activist_codes.csv`** | Activist code IDs, types, and descriptions | Tag contacts with codes (e.g. `Yard Sign Requested`) |
| **`van_metadata_consolidated.json`** | Single master JSON with all metadata and fetch timestamp | Ideal for programmatic ingestion into web apps or automated pipelines |

---

## Example: Mapping a Vendor Call Sheet in Python

When your paid phone vendor sends back a spreadsheet (`call_results.xlsx`), use the generated CSVs to quickly map string codes into VAN IDs:

```python
import pandas as pd

# Load vendor databack
calls = pd.read_excel("call_results.xlsx")

# Load VAN metadata
results = pd.read_csv("metadata/result_codes.csv")
responses = pd.read_csv("metadata/survey_responses.csv")

# Map call outcome (e.g. 'Busy' -> 18, 'Complete' -> 14)
disposition_map = {"Complete": 14, "Busy": 18, "Answer Machine": 19, "Disconnect": 25}
calls["result_code_id"] = calls["status"].map(disposition_map)

# Map candidate support response to VAN surveyResponseId
candidate_q = responses[responses["surveyQuestionName"] == "Candidate Support"]
response_map = dict(zip(candidate_q["responseName"], candidate_q["surveyResponseId"]))
calls["survey_response_id"] = calls["answer"].map(response_map)

# Ready for VAN bulk upload!
calls[["van_id", "result_code_id", "survey_response_id"]].to_csv("ready_for_van.csv", index=False)
```

---

## Command-Line Options

```text
usage: pull_metadata.py [-h] [--env-file ENV_FILE] [--output-dir OUTPUT_DIR]
                        [--format {csv,json,both}] [--db-mode {0,1}]
                        [--statuses STATUSES] [--no-activist-codes] [--test] [-v]

options:
  -h, --help            Show this help message and exit
  --env-file ENV_FILE   Path to a custom .env file
  --output-dir DIR      Output directory (default: ./metadata)
  --format {csv,json,both}
                        Export format: csv, json, or both (default: both)
  --db-mode {0,1}       Override database mode (0 = My Voters, 1 = My Campaign)
  --statuses STATUSES   Survey question statuses (default: 'Active,Archived')
  --no-activist-codes   Skip fetching activist codes
  --test, --check       Test connection and print key info without fetching all data
  -v, --verbose         Print verbose request URLs
```

### Useful CLI Examples

- **Fetch including inactive questions:**
  ```bash
  python pull_metadata.py --statuses "Active,Archived,Inactive"
  ```
- **Export CSVs only into a custom directory:**
  ```bash
  python pull_metadata.py --output-dir ./van_data --format csv
  ```
- **Work in My Campaign mode:**
  ```bash
  python pull_metadata.py --db-mode 1
  ```

---

## Troubleshooting & FAQs

### `401 Unauthorized`
- Make sure `app_name` in `.env` exactly matches the Application Name assigned to your key.
- Verify the API key has not expired.
- Check if your key belongs to **My Voters** (`db_mode=0`) or **My Campaign** (`db_mode=1`).

### `403 Forbidden`
- Your API key does not have permission to access that specific endpoint (e.g., Activist Codes or Survey Questions). Contact your VAN Administrator to add the appropriate User Permissions.
- If you lack permissions for activist codes, run with `--no-activist-codes`.

### Security Note
- Never commit your `.env` file or API keys to GitHub.
- `.env` and `metadata/` are included in `.gitignore` by default to protect your committee's data.

---

## License

This project is licensed under the [MIT License](LICENSE).
Feel free to fork, clone, and share with fellow campaign data volunteers!
