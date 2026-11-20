//! Rewrites only old Quick round-part aliases whose missing dimensions are
//! supplied by the legacy parser. The result keeps inferred fields separate
//! so the caller can report them as derived rather than user-provided.

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct RoundDefaultAlias {
    pub(super) canonical_text: String,
    pub(super) inferred_fields: Vec<&'static str>,
}

/// Canonicalize a standalone legacy round noun and its optional single
/// dimension. Multi-dimension inputs are deliberately left to the regular
/// round-alias parser so old cone-vs-cylinder precedence remains intact.
pub(super) fn canonicalize_round_default_alias(input: &str) -> Option<RoundDefaultAlias> {
    let (prefix, noun, rest) = split_leading_noun(input)?;
    let rest = rest.trim();

    let rewrite = match noun {
        "圆钢" | "凸台" | "圆凸台" | "凸缘" => canonical_cylinder(prefix, rest),
        "球" => canonical_sphere(prefix, rest),
        "锥体" | "锥形" => canonical_cone(prefix, rest),
        // This rule is limited to a leading standalone noun. The old parser
        // treats top-level “凸台” as a cylinder, while infix secondary-boss
        // wording is rejected by `split_leading_noun` and stays unchanged.
        _ => None,
    }?;

    Some(rewrite)
}

fn split_leading_noun(input: &str) -> Option<(&str, &'static str, &str)> {
    const FILLERS: &[&str] = &[
        "请帮我画一个",
        "帮我画一个",
        "请帮我做一个",
        "帮我做一个",
        "请画一个",
        "请做一个",
        "画一个",
        "做一个",
        "帮我画",
        "帮我做",
        "请画",
        "请做",
        "画",
        "做",
    ];
    const NOUNS: &[&str] = &["圆凸台", "圆钢", "凸台", "凸缘", "锥体", "锥形", "球"];

    let trimmed = input.trim_start();
    let mut prefix_len = input.len() - trimmed.len();
    let mut body = trimmed;
    if let Some(filler) = FILLERS.iter().find(|filler| body.starts_with(**filler)) {
        prefix_len += filler.len();
        body = &body[filler.len()..];
        let whitespace_len = body.len() - body.trim_start().len();
        prefix_len += whitespace_len;
        body = body.trim_start();
    }

    let noun = NOUNS.iter().find(|noun| body.starts_with(**noun))?;
    let rest = &body[noun.len()..];
    // A known alias must end at a dimension field or at end of input. This
    // rejects longer names such as “圆钢管” and unrelated suffix text.
    if !rest.is_empty() && !starts_dimension(rest.trim_start()) {
        return None;
    }

    let prefix = &input[..prefix_len];
    Some((prefix, *noun, rest))
}

fn canonical_cylinder(prefix: &str, dimensions: &str) -> Option<RoundDefaultAlias> {
    let parsed = parse_single_dimension(
        dimensions,
        &[
            "直径",
            "外径",
            "底径",
            "⌀",
            "ø",
            "半径",
            "diameter",
            "dia",
            "radius",
            "高度",
            "高",
            "长度",
            "长",
            "thickness",
            "height",
            "length",
            "厚度",
            "厚",
        ],
    )?;
    match parsed {
        None => Some(RoundDefaultAlias {
            canonical_text: format!("{prefix}圆柱体 直径50 高50"),
            inferred_fields: vec!["diameter=50 mm", "height=50 mm"],
        }),
        Some((key, value))
            if matches!(
                key,
                "直径" | "外径" | "底径" | "diameter" | "dia" | "⌀" | "ø"
            ) =>
        {
            Some(RoundDefaultAlias {
                canonical_text: format!("{prefix}圆柱体 直径{value} 高50"),
                inferred_fields: vec!["height=50 mm"],
            })
        }
        Some((key, value)) if key == "半径" || key == "radius" => Some(RoundDefaultAlias {
            canonical_text: format!("{prefix}圆柱体 半径{value} 高50"),
            inferred_fields: vec!["height=50 mm"],
        }),
        // A lone axial size uses the legacy radius default.
        Some((_key, value)) => Some(RoundDefaultAlias {
            canonical_text: format!("{prefix}圆柱体 直径50 高{value}"),
            inferred_fields: vec!["diameter=50 mm"],
        }),
    }
}

fn canonical_sphere(prefix: &str, dimensions: &str) -> Option<RoundDefaultAlias> {
    let parsed = parse_single_dimension(
        dimensions,
        &["半径", "直径", "⌀", "ø", "radius", "diameter", "dia"],
    )?;
    match parsed {
        None => Some(RoundDefaultAlias {
            canonical_text: format!("{prefix}球体 半径25"),
            inferred_fields: vec!["radius=25 mm"],
        }),
        Some((key, value)) if matches!(key, "直径" | "diameter" | "dia" | "⌀" | "ø") => {
            Some(RoundDefaultAlias {
                canonical_text: format!("{prefix}球体 直径{value}"),
                inferred_fields: Vec::new(),
            })
        }
        Some((_key, value)) => Some(RoundDefaultAlias {
            canonical_text: format!("{prefix}球体 半径{value}"),
            inferred_fields: Vec::new(),
        }),
    }
}

