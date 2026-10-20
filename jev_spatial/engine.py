"""Connection and execution boundary: source database is always read-only."""
import os
import json
from pathlib import Path

import duckdb

from .jev import register_jev


def connect(database, extension=None):
    if not Path(database).is_file():
        raise FileNotFoundError(database)
    if extension is not None:
        extension = Path(extension).resolve(strict=True)
    con = duckdb.connect(str(database), read_only=True,
                         config={"allow_unsigned_extensions": "true"} if extension else {})
    try:
        con.execute("LOAD spatial")
        if extension:
            con.execute("LOAD '" + str(extension).replace("'", "''") + "'")
    except Exception:
        con.close()
        raise
    return con


class NativeRuntime:
    """Adapter to the database-scoped Rust runtime; no Python inference is imported."""
    def __init__(self, con, model):
        self.con = con
        path = Path(model).resolve(strict=True)
        con.execute('SELECT jev_laya_load(?)', [str(path)]).fetchone()

    def reset(self):
        self.con.execute('SELECT jev_laya_reset()').fetchone()

    @property
    def evaluations(self):
        return json.loads(self.con.execute('SELECT jev_laya_stats()').fetchone()[0])['evaluations']


def load_agent(model, device="cpu"):
    model = Path(model).resolve()
    if not (model / "rl_agent_config.json").is_file() or not (model / "model.safetensors").is_file():
        raise FileNotFoundError(f"Expected a downloaded local Laya checkpoint at {model}")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ.setdefault("USE_TF", "0")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import laya
    return laya.load(str(model), device=device)


def execute(con, compiled, runtime=None):
    if not compiled.semantic:
        return con.execute(compiled.sql, compiled.parameters)
    if runtime is None:
        raise ValueError("Semantic query requires a registered Laya runtime")
    runtime.reset()
    # A separate statement prevents optimizer rewrites from moving inference ahead of exact filtering.
    con.execute('CREATE OR REPLACE TEMP TABLE "_jev_candidates" AS ' + compiled.sql, compiled.parameters)
    return con.execute(f'SELECT c.* FROM "_jev_candidates" c WHERE {compiled.semantic_where}',
                       [compiled.semantic])


__all__ = ["connect", "execute", "load_agent", "register_jev", "NativeRuntime"]
