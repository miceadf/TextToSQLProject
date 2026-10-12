"""Phase 9-10, entirely fake models/readers/executors/transports."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.adaptive.catalog.contracts import DatabaseCatalog
from backend.adaptive.evaluation import blind_eval as blind
from backend.adaptive.evaluation import direct_sql_pg as pg
from backend.adaptive.evaluation import legacy_chat_compare as legacy
from backend.adaptive.evaluation.direct_sql import DirectSqlRejected
from backend.adaptive.fixtures import other_school

SECRET = "sk-test-secret-12345678901234567890"


@pytest.mark.parametrize("reference_database", ["X", None])
def test_queryir_legacy_cli_reference_database_selection(tmp_path, monkeypatch, reference_database):
    from backend.adaptive.api import dev_launcher
    from backend.adaptive.harness import postgres
    loads, connected, reference_calls, requests, closed = [], [], [], [], []
    config = SimpleNamespace(schema="reference-schema")
    def load(root, database_id, env):
        loads.append(database_id)
        return context(), Executor(), config
    monkeypatch.setattr(pg, "load_context", load)
    monkeypatch.setattr(dev_launcher, "read_env_file", lambda *a: {})
    monkeypatch.setattr(blind, "_chat_model", lambda *a: {"id": "general", "tag": "fake"})
    def http(method, url, payload):
        requests.append(payload)
        return {"status": "SUCCEEDED", "rows": [{"course_code": "CS101", "section": "01"}]}
    monkeypatch.setattr(blind, "_http_json", http)
    case = legacy.CompareCase(case_id="q01", scope=legacy.Scope.ONTOLOGY, question="q",
                              reference_sql=legacy.REFERENCE_PREFIX + "TRUE")
    monkeypatch.setattr(legacy, "load_question_set", lambda: SimpleNamespace(cases=[case]))
    cursor = SimpleNamespace(execute=lambda *a: None)
    connection = SimpleNamespace(set_session=lambda **kw: None, cursor=lambda: cursor,
                                 close=lambda: closed.append(True))
    monkeypatch.setattr(postgres, "_production_connector", lambda actual: connected.append(actual) or connection)
    monkeypatch.setattr(legacy, "reference_keys", lambda actual, sql: reference_calls.append((actual, sql)) or {("CS101", "01")})
    argv = ["run", "--arm", "queryir", "--set", "legacy", "--database", "legacy-view",
            "--model", "general", "--base-url", "http://127.0.0.1:19999", "--root", str(tmp_path)]
    if reference_database:
        argv += ["--reference-database", reference_database]
    assert pg.main(argv) == 0
    assert loads == [reference_database or "legacy-view"]
    assert connected == [config] and reference_calls == [(cursor, case.reference_sql)] and closed == [True]
    assert [request["database_id"] for request in requests] == ["legacy-view"]
    document = json.loads(next((tmp_path / ".schema-adaptive" / "direct-sql-pg").glob("run-*.json")).read_text(encoding="utf-8"))
    assert document["results"][0]["result"] == "PASS"


def catalog():
    return DatabaseCatalog.model_validate({"database_id": "school-a", "dialect": "postgres",
        "schema_fingerprint": "a" * 64, "tables": [{"name": "courses", "primary_key": ["code"],
        "columns": [{"name": name, "declared_type": kind, "affinity": "TEXT", "nullable": True}
                    for name, kind in [("name", "text"), ("code", "text"), ("section", "text"),
                                       ("credits", "integer"), ("date", "date"), ("time", "time"), ("broken", "text")]]}]})


class Reader:
    def __init__(self):
        self.calls = []

    def read(self, database_id, table, column, limit, *, sample=False):
        self.calls.append((database_id, table, column, limit, sample))
        if column == "broken":
            raise RuntimeError(SECRET)
        if column == "name":
            return ["소프트웨어학부", "전자전기 공학부"]
        return [str(i) + "x" * 80 for i in reversed(range(14))] if sample else ["actual"]


def context():
    return pg.build_context("school-a", "school", catalog(), Reader())


class Model:
    def __init__(self, output):
        self.output, self.calls = output, []

    def generate_sql(self, request):
        self.calls.append(request)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


class Executor:
    def __init__(self, columns=None, rows=None):
        self.columns = columns if columns is not None else ["course_code", "section", "extra"]
        self.rows = rows if rows is not None else [{"course_code": "CS101", "section": "01", "extra": SECRET}]
        self.calls = []

    def execute(self, database_id, query):
        self.calls.append((database_id, query))
        return self.columns, self.rows


def test_context_prompt_samples_hints_and_no_dictionary():
    reader = Reader()
    ctx = pg.build_context("school-a", "school", catalog(), reader)
    assert ctx.schema_text == context().schema_text
    for line in ctx.schema_text.splitlines():
        if "examples: " in line:
            values = json.loads(line.split("examples: ")[1])
            assert len(values) <= 10 and all(len(value) <= 40 for value in values)
    assert all(any(call[2] == column and call[4] for call in reader.calls)
               for column in ("credits", "date", "time"))
    assert '"school"."courses"' in ctx.schema_text
    assert "broken\" examples" not in ctx.schema_text
    assert '"school"."courses"."name" = \'소프트웨어학부\'' in ctx.render("소프트웨어학부")
    assert "소웨" not in ctx.render("소웨")
    assert '"school"."courses"."name" = \'전자전기 공학부\'' in ctx.render("전자전기")
    question = "{context} {question} {0.__class__}"
    assert question in pg.build_prompt(ctx, question)
    assert pg.TEMPLATE_SHA256 == hashlib.sha256(pg.DIRECT_SQL_PG_TEMPLATE.encode()).hexdigest()
    assert pg.TEMPLATE_SHA256 == "b1cca141488811c4c36067f43865aaa491857b1339ffaf24b6dd0d24bee531ca"
    assert SECRET not in repr(ctx)


@pytest.mark.parametrize("sql", [
    "SELECT code AS course_code FROM courses",
    'SELECT code FROM school.courses',
    'SELECT code FROM SCHOOL.Courses',
    'WITH C AS (SELECT code FROM Courses) SELECT code FROM c',
    'WITH c AS (SELECT code FROM courses) SELECT code FROM C',
    'WITH "C" AS (SELECT code FROM courses) SELECT code FROM "C"',
    'WITH c AS (SELECT code FROM courses) SELECT code FROM c',
    'SELECT code FROM courses UNION SELECT code FROM courses',
    'SELECT code FROM courses INTERSECT SELECT code FROM courses',
    'SELECT code FROM courses EXCEPT SELECT code FROM courses',
])
def test_policy_allows(sql):
    pg.check_sql(sql, context())


@pytest.mark.parametrize("sql", [
    "DELETE FROM courses", "UPDATE courses SET code='x'", "CREATE TABLE x (a int)",
    "SET search_path=public", "COPY courses TO '/tmp/x'", "BEGIN", "SELECT 1; SELECT 2",
    "SELECT * FROM public.courses", "SELECT * FROM pg_catalog.pg_class",
    'SELECT * FROM "School".courses', 'SELECT * FROM school."Courses"',
    'WITH "C" AS (SELECT code FROM courses) SELECT code FROM c',
    'WITH c AS (SELECT code FROM courses) SELECT code FROM "C"',
    "SELECT * FROM information_schema.tables", "SELECT * FROM missing",
    "SELECT code INTO x FROM courses", "WITH c AS (DELETE FROM courses RETURNING code) SELECT * FROM c",
    "SELECT * FROM courses FOR UPDATE",
    *[f"SELECT {name}('x')" for name in ["PG_SLEEP", "lo_export", "dblink_connect", "CURRENT_SETTING",
        "set_config", "query_to_xml", "query_to_xmlschema", "txid_current", "nextval", "setval"]],
    "WITH c AS (SELECT * FROM public.courses) SELECT * FROM c",
    "SELECT * FROM pg_catalog.generate_series(1,2)",
])
def test_policy_rejects(sql):
    with pytest.raises(DirectSqlRejected):
        pg.check_sql(sql, context())


@pytest.mark.parametrize("expression", ["public.leak_code()", "leak_code()", "pg_catalog.lower(code)"])
def test_policy_rejects_unknown_and_schema_qualified_functions(expression):
    with pytest.raises(DirectSqlRejected) as caught:
        pg.check_sql(f"SELECT {expression} AS course_code FROM courses", context())
    assert caught.value.outcome is pg.DirectSqlOutcome.SQL_FORBIDDEN_NODE


@pytest.mark.parametrize("expression", [
    "split_part(code, '-', 1)", "string_to_array(code, '-')",
    "regexp_split_to_array(code, '-')", "array_to_string(ARRAY[code], '-')",
    "unnest(ARRAY[code])", "btrim(code)", "initcap(code)",
    "to_char(date, 'YYYY')", "date_part('year', date)", "count(*)", "lower(code)",
])
def test_policy_allows_builtin_functions(expression):
    pg.check_sql(f"SELECT {expression} AS course_code FROM courses", context())


def test_result_wrapper_keeps_trailing_comment_inside_subquery():
    import sqlglot
    from sqlglot import exp

    sql = "SELECT code AS course_code FROM courses -- c"
    executor = Executor()
    response = pg.answer(context(), "q", Model(sql), executor)
    wrapped = executor.calls[0][1].sql
    assert wrapped == f"SELECT * FROM (\n{sql}\n) AS direct_sql_result LIMIT 200"
    root = sqlglot.parse_one(wrapped, read="postgres")
    assert root.find(exp.Subquery).alias == "direct_sql_result"
    assert root.args["limit"].expression.this == "200"
    assert response["status"] == "SUCCEEDED"


def test_answer_same_three_graders_and_no_extra_values():
    model, executor = Model("SELECT code AS course_code FROM courses WHERE name LIKE '%x%'"), Executor()
    response = pg.answer(context(), "question", model, executor)
    assert len(model.calls) == len(executor.calls) == 1
    assert "%%x%%" in executor.calls[0][1].sql
    assert response["rows"] == [{"course_code": "CS101", "section": "01"}]
    question = blind.Question(id="q1", text="question", expect={"codes": ["CS101"]})
    assert blind.grade(question, response)["result"] == "PASS"
    question_other = other_school.Question("q", "q", lambda r: True, "test")
    expected = other_school.gold(question_other)
    good = {**response, "rows": [{"course_code": code} for code in expected]}
    assert other_school.grade(question_other, good)[0]
    case = legacy.CompareCase(case_id="q01", scope=legacy.Scope.ONTOLOGY, question="q",
                              reference_sql=legacy.REFERENCE_PREFIX + "TRUE")
    payload = {"answered": True, "keys": [["CS101", "01"]]}
    assert legacy.score_case(case, {"queryir": payload}, {("CS101", "01")})["queryir"]["outcome"] == "EXACT"
    wrong = {**response, "rows": [{"course_code": "wrong", "section": "01"}]}
    record = pg.safe_record(wrong, blind.grade(question, wrong))
    assert pg.summary([record])["silent_wrong"] == 1
    assert record["failure_code"] == "INCORRECT_RESULT"
    dumped = json.dumps(record)
    for secret in (SECRET, "SELECT", "question", "CS101", "wrong", "schema_text"):
        assert secret not in dumped
    unknown = pg.answer(context(), "q", Model("UNKNOWN"), executor)
    decline = blind.Question(id="d", text="q", expect="decline")
    assert blind.grade(decline, unknown)["result"] == "PASS"
    assert pg.summary([pg.safe_record(unknown, blind.grade(decline, unknown))])["decline_correct"] == 1
    missing = pg.answer(context(), "q", Model("SELECT 1"), Executor(["x"], [{"x": 1}]))
    assert missing["status"] == "FAILED" and missing["failure_code"] == "RESULT_MISSING_COURSE_CODE"


@pytest.mark.parametrize("output,code", [("", "EMPTY_OUTPUT"), ("SELECT (", "SQL_PARSE_ERROR"),
    ("SELECT pg_sleep(1)", "SQL_FORBIDDEN_NODE"), (RuntimeError(SECRET), "PROVIDER_ERROR")])
def test_failures_single_call_no_execution(output, code):
    executor, model = Executor(), Model(output)
    response = pg.answer(context(), "q", model, executor)
    assert response["failure_code"] == code
    assert len(model.calls) == 1 and not executor.calls
    assert SECRET not in json.dumps(response)


def write_questions(root):
    directory = blind.eval_dir(root, "sample")
    directory.mkdir(parents=True)
    path = directory / "questions.json"
    path.write_text(json.dumps({"format": blind.QUESTIONS_FORMAT, "database_id": "school-a",
        "author": "teammate", "fixed_at": "2026-10-12", "questions": [
        {"id": "q", "text": "private-question", "expect": {"codes": ["CS101"]}}]}), encoding="utf-8")
    return path


def test_blind_numbering_lock_old_arm_and_safe_storage(tmp_path):
    path = write_questions(tmp_path)
    ask = lambda *a: {"status": "SUCCEEDED", "failure_code": "CORRECT", "rows": [{"course_code": "CS101"}],
                      "understood": {"filters": [{"value": SECRET}]}, "elapsed_ms": 12.5}
    old = blind.run(tmp_path, "sample", ask=ask)
    new = blind.run(tmp_path, "sample", ask=ask, arm="direct-sql", sanitize=True,
        run_metadata={"prompt_version": pg.PROMPT_VERSION, "template_sha256": pg.TEMPLATE_SHA256})
    assert old["run"] == 1 and new["run"] == 2
    assert [r["arm"] for r in blind.report(tmp_path, "sample")] == ["queryir", "direct-sql"]
    assert new["metrics"]["correct"] == 1 and new["results"][0]["elapsed_ms"] == 12.5
    text = (path.parent / "run-002.json").read_text()
    assert all(secret not in text for secret in (SECRET, "CS101", "private-question", "understood", "rows"))
    path.write_text(path.read_text().replace("private-question", "changed"))
    with pytest.raises(blind.BlindEvalError, match="QUESTIONS_CHANGED"):
        blind.run(tmp_path, "sample", ask=ask, arm="direct-sql", sanitize=True)


def test_other_school_cli_fake_and_private_output(tmp_path, monkeypatch, capsys):
    executor = Executor()
    monkeypatch.setattr(pg, "load_context", lambda *a: (context(), executor, None))
    model = Model("UNKNOWN")
    monkeypatch.setattr(pg, "make_model", lambda *a: (model, "fake"))
    assert pg.main(["run", "--set", "other_school", "--database", "school-a", "--model", "general", "--root", str(tmp_path)]) == 0
    assert len(model.calls) == 12
    document = json.loads(next((tmp_path / ".schema-adaptive" / "direct-sql-pg").glob("run-*.json")).read_text())
    assert document["metrics"]["total"] == 12 and document["model"] == {"id": "general", "tag": "fake"}
    assert document["template_sha256"] == pg.TEMPLATE_SHA256
    assert "UNKNOWN" not in json.dumps(document)
    assert pg.main(["run", "--set", "other_school", "--database", "school-a", "--model", "general", "--root", str(tmp_path), "--out", str(tmp_path / "public")]) == 2
    assert len(model.calls) == 12
    assert SECRET not in capsys.readouterr().out


@pytest.mark.parametrize("model_id", ["general", "legacy-tuned"])
def test_queryir_cli_fake_http_latency(tmp_path, monkeypatch, model_id):
    calls = []
    monkeypatch.setattr(blind, "_chat_model", lambda *a: {"id": model_id, "tag": "fake"})
    def http(method, url, payload):
        calls.append((method, url, payload))
        return {"status": "UNSUPPORTED", "understood": {"filters": [{"value": SECRET}]}}
    monkeypatch.setattr(blind, "_http_json", http)
    assert pg.main(["run", "--set", "other_school", "--database", "school-a", "--model", model_id,
                   "--arm", "queryir", "--base-url", "http://127.0.0.1:19999", "--root", str(tmp_path)]) == 0
    assert len(calls) == 12 and all(c[2]["model"] == model_id for c in calls)
    document = json.loads(next((tmp_path / ".schema-adaptive" / "direct-sql-pg").glob("run-*.json")).read_text())
    assert document["metrics"]["latency_median_ms"] >= 0
    assert SECRET not in json.dumps(document)


def test_runtime_isolation():
    root = Path(__file__).resolve().parents[2] / "backend" / "adaptive"
    for folder in ("api", "harness", "compiler", "policy", "registration", "mapping", "catalog", "clarification"):
        for path in (root / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    assert "direct_sql_pg" not in (node.module or "")
                    assert all("direct_sql_pg" not in name.name for name in node.names)
                elif isinstance(node, ast.Import):
                    assert all("direct_sql_pg" not in name.name for name in node.names)


def test_registered_loader_uses_only_catalog_and_source(monkeypatch, tmp_path):
    from backend.adaptive.registration import postgres_registrations as registration
    from backend.adaptive.harness import postgres_values
    config = SimpleNamespace(schema="school")
    loaded = SimpleNamespace(catalog=catalog(), manifest=SimpleNamespace(
        source_id="default", source_identity="digest", postgres_schema="school"))
    source = SimpleNamespace(identity=lambda: "digest", config=lambda schema: config)
    monkeypatch.setattr(registration, "load_postgres_registration", lambda **kw: loaded)
    monkeypatch.setattr(registration, "sources_from_env", lambda env: {"default": source})
    monkeypatch.setattr(postgres_values.PostgresDistinctValueReader, "__init__", lambda *a, **k: None)
    calls = []
    def read(self, *args, **kwargs):
        calls.append(kwargs)
        return []
    monkeypatch.setattr(postgres_values.PostgresDistinctValueReader, "read", read)
    from backend.adaptive.harness import postgres
    monkeypatch.setattr(postgres, "PostgresReadOnlyExecutor", lambda configs: Executor())
    ctx, _executor, actual_config = pg.load_context(tmp_path, "school-a", {})
    assert ctx.database_id == "school-a" and actual_config is config
    assert calls and all(call["ordered"] for call in calls)
    # No mapping, dictionary or transform attributes exist on the fake load.
    source.identity = lambda: "different"
    with pytest.raises(ValueError, match="SOURCE_MISMATCH"):
        pg.load_context(tmp_path, "school-a", {})


def test_ordered_sample_reader_keeps_default_query_and_bounded_cleanup():
    from backend.adaptive.catalog.postgres import PostgresConnectionConfig
    from backend.adaptive.harness.postgres_values import PostgresDistinctValueReader
    calls, cleanup = [], []
    cursor = SimpleNamespace(execute=lambda sql, params: calls.append((sql, params)),
                             fetchall=lambda: [(str(i),) for i in range(11)])
    connection = SimpleNamespace(cursor=lambda: cursor, rollback=lambda: cleanup.append("rollback"),
                                 close=lambda: cleanup.append("close"))
    config = PostgresConnectionConfig("dbname=fake", schema="school")
    reader = PostgresDistinctValueReader({"school-a": config}, connector=lambda config: connection)
    assert len(reader.read("school-a", "courses", "code", 10, sample=True, ordered=True)) == 10
    sql, params = calls[-1]
    assert 'CAST("code" AS text) COLLATE "C"' in sql and 'ORDER BY 1 LIMIT %s' in sql
    assert params == (11,) and cleanup == ["rollback", "close"]
    reader.read("school-a", "courses", "code", 10, sample=True)
    assert 'ORDER BY' not in calls[-1][0] and 'COLLATE' not in calls[-1][0]


def test_cap_summary_latency_and_parse_log_redaction(caplog):
    record = {"id": "a", "result": "CAPPED", "status": "SUCCEEDED", "row_count": 200, "elapsed_ms": 10}
    records = [record, {"id": "b", "result": "FAIL", "status": "FAILED", "failure_code": "SQL_PARSE_ERROR", "elapsed_ms": 30}]
    totals = pg.summary(records)
    assert totals["capped"] == 1 and totals["generation_failure"] == 1
    assert totals["latency_median_ms"] == 20 and totals["latency_p90_ms"] == 30
    assert pg.summary([{"status": "NOT_UNDERSTOOD", "result": "FAIL"}])["generation_failure"] == 1
    with pytest.raises(DirectSqlRejected):
        pg.check_sql("SHOW " + SECRET, context())
    assert SECRET not in caplog.text


def test_legacy_cli_uses_reference_scoring_readonly_and_no_raw_storage(tmp_path, monkeypatch):
    from backend.adaptive.harness import postgres
    config = SimpleNamespace(schema="school")
    monkeypatch.setattr(pg, "load_context", lambda *a: (context(), Executor(["course_code", "section"], []), config))
    model = Model("SELECT code AS course_code, section FROM courses")
    monkeypatch.setattr(pg, "make_model", lambda *a: (model, "fake"))
    calls, closed = [], []
    cursor = SimpleNamespace(execute=lambda *a: calls.append(a), fetchall=lambda: [])
    connection = SimpleNamespace(set_session=lambda **kw: calls.append(kw), cursor=lambda: cursor,
                                 close=lambda: closed.append(True))
    monkeypatch.setattr(postgres, "_production_connector", lambda config: connection)
    assert pg.main(["run", "--set", "legacy", "--database", "school-a", "--model", "general", "--root", str(tmp_path)]) == 0
    assert len(model.calls) == 30 and closed == [True]
    assert calls[0] == {"readonly": True}
    assert calls[1][1] == ("school",)
    document = json.loads(next((tmp_path / ".schema-adaptive" / "direct-sql-pg").glob("run-*.json")).read_text())
    assert document["metrics"]["total"] == 30
    assert all("elapsed_ms" in item for item in document["results"])
    assert all(secret not in json.dumps(document) for secret in (SECRET, "SELECT", "v_course_info", "rows", "prompt\""))


def test_blind_cli_rejects_wrong_database_before_calls(tmp_path, monkeypatch):
    write_questions(tmp_path)
    monkeypatch.setattr(pg, "load_context", lambda *a: pytest.fail("must not load DB"))
    assert pg.main(["run", "--set", "blind:sample", "--database", "wrong", "--model", "general", "--root", str(tmp_path)]) == 2
    assert not list(blind.eval_dir(tmp_path, "sample").glob("run-*.json"))


def test_private_root_cannot_resolve_outside_repository(tmp_path, monkeypatch):
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == tmp_path / ".schema-adaptive":
            return tmp_path.parent / "outside"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="OUTPUT_NOT_PRIVATE"):
        pg.private_directory(tmp_path, None)


@pytest.mark.parametrize("count,legacy_outcome,blind_outcome", [
    (200, "CORRECT_SUBSET", "CAPPED"), (199, "INCOMPLETE", "FAIL"),
])
def test_result_cap_wrapper_and_existing_graders(count, legacy_outcome, blind_outcome, monkeypatch):
    sql = "SELECT code AS course_code, section FROM courses"
    rows = [{"course_code": f"CS{i % 100}", "section": str(i)} for i in range(count)]
    executor = Executor(["course_code", "section"], rows)
    checked = []
    original = pg.check_sql
    def check(statement, ctx):
        checked.append(statement)
        original(statement, ctx)
    monkeypatch.setattr(pg, "check_sql", check)
    response = pg.answer(context(), "q", Model(sql), executor)
    assert checked == [sql]
    assert executor.calls[0][1].sql == f"SELECT * FROM (\n{sql}\n) AS direct_sql_result LIMIT 200"
    assert response["status"] == "SUCCEEDED"
    assert response["row_count"] == count and response["limit"] == 200
    assert response["sql_sha256"] == hashlib.sha256(sql.encode()).hexdigest()
    question = blind.Question(id="q", text="q", expect={"codes": [f"CS{i}" for i in range(101)]})
    assert blind.grade(question, response)["result"] == blind_outcome
    payload = legacy.ask_queryir(lambda *a: (200, response), "q")
    case = legacy.CompareCase(case_id="q01", scope=legacy.Scope.ONTOLOGY, question="q",
                              reference_sql=legacy.REFERENCE_PREFIX + "TRUE")
    score = legacy.score_case(case, {"queryir": payload}, {(f"CS{i % 100}", str(i)) for i in range(201)})["queryir"]
    assert score["outcome"] == legacy_outcome
    if count == 200:
        assert score["outcome"] in legacy.ACCEPTABLE
    exact = question.model_copy(update={"expect": question.expect.model_copy(update={"codes": tuple(f"CS{i}" for i in range(100))})})
    if count == 199:
        assert blind.grade(exact, response)["result"] == "PASS"


@pytest.mark.parametrize("arm,model", [("direct-sql", "openai"), ("queryir", "openai"),
                                        ("direct-sql", "legacy-tuned")])
def test_cli_rejects_excluded_models_before_io(arm, model, monkeypatch):
    monkeypatch.setattr(pg, "private_directory", lambda *a: pytest.fail("must reject before I/O"))
    with pytest.raises(SystemExit) as caught:
        pg.main(["run", "--set", "other_school", "--database", "school-a",
                 "--arm", arm, "--model", model])
    assert caught.value.code == 2


def test_make_model_rejects_openai():
    with pytest.raises(ValueError, match="INVALID_MODEL"):
        pg.make_model("openai", {})


def test_legacy_cli_grades_queryir_not_understood_like_direct_sql_unknown(tmp_path, monkeypatch):
    from backend.adaptive.harness import postgres

    case = legacy.CompareCase(case_id="q01", scope=legacy.Scope.UNSUPPORTED_FIELD, question="q")
    monkeypatch.setattr(legacy, "load_question_set", lambda: SimpleNamespace(cases=[case]))
    monkeypatch.setattr(pg, "load_context", lambda *a: (context(), Executor(), SimpleNamespace(schema="school")))
    monkeypatch.setattr(pg, "make_model", lambda *a: (Model("UNKNOWN"), "fake"))
    monkeypatch.setattr(blind, "_chat_model", lambda *a: {"id": "general", "tag": "fake"})
    monkeypatch.setattr(blind, "_http_json", lambda *a: {"status": "NOT_UNDERSTOOD"})
    cursor = SimpleNamespace(execute=lambda *a: None)
    connection = SimpleNamespace(set_session=lambda **kw: None, cursor=lambda: cursor, close=lambda: None)
    monkeypatch.setattr(postgres, "_production_connector", lambda config: connection)
    results = []
    for arm in ("direct-sql", "queryir"):
        assert pg.main(["run", "--set", "legacy", "--database", "school-a", "--model", "general",
                        "--arm", arm, "--base-url", "http://127.0.0.1:19999", "--root", str(tmp_path)]) == 0
        path = sorted((tmp_path / ".schema-adaptive" / "direct-sql-pg").glob("run-*.json"))[-1]
        results.append(json.loads(path.read_text())["results"][0]["result"])
    assert results == ["PASS", "PASS"]
