"""A small, fail-closed English grammar and parameterized SQL compiler."""
from dataclasses import dataclass, field
import math
import re

from .catalog import extensions, identifier


@dataclass(frozen=True)
class Filter:
    field: str
    op: str
    value: str | float


@dataclass(frozen=True)
class Relation:
    target: str
    predicate: str = "within_distance"
    filters: tuple[Filter, ...] = ()
    distance_m: float | None = None
    negate: bool = False


@dataclass(frozen=True)
class Query:
    entity: str = "Wells"
    filters: tuple[Filter, ...] = ()
    relation: Relation | None = None
    semantic: str | None = None
    semantic_fields: tuple[str, ...] = ()


@dataclass
class Compiled:
    sql: str
    where: str
    parameters: list = field(default_factory=list)
    semantic: str | None = None
    semantic_fields: tuple[str, ...] = ()

    @property
    def semantic_where(self):
        if not self.semantic:
            return None
        fields = ", ".join(f"{identifier(f)} := c.{identifier(f)}" for f in self.semantic_fields)
        return f"jev(struct_pack({fields}), ?)"


NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
UNIT = r"(?:kilometers?|kilometres?|km|meters?|metres?|m)"
OPERATORS = {">": ">", ">=": ">=", "<": "<", "<=": "<=", "=": "=",
             "greater than": ">", "deeper than": ">", "shallower than": "<",
             "at least": ">=", "at most": "<=", "no deeper than": "<="}


def meters(number, unit):
    value = float(number) * (1000 if unit.lower().startswith("k") else 1)
    if not math.isfinite(value) or value < 0:
        raise ValueError("Distance/depth must be a finite nonnegative number")
    return value


def parse_question(question, catalog):
    """Recognize Wells depth + proximity requests; reject all unconsumed text."""
    text = re.sub(r"\s+", " ", question.strip().rstrip(".?")).lower()
    text = re.sub(r"^(?:show|find|list|get|select) (?:me )?(?:all )?", "", text)
    if not text.startswith("wells "):
        raise ValueError("Expected 'Show all wells with depth > 350 meters and within 5 km of an in-service pipeline'")
    text = text[6:]
    filters = []
    op_pattern = "|".join(re.escape(o) for o in sorted(OPERATORS, key=len, reverse=True))
    depth = re.match(rf"(?:with |where )?(?:well water depth|water depth|depth) ({op_pattern}) ({NUMBER}) ({UNIT})(?= |$)", text)
    if depth:
        filters.append(Filter("water_depth", OPERATORS[depth[1]], meters(depth[2], depth[3])))
        text = text[depth.end():]
        if text:
            if not text.startswith(" and "):
                raise ValueError("Expected AND after depth condition")
            text = text[5:]
    relation = None
    if text:
        match = re.fullmatch(rf"(?:that are |are )?(not )?within ({NUMBER}) ({UNIT}) of (?:an? )?(in[- ]service )?pipelines?", text)
        if not match:
            raise ValueError(f"Unsupported or ambiguous clause: {text!r}; no partial query was generated")
        target_filters = (Filter("current_phase", "=", "IN SERVICE"),) if match[4] else ()
        relation = Relation("Pipelines", filters=target_filters,
                            distance_m=meters(match[2], match[3]), negate=bool(match[1]))
    if not filters and not relation:
        raise ValueError("A depth or spatial condition is required")
    query = Query(filters=tuple(filters), relation=relation)
    compile_query(query, catalog)
    return query


def compile_query(query, catalog):
    params = []

    def table(name):
        if name not in catalog.tables:
            raise ValueError(f"Unknown table: {name}")
        return 'main.' + identifier(name)

    def predicates(entity, alias, filters):
        parts = []
        for f in filters:
            meta = catalog.field(entity, f.field)
            if f.op not in {"=", "!=", ">", ">=", "<", "<=", "ILIKE"}:
                raise ValueError(f"Unsupported operator: {f.op}")
            if meta["datatype"] in {"Integer", "SmallInteger", "Float", "Double"}:
                if isinstance(f.value, bool) or not isinstance(f.value, (int, float)) or not math.isfinite(f.value):
                    raise ValueError(f"Expected finite number for {f.field}")
                if f.op == "ILIKE":
                    raise ValueError("ILIKE requires a string field")
                value = "CAST(? AS DOUBLE PRECISION)"
            else:
                if meta["datatype"] != "String" or not isinstance(f.value, str):
                    raise ValueError(f"Unsupported filter type for {f.field}")
                value = "?"
            parts.append(f"{alias}.{identifier(f.field)} {f.op} {value}")
            params.append(f.value)
        return parts

    source = table(query.entity)
    clauses = predicates(query.entity, "w", query.filters)
    if query.relation:
        r = query.relation
        target = table(r.target)
        catalog.field(query.entity, "geometry")
        catalog.field(r.target, "geometry")
        if r.predicate not in catalog.relations:
            raise ValueError(f"Relation absent from ontology: {r.predicate}")
        target_clauses = predicates(r.target, "p", r.filters)
        a, b = 'w."geometry"', 'p."geometry"'
        if r.predicate == "within_distance":
            if isinstance(r.distance_m, bool) or not isinstance(r.distance_m, (int, float)) or not math.isfinite(r.distance_m) or r.distance_m < 0:
                raise ValueError("within_distance requires finite nonnegative meters")
            project = lambda g: f"ST_Transform({g}, 'EPSG:4326', 'EPSG:3035', always_xy := TRUE)"
            spatial = f"ST_DWithin({project(a)}, {project(b)}, CAST(? AS DOUBLE PRECISION))"
            params.append(r.distance_m)
        else:
            functions = {"within": "ST_Within", "intersects": "ST_Intersects", "contains": "ST_Contains",
                         "touches": "ST_Touches", "disjoint": "ST_Disjoint"}
            if r.predicate not in functions or r.distance_m is not None:
                raise ValueError("Unsupported relation or distance on a topological predicate")
            spatial = f"{functions[r.predicate]}({a}, {b})"
        # Empty/missing geometries are unknown, never positive or negative spatial evidence.
        target_clauses += [f"{b} IS NOT NULL", f"NOT ST_IsEmpty({b})", spatial]
        clauses += [f"{a} IS NOT NULL", f"NOT ST_IsEmpty({a})",
                    f"{'NOT ' if r.negate else ''}EXISTS (SELECT 1 FROM {target} p WHERE " + " AND ".join(target_clauses) + ")"]
    if query.semantic:
        if not query.semantic.strip() or not query.semantic_fields:
            raise ValueError("Semantic predicates require explicit text fields")
        for name in query.semantic_fields:
            meta = catalog.field(query.entity, name)
            if meta["datatype"] != "String" or name == "geometry" or "wkid" in extensions(meta):
                raise ValueError("Only text attributes may be sent to the semantic predicate")
    elif query.semantic_fields:
        raise ValueError("semantic_fields requires a semantic question")
    where = " AND ".join(clauses) or "TRUE"
    return Compiled(f"SELECT w.* FROM {source} w WHERE {where}", where, params,
                    query.semantic, query.semantic_fields)
