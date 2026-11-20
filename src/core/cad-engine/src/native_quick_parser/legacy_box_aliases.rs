//! Narrow compatibility aliases for old Quick rectangular-prism vocabulary.
//!
//! This module only canonicalizes a part name at the beginning of an input.
//! It does not infer dimensions or turn arbitrary mentions of boxes/plates in
//! feature descriptions into a base solid.

const BOX_ALIASES: &[&str] = &[
    "长方体",
    "立方体",
    "正方体",
    "方块",
    "矩形块",
    "矩形体",
    "方体",
    "方形块",
    "底板",
    "安装板",
    "垫板",
    "盖板",
    "基板",
    "铝板",
    "钢板",
    "铁板",
    "铜板",
    "平板",
    "板料",
    "板材",
    "面板",
    "隔板",
    "板",
    "箱体",
    "方箱",
    "机箱",
    "壳体",
    "机座",
    "基座",
];

const LEADING_FILLER: &[&str] = &[
    "请帮我画一个",
    "帮我画一个",
    "请画一个",
    "请做一个",
    "帮我做一个",
    "画一个",
    "做一个",
    "帮我画",
    "请画",
    "画",
    "做",
];

/// Return the same input with a leading legacy box alias replaced by
/// `长方体`. `text` is expected to have gone through the caller's normalizer
/// (so multiplication signs are already `x`).
///
/// Returns `None` for incidental/infix mentions and names without an explicit
/// alias boundary. The generic parser remains responsible for the dimensions.
pub(super) fn canonicalize_box_alias(text: &str) -> Option<String> {
    let trimmed = text.trim_start();
    let leading_whitespace = text.len() - trimmed.len();
    let mut start = leading_whitespace;

    for filler in LEADING_FILLER {
        if trimmed.starts_with(filler) {
            start += filler.len();
            while text[start..]
                .chars()
                .next()
                .is_some_and(char::is_whitespace)
            {
                start += text[start..].chars().next()?.len_utf8();
            }
            break;
        }
    }

    let rest = &text[start..];
    let alias = BOX_ALIASES.iter().find(|alias| {
        rest.starts_with(**alias)
            && rest[alias.len()..].chars().next().is_none_or(|next| {
                next.is_whitespace() || next.is_ascii_digit() || "：:,;，。".contains(next)
            })
    })?;

    let suffix_start = start + alias.len();
    let mut canonical = String::with_capacity(text.len() + "长方体".len());
    canonical.push_str(&text[..start]);
    canonical.push_str("长方体");
    canonical.push_str(&text[suffix_start..]);
    Some(canonical)
}

#[cfg(test)]
mod tests {
    use super::canonicalize_box_alias;

    #[test]
    fn canonicalizes_only_leading_legacy_box_names() {
        for (input, expected) in [
            ("立方体 长20宽20高20", "长方体 长20宽20高20"),
            ("正方体 边长20", "长方体 边长20"),
            ("方块 20x20x20", "长方体 20x20x20"),
            ("矩形体 长20宽10高5", "长方体 长20宽10高5"),
            ("方形块 长20宽10高5", "长方体 长20宽10高5"),
            ("铝板 100x50x3", "长方体 100x50x3"),
            ("钢板 100x50x3", "长方体 100x50x3"),
            ("箱体 100x50x30", "长方体 100x50x30"),
            ("机座 100x50x30", "长方体 100x50x30"),
            ("基座 100x50x30", "长方体 100x50x30"),
            ("帮我画一个 方块 20x20x20", "帮我画一个 长方体 20x20x20"),
        ] {
            assert_eq!(
                canonicalize_box_alias(input).as_deref(),
                Some(expected),
                "{input}"
            );
        }
    }

    #[test]
    fn rejects_infix_or_unbounded_mentions() {
        for input in [
            "在长方体顶面开一个槽",
            "这是铝板材质的零件",
            "箱体结构",
            "请描述一个工件",
        ] {
            assert_eq!(canonicalize_box_alias(input), None, "{input}");
        }
    }
}
