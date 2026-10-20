use duckdb::{
    Connection,
    core::{DataChunkHandle, FlatVector, Inserter, LogicalTypeHandle, LogicalTypeId},
    duckdb_entrypoint_c_api,
    types::DuckString,
    vscalar::{ScalarFunctionSignature, VScalar},
    vtab::arrow::WritableVector,
};
use laya::{Answer, LayaEngine, LayaOptions, Question, QuestionSet};
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet, hash_map::Entry},
    error::Error,
    path::PathBuf,
    sync::{Arc, Mutex},
};

type Result<T> = std::result::Result<T, Box<dyn Error>>;
type Shared = Arc<Mutex<Runtime>>;
const MAX_EVALUATIONS: usize = 1000;
const MAX_INPUT_BYTES: usize = 1_048_576;

#[derive(Default)]
struct Runtime {
    engine: Option<LayaEngine>,
    directory: Option<PathBuf>,
    cache: HashMap<String, Value>,
    evaluations: usize,
    cache_hits: usize,
    errors: usize,
}

impl Runtime {
    fn stats(&self) -> Value {
        json!({"backend": "rust-laya-onnx", "version": env!("CARGO_PKG_VERSION"),
               "model_directory": self.directory, "evaluations": self.evaluations,
               "cache_hits": self.cache_hits, "cache_entries": self.cache.len(),
               "errors": self.errors,
               "max_evaluations": MAX_EVALUATIONS,
               "clamped_temperatures": self.engine.as_ref().map(|e| &e.config().clamped_temperatures)})
    }

    fn reset(&mut self) {
        self.cache.clear();
        self.evaluations = 0;
        self.cache_hits = 0;
        self.errors = 0;
    }

    fn load(&mut self, path: &str) -> Result<()> {
        let directory = std::fs::canonicalize(path)?;
        if !directory.is_dir() {
            return Err("Expected a local ONNX model directory".into());
        }
        let engine = LayaEngine::create(LayaOptions {
            model_directory: Some(directory.clone()),
            allow_download: false,
            intra_op_threads: Some(2),
            inter_op_threads: Some(1),
            ..Default::default()
        })?;
        self.engine = Some(engine);
        self.directory = Some(directory);
        self.reset();
        Ok(())
    }

    fn evaluate(
        &mut self,
        state: &str,
        prompt: &str,
        kind: &str,
        labels: &[String],
    ) -> Result<&Value> {
        let options = serde_json::to_string(labels)?;
        if state.len() + prompt.len() + options.len() > MAX_INPUT_BYTES {
            return Err("Jev input exceeds 1 MiB".into());
        }
        let key = serde_json::to_string(&(state, prompt, kind, options))?;
        let entry = match self.cache.entry(key) {
            Entry::Occupied(entry) => {
                self.cache_hits += 1;
                return Ok(entry.into_mut());
            }
            Entry::Vacant(entry) => entry,
        };
        // Only successful, validated requests enter the cache.
        let question = make_question(prompt, kind, labels)?;
        let state: Value = serde_json::from_str(state)?;
        let engine = self
            .engine
            .as_ref()
            .ok_or("Call jev_laya_load('/local/onnx/directory') first")?;
        if self.evaluations >= MAX_EVALUATIONS {
            return Err(
                "Laya evaluation budget exhausted; call jev_laya_reset() explicitly".into(),
            );
        }
        let questions = QuestionSet::new().with("answer", question)?;
        self.evaluations += 1;
        let result = (|| -> Result<Value> {
            // shortcut: one forward pass per distinct row; add multi-state batching after profiling real workloads.
            let result = engine.predict(state, &questions)?;
            if let Some(usage) = result.state_usage() {
                if usage.truncated {
                    return Err("Laya truncated state; refusing an incomplete judgment".into());
                }
                if !usage.options.is_empty() {
                    return Err("Laya options collapsed after tokenization".into());
                }
            }
            let answer = result.get("answer").ok_or("Laya returned no answer")?;
            checked_answer(answer, kind, labels)
        })();
        let answer = match result {
            Ok(answer) => answer,
            Err(error) => {
                self.errors += 1;
                return Err(error);
            }
        };
        Ok(entry.insert(answer))
    }
}

