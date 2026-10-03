//! Compatibility implementation for the legacy desktop online.ask workflow.
//!
//! Manual desktop lookup uses this adapter over NativeWeb. DSH web tools remain
//! a separate single-query API.

use crate::native_web::NativeWeb;
use regex::Regex;
use serde_json::{Map, Value, json};
use std::sync::atomic::AtomicBool;
use std::time::Duration;

pub const LEGACY_TIMEOUT: Duration = Duration::from_secs(8);
const PAGE_TEXT_LIMIT: usize = 4_000;

#[derive(Clone, Debug, PartialEq)]
pub struct LookupResult {
    pub result_id: String,
    pub title: String,
    pub snippet: String,
    pub url: String,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct SearchReply {
    pub results: Vec<LookupResult>,
    pub diagnostics: Value,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct PageReply {
    pub text: String,
    pub diagnostics: Value,
}

/// Network boundary injected by tests. Production uses NativeWeb; tests replay
/// fixtures without contacting a server.
pub trait LookupBackend {
    fn search(&mut self, query: &str, timeout: Duration) -> Result<SearchReply, String>;
    fn open_result(&mut self, result_id: &str, timeout: Duration) -> Result<PageReply, String>;
}

pub struct NativeLookupBackend<'a> {
    web: &'a NativeWeb,
    cancelled: &'a AtomicBool,
}

impl<'a> NativeLookupBackend<'a> {
    pub fn new(web: &'a NativeWeb, cancelled: &'a AtomicBool) -> Self {
        Self { web, cancelled }
    }
}

impl LookupBackend for NativeLookupBackend<'_> {
    fn search(&mut self, query: &str, timeout: Duration) -> Result<SearchReply, String> {
        // The old online.ask calls online.search, which already applies an
        // ASCII relevance filter before ask's Chinese relevance/fallback step.
        let value = self.web.search(query, timeout, self.cancelled)?;
        let mut results = Vec::new();
        for row in value["results"].as_array().into_iter().flatten() {
            let (Some(result_id), Some(url)) = (row["result_id"].as_str(), row["url"].as_str())
            else {
                continue;
            };
            results.push(LookupResult {
                result_id: result_id.to_owned(),
                title: row["title"].as_str().unwrap_or_default().to_owned(),
                snippet: row["snippet"].as_str().unwrap_or_default().to_owned(),
                url: url.to_owned(),
            });
        }
        Ok(SearchReply {
            results,
            diagnostics: value.get("diagnostics").cloned().unwrap_or(Value::Null),
        })
    }

    fn open_result(&mut self, result_id: &str, timeout: Duration) -> Result<PageReply, String> {
        let value = self.web.open_result(result_id, timeout, self.cancelled)?;
        Ok(PageReply {
            text: value["text"].as_str().unwrap_or_default().to_owned(),
            diagnostics: value.get("diagnostics").cloned().unwrap_or(Value::Null),
        })
    }
}

/// Match the legacy online.ask defaults and established response fields.
/// page_read_errors is an additive native diagnostic emitted only on read errors.
pub fn ask<B: LookupBackend>(backend: &mut B, phrase: &str) -> Result<Value, String> {
    ask_with_options(backend, phrase, LEGACY_TIMEOUT, true)
}

