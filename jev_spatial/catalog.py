"""Read the NorthSea Ossie contract; never execute SQL found in metadata."""
import hashlib
import json
import re
from pathlib import Path

import yaml


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value):
        raise ValueError(f"Unsupported identifier: {value!r}")
    return '"' + value + '"'


def extensions(field):
    result = {}
    for extension in field.get("custom_extensions", []):
        if extension["vendor_name"] == "ARCGIS":
            result.update(json.loads(extension["data"]))
    return result


def synonyms(field):
    values = field.get("ai_context", {}).get("synonyms", [])
    if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError(f"Invalid synonyms for {field['name']}")
    return {v.strip().casefold() for v in values}


class Catalog:
    def __init__(self, project):
        project = Path(project)
        self.metadata_layout = "knowledge/datasets" if (project / "knowledge/datasets").exists() else "okf"
        paths = [project / "NorthSea.ossie.yaml", project / "NorthSea.ontology.yaml",
                 project / self.metadata_layout / "Wells.md", project / self.metadata_layout / "Pipelines.md"]
        contents = [p.read_bytes() for p in paths]
        texts = [data.decode("utf-8") for data in contents]
        self.sources = {str(p): hashlib.sha256(data).hexdigest() for p, data in zip(paths, contents)}
        ossie, ontology = [yaml.safe_load(t) for t in texts[:2]]
        self.tables = {t["name"]: t for t in ossie["datasets"]}
        self.fields = {name: {f["name"]: f for f in t["fields"]} for name, t in self.tables.items()}
        self.relations = {r["name"] for c in ontology["ontology"] if c["concept"] == "Feature"
                          for r in c.get("relationships", [])}
        if "EPSG:3035" not in ossie["ai_context"]["instructions"]:
            raise ValueError("NorthSea contract must specify EPSG:3035 for metric distances")
        if self.metadata_layout == "knowledge/datasets":
            for table, text in zip(("Wells", "Pipelines"), texts[2:]):
                header = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.DOTALL)
                metadata = yaml.safe_load(header[1]) if header else None
                if not isinstance(metadata, dict) or any(metadata.get(k) != v for k, v in {
                    "type": "Ossie Dataset", "title": table, "source": self.tables[table]["source"],
                }.items()):
                    raise ValueError(f"Knowledge dataset identity disagrees with Ossie: {table}")
            depth_aliases = {"depth", "water depth", "well water depth"}
            if not synonyms(self.field("Wells", "water_depth")) & depth_aliases:
                raise ValueError("Ossie must identify water_depth with a supported depth synonym")
            for name, meta in self.fields["Wells"].items():
                if name != "water_depth" and (synonyms(meta) | {name.replace("_", " ").casefold()}) & depth_aliases:
                    raise ValueError(f"Ambiguous depth alias on Wells.{name}")
            phase = self.field("Pipelines", "current_phase")
            values = phase.get("ai_context", {}).get("sample_values", [])
            if phase["datatype"] != "String" or not isinstance(values, list) or "IN SERVICE" not in values:
                raise ValueError("Ossie current_phase must list the IN SERVICE status")
        else:
            # shortcut: legacy NorthSea OKF uses field headings; use a versioned parser for other projects.
            section = texts[2].partition("## `water_depth`")[2].split("\n## ", 1)[0]
            if "water depth, depth" not in section or "IN SERVICE" not in texts[3]:
                raise ValueError("Required OKF depth/status mappings are missing")
        self.depth_description = self.field("Wells", "water_depth")["description"]
        if self.field("Wells", "water_depth")["datatype"] not in {"Float", "Double", "Integer", "SmallInteger"}:
            raise ValueError("water_depth must be numeric")
        if extensions(self.field("Wells", "water_depth")).get("unit") != "meters":
            raise ValueError("water_depth must be measured in meters")
        for table in ("Wells", "Pipelines", "Discoveries"):
            identifier(table)
            if self.tables[table]["source"].split(".")[-2:] != ["main", table]:
                raise ValueError(f"Unexpected source for {table}")
            if extensions(self.field(table, "geometry")).get("wkid") != 4326:
                raise ValueError(f"Expected EPSG:4326 geometry for {table}")
        self.notes = [f"metadata → {self.metadata_layout}; source files are hashed",
                      f"depth → Wells.water_depth: {self.depth_description}",
                      "in-service → Pipelines.current_phase = 'IN SERVICE' (any pipeline type)",
                      "distance → EPSG:3035 planar meters, longitude/latitude axis order",
                      "within distance is inclusive; relation uses EXISTS, returning each well once"]

    def field(self, table, field):
        try:
            return self.fields[table][field]
        except KeyError:
            raise ValueError(f"Unknown field {table}.{field}") from None

    def validate_database(self, con):
        for table, required in {"Wells": ["OBJECTID", "water_depth", "geometry"],
                                "Pipelines": ["OBJECTID", "current_phase", "geometry"]}.items():
            columns = dict(con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'main' AND table_name = ?", [table]).fetchall())
            for field in required:
                if field not in columns:
                    raise ValueError(f"Database is missing {table}.{field}")
            if not columns["geometry"].startswith("GEOMETRY"):
                raise ValueError(f"{table}.geometry must be GEOMETRY")
        # A single sp_ref row is this project's database-wide CRS contract.
        refs = con.execute("SELECT DISTINCT wkid FROM main.sp_ref").fetchall()
        if refs != [(4326,)]:
            raise ValueError(f"Database CRS disagrees with Ossie: {refs}")
