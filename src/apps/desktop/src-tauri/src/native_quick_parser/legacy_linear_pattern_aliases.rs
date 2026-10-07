//! Conservative compatibility aliases for the legacy Quick linear pattern syntax.
//!
//! This module deliberately does not parse geometry or mutate a CAD plan. It only
//! rewrites an unambiguous, explicitly-counted linear-pattern phrase into the
//! canonical `阵列N个` spelling already understood by the parent parser.

use regex::Regex;

/// Rewrite one explicit linear-pattern synonym to the parser's canonical spelling.
///
/// The alias must be followed immediately by a count (at least two instances),
/// and the request must also state a positive spacing. Circular/PCD language,
/// an existing canonical pattern marker, multiple aliases, and phrases that can
/// describe a non-linear distribution are rejected instead of being guessed.
/// The parent parser can call this before its ordinary normalization/parsing:
/// `let canonical = canonicalize_explicit_linear_alias(text).unwrap_or_else(|| text.to_owned());`
pub(super) fn canonicalize_explicit_linear_alias(text: &str) -> Option<String> {
    let lower = text.to_lowercase();

    // Do not reinterpret circular distributions or pitch-circle hole layouts as
    // a linear feature pattern, even when they happen to mention a spacing.
    if contains_any(
        &lower,
        &[
            "阵列",
            "pcd",
            "分度圆",
            "节圆",
            "分布圆",
            "圆周",
            "环形",
            "绕圆",
            "均布",
            "等角",
            "角度",
            "相位",
        ],
    ) {
        return None;
    }

    // A stated spacing is required. Bare “复制几个” can mean independent
    // instances or a copy operation without a defined linear transform.
    let spacing =
        Regex::new(r"(?:间距|间隔|步距|pitch)\s*[:：=]?\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)?").ok()?;
    let Some(spacing_match) = spacing.captures(&lower) else {
        return None;
    };
    let spacing_value = spacing_match.get(1)?.as_str().parse::<f64>().ok()?;
    if !spacing_value.is_finite() || spacing_value <= 0.0 {
        return None;
    }

    // Only one direct alias/count pair is accepted. In particular, do not pick
    // one count from a sentence that contains several possible copy/distribution
    // operations.
    let alias = Regex::new(r"(复制|拷贝|排列|分布)\s*([0-9]+)\s*(?:个|份|次)").ok()?;
    let matches: Vec<_> = alias.captures_iter(text).collect();
    if matches.len() != 1 {
        return None;
    }
    let captures = &matches[0];
    let count = captures.get(2)?.as_str().parse::<u32>().ok()?;
    if !(2..=1000).contains(&count) {
        return None;
    }

    let whole = captures.get(0)?;

    // Reject ambiguous compound wording where “分布” may be a descriptive
    // property rather than an operation. Scope direction to the alias clause:
    // an X direction in a separate hole clause must not make a bare
    // “分布4个” look like a directed pattern. Direction is optional for the
    // other aliases because the legacy parser defaults their axis to Z.
    let alias_match = captures.get(1)?;
    if alias_match.as_str() == "分布" {
        let alias_clause = super::split_clauses(text)
            .into_iter()
            .find(|clause| clause.contains(whole.as_str()))?;
        if !contains_any(
            &alias_clause.to_lowercase(),
            &["沿x", "沿y", "沿z", "x方向", "y方向", "z方向", "方向"],
        ) {
            return None;
        }
    }

    let canonical = format!("阵列{}个", count);
    let mut output = String::with_capacity(text.len() + canonical.len());
    output.push_str(&text[..whole.start()]);
    output.push_str(&canonical);
    output.push_str(&text[whole.end()..]);
    Some(output)
}

/// Canonicalize a single old Quick linear-pattern verb when some or all
/// transform values are omitted. The parent parser supplies the legacy
/// count/axis/step defaults and records their derived provenance.
pub(super) fn canonicalize_legacy_linear_alias(text: &str) -> Option<String> {
    let lower = text.to_lowercase();
    if contains_any(
        &lower,
        &[
            "阵列", "linear array", "pcd", "分度圆", "节圆", "分布圆", "圆周", "环形",
            "绕圆", "均布", "等角", "角度", "相位",
        ],
    ) {
        return None;
    }
    let aliases = Regex::new(r"复制|拷贝|排列|分布").ok()?;
    let matches: Vec<_> = aliases.find_iter(text).collect();
    if matches.len() != 1 {
        return None;
    }
    let found = matches[0];
    if found.as_str() == "分布" {
        let clause = super::split_clauses(text)
            .into_iter()
            .find(|clause| clause.contains(found.as_str()))?;
        if !contains_any(
            &clause.to_lowercase(),
            &["沿x", "沿y", "沿z", "x方向", "y方向", "z方向", "方向"],
        ) {
            return None;
        }
    }
    let mut output = String::with_capacity(text.len() + 2);
    output.push_str(&text[..found.start()]);
    output.push_str("阵列");
    output.push_str(&text[found.end()..]);
    Some(output)
}