pub fn ask_with_options<B: LookupBackend>(
    backend: &mut B,
    phrase: &str,
    timeout: Duration,
    deep: bool,
) -> Result<Value, String> {
    let query = phrase.trim().to_owned();
    let (terms, codes) = key_terms(&query);
    let mut tried = Vec::new();
    let mut selected: Option<Vec<LookupResult>> = None;
    let mut notes = Vec::new();

    for suffix in [" 标准 规格 尺寸表 mm", " 规格参数 mm", " 尺寸", ""] {
        let expanded = format!("{query}{suffix}");
        let dedupe_key = expanded.trim().to_owned();
        if tried.iter().any(|item| item == &dedupe_key) {
            continue;
        }
        tried.push(dedupe_key);
        let raw = backend.search(&expanded, timeout)?.results;
        if raw.is_empty() {
            continue;
        }
        let keep = raw
            .iter()
            .filter(|row| is_relevant(row, &terms, &codes))
            .cloned()
            .collect::<Vec<_>>();
        if keep.len() >= 2 {
            notes.push(format!(
                "查询「{expanded}」命中 {} 条相关结果 (共 {} 条)",
                keep.len(),
                raw.len()
            ));
            selected = Some(keep);
            break;
        }
        if selected.is_none() {
            notes.push(format!(
                "查询「{expanded}」命中 {} 条 (相关性过滤后不足, 保留原始结果)",
                keep.len()
            ));
            // Legacy ask retains the first non-empty raw response as fallback,
            // then continues searching for a query with two relevant rows.
            selected = Some(raw);
        }
    }

    let Some(results) = selected else {
        return Ok(json!({
            "ok": false,
            "results": [],
            "suggestion": null,
            "reason": "搜索没有返回结果 (网络不可用或被拦截)",
            "query": query,
            "notes": notes
        }));
    };

    let snippets = results
        .iter()
        .filter(|row| !row.snippet.is_empty())
        .map(|row| row.snippet.clone())
        .collect::<Vec<_>>();
    let titles = results
        .iter()
        .map(|row| row.title.clone())
        .collect::<Vec<_>>();
    let mut all_snippets = snippets.clone();
    let mut page_read_errors = Vec::new();

    if deep {
        for row in results.iter().take(2) {
            match backend.open_result(&row.result_id, timeout) {
                Ok(page) if !page.text.is_empty() => {
                    all_snippets.push(truncate_chars(&page.text, PAGE_TEXT_LIMIT));
                    notes.push(format!(
                        "已读取详情页: {}",
                        truncate_chars(
                            if row.title.is_empty() {
                                &row.url
                            } else {
                                &row.title
                            },
                            if row.title.is_empty() { 40 } else { 30 }
                        )
                    ));
                }
                Ok(page) => {
                    let status = page.diagnostics["status"].as_str().unwrap_or("unknown");
                    if !matches!(status, "empty_body" | "ok") {
                        page_read_errors.push(json!({
                            "title": row.title,
                            "url": row.url,
                            "status": status,
                            "message": page.diagnostics["message"]
                        }));
                    }
                }
                Err(error) => page_read_errors.push(json!({
                    "title": row.title,
                    "url": row.url,
                    "status": "read_error",
                    "message": error
                })),
            }
        }
    }

    let params = extract_params(&[all_snippets.clone(), titles].concat());
    if !params.is_empty() {
        notes.push(format!(
            "从网页中提取到参数: {}",
            params
                .iter()
                .map(|(name, value)| format!("{name}={}", format_g(*value)))
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }

    // Legacy ask passes its complete snippet list to build_candidate, where the
    // search snippets are appended a second time before parameter extraction.
    let (suggestion, reason) = build_candidate(&query, &results, &all_snippets);
    let mut result = json!({
        "ok": suggestion.is_some(),
        "results": results.iter().map(legacy_result).collect::<Vec<_>>(),
        "suggestion": suggestion,
        "reason": reason,
        "query": query,
        "notes": notes,
        "params": params_to_value(&params)
    });
    if !page_read_errors.is_empty() {
        result["page_read_errors"] = json!(page_read_errors);
    }
    Ok(result)
}

fn legacy_result(row: &LookupResult) -> Value {
    json!({"title":row.title,"snippet":row.snippet,"url":row.url})
}

fn key_terms(phrase: &str) -> (Vec<String>, Vec<String>) {
    let mut terms = Vec::new();
    let chars = phrase.chars().collect::<Vec<_>>();
    let mut start = 0;
    while start < chars.len() {
        if !is_han(chars[start]) {
            start += 1;
            continue;
        }
        let begin = start;
        while start < chars.len() && is_han(chars[start]) {
            start += 1;
        }
        let run = chars[begin..start].iter().collect::<String>();
        let run_chars = run.chars().collect::<Vec<_>>();
        for pair in run_chars.windows(2) {
            terms.push(pair.iter().collect());
        }
        if run_chars.len() <= 4 {
            terms.push(run);
        }
    }
    // Preserve the legacy literal-d regex rather than silently changing filtering.
    let code_regex = Regex::new(r"[A-Za-z]{0,3}d{2,}[A-Za-z]*").expect("valid legacy regex");
    let codes = code_regex
        .find_iter(phrase)
        .map(|matched| matched.as_str().to_owned())
        .collect();
    (terms, codes)
}

fn is_han(character: char) -> bool {
    ('\u{4e00}'..='\u{9fff}').contains(&character)
}

fn is_relevant(row: &LookupResult, terms: &[String], codes: &[String]) -> bool {
    let text = format!("{} {}", row.title, row.snippet);
    if !terms.is_empty() {
        return terms.iter().any(|term| text.contains(term));
    }
    if !codes.is_empty() {
        return codes.iter().any(|code| text.contains(code));
    }
    true
}

#[derive(Clone, Debug)]
struct ParamRule {
    name: &'static str,
    words: &'static [&'static str],
}

const PARAM_RULES: &[ParamRule] = &[
    ParamRule {
        name: "直径",
        words: &[
            "直径",
            "外径",
            "孔径",
            "螺纹直径",
            "大径",
            "公称直径",
            "⌀",
            "Φ",
            "φ",
        ],
    },
    ParamRule {
        name: "内径",
        words: &["内径", "通径", "小径"],
    },
    ParamRule {
        name: "长度",
        words: &["长度", "总长", "全长", "螺杆长度", "长"],
    },
    ParamRule {
        name: "宽度",
        words: &["宽度", "宽", "对边宽度", "对边"],
    },
    ParamRule {
        name: "高度",
        words: &["高度", "厚度", "厚", "总高", "头部高度", "高"],
    },
    ParamRule {
        name: "齿数",
        words: &["齿数"],
    },
    ParamRule {
        name: "模数",
        words: &["模数"],
    },
];

fn extract_params(snippets: &[String]) -> Vec<(String, f64)> {
    let joined = snippets.join(" ");
    let unit_mul = |unit: &str| match unit {
        "厘米" | "cm" => 10.0,
        "米" | "m" => 1000.0,
        "英寸" | "寸" => 25.4,
        _ => 1.0,
    };
    let mut output = Vec::new();
    for rule in PARAM_RULES {
        let alternatives = rule.words.join("|");
        let pattern = format!(
            r"(?:{alternatives})\s*(?:为|是|=|:|：)?\s*(\d+(?:\.\d+)?)\s*(毫米|mm|厘米|cm|米|m|英寸|寸|度|°)?"
        );
        let regex = Regex::new(&pattern).expect("valid parameter regex");
        let mut counts: Vec<(f64, usize)> = Vec::new();
        for captures in regex.captures_iter(&joined) {
            let Some(number) = captures
                .get(1)
                .and_then(|value| value.as_str().parse::<f64>().ok())
            else {
                continue;
            };
            let unit = captures.get(2).map(|value| value.as_str()).unwrap_or("");
            let value = round_three(number * unit_mul(unit));
            if value <= 0.0 || value > 100_000.0 {
                continue;
            }
            if let Some((_, count)) = counts.iter_mut().find(|(existing, _)| *existing == value) {
                *count += 1;
            } else {
                counts.push((value, 1));
            }
        }
        // Python max() keeps the first item when keys tie; Rust max_by_key()
        // returns the last tied item, so keep the old insertion-order behavior.
        let mut best_count = 0;
        let mut best_value = None;
        for (value, count) in counts {
            if count > best_count {
                best_count = count;
                best_value = Some(value);
            }
        }
        if let Some(value) = best_value {
            output.push((rule.name.to_owned(), value));
        }
    }
    output
}

fn round_three(value: f64) -> f64 {
    (value * 1000.0).round_ties_even() / 1000.0
}

fn params_to_value(params: &[(String, f64)]) -> Value {
    let mut object = Map::new();
    for (key, value) in params {
        object.insert(key.clone(), json!(value));
    }
    Value::Object(object)
}

fn build_candidate(
    phrase: &str,
    results: &[LookupResult],
    extra_texts: &[String],
) -> (Option<String>, String) {
    if results.is_empty() {
        return (None, "没有搜索到结果".into());
    }
    let mut snippets = results
        .iter()
        .filter(|row| !row.snippet.is_empty())
        .map(|row| row.snippet.clone())
        .collect::<Vec<_>>();
    let titles = results
        .iter()
        .map(|row| row.title.clone())
        .collect::<Vec<_>>();
    snippets.extend(extra_texts.iter().cloned());
    let mut params = extract_params(&[snippets, titles].concat());
    let mut why = params
        .iter()
        .map(|(name, value)| format!("{name}≈{}", format_g(*value)))
        .collect::<Vec<_>>();

    let m_regex = Regex::new(r"[Mm]\s*(\d+(?:\.\d+)?)").expect("valid M-size regex");
    if let Some(captures) = m_regex
        .captures_iter(phrase)
        .find(|captures| has_non_alphanumeric_edges(phrase, captures.get(0).expect("match")))
    {
        let value_text = captures.get(1).expect("capture group").as_str();
        if let Ok(value) = value_text.parse::<f64>() {
            set_param(&mut params, "直径", value);
            why.push(format!("公称直径 M{value_text} (以此为准)"));
        }
    }

    let bearing_regex = Regex::new(r"([23678])([0-9])([0-9]{2})").expect("valid bearing regex");
    if let Some(captures) = bearing_regex
        .captures_iter(phrase)
        .find(|captures| has_non_digit_edges(phrase, captures.get(0).expect("match"), true))
        && phrase.contains("轴承")
        && get_param(&params, "内径").is_none()
        && get_param(&params, "直径").is_none()
    {
        let tail = captures
            .get(3)
            .and_then(|value| value.as_str().parse::<i32>().ok())
            .unwrap_or(0);
        let bore = match tail {
            0 => 10.0,
            1 => 12.0,
            2 => 15.0,
            3 => 17.0,
            value => value as f64 * 5.0,
        };
        set_param(&mut params, "内径", bore);
        why.push(format!(
            "按轴承型号 {} 推算内径 {}mm",
            captures.get(0).expect("full capture").as_str(),
            format_g(bore)
        ));
    }

    let profile_regex = Regex::new(r"([0-9]{2})([0-9]{2})").expect("valid profile regex");
    if let Some(captures) = profile_regex
        .captures_iter(phrase)
        .find(|captures| has_non_digit_edges(phrase, captures.get(0).expect("match"), false))
        && (phrase.contains("型材") || phrase.contains("方管"))
    {
        let a = captures
            .get(1)
            .and_then(|value| value.as_str().parse::<i32>().ok())
            .unwrap_or(0);
        let b = captures
            .get(2)
            .and_then(|value| value.as_str().parse::<i32>().ok())
            .unwrap_or(0);
        if (10..=200).contains(&a) && (10..=200).contains(&b) {
            why.push(format!(
                "按型材截面 {a}×{b} 估算 (壁厚 2mm, 默认长度 100mm, 用圆管近似)"
            ));
            return (
                Some(format!(
                    "套筒 外径{} 内径{} 长100",
                    format_g(a as f64),
                    format_g((a - 4).max(1) as f64)
                )),
                why.join("; "),
            );
        }
    }

    let dia = get_param(&params, "直径").or_else(|| get_param(&params, "内径"));
    let Some(dia) = dia else {
        return (
            None,
            format!(
                "搜索结果里没有找到可用的尺寸参数。（搜到 {} 条结果，但没有带单位的直径/长度等信息）",
                results.len()
            ),
        );
    };

    let std = [
        "内六角",
        "螺栓",
        "螺钉",
        "螺母",
        "垫圈",
        "轴承",
        "法兰",
        "齿轮",
    ]
    .iter()
    .find(|kind| phrase.contains(**kind))
    .copied();
    let length = get_param(&params, "长度")
        .or_else(|| get_param(&params, "高度"))
        .unwrap_or(dia * 3.0);
    let (suggestion, extra_reason) = match std {
        Some("内六角" | "螺栓" | "螺钉") => {
            let factor = if std == Some("螺钉") { 1.6 } else { 1.5 };
            let head_diameter = round_two(dia * factor);
            let head_height = round_two(dia);
            let desc = format!(
                "阶梯轴 直径{} 长{}, 直径{} 长{}",
                format_g(dia),
                format_g(length),
                format_g(head_diameter),
                format_g(head_height)
            );
            let reason = format!(
                "按{}估算: 杆径{} 杆长{} 头径{} 头高{}",
                std.unwrap_or_default(),
                format_g(dia),
                format_g(length),
                format_g(head_diameter),
                format_g(head_height)
            );
            (desc, reason)
        }
        Some("螺母") => {
            let outer = round_two(dia * 1.6);
            let height = round_two(dia * 0.8);
            (
                format!(
                    "套筒 外径{} 内径{} 长{}",
                    format_g(outer),
                    format_g(dia),
                    format_g(height)
                ),
                "按螺母估算".into(),
            )
        }
        Some("垫圈") => {
            let outer = round_two(dia * 2.2);
            let height = round_two(dia * 0.2);
            (
                format!(
                    "套筒 外径{} 内径{} 长{}",
                    format_g(outer),
                    format_g(dia),
                    format_g(height)
                ),
                "按垫圈估算".into(),
            )
        }
        _ => {
            let mut parts = Vec::new();
            if let Some(diameter) = get_param(&params, "直径") {
                parts.push(format!("直径{}", format_g(diameter)));
            }
            if let Some(inner) = get_param(&params, "内径") {
                parts.push(format!("内径{}", format_g(inner)));
            }
            parts.push(format!("长{}", format_g(length)));
            (
                format!("套筒 {}", parts.join(" ")),
                "按搜到的尺寸估算".into(),
            )
        }
    };
    why.push(extra_reason);
    (Some(suggestion), why.join("; "))
}

fn get_param(params: &[(String, f64)], name: &str) -> Option<f64> {
    params
        .iter()
        .find(|(key, _)| key == name)
        .map(|(_, value)| *value)
}

fn set_param(params: &mut Vec<(String, f64)>, name: &str, value: f64) {
    if let Some((_, existing)) = params.iter_mut().find(|(key, _)| key == name) {
        *existing = value;
    } else {
        params.push((name.to_owned(), value));
    }
}

fn round_two(value: f64) -> f64 {
    (value * 100.0).round_ties_even() / 100.0
}

fn has_non_alphanumeric_edges(phrase: &str, matched: regex::Match<'_>) -> bool {
    let before = phrase[..matched.start()].chars().next_back();
    let after = phrase[matched.end()..].chars().next();
    !before.is_some_and(|value| value.is_ascii_alphanumeric())
        && !after.is_some_and(|value| value.is_ascii_digit())
}

fn has_non_digit_edges(phrase: &str, matched: regex::Match<'_>, reject_dash_after: bool) -> bool {
    let before = phrase[..matched.start()].chars().next_back();
    let after = phrase[matched.end()..].chars().next();
    !before.is_some_and(|value| value.is_ascii_digit())
        && !after.is_some_and(|value| value.is_ascii_digit() || (reject_dash_after && value == '-'))
}

fn truncate_chars(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

fn format_g(value: f64) -> String {
    if value == 0.0 {
        return "0".into();
    }
    let exponent = value.abs().log10().floor() as i32;
    if exponent < -4 || exponent >= 6 {
        let formatted = format!("{value:.5e}");
        let (mantissa, exponent_text) = formatted.split_once('e').unwrap_or((&formatted, "0"));
        let mantissa = mantissa.trim_end_matches('0').trim_end_matches('.');
        let exponent_num = exponent_text.parse::<i32>().unwrap_or(0);
        return format!("{mantissa}e{exponent_num:+03}");
    }
    let precision = (5 - exponent).max(0) as usize;
    let fixed = format!("{value:.precision$}");
    fixed.trim_end_matches('0').trim_end_matches('.').to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Map;

    struct FixtureBackend {
        searches: Vec<SearchReply>,
        search_queries: Vec<String>,
        pages: Map<String, Value>,
        opened: Vec<String>,
    }

    impl FixtureBackend {
        fn from_case(case: &Value) -> Self {
            let searches = case["search_responses"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|response| SearchReply {
                    results: response["rows"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(|row| {
                            let url = row["url"].as_str()?.to_owned();
                            Some(LookupResult {
                                result_id: url.clone(),
                                title: row["title"].as_str().unwrap_or_default().to_owned(),
                                snippet: row["snippet"].as_str().unwrap_or_default().to_owned(),
                                url,
                            })
                        })
                        .collect(),
                    diagnostics: Value::Null,
                })
                .collect();
            Self {
                searches,
                search_queries: Vec::new(),
                pages: case["page_responses"]
                    .as_object()
                    .cloned()
                    .unwrap_or_default(),
                opened: Vec::new(),
            }
        }
    }

    impl LookupBackend for FixtureBackend {
        fn search(&mut self, query: &str, _timeout: Duration) -> Result<SearchReply, String> {
            self.search_queries.push(query.to_owned());
            let index = self.search_queries.len() - 1;
            Ok(self.searches.get(index).cloned().unwrap_or_default())
        }

        fn open_result(
            &mut self,
            result_id: &str,
            _timeout: Duration,
        ) -> Result<PageReply, String> {
            self.opened.push(result_id.to_owned());
            let page = self.pages.get(result_id).cloned().unwrap_or(Value::Null);
            let text = if page["text_repeat"].is_object() {
                let spec = &page["text_repeat"];
                let repeated = spec["value"].as_str().unwrap_or_default();
                let count = spec["count"].as_u64().unwrap_or_default() as usize;
                format!(
                    "{}{}",
                    repeated.repeat(count),
                    spec["suffix"].as_str().unwrap_or_default()
                )
            } else {
                page["text"].as_str().unwrap_or_default().to_owned()
            };
            Ok(PageReply {
                text,
                diagnostics: page.get("diagnostics").cloned().unwrap_or_else(
                    || json!({"status":page["status"].as_str().unwrap_or("empty_body")}),
                ),
            })
        }
    }

    #[test]
    fn rust_output_matches_python_ask_golden_fixtures() {
        let fixtures: Value = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/fixtures/native_online_lookup.json"
        )))
        .unwrap();
        let fields = [
            "ok",
            "results",
            "suggestion",
            "reason",
            "query",
            "notes",
            "params",
        ];
        for case in fixtures.as_array().unwrap() {
            let mut backend = FixtureBackend::from_case(case);
            let result = ask(&mut backend, case["query"].as_str().unwrap()).unwrap();
            let expected = &case["expected"];
            assert_eq!(
                backend.search_queries,
                expected["search_queries"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|query| query.as_str().unwrap().to_owned())
                    .collect::<Vec<_>>(),
                "search query sequence: {}",
                case["id"]
            );
            assert_eq!(
                backend.opened,
                expected["opened_urls"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|url| url.as_str().unwrap().to_owned())
                    .collect::<Vec<_>>(),
                "deep page sequence: {}",
                case["id"]
            );
            let expected_response = &expected["response"];
            for field in fields {
                match expected_response.get(field) {
                    Some(value) => assert_eq!(result[field], *value, "{}: {field}", case["id"]),
                    None => assert!(
                        result.get(field).is_none(),
                        "{} unexpectedly has {field}",
                        case["id"]
                    ),
                }
            }
            if let Some(rows) = result["results"].as_array() {
                for row in rows {
                    assert_eq!(
                        row.as_object().unwrap().len(),
                        3,
                        "legacy row shape: {}",
                        case["id"]
                    );
                    assert!(row.get("result_id").is_none());
                }
            }
        }
    }

    #[test]
    fn page_read_security_failures_are_reported_without_claiming_a_body_read() {
        let rows = vec![
            LookupResult {
                result_id: "unsafe-a".into(),
                title: "产品甲".into(),
                snippet: "外径6mm".into(),
                url: "http://127.0.0.1/private".into(),
            },
            LookupResult {
                result_id: "unsafe-b".into(),
                title: "产品乙".into(),
                snippet: "外径6mm".into(),
                url: "http://127.0.0.1/private-two".into(),
            },
        ];
        let mut backend = FixtureBackend {
            searches: vec![SearchReply {
                results: rows,
                diagnostics: Value::Null,
            }],
            search_queries: Vec::new(),
            pages: Map::from_iter([
                (
                    "unsafe-a".into(),
                    json!({"text":"","status":"unsafe_url","message":"blocked unsafe URL"}),
                ),
                (
                    "unsafe-b".into(),
                    json!({"text":"","status":"unsafe_url","message":"blocked unsafe URL"}),
                ),
            ]),
            opened: Vec::new(),
        };
        let result = ask(&mut backend, "产品").unwrap();
        assert_eq!(backend.opened, ["unsafe-a", "unsafe-b"]);
        assert_eq!(
            result["notes"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|note| note.as_str().unwrap().starts_with("已读取详情页:"))
                .count(),
            0
        );
        assert_eq!(result["page_read_errors"].as_array().unwrap().len(), 2);
        assert_eq!(result["page_read_errors"][0]["status"], "unsafe_url");
    }

    #[test]
    fn search_failures_propagate_as_native_command_errors() {
        struct FailingBackend;
        impl LookupBackend for FailingBackend {
            fn search(&mut self, _query: &str, _timeout: Duration) -> Result<SearchReply, String> {
                Err("fixture search timeout".into())
            }
            fn open_result(
                &mut self,
                _result_id: &str,
                _timeout: Duration,
            ) -> Result<PageReply, String> {
                unreachable!("search failed before page reads")
            }
        }
        assert_eq!(
            ask(&mut FailingBackend, "垫圈").unwrap_err(),
            "fixture search timeout"
        );
    }
}
