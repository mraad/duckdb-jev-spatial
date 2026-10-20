"""Jev-shaped SQL functions backed by Laya's local typed decisions."""
import json
import math
from collections import defaultdict
from threading import RLock

import duckdb
import pyarrow as pa


class JevRuntime:
    def __init__(self, agent, *, threshold=0.5, batch_size=16, max_evaluations=1000):
        if not 0 < threshold <= 1 or batch_size < 1 or max_evaluations < 1:
            raise ValueError("Invalid threshold, batch size, or evaluation budget")
        self.agent = agent
        self.threshold = threshold
        self.batch_size = batch_size
        self.max_evaluations = max_evaluations
        self.lock = RLock()
        self.reset()

    def reset(self):
        """Start a new evaluation scope; call between statements, never during execution."""
        with self.lock:
            self.cache = {}
            self.evaluations = 0
            self.batches = 0

    def evaluate(self, kind, states, questions, options=None):
        with self.lock:
            return self._evaluate(kind, states, questions, options)

    def _evaluate(self, kind, states, questions, options):
        keys = []
        groups = defaultdict(dict)
        for i, (state, question) in enumerate(zip(states.to_pylist(), questions.to_pylist())):
            labels = options[i].as_py() if options is not None else None
            if state is None or question is None or (options is not None and labels is None):
                keys.append(None)
                continue
            if not question.strip():
                raise ValueError("Jev question must not be blank")
            if labels is not None:
                if not 2 <= len(labels) <= 255 or any(not isinstance(x, str) or not x.strip() for x in labels) or len(set(labels)) != len(labels):
                    raise ValueError("Supply 2–255 distinct, nonempty option labels")
                labels = tuple(labels)
            parsed = json.loads(state)
            state = json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            group = (kind, question, labels)
            key = (*group, state)
            keys.append(key)
            if key not in self.cache:
                groups[group][key] = parsed
        missing = sum(map(len, groups.values()))
        if self.evaluations + missing > self.max_evaluations:
            raise RuntimeError("Jev evaluation budget exceeded; materialize/filter candidates or raise max_evaluations")
        for (primitive, question, labels), items in groups.items():
            definition = {"type": primitive, "instructions": question}
            if labels:
                definition["criteria"] = dict(zip(labels, labels)) if primitive == "choice" else list(labels)
            entries = list(items.items())
            for start in range(0, len(entries), self.batch_size):
                batch = entries[start:start + self.batch_size]
                self.evaluations += len(batch)
                self.batches += 1
                results = self.agent.predict_batch([v for _, v in batch], {"answer": definition}, batch_size=self.batch_size)
                if len(results) != len(batch):
                    raise ValueError("Laya returned the wrong number of results")
                for (key, _), result in zip(batch, results):
                    if result.get("usage", {}).get("truncated") or result.get("usage", {}).get("options"):
                        raise ValueError("Laya truncated evidence or collapsed options; shorten the input")
                    answer = result["answers"]["answer"]
                    value = answer[primitive]
                    if answer.get("abstained"):
                        value = None
                    elif primitive == "choice":
                        if value not in labels:
                            raise ValueError("Laya returned a choice outside the supplied options")
                    elif not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= (1 if primitive == "noul" else len(labels) - 1):
                        raise ValueError("Laya returned an invalid probability/score")
                    self.cache[key] = value
        return pa.array([None if k is None else self.cache[k] for k in keys],
                        type=pa.string() if kind == "choice" else pa.float64())


def register_jev(con, agent, **kwargs):
    """Register connection-local functions; use STRUCT/text rows, excluding geometry."""
    runtime = JevRuntime(agent, **kwargs)
    varchar = duckdb.sqltypes.VARCHAR
    con.create_function("_laya_noul", lambda s, q: runtime.evaluate("noul", s, q),
                        [varchar, varchar], duckdb.sqltypes.DOUBLE, type="arrow",
                        null_handling="special", side_effects=True)
    con.create_function("_laya_choice", lambda s, q, o: runtime.evaluate("choice", s, q, o),
                        [varchar, varchar, duckdb.list_type(varchar)], varchar, type="arrow",
                        null_handling="special", side_effects=True)
    con.create_function("_laya_score", lambda s, q, o: runtime.evaluate("score", s, q, o),
                        [varchar, varchar, duckdb.list_type(varchar)], duckdb.sqltypes.DOUBLE, type="arrow",
                        null_handling="special", side_effects=True)
    con.execute("CREATE TEMP MACRO jev_prob(row_value, question) AS _laya_noul(to_json(row_value), question)")
    con.execute(f"CREATE TEMP MACRO jev(row_value, question) AS (jev_prob(row_value, question) >= {runtime.threshold})")
    con.execute("CREATE TEMP MACRO jev_choice(row_value, question, options) AS _laya_choice(to_json(row_value), question, options)")
    con.execute("CREATE TEMP MACRO jev_score(row_value, question, rubric) AS _laya_score(to_json(row_value), question, rubric)")
    return runtime