fn checked_answer(answer: &Answer, kind: &str, labels: &[String]) -> Result<Value> {
    let probability = |p: f64| p.is_finite() && (0.0..=1.0).contains(&p);
    let distribution = |values: &[f64]| {
        values.len() == labels.len()
            && !values.is_empty()
            && values.iter().all(|&p| probability(p))
            // Laya rounds each probability to four decimal places.
            && (values.iter().sum::<f64>() - 1.0).abs() <= values.len() as f64 * 0.00005 + 1e-9
    };
    if !probability(answer.confidence())
        || !probability(answer.answer_confidence())
        || !probability(answer.action().act_probability)
    {
        return Err("Laya returned invalid confidence or action probability".into());
    }
    let mut value = match (kind, answer) {
        ("noul", Answer::Noul(a)) if labels.is_empty() && probability(a.probability) => {
            json!({"type":"noul", "noul":a.probability})
        }
        ("choice", Answer::Choice(a))
            if labels.contains(&a.choice)
                && a.probabilities.keys().eq(labels.iter())
                && distribution(&a.probabilities.values().copied().collect::<Vec<_>>()) =>
        {
            let probabilities: serde_json::Map<String, Value> = a
                .probabilities
                .iter()
                .map(|(label, p)| (label.clone(), json!(p)))
                .collect();
            json!({"type":"choice", "choice":a.choice, "probabilities":probabilities})
        }
        ("score", Answer::Score(a))
            if distribution(&a.probabilities)
                && a.legend.len() == labels.len()
                && a.legend
                    .iter()
                    .zip(labels)
                    .all(|(value, label)| value.as_str() == Some(label.as_str()))
                && a.score.is_finite()
                && (0.0..=labels.len().saturating_sub(1) as f64).contains(&a.score) =>
        {
            json!({"type":"score", "score":a.score, "legend":a.legend, "probabilities":a.probabilities})
        }
        _ => return Err("Laya returned an invalid typed answer".into()),
    };
    value["confidence"] = json!(answer.confidence());
    value["answer_confidence"] = json!(answer.answer_confidence());
    value["action"] = json!({"act_probability":answer.action().act_probability});
    Ok(value)
}

fn make_question(prompt: &str, kind: &str, labels: &[String]) -> Result<Question> {
    if prompt.trim().is_empty() {
        return Err("Question must be nonempty".into());
    }
    if kind == "noul" {
        if !labels.is_empty() {
            return Err("Noul does not take options".into());
        }
        return Ok(Question::noul(prompt, Value::Null, Value::Null));
    }
    if labels.is_empty()
        || labels.iter().any(|s| s.trim().is_empty())
        || labels.iter().collect::<HashSet<_>>().len() != labels.len()
    {
        return Err("Options must be distinct, nonempty text labels".into());
    }
    match kind {
        "choice" => Ok(Question::choice(
            prompt,
            labels.iter().map(|label| (label.clone(), label.clone())),
        )?),
        "score" => Ok(Question::score(prompt, labels.to_vec())?),
        _ => Err("Unknown Laya question kind".into()),
    }
}

fn text(input: &DataChunkHandle, column: usize, row: usize) -> Option<String> {
    let vector = input.flat_vector(column);
    if vector.row_is_null(row as u64) {
        return None;
    }
    Some(string_at(&vector, row))
}

fn signature(arity: usize) -> Vec<ScalarFunctionSignature> {
    vec![ScalarFunctionSignature::exact(
        (0..arity).map(|_| LogicalTypeId::Varchar.into()).collect(),
        LogicalTypeId::Varchar.into(),
    )]
}

