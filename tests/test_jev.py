import duckdb
import pytest

from jev_spatial import register_jev


def test_four_functions_and_caching(con, agent):
    runtime = register_jev(con, agent)
    rows = con.execute("SELECT jev({'status':status}, 'operating?'), jev_prob({'status':status}, 'operating?'), jev_choice({'status':status}, 'state?', ['active','inactive']), jev_score({'status':status}, 'urgency?', ['low','high']) FROM (VALUES ('operating'),('retired'),('operating')) t(status)").fetchall()
    assert rows == [(True, 0.9, 'active', 0.7), (False, 0.1, 'active', 0.7), (True, 0.9, 'active', 0.7)]
    assert runtime.evaluations == 6
    runtime.reset()
    assert runtime.evaluations == 0 and not runtime.cache


def test_nulls(con, agent):
    runtime = register_jev(con, agent)
    assert con.execute("SELECT jev(NULL,'x'), jev({'status':'operating'},NULL), jev_choice('x','y',NULL)").fetchone() == (None, None, None)
    assert runtime.evaluations == 0


def test_batching_and_dedup(con, agent):
    runtime = register_jev(con, agent, batch_size=4)
    rows = con.execute("SELECT jev({'status':'operating', 'id': i % 9}, 'operating?') FROM range(2049) t(i)").fetchall()
    assert len(rows) == 2049 and all(r[0] for r in rows)
    assert runtime.evaluations == 9 and agent.calls == 3


def test_budget_prevents_inference(con, agent):
    runtime = register_jev(con, agent, max_evaluations=1)
    with pytest.raises(duckdb.InvalidInputException, match='budget exceeded'):
        con.execute("SELECT jev({'status':status}, 'operating?') FROM (VALUES ('operating'),('retired')) t(status)").fetchall()
    assert runtime.evaluations == 0 and agent.calls == 0


@pytest.mark.parametrize('options', ["[]", "['one']", "['one','one']", "['one',NULL]", "['one','']"])
def test_invalid_options(con, agent, options):
    register_jev(con, agent)
    with pytest.raises(duckdb.InvalidInputException, match='option labels'):
        con.execute(f"SELECT jev_choice('text','pick',{options})").fetchall()


def test_truncation_is_error(con, agent):
    agent.truncated = True
    register_jev(con, agent)
    with pytest.raises(duckdb.InvalidInputException, match='truncated evidence'):
        con.execute("SELECT jev({'status':'operating'},'operating?')").fetchall()


def test_threshold_uses_noul_not_symmetric_confidence(con, agent):
    register_jev(con, agent, threshold=0.95)
    assert con.execute("SELECT jev({'status':'operating'},'operating?')").fetchone() == (False,)
