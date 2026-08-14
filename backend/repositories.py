"""Excel utility functions and resource repositories (machines, cutting tools)."""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MACHINE_FILE = Path(
    os.getenv("MACHINE_DB_FILE", str(DATA_DIR / "machines.xlsx"))
).expanduser().resolve()
TOOL_FILE = Path(
    os.getenv("CUTTING_TOOL_DB_FILE", str(DATA_DIR / "tools.xlsx"))
).expanduser().resolve()
MACHINE_SHEET = "Export"
TOOL_SHEET = "Tool_Selection"


# ============================================================
# Excel utility functions
# ============================================================

def normalize_excel_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def parse_optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1", "yes", "stopped"}:
        return True
    if text in {"false", "no", "n", "0", "no", "active"}:
        return False
    return None


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def convert_length_to_mm(value: Any, unit: Any) -> Optional[float]:
    number = to_float(value)
    if number is None:
        return None
    unit_text = str(unit or "mm").strip().lower()
    if unit_text in {"mm", "millimeter", "millimeters"}:
        return number
    if unit_text in {"cm", "centimeter", "centimeters"}:
        return number * 10
    if unit_text in {"m", "meter", "meters"}:
        return number * 1000
    if unit_text in {"in", "inch", "inches", '"'}:
        return number * 25.4
    return None


def clean_number(value: Optional[float], digits: int = 3) -> Any:
    if value is None:
        return None
    value = round(float(value), digits)
    return int(value) if value.is_integer() else value


# ============================================================
# Material mapping
# ============================================================

MATERIAL_MAPPING = {
    "STEEL": "P", "CARBON STEEL": "P", "ALLOY STEEL": "P",
    "TOOL STEEL": "P", "45": "P", "45#": "P", "C45": "P",
    "AISI 1045": "P", "1045": "P", "Q235": "P", "40CR": "P",
    "42CRMO": "P", "42CRMO4": "P", "AISI 4140": "P",
    "4140": "P", "35CRMO": "P", "CR12MOV": "P", "H13": "P",
    "P20": "P", "20CR": "P", "20CRMNTI": "P", "45MN2": "P",
    "STAINLESS": "M", "STAINLESS STEEL": "M",
    "303": "M", "AISI 303": "M",
    "304": "M", "304L": "M", "SUS304": "M", "AISI 304": "M",
    "316": "M", "316L": "M", "SUS316": "M", "AISI 316": "M",
    "410": "M", "420": "M", "430": "M", "17-4PH": "M",
    "2CR13": "M", "1CR17NI2": "M",
    "CAST IRON": "K", "GRAY IRON": "K", "GREY IRON": "K",
    "DUCTILE IRON": "K", "HT200": "K", "HT250": "K",
    "GG25": "K", "QT450": "K", "QT600": "K", "GGG40": "K",
    "ALUMINUM": "N", "ALUMINIUM": "N", "COPPER": "N",
    "BRASS": "N", "BRONZE": "N", "6061": "N", "6061-T6": "N",
    "7075": "N", "7075-T6": "N", "H62": "N",
    "SUPER ALLOY": "S", "INCONEL": "S", "INCONEL 718": "S",
    "GH4169": "S", "TITANIUM": "S", "TITANIUM ALLOY": "S",
    "TC4": "S", "TI-6AL-4V": "S", "HARDENED": "H",
    "HARDENED STEEL": "H", "BEARING STEEL": "H",
    "GCR15": "H", "SUJ2": "H", "HRC55": "H",
}

PROCESS_ALIASES = {
    "TURNING": "ISO TURNING",
    "ISO TURNING": "ISO TURNING",
    "MILLING": "INDEXABLE MILLING",
    "INDEXABLE MILLING": "INDEXABLE MILLING",
    "SOLID CARBIDE": "SOLID CARBIDE / MULTI-MASTER",
    "DRILLING": "DRILLING",
    "PARTING": "PARTING",
    "GROOVING": "GROOVE TURN",
    "THREADING": "THREADING",
}

TOOL_COLUMN_MAPPING = {
    "iso_material_class": "material_category",
    "material_name": "material_name",
    "iscar_material_group": "material_group",
    "machining_process": "machining_process",
    "hard_to_tough_rank": "hard_tough_rank",
    "grades_in_sequence": "grades_in_sequence",
    "grade": "cutting_tool_grade",
    "first_choice": "first_choice",
    "popular_grade_application_iso": "applicable_materials",
    "coating_type": "coating_type",
    "agent_key": "agent_key",
    "notes": "notes",
}