fn canonical_cone(prefix: &str, dimensions: &str) -> Option<RoundDefaultAlias> {
    let parsed = parse_single_dimension(
        dimensions,
        &[
            "底径",
            "底部直径",
            "大端直径",
            "直径",
            "⌀",
            "ø",
            "厚度",
            "thickness",
            "底半径",
            "底部半径",
            "半径",
            "高度",
            "高",
            "厚",
            "length",
            "height",
            "diameter",
            "radius",
        ],
    )?;
    match parsed {
        None => Some(RoundDefaultAlias {
            // A lone bottom diameter maps to the old R25/H50 defaults. Keep
            // only one dimension label: “diameter + height” activates the
            // legacy plain-cylinder shortcut before cone classification.
            canonical_text: format!("{prefix}圆锥 底径50"),
            inferred_fields: vec!["base_diameter=50 mm", "height=50 mm"],
        }),
        Some((key, value))
            if matches!(
                key,
                "高" | "高度" | "厚" | "厚度" | "height" | "length" | "thickness"
            ) =>
        {
            Some(RoundDefaultAlias {
                canonical_text: format!("{prefix}圆锥 高{value}"),
                inferred_fields: vec!["base_radius=25 mm"],
            })
        }
        Some((key, value)) if matches!(key, "底半径" | "底部半径" | "半径" | "radius") => {
            Some(RoundDefaultAlias {
                canonical_text: format!("{prefix}圆锥 底半径{value}"),
                // Python uses the final raw radius as the missing height.
                inferred_fields: vec!["height=radius value (legacy raw-dimension rule)"],
            })
        }
        Some((_key, value)) => Some(RoundDefaultAlias {
            canonical_text: format!("{prefix}圆锥 底径{value}"),
            // Python uses a lone raw diameter as both base diameter and
            // missing cone height; this odd historical rule is intentional.
            inferred_fields: vec!["height=diameter value (legacy raw-dimension rule)"],
        }),
    }
}

fn starts_dimension(text: &str) -> bool {
    [
        "直径",
        "外径",
        "底径",
        "底部直径",
        "大端直径",
        "半径",
        "底半径",
        "底部半径",
        "高度",
        "高",
        "厚度",
        "厚",
        "长度",
        "height",
        "length",
        "thickness",
        "diameter",
        "radius",
        "⌀",
        "ø",
    ]
    .iter()
    .any(|key| text.starts_with(key))
}

/// Parse exactly zero or one labelled dimension; a second dimension means
/// normal full-dimension alias handling should take precedence.
fn parse_single_dimension<'a>(
    text: &'a str,
    keys: &[&'static str],
) -> Option<Option<(&'static str, &'a str)>> {
    let text = text.trim();
    if text.is_empty() {
        return Some(None);
    }
    let key = keys.iter().find(|key| text.starts_with(**key)).copied()?;
    let value = text[key.len()..].trim_start();
    let digits = value
        .chars()
        .take_while(|ch| ch.is_ascii_digit() || *ch == '.')
        .count();
    if digits == 0 || !value[..digits].chars().any(|ch| ch.is_ascii_digit()) {
        return None;
    }
    let after_number = &value[digits..];
    let leading_unit_space = after_number.len() - after_number.trim_start().len();
    let mut suffix = after_number.trim_start();
    let mut unit_len = 0;
    for unit in ["毫米", "厘米", "mm", "cm"] {
        if let Some(rest) = suffix.strip_prefix(unit) {
            suffix = rest.trim();
            unit_len = unit.len();
            break;
        }
    }
    if !suffix.is_empty() {
        return None;
    }
    let raw_value_end = digits + leading_unit_space + unit_len;
    Some(Some((key, value[..raw_value_end].trim())))
}

#[cfg(test)]
mod tests {
    use super::{RoundDefaultAlias, canonicalize_round_default_alias};

    fn expected(text: &str, fields: &[&'static str]) -> RoundDefaultAlias {
        RoundDefaultAlias {
            canonical_text: text.to_owned(),
            inferred_fields: fields.to_vec(),
        }
    }

    #[test]
    fn restores_measured_legacy_default_round_results() {
        let cases = [
            (
                "圆钢",
                expected("圆柱体 直径50 高50", &["diameter=50 mm", "height=50 mm"]),
            ),
            (
                "圆钢 直径20",
                expected("圆柱体 直径20 高50", &["height=50 mm"]),
            ),
            (
                "圆凸台 直径20",
                expected("圆柱体 直径20 高50", &["height=50 mm"]),
            ),
            (
                "凸台",
                expected(
                    "圆柱体 直径50 高50",
                    &["diameter=50 mm", "height=50 mm"],
                ),
            ),
            (
                "凸台直径20",
                expected("圆柱体 直径20 高50", &["height=50 mm"]),
            ),
            (
                "请帮我做一个凸台直径20",
                expected("请帮我做一个圆柱体 直径20 高50", &["height=50 mm"]),
            ),
            (
                "凸缘 直径20",
                expected("圆柱体 直径20 高50", &["height=50 mm"]),
            ),
            ("球", expected("球体 半径25", &["radius=25 mm"])),
            (
                "帮我画一个圆钢直径20",
                expected("帮我画一个圆柱体 直径20 高50", &["height=50 mm"]),
            ),
            (
                "帮我做一个球",
                expected("帮我做一个球体 半径25", &["radius=25 mm"]),
            ),
            (
                "锥体",
                expected("圆锥 底径50", &["base_diameter=50 mm", "height=50 mm"]),
            ),
            (
                "锥形 直径20",
                expected(
                    "圆锥 底径20",
                    &["height=diameter value (legacy raw-dimension rule)"],
                ),
            ),
        ];
        for (input, output) in cases {
            assert_eq!(
                canonicalize_round_default_alias(input),
                Some(output),
                "{input}"
            );
        }
    }

    #[test]
    fn refuses_compounds_full_dimension_inputs_and_ambiguous_nouns() {
        for input in [
            "底板上做一个圆凸台直径20",
            "底板上做一个凸台直径20",
            "圆钢管直径20",
            "圆钢直径20高30",
            "锥体底径20高30",
            "锥形直径20高30",
            "圆钢直径",
            "球半径",
            "帮我做一个螺纹球",
        ] {
            assert_eq!(canonicalize_round_default_alias(input), None, "{input}");
        }
    }
}
