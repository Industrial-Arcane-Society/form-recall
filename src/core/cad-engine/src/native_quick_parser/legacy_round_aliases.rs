//! Strict compatibility aliases for the legacy Quick parser's round primitives.
//!
//! This helper intentionally accepts only a single noun followed by explicit
//! dimension clauses. It does not rewrite compound descriptions such as
//! "底板上做一个凸台" where the noun may describe a secondary feature.

/// Rewrite a bare legacy round-part noun to a noun already understood by the
/// native Quick grammar. Returns `None` for ambiguous or compound text.
pub(super) fn canonicalize_round_alias(input: &str) -> Option<String> {
    let trimmed = input.trim();
    if trimmed.is_empty()
        || trimmed
            .chars()
            .any(|c| matches!(c, '\n' | '\r' | ';' | '；' | '。'))
    {
        return None;
    }

    let aliases = [
        ("柱体", "圆柱体"),
        ("圆钢", "圆柱体"),
        ("凸台", "圆柱体"),
        ("圆盘", "圆柱体"),
        ("盘体", "圆柱体"),
        // The old parser classifies this exact phrase as a cylinder because
        // its diameter+height shortcut runs before the cone noun check.
        ("锥体", "圆柱体"),
    ];

    for (alias, canonical) in aliases {
        let Some(rest) = trimmed.strip_prefix(alias) else {
            continue;
        };
        // A noun must end here; avoid rewriting longer words containing it.
        if rest
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_alphanumeric() || c == '_')
        {
            continue;
        }
        let rest = rest.trim();
        if has_explicit_round_dimensions(rest) {
            return Some(format!("{canonical} {rest}"));
        }
    }

    // "球" is already a legacy sphere noun, but native Quick deliberately
    // requires the unambiguous form "球体". Keep this equally strict.
    if let Some(rest) = trimmed.strip_prefix("球") {
        if !rest.starts_with('体') {
            let rest = rest.trim();
            if has_explicit_sphere_dimensions(rest) {
                return Some(format!("球体 {rest}"));
            }
        }
    }
    None
}

fn has_explicit_round_dimensions(text: &str) -> bool {
    let diameter = ["直径", "外径", "底径", "diameter", "dia"]
        .iter()
        .any(|key| has_numeric_dimension(text, key));
    let height = [
        "高",
        "高度",
        "长度",
        "厚",
        "厚度",
        "height",
        "length",
        "thickness",
    ]
    .iter()
    .any(|key| has_numeric_dimension(text, key));
    diameter
        && height
        && only_dimension_fields(
            text,
            &[
                "thickness",
                "diameter",
                "height",
                "length",
                "底径",
                "直径",
                "外径",
                "高度",
                "长度",
                "厚度",
                "厚",
                "高",
                "dia",
            ],
        )
}

fn has_explicit_sphere_dimensions(text: &str) -> bool {
    ["半径", "radius", "直径", "diameter", "dia"]
        .iter()
        .any(|key| has_numeric_dimension(text, key))
        && only_dimension_fields(text, &["diameter", "radius", "直径", "半径", "dia"])
}

fn has_numeric_dimension(text: &str, key: &str) -> bool {
    let Some(index) = text.find(key) else {
        return false;
    };
    let after = text[index + key.len()..].trim_start();
    let digits = after
        .chars()
        .take_while(|c| c.is_ascii_digit() || *c == '.')
        .count();
    digits > 0 && after[..digits].chars().any(|c| c.is_ascii_digit())
}

fn only_dimension_fields(mut text: &str, keys: &[&str]) -> bool {
    let mut fields = 0;
    loop {
        text = text.trim_start();
        if text.is_empty() {
            return fields > 0;
        }
        let Some(key) = keys.iter().find(|key| text.starts_with(**key)) else {
            return false;
        };
        text = &text[key.len()..];
        let number_chars = text
            .chars()
            .take_while(|c| c.is_ascii_digit() || *c == '.')
            .count();
        if number_chars == 0 || !text[..number_chars].chars().any(|c| c.is_ascii_digit()) {
            return false;
        }
        text = &text[number_chars..];
        text = text.trim_start();
        for unit in ["毫米", "厘米", "mm", "cm"] {
            if let Some(rest) = text.strip_prefix(unit) {
                text = rest;
                break;
            }
        }
        fields += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::canonicalize_round_alias;

    #[test]
    fn rewrites_only_the_seven_audited_legacy_phrases() {
        let cases = [
            ("柱体 直径20高50", "圆柱体 直径20高50"),
            ("圆钢 直径20长度50", "圆柱体 直径20长度50"),
            ("凸台 直径20高10", "圆柱体 直径20高10"),
            ("圆盘 直径50厚10", "圆柱体 直径50厚10"),
            ("盘体 直径50厚10", "圆柱体 直径50厚10"),
            ("球 半径10", "球体 半径10"),
            ("锥体 底径20高30", "圆柱体 底径20高30"),
        ];
        for (input, expected) in cases {
            assert_eq!(canonicalize_round_alias(input).as_deref(), Some(expected));
        }
    }

    #[test]
    fn leaves_ambiguous_or_incomplete_nouns_untouched() {
        for input in [
            "凸台",
            "底板上做一个凸台 直径20高10",
            "圆盘直径50",
            "球半径",
            "球体半径10",
            "锥体底径20高30并打孔",
            "圆钢管直径20长度50",
        ] {
            assert_eq!(canonicalize_round_alias(input), None, "{input}");
        }
    }
}
