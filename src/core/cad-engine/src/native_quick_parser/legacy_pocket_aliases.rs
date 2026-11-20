//! Local lexical aliases for rectangular pockets supported by Python Quick.
//!
//! The caller must pass one clause produced by `split_clauses`, never a full
//! prompt. Missing or zero dimensions are intentionally allowed here: the
//! existing slot builder applies the same 20/20/5 mm defaults as legacy Quick
//! and marks those values as derived with a structured warning.

use regex::Regex;

/// Canonicalize exactly one legacy pocket word to the supported word `槽`.
///
/// Template/material language and clauses that combine multiple operations
/// stay on their existing parser route. Existing `槽` clauses are untouched.
pub(super) fn canonicalize_legacy_pocket_alias(clause: &str) -> Option<String> {
    let lower = clause.to_lowercase();

    // `型材` and material names can route to legacy catalog/template behavior;
    // do not reinterpret them as a pocket. A same-clause second operation is
    // ambiguous and must be reported by the caller rather than half-parsed.
    if contains_any(
        &lower,
        &[
            "材质",
            "材料",
            "型材",
            "铝",
            "钢",
            "铁",
            "铜",
            "塑料",
            "尼龙",
            "树脂",
            "pvc",
            "abs",
            "孔",
            "槽",
            "slot",
            "pocket",
            "keyway",
            "切除",
            "挖掉",
            "凸台",
            "螺纹",
            "倒角",
            "圆角",
            "阵列",
            "并",
            "同时",
            "然后",
            "接着",
            "以及",
            "再",
            "长方体",
            "长方块",
            "矩形块",
            "立方体",
            "圆柱",
            "圆棒",
            "圆盘",
            "底板",
            "钢板",
            "铝板",
            "cuboid",
            "box",
            ",",
            "，",
            ";",
            "；",
            "。",
            "\n",
            "\r",
        ],
    ) {
        return None;
    }

    let aliases = Regex::new(r"(内腔|型腔|开口)").expect("fixed pocket alias regex");
    let found: Vec<_> = aliases.captures_iter(clause).collect();
    if found.len() != 1 {
        return None;
    }

    let matched = found[0].get(1)?;
    let mut output = String::with_capacity(clause.len());
    output.push_str(&clause[..matched.start()]);
    output.push_str("槽");
    output.push_str(&clause[matched.end()..]);
    Some(output)
}

fn contains_any(text: &str, needles: &[&str]) -> bool {
    needles.iter().any(|needle| text.contains(needle))
}

#[cfg(test)]
mod tests {
    use super::canonicalize_legacy_pocket_alias;

    #[test]
    fn canonicalizes_explicit_partial_and_omitted_legacy_dimensions() {
        for (input, expected) in [
            ("内腔长30宽20深5", "槽长30宽20深5"),
            (
                "型腔 长度30mm 宽度20mm 深度5mm",
                "槽 长度30mm 宽度20mm 深度5mm",
            ),
            ("开口长30宽20深5", "槽长30宽20深5"),
            ("内腔长30宽20", "槽长30宽20"),
            ("型腔长30深5", "槽长30深5"),
            ("开口", "槽"),
            ("内腔长0宽20深5", "槽长0宽20深5"),
        ] {
            assert_eq!(
                canonicalize_legacy_pocket_alias(input).as_deref(),
                Some(expected),
                "{input}"
            );
        }
    }

    #[test]
    fn leaves_templates_compounds_existing_slots_and_whole_prompts_unchanged() {
        for input in [
            "型材内腔长30宽20深5",
            "内腔长30宽20深5并打孔直径6",
            "开口槽长30宽20深5",
            "长方体100x80x20内腔长30宽20深5",
            "内腔长30宽20深5，另一个开口长10宽10深2",
            "内腔",
        ] {
            assert_eq!(canonicalize_legacy_pocket_alias(input), None, "{input}");
        }
    }
}
