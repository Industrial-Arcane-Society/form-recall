//! Deterministic fastener size parsing shared by native construction and provenance checks.

pub fn parse_bolt_size(original_size: &str) -> Result<(f64, f64), String> {
    let normalized = original_size
        .trim()
        .to_ascii_uppercase()
        .replace([' ', '\t'], "")
        .replace(['×', '✕', '*', '-'], "X");
    let spec = normalized
        .strip_prefix('M')
        .ok_or("螺钉规格须为 M5x8 格式。")?;
    let (diameter_text, length_text) = spec
        .split_once('X')
        .ok_or("螺钉规格须同时提供 M 规格和长度，例如 M5x8。")?;
    let diameter = diameter_text
        .parse::<f64>()
        .map_err(|_| "螺钉公称直径无效。")?;
    let length = length_text.parse::<f64>().map_err(|_| "螺钉长度无效。")?;
    if !diameter.is_finite() || diameter <= 0.0 || !length.is_finite() || length <= 0.0 {
        return Err("螺钉公称直径和长度必须为正有限数。".into());
    }
    if diameter.fract().abs() > 1e-9 {
        return Err("本地 ISO 4762 标准表只包含整数 M 规格。".into());
    }
    Ok((diameter, length))
}

/// Return the exact M-size token from the user turn if it supports both requested values.
pub fn find_bolt_size_quote(prompt: &str, diameter: f64, length: f64) -> Option<String> {
    let chars = prompt.chars().collect::<Vec<_>>();
    for start in 0..chars.len() {
        if !matches!(chars[start], 'M' | 'm')
            || (start > 0 && (chars[start - 1].is_ascii_alphanumeric() || chars[start - 1] == '_'))
        {
            continue;
        }
        let mut cursor = start + 1;
        skip_space(&chars, &mut cursor);
        if !consume_number(&chars, &mut cursor) {
            continue;
        }
        skip_space(&chars, &mut cursor);
        if cursor >= chars.len() || !matches!(chars[cursor], 'x' | 'X' | '×' | '✕' | '*' | '-') {
            continue;
        }
        cursor += 1;
        skip_space(&chars, &mut cursor);
        if !consume_number(&chars, &mut cursor) {
            continue;
        }
        let quote = chars[start..cursor].iter().collect::<String>();
        if let Ok((quoted_diameter, quoted_length)) = parse_bolt_size(&quote)
            && (quoted_diameter - diameter).abs() <= 1e-9
            && (quoted_length - length).abs() <= 1e-9
        {
            return Some(quote);
        }
    }
    None
}

/// Interpret values only from an exact fastener size token, not from nearby unrelated numbers.
pub fn user_size_value(quote: &str, parameter: &str) -> Option<f64> {
    let (diameter, length) = parse_bolt_size(quote).ok()?;
    match parameter {
        "shank_d" => Some(diameter),
        "length" => Some(length),
        _ => None,
    }
}

fn skip_space(chars: &[char], cursor: &mut usize) {
    while chars
        .get(*cursor)
        .is_some_and(|value| value.is_whitespace())
    {
        *cursor += 1;
    }
}

fn consume_number(chars: &[char], cursor: &mut usize) -> bool {
    let start = *cursor;
    let mut saw_digit = false;
    let mut saw_decimal = false;
    while let Some(value) = chars.get(*cursor) {
        if value.is_ascii_digit() {
            saw_digit = true;
            *cursor += 1;
        } else if *value == '.' && !saw_decimal {
            saw_decimal = true;
            *cursor += 1;
        } else {
            break;
        }
    }
    if saw_digit {
        true
    } else {
        *cursor = start;
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_common_bolt_size_separators_case_insensitively() {
        for size in ["M5x8", "m5X8", "M5×8", "M5 ✕ 8", "M5*8", "M5-8"] {
            assert_eq!(parse_bolt_size(size).unwrap(), (5.0, 8.0), "{size}");
        }
        assert_eq!(parse_bolt_size("M5x8.5").unwrap(), (5.0, 8.5));
    }

    #[test]
    fn rejects_invalid_or_unsupported_bolt_sizes_without_guessing() {
        for size in ["M5", "M5x", "M5x8x2", "M5x0", "M0x8", "M5.5x8", "5x8"] {
            assert!(parse_bolt_size(size).is_err(), "{size}");
        }
    }

    #[test]
    fn extracts_exact_user_size_and_maps_spec_positions() {
        let prompt = "帮我做一颗 M5 × 8 mm 的内六角螺钉，旁边尺寸是 100 mm";
        let quote = find_bolt_size_quote(prompt, 5.0, 8.0).unwrap();
        assert_eq!(quote, "M5 × 8");
        assert_eq!(user_size_value(&quote, "shank_d"), Some(5.0));
        assert_eq!(user_size_value(&quote, "length"), Some(8.0));
        assert_eq!(user_size_value(&quote, "head_d"), None);
        assert!(find_bolt_size_quote(prompt, 5.0, 10.0).is_none());
    }
}
