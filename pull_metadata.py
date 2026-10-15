#!/usr/bin/env python3
"""
NGP VAN Metadata Grabber
------------------------
Pulls reference metadata from the NGP VAN API (v4):
  - Survey Questions & Survey Question Responses
  - Input Types (e.g. API, Bulk, VPB, Manual, Mobile)
  - Contact Types (e.g. Phone, Walk, Paid Phone)
  - Result Codes (e.g. Canvassed, Busy, Disconnected, Left Message, Refused)
  - Activist Codes (e.g. Volunteer, Yard Sign, Donor)

Outputs clean, ready-to-use CSV and JSON reference files so data volunteers
can quickly map external canvass/phonebank spreadsheets to VAN IDs for bulk upload.

Documentation: https://docs.ngpvan.com/reference/overview
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import find_dotenv, load_dotenv

# Try importing pandas for tabular exports, fall back to Python standard library
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


def load_credentials(env_file: Optional[str] = None) -> Dict[str, str]:
    """Load and normalize NGP VAN credentials from environment variables or .env file."""
    if env_file:
        load_dotenv(dotenv_path=env_file, override=True)
    else:
        script_dir = Path(__file__).resolve().parent
        local_env = script_dir / ".env"
        if local_env.exists():
            load_dotenv(dotenv_path=local_env, override=False)
        else:
            load_dotenv(find_dotenv(), override=False)

    app_name = (
        os.getenv("app_name")
        or os.getenv("APP_NAME")
        or os.getenv("van_app_name")
        or os.getenv("VAN_APP_NAME")
        or os.getenv("van_application_name")
        or os.getenv("VAN_APPLICATION_NAME")
        or ""
    ).strip()

    api_key = (
        os.getenv("van_api_key")
        or os.getenv("VAN_API_KEY")
        or os.getenv("api_key")
        or os.getenv("API_KEY")
        or ""
    ).strip()

    db_mode = (
        os.getenv("db_mode")
        or os.getenv("DB_MODE")
        or os.getenv("van_db_mode")
        or os.getenv("VAN_DB_MODE")
        or "0"
    ).strip()

    base_url = (
        os.getenv("base_url")
        or os.getenv("BASE_URL")
        or os.getenv("van_base_url")
        or os.getenv("VAN_BASE_URL")
        or "https://api.securevan.com/v4"
    ).strip().rstrip("/")

    return {
        "app_name": app_name,
        "api_key": api_key,
        "db_mode": db_mode,
        "base_url": base_url,
    }


class VanApiClient:
    """Client for interacting with the NGP VAN REST API (v4)."""

    def __init__(
        self,
        app_name: str,
        api_key: str,
        db_mode: str = "0",
        base_url: str = "https://api.securevan.com/v4",
        verbose: bool = False,
    ):
        self.app_name = app_name
        self.api_key = api_key
        self.db_mode = str(db_mode)
        self.base_url = base_url.rstrip("/")
        self.verbose = verbose

        # Determine HTTP Basic Auth password:
        # VAN expects "<API_KEY>|<DB_MODE>" where DB_MODE is 0 (My Voters) or 1 (My Campaign).
        # If the API key already includes "|0" or "|1", do not duplicate it.
        if "|" in self.api_key:
            self.password = self.api_key
        else:
            self.password = f"{self.api_key}|{self.db_mode}"

        self.session = requests.Session()
        self.session.auth = (self.app_name, self.password)
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"VanMetadataGrabber/1.0 ({self.app_name or 'volunteer'})",
        })

    def request(
        self, method: str, endpoint_or_url: str, params: Optional[Dict[str, Any]] = None
    ) -> requests.Response:
        """Execute an HTTP request with error checking and helpful diagnostic messages."""
        if endpoint_or_url.startswith("http://") or endpoint_or_url.startswith("https://"):
            url = endpoint_or_url
        else:
            path = endpoint_or_url.lstrip("/")
            url = f"{self.base_url}/{path}"

        if self.verbose:
            print(f"[{method}] {url} | params={params}")

        try:
            response = self.session.request(method=method, url=url, params=params, timeout=45)
        except requests.RequestException as err:
            print(f"\n[ERROR] Network error while connecting to {url}:\n{err}", file=sys.stderr)
            raise

        if response.status_code == 401:
            print(
                "\n[ERROR 401: Unauthorized] NGP VAN rejected your credentials.\n"
                "Please verify:\n"
                f"  1. app_name matches the Application Name assigned to your key: '{self.app_name}'\n"
                f"  2. van_api_key is active and not expired.\n"
                f"  3. db_mode is correct (0 = My Voters, 1 = My Campaign). Currently using: {self.db_mode}\n",
                file=sys.stderr,
            )
            response.raise_for_status()

        if response.status_code == 403:
            print(
                "\n[ERROR 403: Forbidden] Your API key does not have permissions for this endpoint.\n"
                f"Target URL: {url}\n"
                "Contact your VAN administrator to verify API user permission groups.\n",
                file=sys.stderr,
            )
            response.raise_for_status()

        if not response.ok:
            error_text = response.text
            try:
                err_json = response.json()
                error_text = json.dumps(err_json, indent=2)
            except Exception:
                pass
            print(
                f"\n[ERROR {response.status_code}] Request to {url} failed:\n{error_text}\n",
                file=sys.stderr,
            )
            response.raise_for_status()

        return response

    def introspect(self) -> List[Dict[str, Any]]:
        """Call /apiKeyProfiles to inspect key identity, committee name, and database access."""
        resp = self.request("GET", "apiKeyProfiles")
        data = resp.json()
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        if isinstance(data, list):
            return data
        return [data]

    def get_paginated_items(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None, max_pages: int = 150
    ) -> List[Dict[str, Any]]:
        """Follow nextPageLink pagination to retrieve all items from an endpoint."""
        items: List[Dict[str, Any]] = []
        next_url: Optional[str] = endpoint
        current_params = dict(params or {})
        page = 1

        while next_url and page <= max_pages:
            resp = self.request("GET", next_url, params=current_params)
            data = resp.json()

            if isinstance(data, list):
                items.extend(data)
                break

            page_items = data.get("items", [])
            items.extend(page_items)

            next_link = data.get("nextPageLink")
            if next_link:
                next_url = next_link
                current_params = None  # query params are embedded in nextPageLink
                page += 1
            else:
                next_url = None

        return items

    def get_survey_questions(self, statuses: str = "Active,Archived") -> List[Dict[str, Any]]:
        """Fetch survey questions and nested response choices."""
        params = {"$top": 100}
        if statuses:
            params["statuses"] = statuses
        return self.get_paginated_items("surveyQuestions", params=params)

    def get_input_types(self) -> List[Dict[str, Any]]:
        """Fetch all input types (e.g. API, Bulk, VPB, Manual, Mobile, Website)."""
        resp = self.request("GET", "canvassResponses/inputTypes")
        data = resp.json()
        return data if isinstance(data, list) else data.get("items", [])

    def get_contact_types(self) -> List[Dict[str, Any]]:
        """Fetch all contact types (e.g. Phone, Walk, Paid Phone)."""
        resp = self.request("GET", "canvassResponses/contactTypes")
        data = resp.json()
        return data if isinstance(data, list) else data.get("items", [])

    def get_result_codes(self) -> List[Dict[str, Any]]:
        """Fetch all result codes (e.g. Canvassed, Busy, Left Message, Disconnected, Refused)."""
        resp = self.request("GET", "canvassResponses/resultCodes")
        data = resp.json()
        return data if isinstance(data, list) else data.get("items", [])

    def get_activist_codes(self, statuses: str = "Active") -> List[Dict[str, Any]]:
        """Fetch activist codes available in this context."""
        params = {"$top": 100}
        if statuses:
            params["statuses"] = statuses
        return self.get_paginated_items("activistCodes", params=params)


# --- Data Transformation & Cleaning Helpers ---

def extract_survey_responses(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten nested survey questions into a clean Question-to-Response lookup table."""
    flattened: List[Dict[str, Any]] = []
    for q in questions:
        q_id = q.get("surveyQuestionId")
        q_name = q.get("name")
        q_type = q.get("type")
        q_cycle = q.get("cycle")
        q_status = q.get("status")
        q_script = q.get("scriptQuestion")

        for r in q.get("responses") or []:
            flattened.append({
                "surveyQuestionId": q_id,
                "surveyQuestionName": q_name,
                "surveyQuestionType": q_type,
                "surveyQuestionCycle": q_cycle,
                "surveyQuestionStatus": q_status,
                "surveyResponseId": r.get("surveyResponseId"),
                "responseName": r.get("name"),
                "responseMediumName": r.get("mediumName"),
                "responseShortName": r.get("shortName"),
                "scriptQuestion": q_script,
            })
    return flattened