/// Report legacy verbs that look like a linear operation but were declined
/// because they are ambiguous (for example multiple copy verbs or directionless
/// “分布”). The parser turns these into an explicit unsupported result rather
/// than silently dropping the requested operation.
pub(super) fn has_ambiguous_legacy_linear_alias(text: &str) -> bool {
    let lower = text.to_lowercase();
    if contains_any(&lower, &["pcd", "分度圆", "节圆", "分布圆", "圆周", "环形", "绕圆", "均布", "等角", "相位"]) {
        return false;
    }
    let Ok(aliases) = Regex::new(r"复制|拷贝|排列|分布") else {
        return false;
    };
    let matches: Vec<_> = aliases.find_iter(text).collect();
    if matches.len() > 1 {
        return true;
    }
    let Some(found) = matches.first() else {
        return false;
    };
    if found.as_str() != "分布" {
        return false;
    }
    let Some(clause) = super::split_clauses(text)
        .into_iter()
        .find(|clause| clause.contains(found.as_str()))
    else {
        return true;
    };
    !contains_any(
        &clause.to_lowercase(),
        &["沿x", "沿y", "沿z", "x方向", "y方向", "z方向", "方向"],
    )
}

fn contains_any(text: &str, needles: &[&str]) -> bool {
    needles.iter().any(|needle| text.contains(needle))
}

#[cfg(test)]
mod tests {
    use super::{
        canonicalize_explicit_linear_alias, canonicalize_legacy_linear_alias,
        has_ambiguous_legacy_linear_alias,
    };

    #[test]
    fn canonicalizes_explicit_copy_with_spacing_in_reference_request() {
        let input = "长方体 长100宽80高20，然后中心孔直径6通孔，然后复制4个，间距40";
        assert_eq!(
            canonicalize_explicit_linear_alias(input).as_deref(),
            Some("长方体 长100宽80高20，然后中心孔直径6通孔，然后阵列4个，间距40")
        );
    }

    #[test]
    fn canonicalizes_explicit_linear_arrangement_synonyms() {
        assert_eq!(
            canonicalize_explicit_linear_alias("沿X方向排列4个，间距40mm").as_deref(),
            Some("沿X方向阵列4个，间距40mm")
        );
        assert_eq!(
            canonicalize_explicit_linear_alias("沿Z方向分布4个，步距12").as_deref(),
            Some("沿Z方向阵列4个，步距12")
        );
    }

    #[test]
    fn rejects_ambiguous_or_unsupported_requests() {
        for input in [
            "复制4个",                                                       // No explicit spacing.
            "复制1个，间距40",            // Not a repeated pattern.
            "复制4个，间距0",             // Invalid spacing.
            "复制4个，间距40，另复制3个", // Multiple operations.
            "绕Z轴圆周分布4个，间距40",   // Circular.
            "PCD100，分布4个，间距40",    // Pitch circle.
            "分布4个，间距40",            // Distribution has no stated linear direction.
            "长方体 长100，然后沿X方向中心孔直径6通孔，然后分布4个，间距40", // Hole direction is not pattern direction.
            "孔均布4个，间距40", // Ambiguous evenly distributed holes.
            "复制四个，间距40",  // Non-numeric count.
            "复制4个，间距四十", // Non-numeric spacing.
        ] {
            assert_eq!(canonicalize_explicit_linear_alias(input), None, "{input}");
        }
    }

    #[test]
    fn canonicalizes_legacy_linear_verbs_with_omitted_values() {
        for (input, expected) in [
            ("长方体，然后复制", "长方体，然后阵列"),
            ("长方体，然后复制4个", "长方体，然后阵列4个"),
            ("长方体，然后排列", "长方体，然后阵列"),
            ("长方体，然后沿Z方向分布", "长方体，然后沿Z方向阵列"),
        ] {
            assert_eq!(canonicalize_legacy_linear_alias(input).as_deref(), Some(expected));
        }
        for input in [
            "长方体，然后分布4个", // Directionless distribution is ambiguous.
            "长方体，然后复制，再拷贝", // Multiple pattern operations.
            "长方体，然后圆周复制4个", // Circular wording must not become linear.
        ] {
            assert_eq!(canonicalize_legacy_linear_alias(input), None, "{input}");
        }
    }

    #[test]
    fn flags_declined_ambiguous_linear_verbs() {
        assert!(has_ambiguous_legacy_linear_alias("长方体，然后分布4个"));
        assert!(has_ambiguous_legacy_linear_alias("长方体沿X轴，然后分布4个"));
        assert!(has_ambiguous_legacy_linear_alias("长方体，然后复制，再拷贝"));
        assert!(!has_ambiguous_legacy_linear_alias("长方体，然后复制"));
        assert!(!has_ambiguous_legacy_linear_alias("长方体，圆周阵列4个"));
    }
}