fn string_at(vector: &FlatVector<'_>, row: usize) -> String {
    // Read only this non-NULL VARCHAR slot; neighboring NULL slots can contain uninitialized data.
    let mut value = unsafe {
        vector
            .as_mut_ptr::<duckdb::ffi::duckdb_string_t>()
            .add(row)
            .read()
    };
    DuckString::new(&mut value).as_str().into_owned()
}

fn row_json(input: &DataChunkHandle, row: usize) -> Result<String> {
    let column = input.flat_vector(0);
    let dtype = column.logical_type();
    match dtype.id() {
        LogicalTypeId::Varchar => {
            let value = string_at(&column, row);
            if dtype
                .get_alias()
                .is_some_and(|a| a.eq_ignore_ascii_case("json"))
            {
                Ok(serde_json::from_str::<Value>(&value)?.to_string())
            } else {
                Ok(Value::String(value).to_string())
            }
        }
        LogicalTypeId::Struct => {
            let fields = input.struct_vector(0);
            let mut value = serde_json::Map::new();
            for index in 0..dtype.num_children() {
                let child = fields.child(index, input.len());
                let item = if child.row_is_null(row as u64) {
                    Value::Null
                } else if child.logical_type().id() == LogicalTypeId::Varchar {
                    Value::String(string_at(&child, row))
                } else {
                    return Err("Select text fields for Laya; use to_json(...) explicitly for other attributes".into());
                };
                value.insert(dtype.child_name(index), item);
            }
            Ok(Value::Object(value).to_string())
        }
        _ => Err("Laya expects a text STRUCT, JSON, or VARCHAR".into()),
    }
}

// 0 = predicate, 1 = probability, 2 = choice, 3 = score, 4 = normalized score,
// 5 = raw JSON answer, 6 = confidence.
struct Decision<const MODE: u8>;
impl<const MODE: u8> VScalar for Decision<MODE> {
    type State = Shared;
    fn signatures() -> Vec<ScalarFunctionSignature> {
        let mut params = vec![LogicalTypeId::Any.into(), LogicalTypeId::Varchar.into()];
        if MODE >= 5 {
            params.push(LogicalTypeId::Varchar.into());
        }
        if MODE >= 2 {
            params.push(LogicalTypeHandle::list(&LogicalTypeId::Varchar.into()));
        }
        let returns = match MODE {
            0 => LogicalTypeId::Boolean,
            2 | 5 => LogicalTypeId::Varchar,
            _ => LogicalTypeId::Double,
        };
        let mut return_type: LogicalTypeHandle = returns.into();
        if MODE == 5 {
            return_type.set_alias("JSON");
        }
        let mut signatures = vec![ScalarFunctionSignature::exact(params, return_type)];
        if MODE == 0 {
            signatures.push(ScalarFunctionSignature::exact(
                vec![
                    LogicalTypeId::Any.into(),
                    LogicalTypeId::Varchar.into(),
                    LogicalTypeId::Double.into(),
                ],
                returns.into(),
            ));
        }
        signatures
    }
    fn volatile() -> bool {
        true
    }
    fn invoke(
        state: &Shared,
        input: &mut DataChunkHandle,
        output: &mut dyn WritableVector,
    ) -> Result<()> {
        let mut runtime = state
            .lock()
            .map_err(|_| "Laya runtime lock poisoned; reopen database")?;
        let mut out = output.flat_vector();
        for row in 0..input.len() {
            if (0..input.num_columns()).any(|c| input.flat_vector(c).row_is_null(row as u64)) {
                out.set_null(row);
                continue;
            }
            let dynamic_kind;
            let kind = match MODE {
                2 => "choice",
                3 | 4 => "score",
                5 | 6 => {
                    dynamic_kind = string_at(&input.flat_vector(2), row);
                    &dynamic_kind
                }
                _ => "noul",
            };
            let threshold = if MODE == 0 && input.num_columns() == 3 {
                let vector = input.flat_vector(2);
                let value = unsafe { vector.as_mut_ptr::<f64>().add(row).read() };
                if !value.is_finite() || !(0.0..=1.0).contains(&value) {
                    return Err("Jev threshold must be finite and between 0 and 1".into());
                }
                value
            } else {
                0.5
            };
            let labels = if MODE >= 2 {
                let column = if MODE >= 5 { 3 } else { 2 };
                let vector = input.list_vector(column);
                let (offset, count) = vector.get_entry(row);
                let child = vector.child(vector.len());
                let mut labels = Vec::with_capacity(count);
                for index in offset..offset + count {
                    if child.row_is_null(index as u64) {
                        return Err("Options must not contain NULL".into());
                    }
                    labels.push(string_at(&child, index));
                }
                labels
            } else {
                Vec::new()
            };
            let answer = runtime.evaluate(
                &row_json(input, row)?,
                &string_at(&input.flat_vector(1), row),
                kind,
                &labels,
            )?;
            match MODE {
                0 => unsafe {
                    out.as_mut_ptr::<bool>()
                        .add(row)
                        .write(answer["noul"].as_f64().ok_or("Invalid probability")? >= threshold)
                },
                2 => out.insert(row, answer["choice"].as_str().ok_or("Invalid choice")?),
                5 => out.insert(row, answer.to_string().as_str()),
                _ => {
                    let key = match MODE {
                        1 => "noul",
                        6 => "confidence",
                        _ => "score",
                    };
                    let mut value = answer[key].as_f64().ok_or("Invalid numeric answer")?;
                    if MODE == 4 {
                        value /= answer["legend"]
                            .as_array()
                            .ok_or("Invalid score legend")?
                            .len()
                            .saturating_sub(1)
                            .max(1) as f64;
                    }
                    if !value.is_finite() {
                        return Err("Nonfinite Laya answer".into());
                    }
                    unsafe { out.as_mut_ptr::<f64>().add(row).write(value) };
                }
            }
        }
        Ok(())
    }
}