def clean_question_summary(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create a flat question summary table suitable for tabular/CSV viewing."""
    summary = []
    for q in questions:
        responses = q.get("responses") or []
        summary.append({
            "surveyQuestionId": q.get("surveyQuestionId"),
            "name": q.get("name"),
            "type": q.get("type"),
            "cycle": q.get("cycle"),
            "status": q.get("status"),
            "mediumName": q.get("mediumName"),
            "shortName": q.get("shortName"),
            "scriptQuestion": q.get("scriptQuestion"),
            "responseCount": len(responses),
            "responseNames": "; ".join([r.get("name", "") for r in responses if r.get("name")]),
        })
    return summary


def clean_contact_types(contact_types: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten and standardize contact types."""
    cleaned = []
    for ct in contact_types:
        cleaned.append({
            "contactTypeId": ct.get("contactTypeId"),
            "name": ct.get("name"),
            "channelTypeName": ct.get("channelTypeName"),
            "contactTypeDisplayName": ct.get("contactTypeDisplayName"),
        })
    return cleaned


def clean_result_codes(result_codes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Unpack nested outcome groups into clean scalar columns."""
    cleaned = []
    for rc in result_codes:
        group = rc.get("resultOutcomeGroup") or {}
        cleaned.append({
            "resultCodeId": rc.get("resultCodeId"),
            "name": rc.get("name"),
            "mediumName": rc.get("mediumName"),
            "shortName": rc.get("shortName"),
            "resultOutcomeGroupId": group.get("resultOutcomeGroupId"),
            "resultOutcomeGroupName": group.get("resultOutcomeGroupName"),
        })
    return cleaned


def clean_activist_codes(activist_codes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten activist codes for CSV export."""
    cleaned = []
    for ac in activist_codes:
        cleaned.append({
            "activistCodeId": ac.get("activistCodeId"),
            "name": ac.get("name"),
            "type": ac.get("type"),
            "status": ac.get("status"),
            "description": ac.get("description"),
            "mediumName": ac.get("mediumName"),
            "shortName": ac.get("shortName"),
        })
    return cleaned


# --- Exporters ---

def save_json(data: Any, filepath: Path) -> None:
    """Save formatted JSON."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_csv(data: List[Dict[str, Any]], filepath: Path) -> None:
    """Save records to CSV using pandas if installed, otherwise csv.DictWriter."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if not data:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("")
        return

    if HAS_PANDAS:
        df = pd.DataFrame(data)
        df.to_csv(filepath, index=False)
    else:
        fieldnames: List[str] = []
        for item in data:
            for k in item.keys():
                if k not in fieldnames:
                    fieldnames.append(k)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)


# --- Main CLI ---

def main():
    parser = argparse.ArgumentParser(
        description="NGP VAN Metadata Grabber - Pull survey questions, responses, contact types, input types, result codes, and activist codes for bulk data uploads."
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="Path to custom .env file (default: looks for .env in current and script dirs)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="metadata",
        help="Directory to save exported files (default: ./metadata)",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "json", "both"],
        default="both",
        help="Export format: csv, json, or both (default: both)",
    )
    parser.add_argument(
        "--db-mode",
        choices=["0", "1"],
        default=None,
        help="Override database mode: 0 for My Voters (default), 1 for My Campaign",
    )
    parser.add_argument(
        "--statuses",
        type=str,
        default="Active,Archived",
        help="Survey question statuses to retrieve: Active, Archived, Inactive (default: 'Active,Archived')",
    )
    parser.add_argument(
        "--no-activist-codes",
        action="store_true",
        help="Skip pulling activist codes",
    )
    parser.add_argument(
        "--test",
        "--check",
        action="store_true",
        dest="test_mode",
        help="Test connection and print API Key Profile information only (does not pull metadata)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print verbose request logs",
    )

    args = parser.parse_args()

    # Load credentials
    creds = load_credentials(args.env_file)
    if args.db_mode:
        creds["db_mode"] = args.db_mode

    app_name = creds["app_name"]
    api_key = creds["api_key"]
    db_mode = creds["db_mode"]
    base_url = creds["base_url"]

    # Validate presence of credentials
    if not app_name or not api_key:
        print("\n" + "=" * 72)
        print(" [!] MISSING NGP VAN CREDENTIALS")
        print("=" * 72)
        print("Please configure your credentials in a .env file:")
        print("  1. Copy .env.example to .env:  cp .env.example .env")
        print("  2. Fill in your app_name and van_api_key in .env")
        print("  3. Set db_mode=0 for My Voters or db_mode=1 for My Campaign")
        print("=" * 72 + "\n")
        sys.exit(1)

    print(f"Connecting to NGP VAN API:")
    print(f"  Base URL:         {base_url}")
    print(f"  Application Name: {app_name}")
    print(f"  API Key:          {'*' * 8}{api_key[-4:] if len(api_key) >= 4 else '***'}")
    print(f"  Database Mode:    {db_mode} ({'My Voters' if db_mode == '0' else 'My Campaign'})")

    client = VanApiClient(
        app_name=app_name,
        api_key=api_key,
        db_mode=db_mode,
        base_url=base_url,
        verbose=args.verbose,
    )

    # 1. Introspection check
    print("\nVerifying credentials via /apiKeyProfiles...")
    try:
        profiles = client.introspect()
        print("[OK] Connected successfully! Key Profile:")
        for profile in profiles:
            print(f"  - Committee:   {profile.get('committeeName', 'N/A')}")
            print(f"  - Database:    {profile.get('databaseName', 'N/A')}")
            print(f"  - Username:    {profile.get('username', 'N/A')}")
            print(f"  - My Voters:   {profile.get('hasMyVoters', 'N/A')}")
            print(f"  - My Campaign: {profile.get('hasMyCampaign', 'N/A')}")
            print(f"  - Key Type:    {profile.get('apiKeyTypeName', 'N/A')}")
    except Exception as err:
        print(f"[!] Authentication / Introspection failed: {err}")
        sys.exit(1)

    if args.test_mode:
        print("\nConnection test passed. Exiting without pulling full metadata.")
        return

    # 2. Pull metadata
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    export_csv = args.format in ("csv", "both")
    export_json = args.format in ("json", "both")

    print(f"\nPulling metadata to: {output_dir}")

    # (a) Survey Questions & Responses
    print(f"\n[1/5] Fetching Survey Questions (statuses: {args.statuses})...")
    raw_questions = client.get_survey_questions(statuses=args.statuses)
    questions_summary = clean_question_summary(raw_questions)
    survey_responses = extract_survey_responses(raw_questions)
    print(f"      Retrieved {len(raw_questions)} survey questions.")
    print(f"      Extracted {len(survey_responses)} survey response choices.")

    if export_json:
        save_json(raw_questions, output_dir / "survey_questions.json")
        save_json(survey_responses, output_dir / "survey_responses.json")
    if export_csv:
        save_csv(questions_summary, output_dir / "survey_questions.csv")
        save_csv(survey_responses, output_dir / "survey_responses.csv")

    # (b) Input Types
    print("\n[2/5] Fetching Input Types...")
    input_types = client.get_input_types()
    print(f"      Retrieved {len(input_types)} input types.")
    if export_json:
        save_json(input_types, output_dir / "input_types.json")
    if export_csv:
        save_csv(input_types, output_dir / "input_types.csv")

    # (c) Contact Types
    print("\n[3/5] Fetching Contact Types...")
    contact_types = client.get_contact_types()
    print(f"      Retrieved {len(contact_types)} contact types.")
    if export_json:
        save_json(contact_types, output_dir / "contact_types.json")
    if export_csv:
        save_csv(clean_contact_types(contact_types), output_dir / "contact_types.csv")

    # (d) Result Codes
    print("\n[4/5] Fetching Result Codes...")
    result_codes = client.get_result_codes()
    print(f"      Retrieved {len(result_codes)} result codes.")
    if export_json:
        save_json(result_codes, output_dir / "result_codes.json")
    if export_csv:
        save_csv(clean_result_codes(result_codes), output_dir / "result_codes.csv")

    # (e) Activist Codes
    activist_codes = []
    if not args.no_activist_codes:
        print("\n[5/5] Fetching Activist Codes...")
        activist_codes = client.get_activist_codes()
        print(f"      Retrieved {len(activist_codes)} activist codes.")
        if export_json:
            save_json(activist_codes, output_dir / "activist_codes.json")
        if export_csv:
            save_csv(clean_activist_codes(activist_codes), output_dir / "activist_codes.csv")
    else:
        print("\n[5/5] Skipping Activist Codes (--no-activist-codes set).")

    # (f) Consolidated Master Summary
    consolidated = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "databaseMode": db_mode,
        "databaseModeLabel": "My Voters" if db_mode == "0" else "My Campaign",
        "keyProfile": profiles[0] if profiles else {},
        "counts": {
            "surveyQuestions": len(raw_questions),
            "surveyResponses": len(survey_responses),
            "inputTypes": len(input_types),
            "contactTypes": len(contact_types),
            "resultCodes": len(result_codes),
            "activistCodes": len(activist_codes),
        },
        "inputTypes": input_types,
        "contactTypes": contact_types,
        "resultCodes": result_codes,
        "activistCodes": activist_codes,
        "surveyQuestions": raw_questions,
    }
    save_json(consolidated, output_dir / "van_metadata_consolidated.json")

    print("\n" + "=" * 72)
    print(" [✓] METADATA EXPORT COMPLETE")
    print("=" * 72)
    print(f"Output files saved in: {output_dir}")
    if export_csv:
        print("  - survey_questions.csv      (All question names, cycles, and script texts)")
        print("  - survey_responses.csv      (Question ID + Response ID mapping lookup)")
        print("  - input_types.csv           (Data ingestion methods: API, Bulk, VPB, etc.)")
        print("  - contact_types.csv         (Voter contact channels: Phone, Walk, etc.)")
        print("  - result_codes.csv          (Contact dispositions: Canvassed, Busy, etc.)")
        if not args.no_activist_codes:
            print("  - activist_codes.csv        (Activist codes: Volunteer, Yard Sign, etc.)")
    if export_json:
        print("  - van_metadata_consolidated.json (Full combined payload)")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    main()