# ============================================================
# MachineRepository
# ============================================================

class MachineRepository:
    required_columns = {
        "Designation", "Unique identifier", "Manufacturer",
        "Capital Asset Classification", "Machine production stopped",
        "Machine type", "Turning length", "Turning length (Unit)",
        "Max. turning diameter rod.", "Max. turning diameter rod. (Unit)",
        "Max. turning diameter chuck.", "Max. turning diameter chuck. (Unit)",
    }

    _cache: tuple[float, pd.DataFrame] | None = None

    GENERIC_CAPABILITY_COLUMNS = {
        "Supported processes", "Max workpiece length", "Max workpiece length (Unit)",
        "Max workpiece diameter", "Max workpiece diameter (Unit)",
    }

    def load(self) -> pd.DataFrame:
        if not MACHINE_FILE.is_file():
            raise FileNotFoundError(f"Machine database not found: {MACHINE_FILE}")
        mtime = MACHINE_FILE.stat().st_mtime
        if MachineRepository._cache and MachineRepository._cache[0] == mtime:
            return MachineRepository._cache[1].copy()
        df = pd.read_excel(MACHINE_FILE, sheet_name=MACHINE_SHEET)
        missing = sorted(self.required_columns - set(df.columns))
        if missing:
            raise ValueError(f"Machine Excel missing required columns: {missing}")
        df = df[df["Designation"].notna() & df["Unique identifier"].notna()].copy()
        MachineRepository._cache = (mtime, df)
        return df.copy()

    def search_turning(
        self,
        required_length_mm: float,
        required_diameter_mm: float,
        top_n: int = 5,
    ) -> dict[str, Any]:
        df = self.load()
        mask = (
            df["Capital Asset Classification"].astype(str).str.contains("Turning", case=False, regex=False, na=False)
            | df["Machine type"].astype(str).str.contains("Lathe", case=False, regex=False, na=False)
        )
        df = df[mask]
        active: list[dict[str, Any]] = []
        stopped: list[dict[str, Any]] = []

        for _, row in df.iterrows():
            raw_status = row.get("Machine production stopped")
            stopped_flag = parse_optional_bool(raw_status)
            if stopped_flag is None:
                status, source = "active", "blank_assumed_active"
            elif stopped_flag is False:
                status, source = "active", "explicit_false"
            else:
                status, source = "stopped", "explicit_true"

            length = convert_length_to_mm(row.get("Turning length"), row.get("Turning length (Unit)"))
            rod = convert_length_to_mm(row.get("Max. turning diameter rod."), row.get("Max. turning diameter rod. (Unit)"))
            chuck = convert_length_to_mm(row.get("Max. turning diameter chuck."), row.get("Max. turning diameter chuck. (Unit)"))

            modes = []
            if rod is not None and rod >= required_diameter_mm:
                modes.append("bar")
            if chuck is not None and chuck >= required_diameter_mm:
                modes.append("chuck")

            if length is None or length < required_length_mm or not modes:
                continue

            diameter_margins = []
            if "bar" in modes:
                diameter_margins.append(float(rod) - required_diameter_mm)
            if "chuck" in modes:
                diameter_margins.append(float(chuck) - required_diameter_mm)

            record = {
                "designation": normalize_excel_value(row.get("Designation")),
                "unique_identifier": normalize_excel_value(row.get("Unique identifier")),
                "manufacturer": normalize_excel_value(row.get("Manufacturer")),
                "machine_type": normalize_excel_value(row.get("Machine type")),
                "production_status": status,
                "production_status_source": source,
                "turning_length_mm": clean_number(length),
                "max_turning_diameter_rod_mm": clean_number(rod),
                "max_turning_diameter_chuck_mm": clean_number(chuck),
                "supported_loading_modes": modes,
                "tool_holding_fixture": normalize_excel_value(row.get("Tool holding fixture")),
                "spindle_speed_max_rpm": clean_number(to_float(row.get("Spindle speed max."))),
                "_fit_score": (
                    max(float(length) - required_length_mm, 0)
                    + (min(diameter_margins) if diameter_margins else 0)
                ),
            }
            (active if status == "active" else stopped).append(record)

        active.sort(key=lambda item: item["_fit_score"])
        stopped.sort(key=lambda item: item["_fit_score"])

        def clean(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [{k: v for k, v in item.items() if k != "_fit_score"} for item in items[:top_n]]

        return {
            "conclusion": "satisfied" if active else "not_satisfied",
            "message": (
                "Found turning machine records matching size and not marked stopped."
                if active
                else "No turning machine records found matching length, diameter and status requirements."
            ),
            "required_length_mm": required_length_mm,
            "required_diameter_mm": required_diameter_mm,
            "active_matches": clean(active),
            "stopped_matches": clean(stopped),
            "data_scope_note": (
                "Empty production status field is treated as not marked stopped; "
                "this field does not represent current machine power-on or scheduling status."
            ),
        }

    def search_process(
        self,
        process: str,
        required_length_mm: float,
        required_diameter_mm: float,
        top_n: int = 5,
    ) -> dict[str, Any]:
        """Match route operations against local machine capability records.

        Turning retains its dedicated bar/chuck matching rules.  Other process
        types use the generic capability columns added to the machine library.
        """
        if process == "ISO Turning":
            return self.search_turning(required_length_mm, required_diameter_mm, top_n)

        df = self.load()
        if not self.GENERIC_CAPABILITY_COLUMNS.issubset(df.columns):
            return {
                "conclusion": "not_covered",
                "message": "Machine library has no generic capability fields for this process.",
                "process": process,
                "required_length_mm": required_length_mm,
                "required_diameter_mm": required_diameter_mm,
                "active_matches": [], "stopped_matches": [],
            }

        process_mask = df["Supported processes"].fillna("").astype(str).apply(
            lambda value: process.casefold() in {item.strip().casefold() for item in value.split("|")}
        )
        active: list[dict[str, Any]] = []
        stopped: list[dict[str, Any]] = []

        for _, row in df[process_mask].iterrows():
            length = convert_length_to_mm(
                row.get("Max workpiece length"), row.get("Max workpiece length (Unit)")
            )
            diameter = convert_length_to_mm(
                row.get("Max workpiece diameter"), row.get("Max workpiece diameter (Unit)")
            )
            if length is None or diameter is None or length < required_length_mm or diameter < required_diameter_mm:
                continue

            stopped_flag = parse_optional_bool(row.get("Machine production stopped"))
            status = "stopped" if stopped_flag is True else "active"
            record = {
                "designation": normalize_excel_value(row.get("Designation")),
                "unique_identifier": normalize_excel_value(row.get("Unique identifier")),
                "manufacturer": normalize_excel_value(row.get("Manufacturer")),
                "machine_type": normalize_excel_value(row.get("Machine type")),
                "supported_processes": normalize_excel_value(row.get("Supported processes")),
                "production_status": status,
                "max_workpiece_length_mm": clean_number(length),
                "max_workpiece_diameter_mm": clean_number(diameter),
                "max_workpiece_weight_kg": clean_number(to_float(row.get("Max workpiece weight"))),
                "max_gear_module": clean_number(to_float(row.get("Max gear module"))),
                "capability_source_url": normalize_excel_value(row.get("Capability source URL")),
                "capability_notes": normalize_excel_value(row.get("Capability notes")),
                "_fit_score": (float(length) - required_length_mm) + (float(diameter) - required_diameter_mm),
            }
            (active if status == "active" else stopped).append(record)

        active.sort(key=lambda item: item["_fit_score"])
        stopped.sort(key=lambda item: item["_fit_score"])

        def clean(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [{key: value for key, value in item.items() if key != "_fit_score"} for item in items[:top_n]]

        return {
            "conclusion": "satisfied" if active else "not_satisfied",
            "message": (
                f"Found active local machine records for {process}."
                if active else f"No active local machine records meet {process} size requirements."
            ),
            "process": process,
            "required_length_mm": required_length_mm,
            "required_diameter_mm": required_diameter_mm,
            "active_matches": clean(active),
            "stopped_matches": clean(stopped),
        }


# ============================================================
# ToolRepository
# ============================================================

class ToolRepository:
    _cache: tuple[float, pd.DataFrame] | None = None

    def load(self) -> pd.DataFrame:
        if not TOOL_FILE.is_file():
            raise FileNotFoundError(f"Tool database not found: {TOOL_FILE}")
        mtime = TOOL_FILE.stat().st_mtime
        if ToolRepository._cache and ToolRepository._cache[0] == mtime:
            return ToolRepository._cache[1].copy()
        df = pd.read_excel(TOOL_FILE, sheet_name=TOOL_SHEET)
        df.columns = (
            df.columns.astype(str).str.strip().str.lower()
            .str.replace(r"[^a-z0-9]+", "_", regex=True).str.strip("_")
        )
        df = df.rename(columns=TOOL_COLUMN_MAPPING)
        required = {
            "material_category", "material_group", "machining_process",
            "hard_tough_rank", "cutting_tool_grade", "first_choice",
        }
        missing = sorted(required - set(df.columns))
        if missing:
            raise ValueError(f"Tool Excel missing required columns: {missing}")
        ToolRepository._cache = (mtime, df)
        return df.copy()

    @staticmethod
    def resolve_material(material: str) -> dict[str, Any]:
        text = str(material or "").strip()
        upper = text.upper()
        if not text:
            raise ValueError("Material cannot be empty.")
        if upper in {"P", "M", "K", "N", "S", "H"}:
            return {"mode": "iso", "value": upper, "label": f"ISO {upper}"}
        if upper in MATERIAL_MAPPING:
            value = MATERIAL_MAPPING[upper]
            return {"mode": "iso", "value": value, "label": f"{text} -> ISO {value}"}

        explicit = re.fullmatch(
            r"(?:ISCAR\s*)?(?:MATERIAL\s*)?GROUP\s*[:#-]?\s*(\d+(?:\s*-\s*\d+)?)", upper
        )
        if explicit is None:
            explicit = re.fullmatch(r"material\s*group\s*[:#-]?\s*(\d+(?:\s*-\s*\d+)?)", text, re.IGNORECASE)
        if explicit:
            return {
                "mode": "group",
                "value": explicit.group(1).replace(" ", ""),
                "label": f"ISCAR material group {explicit.group(1)}",
            }

        for key, value in sorted(MATERIAL_MAPPING.items(), key=lambda item: len(item[0]), reverse=True):
            if len(key) >= 3 and (key in upper or upper in key):
                return {"mode": "iso", "value": value, "label": f"{text} -> ISO {value}"}
        raise ValueError(
            f"Unrecognized material: {material}. Please enter common grade, ISO category or ISCAR material group."
        )

    @staticmethod
    def group_matches(excel_value: Any, query: str) -> bool:
        excel_text = str(excel_value).strip()
        if query.isdigit():
            number = int(query)
            numbers = [int(item) for item in re.findall(r"\d+", excel_text)]
            if len(numbers) >= 2:
                return numbers[0] <= number <= numbers[1]
            return number in numbers
        return excel_text.casefold() == query.casefold()

    def search(self, material: str, process: str, top_n: int = 3) -> dict[str, Any]:
        df = self.load()
        material_info = self.resolve_material(material)
        process_query = PROCESS_ALIASES.get(process.strip().upper(), process.strip().upper())

        if material_info["mode"] == "iso":
            material_mask = df["material_category"].fillna("").astype(str).str.strip().str.upper() == material_info["value"]
        else:
            material_mask = df["material_group"].apply(lambda value: self.group_matches(value, material_info["value"]))

        process_series = df["machining_process"].fillna("").astype(str).str.strip().str.upper()
        process_mask = process_series == process_query
        if not process_mask.any():
            process_mask = process_series.str.contains(process_query, regex=False, na=False)

        selected = df.loc[material_mask & process_mask].copy()
        if selected.empty:
            return {
                "conclusion": "not_covered",
                "message": "No material and process combination found in tool table.",
                "process": process,
                "process_interpreted_as": process_query,
                "material_interpretation": material_info["label"],
                "recommendations": [],
            }

        selected["_rank"] = pd.to_numeric(selected["hard_tough_rank"], errors="coerce")
        selected["_first_choice"] = selected["first_choice"].fillna("").astype(str).str.strip().str.lower().isin({"yes", "true", "1", "y"})
        selected = selected.sort_values(["_first_choice", "_rank"], ascending=[False, True], na_position="last")

        recommendations = []
        for _, row in selected.head(top_n).iterrows():
            rank = to_float(row.get("_rank"))
            recommendations.append({
                "cutting_tool_grade": normalize_excel_value(row.get("cutting_tool_grade")),
                "machining_process": normalize_excel_value(row.get("machining_process")),
                "first_choice": bool(row.get("_first_choice")),
                "hard_tough_rank": int(rank) if rank is not None else None,
                "coating_type": normalize_excel_value(row.get("coating_type")),
                "applicable_materials": normalize_excel_value(row.get("applicable_materials")),
                "material_category": normalize_excel_value(row.get("material_category")),
                "material_group": normalize_excel_value(row.get("material_group")),
            })
        return {
            "conclusion": "satisfied",
            "message": "Found matching grades in tool table for material and process.",
            "process": process,
            "process_interpreted_as": process_query,
            "material_interpretation": material_info["label"],
            "recommendations": recommendations,
        }