struct Control<const MODE: u8>;
impl<const MODE: u8> VScalar for Control<MODE> {
    type State = Shared;
    fn signatures() -> Vec<ScalarFunctionSignature> {
        signature(if MODE == 0 { 1 } else { 0 })
    }
    fn volatile() -> bool {
        true
    }
    fn invoke(
        state: &Shared,
        input: &mut DataChunkHandle,
        output: &mut dyn WritableVector,
    ) -> Result<()> {
        if input.len() != 1 {
            return Err("Call Laya control functions in a separate single-row SELECT".into());
        }
        let mut runtime = state
            .lock()
            .map_err(|_| "Laya runtime lock poisoned; reopen database")?;
        match MODE {
            0 => runtime.load(&text(input, 0, 0).ok_or("Model directory must not be NULL")?)?,
            1 => runtime.reset(),
            _ => (),
        }
        output
            .flat_vector()
            .insert(0, runtime.stats().to_string().as_str());
        Ok(())
    }
}

#[duckdb_entrypoint_c_api(ext_name = "jev_spatial", min_duckdb_version = "v1.5.6")]
pub fn extension_entrypoint(con: Connection) -> Result<()> {
    let conflicts: i64 = con.query_row(
        "SELECT count(*) FROM duckdb_functions() WHERE function_name IN ('jev','jev_prob','jev_choice','jev_score',
         'jev_score_norm','jev_eval','jev_confidence',
         'jev_laya_load','jev_laya_reset','jev_laya_stats')", [], |row| row.get(0))?;
    if conflicts != 0 {
        return Err("Jev functions already exist; use a fresh database connection without another Jev backend".into());
    }
    // LOAD uses an installed extension; do not INSTALL or fetch dependencies during initialization.
    con.execute_batch("LOAD spatial;")?;
    let state = Shared::default();
    con.register_scalar_function_with_state::<Decision<0>>("jev", &state)?;
    con.register_scalar_function_with_state::<Decision<1>>("jev_prob", &state)?;
    con.register_scalar_function_with_state::<Decision<2>>("jev_choice", &state)?;
    con.register_scalar_function_with_state::<Decision<3>>("jev_score", &state)?;
    con.register_scalar_function_with_state::<Decision<4>>("jev_score_norm", &state)?;
    con.register_scalar_function_with_state::<Decision<5>>("jev_eval", &state)?;
    con.register_scalar_function_with_state::<Decision<6>>("jev_confidence", &state)?;
    con.register_scalar_function_with_state::<Control<0>>("jev_laya_load", &state)?;
    con.register_scalar_function_with_state::<Control<1>>("jev_laya_reset", &state)?;
    con.register_scalar_function_with_state::<Control<2>>("jev_laya_stats", &state)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use laya::{ActionInfo, NoulAnswer, ScoreAnswer};

    #[test]
    fn malformed_diagnostics_are_rejected() {
        let good = Answer::Noul(NoulAnswer::new(0.8, 0.8, 0.8, ActionInfo::default()));
        assert_eq!(checked_answer(&good, "noul", &[]).unwrap()["noul"], 0.8);
        assert!(checked_answer(&good, "choice", &["a".into()]).is_err());
        for (p, confidence, answer_confidence) in [
            (f64::NAN, 0.8, 0.8),
            (1.1, 0.8, 0.8),
            (0.8, f64::INFINITY, 0.8),
            (0.8, 0.8, -0.1),
        ] {
            let answer = Answer::Noul(NoulAnswer::new(
                p,
                confidence,
                answer_confidence,
                ActionInfo::default(),
            ));
            assert!(checked_answer(&answer, "noul", &[]).is_err());
        }
    }

    #[test]
    fn score_diagnostics_require_complete_valid_distributions() {
        let labels = vec!["low".to_owned(), "high".to_owned()];
        for probabilities in [
            vec![],
            vec![1.0],
            vec![0.2, 0.3],
            vec![f64::NAN, 0.5],
            vec![-0.1, 1.1],
        ] {
            let answer = Answer::Score(ScoreAnswer::new(
                0.5,
                vec![json!("low"), json!("high")],
                probabilities,
                0.0,
                0.5,
                ActionInfo::default(),
            ));
            assert!(checked_answer(&answer, "score", &labels).is_err());
        }
        let rounded = Answer::Score(ScoreAnswer::new(
            1.0,
            vec![json!("a"), json!("b"), json!("c")],
            vec![0.3333; 3],
            0.0,
            0.3333,
            ActionInfo::default(),
        ));
        assert!(checked_answer(&rounded, "score", &["a".into(), "b".into(), "c".into()]).is_ok());
        assert!(checked_answer(&rounded, "score", &["c".into(), "b".into(), "a".into()]).is_err());
    }

    #[test]
    fn invalid_questions_fail_before_inference() {
        for (prompt, kind, labels) in [
            ("", "noul", vec![]),
            ("Q", "choice", vec![]),
            ("Q", "score", vec!["a".into(), "a".into()]),
            ("Q", "choice", vec![" ".into()]),
            ("Q", "unknown", vec!["a".into()]),
        ] {
            assert!(make_question(prompt, kind, &labels).is_err());
        }
        assert!(make_question("Q", "choice", &["a".into(), "b".into()]).is_ok());
    }
    #[test]
    fn unloaded_runtime_fails_without_network_resolution() {
        let mut runtime = Runtime::default();
        assert!(
            runtime
                .evaluate("{}", "Q", "noul", &[])
                .unwrap_err()
                .to_string()
                .contains("jev_laya_load")
        );
        assert_eq!(runtime.evaluations, 0);
        assert!(runtime.load("/definitely/missing/local/model").is_err());
    }
}
